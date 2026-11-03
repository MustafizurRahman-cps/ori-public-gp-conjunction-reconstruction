#!/usr/bin/env python3
"""Recover G7 R6 A2 materialization after the post-pass dynamic-import failure.

This recovery is intentionally narrow.

The original materializer completed:
  * the single 1,386-file raw-history pass,
  * inline SHA verification of every main raw file,
  * construction of the 2,020,081 replay-update schedule,
  * construction of the 107,740 temporal-support subset, and
  * required predecessor-payload materialization.

It then failed before TLE parse-readiness evaluation because its generic
importlib helper executed a dataclass-bearing module without first registering
that module in sys.modules.

This script DOES NOT reread GP_HISTORY and DOES NOT repeat materialization.
It opens only the preserved incomplete SQLite checkpoint, repairs the dynamic
module-loading mechanism in memory, performs the remaining TLE parse-readiness
stage, finalizes the checkpoint, and writes the small summary/audit artifacts.

No orbital propagation, candidate generation, reference association, S05/S06/
S07 calculation, CAL0 refit, or network access is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_HEAD = "da5dfeb8f18d3d4ed9a96a33e3ba64010debe839"
ARCHITECTURE_TAG = "g7-r6-a2-execution-architecture-frozen"

ORIGINAL_MATERIALIZER = (
    ROOT / "scripts/g7/materialize_g7r6_a2_inputs.py"
)
EXPECTED_MATERIALIZER_SHA256 = (
    "182fa120dc2320571ea4191ce6ab04a1192934de6602ee746cfedd23d5295584"
)

INCOMPLETE_DB = (
    ROOT / "data/raw/g7/G7R6_A2_Materialized.incomplete.sqlite3"
)
FINAL_DB = (
    ROOT / "data/raw/g7/G7R6_A2_Materialized.sqlite3"
)
SUMMARY_OUT = (
    ROOT / "results/g7/G7R6_A2_Materialization_Summary.json"
)
AUDIT_OUT = (
    ROOT / "results/g7/G7R6_A2_Materialization_Audit.txt"
)

EXPECTED_EVENTS = 6_927
EXPECTED_OBJECTS = 13_854
EXPECTED_MAIN_FILES = 1_386
EXPECTED_RAW_ROWS = 5_245_230
EXPECTED_LOCAL_MAIN_ROWS = 5_153_040
EXPECTED_EXCLUDED_TRANSPORT_ROWS = (
    EXPECTED_RAW_ROWS - EXPECTED_LOCAL_MAIN_ROWS
)

EXPECTED_ELIGIBLE_REPLAYS = 2_020_081
EXPECTED_TEMPORAL_SUPPORT_REPLAYS = 107_740
EXPECTED_TOO_EARLY_REPLAYS = 1_912_341
EXPECTED_ZERO_SUPPORT_EVENTS = 1


class RecoveryError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RecoveryError(message)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_load_module(name: str, path: Path):
    """importlib loader compatible with dataclasses + postponed annotations."""
    spec = importlib.util.spec_from_file_location(name, path)
    require(
        spec is not None and spec.loader is not None,
        f"cannot load module: {path}",
    )

    module = importlib.util.module_from_spec(spec)

    # Critical correction:
    # dataclasses resolves annotation context through
    # sys.modules[cls.__module__].  The original loader omitted this step.
    prior = sys.modules.get(name)
    sys.modules[name] = module

    try:
        spec.loader.exec_module(module)
    except Exception:
        if prior is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = prior
        raise

    return module


def load_original_materializer():
    require(
        ORIGINAL_MATERIALIZER.is_file(),
        "original materializer source is missing",
    )
    observed = sha256(ORIGINAL_MATERIALIZER)
    require(
        observed == EXPECTED_MATERIALIZER_SHA256,
        "original materializer source hash changed\n"
        f"observed={observed}\n"
        f"expected={EXPECTED_MATERIALIZER_SHA256}",
    )

    # Use a normal import so ObjectSpec itself is registered correctly.
    g7_dir = str(ORIGINAL_MATERIALIZER.parent)
    if g7_dir not in sys.path:
        sys.path.insert(0, g7_dir)

    import materialize_g7r6_a2_inputs as m

    require(
        Path(m.__file__).resolve() == ORIGINAL_MATERIALIZER.resolve(),
        "unexpected materializer module was imported",
    )
    require(
        sha256(Path(m.__file__)) == EXPECTED_MATERIALIZER_SHA256,
        "imported materializer hash changed",
    )

    # Patch only the defective generic dynamic loader, in memory.
    m.load_module = safe_load_module
    return m


def verify_repository_boundary(m) -> tuple[dict[str, str], dict]:
    m.verify_repository_boundary()

    require(
        m.git_value("rev-parse", "HEAD") == EXPECTED_HEAD,
        "unexpected repository HEAD",
    )
    require(
        m.git_value(
            "rev-parse",
            f"refs/tags/{ARCHITECTURE_TAG}^{{commit}}",
        )
        == EXPECTED_HEAD,
        "architecture tag target changed",
    )

    frozen_hashes = m.verify_small_hashes()
    environment = m.verify_environment()
    m.verify_db_ignore()

    require(
        INCOMPLETE_DB.is_file(),
        "preserved incomplete materialization database is missing",
    )
    require(
        not FINAL_DB.exists(),
        "final materialized database already exists",
    )
    require(
        not SUMMARY_OUT.exists(),
        "materialization summary already exists",
    )
    require(
        not AUDIT_OUT.exists(),
        "materialization audit already exists",
    )

    return frozen_hashes, environment


def verify_checkpoint_before_recovery(
    connection: sqlite3.Connection,
    m,
) -> dict:
    quick = connection.execute("PRAGMA quick_check").fetchone()[0]
    require(quick == "ok", f"incomplete SQLite quick_check failed: {quick}")

    fk_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
    require(not fk_rows, f"foreign-key violations in incomplete DB: {fk_rows[:5]}")

    metadata = dict(
        connection.execute(
            "SELECT key,value FROM metadata"
        ).fetchall()
    )

    expected_metadata = {
        "schema_version": "G7R6_A2_MATERIALIZED_V1",
        "parent_head": EXPECTED_HEAD,
        "architecture_tag": ARCHITECTURE_TAG,
        "events": str(EXPECTED_EVENTS),
        "objects": str(EXPECTED_OBJECTS),
        "expected_main_files": str(EXPECTED_MAIN_FILES),
        "expected_eligible_replays": str(EXPECTED_ELIGIBLE_REPLAYS),
        "expected_temporal_support_replays":
            str(EXPECTED_TEMPORAL_SUPPORT_REPLAYS),
        "materializer_sha256": EXPECTED_MATERIALIZER_SHA256,
    }

    for key, expected in expected_metadata.items():
        require(
            metadata.get(key) == expected,
            f"incomplete DB metadata mismatch: {key}",
        )

    require(
        not any(key.startswith("result::") for key in metadata),
        "result metadata already exists; refusing duplicate recovery",
    )

    counts = {
        "events": int(
            connection.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]
        ),
        "objects": int(
            connection.execute(
                "SELECT COUNT(*) FROM objects"
            ).fetchone()[0]
        ),
        "main_raw_paths": int(
            connection.execute(
                "SELECT COUNT(DISTINCT main_raw_path) FROM objects"
            ).fetchone()[0]
        ),
        "replay_updates": int(
            connection.execute(
                "SELECT COUNT(*) FROM replay_updates"
            ).fetchone()[0]
        ),
        "temporal_support_updates": int(
            connection.execute(
                "SELECT COUNT(*) FROM replay_updates "
                "WHERE temporal_support=1"
            ).fetchone()[0]
        ),
        "too_early_updates": int(
            connection.execute(
                "SELECT COUNT(*) FROM replay_updates "
                "WHERE temporal_support=0"
            ).fetchone()[0]
        ),
        "history_records": int(
            connection.execute(
                "SELECT COUNT(*) FROM history_records"
            ).fetchone()[0]
        ),
        "support_payloads": int(
            connection.execute(
                "SELECT COUNT(*) FROM support_payloads"
            ).fetchone()[0]
        ),
        "invalid_quality_records": int(
            connection.execute(
                "SELECT COUNT(*) FROM invalid_quality_records"
            ).fetchone()[0]
        ),
    }

    require(counts["events"] == EXPECTED_EVENTS, "checkpoint event count changed")
    require(counts["objects"] == EXPECTED_OBJECTS, "checkpoint object count changed")
    require(
        counts["main_raw_paths"] == EXPECTED_MAIN_FILES,
        "checkpoint main raw-path count changed",
    )
    require(
        counts["replay_updates"] == EXPECTED_ELIGIBLE_REPLAYS,
        "checkpoint eligible replay count changed",
    )
    require(
        counts["temporal_support_updates"]
        == EXPECTED_TEMPORAL_SUPPORT_REPLAYS,
        "checkpoint temporal-support count changed",
    )
    require(
        counts["too_early_updates"] == EXPECTED_TOO_EARLY_REPLAYS,
        "checkpoint too-early count changed",
    )

    mismatch_eligible = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT
                    e.event_index,
                    e.expected_eligible_replays,
                    COUNT(r.replay_sequence) AS actual_count
                FROM events e
                LEFT JOIN replay_updates r
                  ON r.event_index=e.event_index
                GROUP BY e.event_index
                HAVING actual_count != e.expected_eligible_replays
            )
            """
        ).fetchone()[0]
    )
    require(
        mismatch_eligible == 0,
        f"per-event eligible replay mismatches: {mismatch_eligible}",
    )

    mismatch_support = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT
                    e.event_index,
                    e.expected_support_replays,
                    SUM(CASE WHEN r.temporal_support=1 THEN 1 ELSE 0 END)
                        AS actual_count
                FROM events e
                LEFT JOIN replay_updates r
                  ON r.event_index=e.event_index
                GROUP BY e.event_index
                HAVING actual_count != e.expected_support_replays
            )
            """
        ).fetchone()[0]
    )
    require(
        mismatch_support == 0,
        f"per-event support replay mismatches: {mismatch_support}",
    )

    zero_support_events = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM events
            WHERE expected_support_replays=0
            """
        ).fetchone()[0]
    )
    require(
        zero_support_events == EXPECTED_ZERO_SUPPORT_EVENTS,
        "zero-support event count changed",
    )

    # Exact support-selected payload identities.
    required_support_keys: set[str] = set()
    for first, second in connection.execute(
        """
        SELECT obj1_record_key, obj2_record_key
        FROM replay_updates
        WHERE temporal_support=1
        """
    ):
        required_support_keys.add(str(first))
        required_support_keys.add(str(second))

    available_payloads = {
        str(row[0]): str(row[1])
        for row in connection.execute(
            """
            SELECT record_key, source_class
            FROM support_payloads
            """
        )
    }

    missing = required_support_keys - set(available_payloads)
    require(
        not missing,
        "support-payload coverage is incomplete before recovery: "
        f"{len(missing)} missing",
    )

    # The failure occurred before the first parse row. Enforce that fact
    # rather than silently resuming an unknown partially parsed state.
    evaluated_required = {
        str(row[0]): str(row[1])
        for row in connection.execute(
            """
            SELECT record_key, parse_status
            FROM support_payloads
            WHERE parse_status != 'NOT_EVALUATED'
            """
        )
        if str(row[0]) in required_support_keys
    }
    require(
        not evaluated_required,
        "required support payloads are already partially parse-evaluated; "
        "this recovery script is intentionally not a generic retry tool",
    )

    selected_source_counts = Counter(
        available_payloads[key]
        for key in required_support_keys
    )

    # In the original materializer, a support-selected acquired predecessor
    # can enter support_payloads only after opening/hash-verifying its dedicated
    # raw predecessor file. Likewise a selected captured predecessor is
    # inserted from the already-completed main raw pass.
    predecessor_resolution = {
        "support_payload_keys_required": len(required_support_keys),
        "captured_predecessor_payloads_inserted": int(
            selected_source_counts[
                "PREDECESSOR_CAPTURED_IN_BATCH_UNION"
            ]
        ),
        "acquired_predecessor_raw_files_opened": int(
            selected_source_counts[
                "PREDECESSOR_ACQUIRED_BY_QUERY"
            ]
        ),
    }

    return {
        "metadata": metadata,
        "counts": counts,
        "required_support_keys": required_support_keys,
        "predecessor_resolution": predecessor_resolution,
        "zero_support_events": zero_support_events,
    }


def recover() -> dict:
    m = load_original_materializer()
    frozen_hashes, environment = verify_repository_boundary(m)

    connection = sqlite3.connect(INCOMPLETE_DB)
    try:
        checkpoint = verify_checkpoint_before_recovery(
            connection,
            m,
        )

        required_support_keys = checkpoint["required_support_keys"]

        # This is the only scientific-computation continuation performed here:
        # TLE construction/parse validity only. satrec_from_record does not
        # propagate an orbit.
        parse_audit = m.parse_support_payloads(
            connection,
            required_support_keys,
        )

        invalid_quality_count = checkpoint["counts"][
            "invalid_quality_records"
        ]

        database_counts = {
            "history_records": int(
                connection.execute(
                    "SELECT COUNT(*) FROM history_records"
                ).fetchone()[0]
            ),
            "support_payloads": int(
                connection.execute(
                    "SELECT COUNT(*) FROM support_payloads"
                ).fetchone()[0]
            ),
            "replay_updates": int(
                connection.execute(
                    "SELECT COUNT(*) FROM replay_updates"
                ).fetchone()[0]
            ),
            "temporal_support_updates": int(
                connection.execute(
                    "SELECT COUNT(*) FROM replay_updates "
                    "WHERE temporal_support=1"
                ).fetchone()[0]
            ),
        }

        final_status = (
            "PASS"
            if invalid_quality_count == 0
            else "HOLD_INVALID_30D_STATE_SIGNATURE"
        )

        predecessor_resolution = checkpoint["predecessor_resolution"]

        result_metadata = {
            "status": final_status,
            "recovery_mode":
                "POST_RAW_PASS_IMPORT_LOADER_FAILURE_RECOVERY",
            "raw_history_reread_during_recovery": False,
            "raw_rows_scanned_initial_pass": EXPECTED_RAW_ROWS,
            "exact_local_main_rows_initial_pass":
                EXPECTED_LOCAL_MAIN_ROWS,
            "transport_rows_excluded_from_exact_local_intervals":
                EXPECTED_EXCLUDED_TRANSPORT_ROWS,
            "main_files_opened_initial_pass": EXPECTED_MAIN_FILES,
            "main_files_inline_sha_verified_initial_pass":
                EXPECTED_MAIN_FILES,
            "quality_source_rows_materialized":
                database_counts["history_records"],
            "invalid_quality_state_signature_rows":
                invalid_quality_count,
            "eligible_replay_updates":
                EXPECTED_ELIGIBLE_REPLAYS,
            "temporal_support_replay_updates":
                EXPECTED_TEMPORAL_SUPPORT_REPLAYS,
            "provably_too_early_updates":
                EXPECTED_TOO_EARLY_REPLAYS,
            "zero_support_events":
                EXPECTED_ZERO_SUPPORT_EVENTS,
            **predecessor_resolution,
            **parse_audit,
            **database_counts,
        }

        m.finalize_db_metadata(
            connection,
            result_metadata,
        )

        integrity = connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
        require(
            integrity == "ok",
            f"SQLite integrity check failed after recovery: {integrity}",
        )

        fk_rows = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        require(
            not fk_rows,
            "foreign-key violations after recovery",
        )

        connection.commit()

    except Exception:
        connection.close()
        raise
    else:
        connection.close()

    # The raw materialization itself is not repeated. Only after all recovery
    # checks pass is the preserved incomplete checkpoint promoted atomically.
    os.replace(INCOMPLETE_DB, FINAL_DB)

    db_sha = sha256(FINAL_DB)
    recovery_sha = sha256(Path(__file__))

    summary = {
        "status": result_metadata["status"],
        "analysis_role": "G7_R6_A2_CAUSAL_HISTORY_MATERIALIZATION",
        "recovery": {
            "performed": True,
            "reason":
                "ORIGINAL_DYNAMIC_IMPORT_HELPER_OMITTED_SYS_MODULES_REGISTRATION",
            "raw_history_reread": False,
            "original_materializer_sha256":
                EXPECTED_MATERIALIZER_SHA256,
            "recovery_script_sha256":
                recovery_sha,
        },
        "parent_head": EXPECTED_HEAD,
        "architecture_tag": ARCHITECTURE_TAG,
        "materializer_sha256": EXPECTED_MATERIALIZER_SHA256,
        "materialized_db": m.relative(FINAL_DB),
        "materialized_db_sha256": db_sha,
        "materialized_db_committable": False,
        "events": EXPECTED_EVENTS,
        "objects": EXPECTED_OBJECTS,
        "reporting_window_days": m.REPORTING_DAYS,
        "quality_window_days": m.QUALITY_WINDOW_DAYS,
        "primary_horizon_days": m.PRIMARY_HORIZON_DAYS,
        "association_tolerance_min": m.ASSOCIATION_TOLERANCE_MIN,
        "frozen_input_hashes": frozen_hashes,
        "environment": environment,
        "raw_history": {
            "main_files_opened_initial_pass": EXPECTED_MAIN_FILES,
            "main_files_inline_sha_verified_initial_pass":
                EXPECTED_MAIN_FILES,
            "raw_history_files_reopened_during_recovery": 0,
            "transported_rows_scanned_initial_pass":
                EXPECTED_RAW_ROWS,
            "exact_local_main_rows_initial_pass":
                EXPECTED_LOCAL_MAIN_ROWS,
            "transport_rows_excluded_from_exact_local_intervals":
                EXPECTED_EXCLUDED_TRANSPORT_ROWS,
            "excluded_row_breakdown_note":
                (
                    "The original post-pass failure occurred after these "
                    "rows had been processed but before its in-memory split "
                    "between non-target transport rows and target rows outside "
                    "object-local intervals was persisted. The exact combined "
                    "excluded count is retained; the split is not invented."
                ),
        },
        "replay_schedule": {
            "eligible_updates": EXPECTED_ELIGIBLE_REPLAYS,
            "temporal_support_updates":
                EXPECTED_TEMPORAL_SUPPORT_REPLAYS,
            "provably_too_early_updates":
                EXPECTED_TOO_EARLY_REPLAYS,
            "zero_support_events":
                EXPECTED_ZERO_SUPPORT_EVENTS,
            "per_event_eligible_reconciliation": "PASS",
            "per_event_support_reconciliation": "PASS",
        },
        "quality_materialization": {
            "history_records":
                database_counts["history_records"],
            "invalid_state_signature_rows":
                invalid_quality_count,
            "window_semantics": "(t_r-30d,t_r]",
        },
        "support_payload_materialization": {
            **predecessor_resolution,
            **parse_audit,
            "support_payload_rows":
                database_counts["support_payloads"],
        },
        "operations": {
            "TLE_parse_validity_checked": True,
            "SGP4_orbital_propagation_performed": False,
            "candidate_generation_performed": False,
            "candidate_refinement_performed": False,
            "reference_association_performed": False,
            "S05_computed": False,
            "S06_computed": False,
            "S07_computed": False,
            "CAL0_refit_performed": False,
            "network_requests": 0,
        },
        "interpretation": (
            "Materialization/parse-readiness evidence only. "
            "PARSE_READY_BOTH does not imply propagation success, candidate "
            "existence, reference association, reconstruction accuracy, "
            "warning performance, or collision risk."
        ),
        "next_safe_action": (
            "INDEPENDENT_MATERIALIZATION_AUDIT"
            if result_metadata["status"] == "PASS"
            else "HOLD_AND_REVIEW_INVALID_30D_STATE_SIGNATURE_ROWS"
        ),
    }

    m.atomic_json(SUMMARY_OUT, summary)

    audit_lines = [
        "G7 R6 A2 MATERIALIZATION RECOVERY AUDIT",
        "========================================",
        "",
        f"STATUS = {summary['status']}",
        f"PARENT_HEAD = {EXPECTED_HEAD}",
        f"ARCHITECTURE_TAG = {ARCHITECTURE_TAG}",
        (
            "ORIGINAL_MATERIALIZER_SHA256 = "
            f"{EXPECTED_MATERIALIZER_SHA256}"
        ),
        f"RECOVERY_SCRIPT_SHA256 = {recovery_sha}",
        "",
        (
            "RECOVERY_REASON = "
            "DYNAMIC_IMPORT_MODULE_NOT_REGISTERED_IN_SYS_MODULES"
        ),
        "RAW_HISTORY_REREAD_DURING_RECOVERY = false",
        "",
        f"MAIN_FILES_OPENED_INITIAL_PASS = {EXPECTED_MAIN_FILES}",
        (
            "MAIN_FILES_INLINE_SHA_VERIFIED_INITIAL_PASS = "
            f"{EXPECTED_MAIN_FILES}"
        ),
        f"RAW_ROWS_SCANNED_INITIAL_PASS = {EXPECTED_RAW_ROWS}",
        (
            "EXACT_LOCAL_MAIN_ROWS_INITIAL_PASS = "
            f"{EXPECTED_LOCAL_MAIN_ROWS}"
        ),
        (
            "TRANSPORT_ROWS_EXCLUDED_FROM_EXACT_LOCAL_INTERVALS = "
            f"{EXPECTED_EXCLUDED_TRANSPORT_ROWS}"
        ),
        (
            "EXCLUDED_ROW_SPLIT = NOT_RECONSTRUCTED_AFTER_POST_PASS_FAILURE"
        ),
        "",
        f"ELIGIBLE_REPLAY_UPDATES = {EXPECTED_ELIGIBLE_REPLAYS}",
        (
            "TEMPORAL_SUPPORT_UPDATES = "
            f"{EXPECTED_TEMPORAL_SUPPORT_REPLAYS}"
        ),
        f"PROVABLY_TOO_EARLY_UPDATES = {EXPECTED_TOO_EARLY_REPLAYS}",
        f"ZERO_SUPPORT_EVENTS = {EXPECTED_ZERO_SUPPORT_EVENTS}",
        "PER_EVENT_ELIGIBLE_RECONCILIATION = PASS",
        "PER_EVENT_SUPPORT_RECONCILIATION = PASS",
        "",
        (
            "SUPPORT_PAYLOAD_KEYS_REQUIRED = "
            f"{predecessor_resolution['support_payload_keys_required']}"
        ),
        (
            "CAPTURED_PREDECESSOR_PAYLOADS_INSERTED = "
            f"{predecessor_resolution['captured_predecessor_payloads_inserted']}"
        ),
        (
            "ACQUIRED_PREDECESSOR_RAW_FILES_OPENED_INITIAL_PASS = "
            f"{predecessor_resolution['acquired_predecessor_raw_files_opened']}"
        ),
        (
            "UNIQUE_SUPPORT_PAYLOAD_PARSE_SUCCESS = "
            f"{parse_audit['unique_support_payload_parse_success']}"
        ),
        (
            "UNIQUE_SUPPORT_PAYLOAD_PARSE_FAILURE = "
            f"{parse_audit['unique_support_payload_parse_failure']}"
        ),
        (
            "PROPAGATION_READY_SUPPORT_REPLAYS = "
            f"{parse_audit['propagation_ready_support_replays']}"
        ),
        (
            "SUPPORT_PARSE_FAILURE_REPLAYS = "
            f"{parse_audit['support_parse_failure_replays']}"
        ),
        "",
        (
            "QUALITY_SOURCE_ROWS_MATERIALIZED = "
            f"{database_counts['history_records']}"
        ),
        (
            "INVALID_30D_STATE_SIGNATURE_ROWS = "
            f"{invalid_quality_count}"
        ),
        "",
        f"MATERIALIZED_DB_SHA256 = {db_sha}",
        "",
        "SGP4_ORBITAL_PROPAGATION_PERFORMED = false",
        "CANDIDATE_GENERATION_PERFORMED = false",
        "REFERENCE_ASSOCIATION_PERFORMED = false",
        "S05_COMPUTED = false",
        "S06_COMPUTED = false",
        "S07_COMPUTED = false",
        "CAL0_REFIT_PERFORMED = false",
        "NETWORK_REQUESTS = 0",
        "",
        "NEXT_ACTION = " + summary["next_safe_action"],
    ]

    m.atomic_text(
        AUDIT_OUT,
        "\n".join(audit_lines) + "\n",
    )

    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--recover-postpass",
        action="store_true",
        required=True,
    )
    parser.add_argument(
        "--authorize-recovery",
        action="store_true",
        required=True,
    )
    args = parser.parse_args()

    require(args.recover_postpass, "post-pass recovery flag missing")
    require(args.authorize_recovery, "explicit recovery authorization missing")

    recover()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
