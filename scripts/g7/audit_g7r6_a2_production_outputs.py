#!/usr/bin/env python3
"""Independent integrity audit of completed G7 R6 A2 production outputs.

This audit performs NO orbital propagation, candidate generation, candidate
refinement, reference association, CAL0 fitting, raw GP_HISTORY access, or
network access.

It independently verifies:
  * authorization commit/tag and frozen artifact hashes;
  * final output cryptographic linkage;
  * materialized-input and checkpoint database identities/integrity;
  * exact 107,740-row temporal-support membership;
  * exact CSV <-> checkpoint payload equivalence;
  * checkpoint per-row payload SHA and per-event transaction SHA;
  * deterministic row ordering and identity uniqueness;
  * replay/event identities against the frozen materialized schedule;
  * status/association semantic consistency;
  * TCA/time arithmetic for associated rows;
  * aggregate counts/sums independently recomputed from final rows.

PASS is an execution/integrity result only. It does not compute or interpret
A2 S05/S06/S07 or Q01-Q05 metrics and does not authorize A0.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

AUTHORIZATION_COMMIT = "7f4ef0a20583b9fa6edcee9fa9f1e9781c97aa64"
AUTHORIZATION_PARENT = "44b9e8c41142774db03797b58f423b623b227862"
AUTHORIZATION_TAG = "g7-r6-a2-production-authorized"

MATERIALIZED_DB = ROOT / "data/raw/g7/G7R6_A2_Materialized.sqlite3"
WORK_DB = ROOT / "data/raw/g7/G7R6_A2_Production_Work.sqlite3"

RESULT_CSV = ROOT / "results/g7/G7R6_A2_Production_Replay_Results.csv"
SUMMARY = ROOT / "results/g7/G7R6_A2_Production_Summary.json"
EXECUTOR_AUDIT = ROOT / "results/g7/G7R6_A2_Production_Execution_Audit.json"
AUTHORIZATION_AUDIT = ROOT / "results/g7/G7R6_A2_Production_Authorization_Execution_Audit.json"

PRODUCTION = ROOT / "scripts/g7/run_g7r6_a2_production.py"
AUTH_LAUNCHER = ROOT / "scripts/g7/run_g7r6_a2_production_authorization.py"
DR0063 = ROOT / "docs/research/DR-0063_G7_R6_A2_Full_Production_Execution_Authorization.md"

OUT = ROOT / "results/g7/G7R6_A2_Production_Independent_Audit.json"

EXPECTED_MATERIALIZED_DB_SHA256 = (
    "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"
)
EXPECTED_PRODUCTION_SHA256 = (
    "4a07fc8d1c96b3c82a71f7431cbe5b4b89d24ba5d984185a300286e14af5b185"
)
EXPECTED_AUTH_LAUNCHER_SHA256 = (
    "e6828b6e89c2c7e369349f3cc57fceeb9ee643500d5787aa3c6ddc67d10069ec"
)
EXPECTED_DR0063_SHA256 = (
    "9715f3db9050ae672c267dc8da4e1ef3c144cefe9715674844b235182d96793f"
)

EXPECTED_RESULT_CSV_SHA256 = (
    "a0217011e0695c240cbc1209f671c2ad7a8cb1948093b1f8c075146819e9efdd"
)
EXPECTED_SUMMARY_SHA256 = (
    "64a0f6ea2ecca370b1c70fbc37e73254e902c33b29b1299580d4236fced7ad83"
)
EXPECTED_EXECUTOR_AUDIT_SHA256 = (
    "1dcfcbc4c36b6e05da545c10c0998135df791780f98dd0e3e64909938f09b898"
)
EXPECTED_AUTHORIZATION_AUDIT_SHA256 = (
    "81d21a320fbad9c49aee2cf45654de3304511afc8a91b77d6682767698d77fe5"
)

EXPECTED_EVENTS = 6_927
EXPECTED_POSITIVE_SUPPORT_EVENTS = 6_926
EXPECTED_ZERO_SUPPORT_EVENTS = 1
EXPECTED_SUPPORT_REPLAYS = 107_740
EXPECTED_PUBLICATION_REPLAYS = 2_020_081
EXPECTED_NON_SUPPORT_REPLAYS = 1_912_341

EXPECTED_PREOUTCOME = {
    "CANDIDATE_SET_READY": 107_396,
    "NO_VALID_CANDIDATE": 3,
    "PROPAGATION_FAILURE": 6,
    "UNRECOVERED_REFINEMENT_FAILURE": 335,
}
EXPECTED_ASSOCIATION = {
    "NOT_APPLICABLE_PREOUTCOME_FAILURE": 344,
    "UNASSOCIATED_REFERENCE_EVENT": 950,
    "VALID_ASSOCIATED": 106_446,
}
EXPECTED_FINAL = {
    "NO_VALID_CANDIDATE": 3,
    "PROPAGATION_FAILURE": 6,
    "UNASSOCIATED_REFERENCE_EVENT": 950,
    "UNRECOVERED_REFINEMENT_FAILURE": 335,
    "VALID_ASSOCIATED": 106_446,
}

EXPECTED_CANDIDATE_ROWS_TOTAL = 14_716_478
EXPECTED_RAW_CANDIDATE_ROWS_TOTAL = 29_370_922
EXPECTED_SAMPLED_GRID_POINTS_TOTAL = 4_639_744_199
EXPECTED_RECOVERED_LOCAL_TOTAL = 13_615
EXPECTED_REJECTED_LOCAL_TOTAL = 13_615
EXPECTED_UNRESOLVED_BOUNDARY_TOTAL = 0

ASSOCIATION_TOLERANCE_S = 300.0
TIME_ARITHMETIC_TOLERANCE_S = 2e-6
FLOAT_ARITHMETIC_TOLERANCE_S = 2e-9
HEX64 = re.compile(r"^[0-9a-f]{64}$")

EXPECTED_RESULT_FIELDS = (
    "event_index",
    "conj_id",
    "g6_temporal_stratum",
    "reference_tca_utc",
    "replay_sequence",
    "replay_us",
    "replay_utc",
    "replay_pre_tca",
    "reference_lead_h",
    "obj1",
    "obj2",
    "obj1_record_key",
    "obj2_record_key",
    "obj1_GP_ID",
    "obj2_GP_ID",
    "parse_status",
    "propagation_status",
    "preoutcome_status",
    "rejected_local_inconsistency_count",
    "recovered_local_inconsistency_count",
    "unresolved_boundary_count",
    "sampled_count",
    "raw_candidate_count",
    "candidate_count",
    "candidate_set_sha256",
    "failure_detail",
    "association_status",
    "final_status",
    "selected_candidate_index",
    "selected_candidate_time_from_replay_s",
    "predicted_tca_utc",
    "predicted_miss_distance_km",
    "signed_tca_residual_s",
)

EXPECTED_CHECKPOINT_METADATA = {
    "checkpoint_schema_version": "1",
    "output_schema_version": "1",
    "executor_sha256": EXPECTED_PRODUCTION_SHA256,
    "parent_commit": "b257cc1ba6942d6ed0fc9b32502e8ed493650856",
    "parent_tag": "g7-r6-a2-pilot-execution-frozen",
    "materialized_db_sha256": EXPECTED_MATERIALIZED_DB_SHA256,
    "controller_sha256":
        "806aff3b06c6efd82726a4d93f0a0ad9e3ab8314e9f5904e4707e38680c4f8de",
    "expected_events": str(EXPECTED_EVENTS),
    "expected_positive_support_events": str(EXPECTED_POSITIVE_SUPPORT_EVENTS),
    "expected_support_replays": str(EXPECTED_SUPPORT_REPLAYS),
    "ordering": "event_index,replay_us,replay_sequence",
    "commit_unit": "complete_event",
    "primary_horizon_s": "432000.0",
    "association_tolerance_min": "5.0",
    "reference_association":
        "DOWNSTREAM_ONLY_AFTER_IMMUTABLE_CANDIDATE_SET",
    "reference_miss_distance": "PROHIBITED",
    "raw_GP_HISTORY": "PROHIBITED",
    "CAL0_refit": "PROHIBITED",
    "network_access": "PROHIBITED",
    "method_retuning": "PROHIBITED",
}


class AuditError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def sha256(path: Path, *, progress_label: str | None = None) -> str:
    h = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    next_report = 256 * 1024 * 1024
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
            done += len(block)
            if progress_label and done >= next_report:
                print(
                    f"{progress_label} = {done}/{total} bytes",
                    flush=True,
                )
                next_report += 256 * 1024 * 1024
    return h.hexdigest()


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def git_value(*args: str) -> str:
    return git(*args).stdout.strip()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def parse_utc(text: str) -> datetime:
    value = text.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None, f"naive UTC value: {text}")
    return result.astimezone(UTC)


def us_to_datetime(value: int) -> datetime:
    return datetime.fromtimestamp(int(value) / 1_000_000.0, tz=UTC)


def iso_utc(value: datetime) -> str:
    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def csv_string(value) -> str:
    return "" if value is None else str(value)


def canonical_json_bytes(payload) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def verify_lineage_and_hashes() -> dict:
    require(
        git_value("rev-parse", "HEAD") == AUTHORIZATION_COMMIT,
        "post-production audit must run at the frozen authorization commit",
    )
    require(
        git_value("rev-list", "-n", "1", AUTHORIZATION_TAG)
        == AUTHORIZATION_COMMIT,
        "production authorization tag target changed",
    )
    require(
        git_value("rev-parse", "HEAD^") == AUTHORIZATION_PARENT,
        "production authorization parent changed",
    )
    require(not OUT.exists(), "independent production audit output already exists")

    expected = {
        PRODUCTION: EXPECTED_PRODUCTION_SHA256,
        AUTH_LAUNCHER: EXPECTED_AUTH_LAUNCHER_SHA256,
        DR0063: EXPECTED_DR0063_SHA256,
        RESULT_CSV: EXPECTED_RESULT_CSV_SHA256,
        SUMMARY: EXPECTED_SUMMARY_SHA256,
        EXECUTOR_AUDIT: EXPECTED_EXECUTOR_AUDIT_SHA256,
        AUTHORIZATION_AUDIT: EXPECTED_AUTHORIZATION_AUDIT_SHA256,
    }

    observed = {}
    for path, expected_sha in expected.items():
        require(path.is_file(), f"required production artifact missing: {path}")
        actual = sha256(path)
        require(
            actual == expected_sha,
            "production artifact SHA mismatch\n"
            f"path={path.relative_to(ROOT).as_posix()}\n"
            f"actual={actual}\nexpected={expected_sha}",
        )
        observed[path.relative_to(ROOT).as_posix()] = actual

    return observed


def verify_json_linkages() -> dict:
    summary = load_json(SUMMARY)
    executor = load_json(EXECUTOR_AUDIT)
    authorization = load_json(AUTHORIZATION_AUDIT)

    require(
        summary["status"] == "G7R6_A2_PRODUCTION_EXECUTION_COMPLETED",
        "production summary status changed",
    )
    require(
        executor["status"] == "G7R6_A2_PRODUCTION_EXECUTION_AUDIT",
        "production executor-audit status changed",
    )
    require(
        authorization["status"]
        == "G7R6_A2_PRODUCTION_AUTHORIZATION_EXECUTION_COMPLETED",
        "production authorization-audit status changed",
    )

    require(
        summary["result_csv_sha256"] == EXPECTED_RESULT_CSV_SHA256,
        "summary/result hash linkage changed",
    )
    require(
        executor["result_csv_sha256"] == EXPECTED_RESULT_CSV_SHA256,
        "executor audit/result hash linkage changed",
    )
    require(
        executor["summary_json_sha256"] == EXPECTED_SUMMARY_SHA256,
        "executor audit/summary hash linkage changed",
    )
    require(
        authorization["production_result_csv_sha256"]
        == EXPECTED_RESULT_CSV_SHA256,
        "authorization audit/result hash linkage changed",
    )
    require(
        authorization["production_summary_sha256"]
        == EXPECTED_SUMMARY_SHA256,
        "authorization audit/summary hash linkage changed",
    )
    require(
        authorization["production_executor_audit_sha256"]
        == EXPECTED_EXECUTOR_AUDIT_SHA256,
        "authorization audit/executor-audit hash linkage changed",
    )

    require(
        authorization["authorization_commit"] == AUTHORIZATION_COMMIT,
        "authorization audit commit changed",
    )
    require(
        authorization["authorization_tag"] == AUTHORIZATION_TAG,
        "authorization audit tag changed",
    )
    require(
        authorization["authorization_tag_target"] == AUTHORIZATION_COMMIT,
        "authorization audit tag target changed",
    )
    require(
        int(authorization["replay_results_written"])
        == EXPECTED_SUPPORT_REPLAYS,
        "authorization audit replay count changed",
    )

    require(
        int(summary["replay_results_written"]) == EXPECTED_SUPPORT_REPLAYS,
        "summary replay-result count changed",
    )
    require(
        int(summary["positive_support_events"])
        == EXPECTED_POSITIVE_SUPPORT_EVENTS,
        "summary positive-support event count changed",
    )
    require(
        int(summary["zero_support_events"]) == EXPECTED_ZERO_SUPPORT_EVENTS,
        "summary zero-support event count changed",
    )
    require(
        int(summary["publication_replay_opportunities"])
        == EXPECTED_PUBLICATION_REPLAYS,
        "summary publication-replay count changed",
    )
    require(
        int(summary["temporal_non_support_replays"])
        == EXPECTED_NON_SUPPORT_REPLAYS,
        "summary temporal-non-support count changed",
    )
    require(
        int(summary["temporal_support_replays"]) == EXPECTED_SUPPORT_REPLAYS,
        "summary temporal-support count changed",
    )
    require(
        int(summary["resumed_from_completed_events"]) == 0
        and int(summary["resumed_from_completed_replays"]) == 0,
        "completed run unexpectedly reports checkpoint resume",
    )

    operations = summary["operations"]
    require(
        operations["raw_GP_HISTORY_opened"] is False,
        "summary reports raw GP_HISTORY access",
    )
    require(
        operations["SGP4_orbital_propagation_performed"] is True,
        "summary does not report authorized SGP4 propagation",
    )
    require(
        operations["candidate_generation_performed"] is True,
        "summary does not report candidate generation",
    )
    require(
        operations["reference_association_performed"] is True,
        "summary does not report reference association",
    )
    require(
        operations["reference_miss_distance_accessed"] is False,
        "summary reports reference miss-distance access",
    )
    require(
        operations["CAL0_refit_performed"] is False,
        "summary reports CAL0 refit",
    )
    require(
        int(operations["network_requests"]) == 0,
        "summary reports network access",
    )

    return {
        "summary": summary,
        "executor_audit": executor,
        "authorization_audit": authorization,
    }


def open_ro(path: Path) -> sqlite3.Connection:
    require(path.is_file(), f"SQLite database missing: {path}")
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def verify_materialized_db() -> tuple[str, dict]:
    actual_sha = sha256(
        MATERIALIZED_DB,
        progress_label="A2_POSTPRODUCTION_MATERIALIZED_DB_HASH_PROGRESS",
    )
    require(
        actual_sha == EXPECTED_MATERIALIZED_DB_SHA256,
        "materialized A2 database SHA changed",
    )

    connection = open_ro(MATERIALIZED_DB)
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        require(quick_check == "ok", "materialized DB quick_check failed")

        counts = {
            "events": int(
                connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            ),
            "publication_replays": int(
                connection.execute(
                    "SELECT COUNT(*) FROM replay_updates"
                ).fetchone()[0]
            ),
            "support_replays": int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM replay_updates
                    WHERE temporal_support=1
                    """
                ).fetchone()[0]
            ),
            "non_support_replays": int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM replay_updates
                    WHERE temporal_support=0
                    """
                ).fetchone()[0]
            ),
            "positive_support_events": int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM events
                    WHERE expected_support_replays > 0
                    """
                ).fetchone()[0]
            ),
            "zero_support_events": int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM events
                    WHERE expected_support_replays = 0
                    """
                ).fetchone()[0]
            ),
            "non_parse_ready_support": int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM replay_updates
                    WHERE temporal_support=1
                      AND pair_parse_status <> 'PARSE_READY_BOTH'
                    """
                ).fetchone()[0]
            ),
        }
    finally:
        connection.close()

    require(counts["events"] == EXPECTED_EVENTS, "materialized event count changed")
    require(
        counts["publication_replays"] == EXPECTED_PUBLICATION_REPLAYS,
        "materialized publication-replay count changed",
    )
    require(
        counts["support_replays"] == EXPECTED_SUPPORT_REPLAYS,
        "materialized support count changed",
    )
    require(
        counts["non_support_replays"] == EXPECTED_NON_SUPPORT_REPLAYS,
        "materialized non-support count changed",
    )
    require(
        counts["positive_support_events"] == EXPECTED_POSITIVE_SUPPORT_EVENTS,
        "materialized positive-support event count changed",
    )
    require(
        counts["zero_support_events"] == EXPECTED_ZERO_SUPPORT_EVENTS,
        "materialized zero-support event count changed",
    )
    require(
        counts["non_parse_ready_support"] == 0,
        "materialized support set contains non-parse-ready rows",
    )

    return actual_sha, counts


def verify_checkpoint_structure() -> tuple[str, dict, dict[int, sqlite3.Row]]:
    work_sha = sha256(
        WORK_DB,
        progress_label="A2_POSTPRODUCTION_CHECKPOINT_HASH_PROGRESS",
    )

    connection = open_ro(WORK_DB)
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        require(quick_check == "ok", "production checkpoint quick_check failed")

        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        require(
            {"metadata", "replay_results", "event_completion"}.issubset(tables),
            "production checkpoint schema incomplete",
        )

        metadata = dict(
            connection.execute("SELECT key, value FROM metadata").fetchall()
        )
        require(
            metadata == EXPECTED_CHECKPOINT_METADATA,
            "production checkpoint metadata changed",
        )

        replay_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM replay_results"
            ).fetchone()[0]
        )
        completion_rows = connection.execute(
            """
            SELECT event_index,
                   expected_support_replays,
                   completed_support_replays,
                   event_result_sha256
            FROM event_completion
            ORDER BY event_index
            """
        ).fetchall()

        require(
            replay_count == EXPECTED_SUPPORT_REPLAYS,
            "checkpoint replay row count changed",
        )
        require(
            len(completion_rows) == EXPECTED_POSITIVE_SUPPORT_EVENTS,
            "checkpoint event-completion count changed",
        )

        completion = {int(row["event_index"]): row for row in completion_rows}
        require(
            len(completion) == len(completion_rows),
            "duplicate event-completion identity",
        )

        orphan = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM replay_results r
                LEFT JOIN event_completion e
                  ON e.event_index = r.event_index
                WHERE e.event_index IS NULL
                """
            ).fetchone()[0]
        )
        require(orphan == 0, "checkpoint contains orphan replay rows")

        count_mismatch = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM event_completion e
                WHERE e.expected_support_replays <> e.completed_support_replays
                   OR e.completed_support_replays <> (
                       SELECT COUNT(*)
                       FROM replay_results r
                       WHERE r.event_index=e.event_index
                   )
                """
            ).fetchone()[0]
        )
        require(
            count_mismatch == 0,
            "checkpoint event/replay cardinality mismatch",
        )
    finally:
        connection.close()

    return work_sha, metadata, completion


def check_row_semantics(row: dict, mismatch_examples: list, counters: dict) -> None:
    def mismatch(message: str) -> None:
        counters["semantic_mismatches"] += 1
        if len(mismatch_examples) < 20:
            mismatch_examples.append(
                {
                    "event_index": row.get("event_index"),
                    "replay_sequence": row.get("replay_sequence"),
                    "message": message,
                }
            )

    pre = row["preoutcome_status"]
    assoc = row["association_status"]
    final = row["final_status"]

    try:
        candidate_count = int(row["candidate_count"])
        raw_candidate_count = int(row["raw_candidate_count"])
        sampled_count = int(row["sampled_count"])
        rejected = int(row["rejected_local_inconsistency_count"])
        recovered = int(row["recovered_local_inconsistency_count"])
        unresolved = int(row["unresolved_boundary_count"])
    except Exception as exc:
        mismatch(f"non-integer numerical diagnostic: {exc}")
        return

    if candidate_count < 0 or raw_candidate_count < 0 or sampled_count < 0:
        mismatch("negative candidate/sample diagnostic")
    if rejected < 0 or recovered < 0 or unresolved < 0:
        mismatch("negative recovery diagnostic")

    if not HEX64.fullmatch(row["candidate_set_sha256"]):
        mismatch("candidate_set_sha256 is not lowercase 64-hex")

    if pre == "CANDIDATE_SET_READY":
        if candidate_count <= 0:
            mismatch("CANDIDATE_SET_READY with candidate_count <= 0")
        if assoc not in {"VALID_ASSOCIATED", "UNASSOCIATED_REFERENCE_EVENT"}:
            mismatch("candidate-ready row has invalid association status")
        if final != assoc:
            mismatch("candidate-ready final_status != association_status")
    else:
        if assoc != "NOT_APPLICABLE_PREOUTCOME_FAILURE":
            mismatch("preoutcome failure has non-NA association status")
        if final != pre:
            mismatch("preoutcome failure final_status != preoutcome_status")
        if candidate_count != 0:
            mismatch("preoutcome failure retains candidates")

    selected_fields = (
        "selected_candidate_index",
        "selected_candidate_time_from_replay_s",
        "predicted_tca_utc",
        "predicted_miss_distance_km",
        "signed_tca_residual_s",
    )

    if assoc == "VALID_ASSOCIATED":
        if any(row[field] == "" for field in selected_fields):
            mismatch("VALID_ASSOCIATED row has blank selected/predicted field")
            return

        try:
            selected_index = int(row["selected_candidate_index"])
            candidate_time = float(
                row["selected_candidate_time_from_replay_s"]
            )
            predicted_distance = float(row["predicted_miss_distance_km"])
            signed_residual = float(row["signed_tca_residual_s"])
            replay_dt = parse_utc(row["replay_utc"])
            predicted_dt = parse_utc(row["predicted_tca_utc"])
            reference_dt = parse_utc(row["reference_tca_utc"])
        except Exception as exc:
            mismatch(f"VALID_ASSOCIATED field parse failure: {exc}")
            return

        if not (1 <= selected_index <= candidate_count):
            mismatch("selected candidate index outside candidate set")
        if candidate_time < 0.0 or candidate_time > 432000.0 + 1e-9:
            mismatch("selected candidate time outside frozen H=5d")
        if not math.isfinite(predicted_distance) or predicted_distance < 0.0:
            mismatch("predicted miss distance invalid")
        if not math.isfinite(signed_residual):
            mismatch("signed TCA residual non-finite")
        elif abs(signed_residual) > ASSOCIATION_TOLERANCE_S + 1e-9:
            mismatch("VALID_ASSOCIATED residual exceeds 5-min tolerance")

        derived_candidate_time = (predicted_dt - replay_dt).total_seconds()
        if abs(derived_candidate_time - candidate_time) > TIME_ARITHMETIC_TOLERANCE_S:
            mismatch("predicted TCA/replay arithmetic mismatch")

        derived_residual = (predicted_dt - reference_dt).total_seconds()
        if abs(derived_residual - signed_residual) > TIME_ARITHMETIC_TOLERANCE_S:
            mismatch("signed TCA residual arithmetic mismatch")

    elif assoc in {
        "UNASSOCIATED_REFERENCE_EVENT",
        "NOT_APPLICABLE_PREOUTCOME_FAILURE",
    }:
        if any(row[field] != "" for field in selected_fields):
            mismatch("non-associated row retains selected/predicted fields")


def audit_all_rows(completion: dict[int, sqlite3.Row]) -> dict:
    checkpoint = open_ro(WORK_DB)
    materialized = open_ro(MATERIALIZED_DB)

    checkpoint_cursor = checkpoint.execute(
        """
        SELECT event_index,
               replay_sequence,
               replay_us,
               payload_json,
               payload_sha256
        FROM replay_results
        ORDER BY event_index, replay_us, replay_sequence
        """
    )

    materialized_cursor = materialized.execute(
        """
        SELECT r.event_index,
               r.replay_sequence,
               r.replay_us,
               r.obj1_record_key,
               r.obj2_record_key,
               r.obj1_gp_id,
               r.obj2_gp_id,
               r.pair_parse_status,
               e.conj_id,
               e.obj1,
               e.obj2,
               e.reference_us,
               e.expected_support_replays
        FROM replay_updates r
        JOIN events e
          ON e.event_index = r.event_index
        WHERE r.temporal_support=1
        ORDER BY r.event_index, r.replay_us, r.replay_sequence
        """
    )

    preoutcome = Counter()
    association = Counter()
    final = Counter()

    aggregate = {
        "candidate_rows_total": 0,
        "raw_candidate_rows_total": 0,
        "sampled_grid_points_total": 0,
        "recovered_local_inconsistency_count_total": 0,
        "rejected_local_inconsistency_count_total": 0,
        "unresolved_boundary_count_total": 0,
    }

    mismatch_examples: list[dict] = []
    counters = {
        "csv_checkpoint_mismatches": 0,
        "checkpoint_payload_sha_mismatches": 0,
        "materialized_identity_mismatches": 0,
        "ordering_mismatches": 0,
        "duplicate_identity_mismatches": 0,
        "semantic_mismatches": 0,
        "event_digest_mismatches": 0,
        "event_count_mismatches": 0,
    }

    row_count = 0
    previous_key = None
    seen = set()

    current_event = None
    event_payloads: list[dict] = []

    def record_example(category: str, event_index, replay_sequence, message: str):
        if len(mismatch_examples) < 20:
            mismatch_examples.append(
                {
                    "category": category,
                    "event_index": event_index,
                    "replay_sequence": replay_sequence,
                    "message": message,
                }
            )

    def close_event(event_index: int | None, payloads: list[dict]) -> None:
        if event_index is None:
            return

        completion_row = completion.get(event_index)
        if completion_row is None:
            counters["event_count_mismatches"] += 1
            record_example(
                "event_completion",
                event_index,
                None,
                "checkpoint replay event missing event_completion row",
            )
            return

        expected_count = int(completion_row["expected_support_replays"])
        completed_count = int(completion_row["completed_support_replays"])

        if len(payloads) != expected_count or len(payloads) != completed_count:
            counters["event_count_mismatches"] += 1
            record_example(
                "event_completion",
                event_index,
                None,
                f"payload rows={len(payloads)}, "
                f"expected={expected_count}, completed={completed_count}",
            )

        digest = hashlib.sha256(canonical_json_bytes(payloads)).hexdigest()
        if digest != str(completion_row["event_result_sha256"]):
            counters["event_digest_mismatches"] += 1
            record_example(
                "event_digest",
                event_index,
                None,
                "event_result_sha256 mismatch",
            )

    try:
        with RESULT_CSV.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)

            require(
                tuple(reader.fieldnames or ()) == EXPECTED_RESULT_FIELDS,
                "production result CSV schema changed",
            )

            for csv_row in reader:
                row_count += 1

                checkpoint_row = checkpoint_cursor.fetchone()
                materialized_row = materialized_cursor.fetchone()

                if checkpoint_row is None:
                    raise AuditError(
                        "result CSV contains more rows than checkpoint"
                    )
                if materialized_row is None:
                    raise AuditError(
                        "result CSV contains more rows than materialized support schedule"
                    )

                event_index = int(csv_row["event_index"])
                replay_sequence = int(csv_row["replay_sequence"])
                replay_us = int(csv_row["replay_us"])
                identity = (event_index, replay_sequence)
                order_key = (event_index, replay_us, replay_sequence)

                if identity in seen:
                    counters["duplicate_identity_mismatches"] += 1
                    record_example(
                        "duplicate_identity",
                        event_index,
                        replay_sequence,
                        "duplicate (event_index,replay_sequence)",
                    )
                seen.add(identity)

                if previous_key is not None and order_key <= previous_key:
                    counters["ordering_mismatches"] += 1
                    record_example(
                        "ordering",
                        event_index,
                        replay_sequence,
                        f"non-increasing order after {previous_key}",
                    )
                previous_key = order_key

                payload_text = str(checkpoint_row["payload_json"])
                payload_sha = hashlib.sha256(
                    payload_text.encode("ascii")
                ).hexdigest()

                if payload_sha != str(checkpoint_row["payload_sha256"]):
                    counters["checkpoint_payload_sha_mismatches"] += 1
                    record_example(
                        "payload_sha",
                        event_index,
                        replay_sequence,
                        "checkpoint payload_sha256 mismatch",
                    )

                payload = json.loads(payload_text)

                for field in EXPECTED_RESULT_FIELDS:
                    if csv_row[field] != csv_string(payload.get(field)):
                        counters["csv_checkpoint_mismatches"] += 1
                        record_example(
                            "csv_checkpoint",
                            event_index,
                            replay_sequence,
                            f"field={field}; csv={csv_row[field]!r}; "
                            f"checkpoint={csv_string(payload.get(field))!r}",
                        )
                        break

                checkpoint_identity = (
                    int(checkpoint_row["event_index"]),
                    int(checkpoint_row["replay_sequence"]),
                    int(checkpoint_row["replay_us"]),
                )
                if checkpoint_identity != (
                    event_index,
                    replay_sequence,
                    replay_us,
                ):
                    counters["csv_checkpoint_mismatches"] += 1
                    record_example(
                        "checkpoint_identity",
                        event_index,
                        replay_sequence,
                        f"checkpoint={checkpoint_identity}",
                    )

                materialized_identity_pairs = {
                    "event_index": int(materialized_row["event_index"]),
                    "replay_sequence": int(materialized_row["replay_sequence"]),
                    "replay_us": int(materialized_row["replay_us"]),
                    "conj_id": str(materialized_row["conj_id"]),
                    "obj1": str(materialized_row["obj1"]),
                    "obj2": str(materialized_row["obj2"]),
                    "obj1_record_key": str(materialized_row["obj1_record_key"]),
                    "obj2_record_key": str(materialized_row["obj2_record_key"]),
                    "obj1_GP_ID": str(materialized_row["obj1_gp_id"]),
                    "obj2_GP_ID": str(materialized_row["obj2_gp_id"]),
                }

                for field, expected_value in materialized_identity_pairs.items():
                    if str(payload[field]) != str(expected_value):
                        counters["materialized_identity_mismatches"] += 1
                        record_example(
                            "materialized_identity",
                            event_index,
                            replay_sequence,
                            f"{field}: payload={payload[field]!r}, "
                            f"materialized={expected_value!r}",
                        )
                        break

                if str(materialized_row["pair_parse_status"]) != "PARSE_READY_BOTH":
                    counters["materialized_identity_mismatches"] += 1
                    record_example(
                        "materialized_parse",
                        event_index,
                        replay_sequence,
                        "temporal-support row not PARSE_READY_BOTH",
                    )

                expected_replay_utc = iso_utc(
                    us_to_datetime(int(materialized_row["replay_us"]))
                )
                expected_reference_utc = iso_utc(
                    us_to_datetime(int(materialized_row["reference_us"]))
                )

                if payload["replay_utc"] != expected_replay_utc:
                    counters["materialized_identity_mismatches"] += 1
                    record_example(
                        "replay_time",
                        event_index,
                        replay_sequence,
                        "replay_utc disagrees with materialized replay_us",
                    )

                if payload["reference_tca_utc"] != expected_reference_utc:
                    counters["materialized_identity_mismatches"] += 1
                    record_example(
                        "reference_time",
                        event_index,
                        replay_sequence,
                        "reference_tca_utc disagrees with materialized reference_us",
                    )

                expected_pre_tca = (
                    int(materialized_row["replay_us"])
                    < int(materialized_row["reference_us"])
                )
                if bool(payload["replay_pre_tca"]) != expected_pre_tca:
                    counters["materialized_identity_mismatches"] += 1
                    record_example(
                        "replay_pre_tca",
                        event_index,
                        replay_sequence,
                        "replay_pre_tca flag mismatch",
                    )

                expected_lead_h = (
                    int(materialized_row["reference_us"])
                    - int(materialized_row["replay_us"])
                ) / 3_600_000_000.0
                try:
                    actual_lead_h = float(payload["reference_lead_h"])
                    if abs(actual_lead_h - expected_lead_h) > FLOAT_ARITHMETIC_TOLERANCE_S:
                        counters["materialized_identity_mismatches"] += 1
                        record_example(
                            "reference_lead_h",
                            event_index,
                            replay_sequence,
                            "reference lead arithmetic mismatch",
                        )
                except Exception:
                    counters["materialized_identity_mismatches"] += 1
                    record_example(
                        "reference_lead_h",
                        event_index,
                        replay_sequence,
                        "reference lead not parseable",
                    )

                check_row_semantics(payload, mismatch_examples, counters)

                preoutcome[str(payload["preoutcome_status"])] += 1
                association[str(payload["association_status"])] += 1
                final[str(payload["final_status"])] += 1

                aggregate["candidate_rows_total"] += int(payload["candidate_count"])
                aggregate["raw_candidate_rows_total"] += int(
                    payload["raw_candidate_count"]
                )
                aggregate["sampled_grid_points_total"] += int(
                    payload["sampled_count"]
                )
                aggregate["recovered_local_inconsistency_count_total"] += int(
                    payload["recovered_local_inconsistency_count"]
                )
                aggregate["rejected_local_inconsistency_count_total"] += int(
                    payload["rejected_local_inconsistency_count"]
                )
                aggregate["unresolved_boundary_count_total"] += int(
                    payload["unresolved_boundary_count"]
                )

                if current_event is None:
                    current_event = event_index
                if event_index != current_event:
                    close_event(current_event, event_payloads)
                    current_event = event_index
                    event_payloads = []
                event_payloads.append(payload)

                if row_count % 10_000 == 0:
                    print(
                        "A2_POSTPRODUCTION_ROW_AUDIT_PROGRESS = "
                        f"{row_count}/{EXPECTED_SUPPORT_REPLAYS}",
                        flush=True,
                    )

            close_event(current_event, event_payloads)

        require(
            checkpoint_cursor.fetchone() is None,
            "checkpoint contains more replay rows than result CSV",
        )
        require(
            materialized_cursor.fetchone() is None,
            "materialized support schedule contains more rows than result CSV",
        )

    finally:
        checkpoint.close()
        materialized.close()

    require(row_count == EXPECTED_SUPPORT_REPLAYS, "result CSV row count changed")
    require(len(seen) == EXPECTED_SUPPORT_REPLAYS, "result identity uniqueness failed")

    recomputed_preoutcome = dict(sorted(preoutcome.items()))
    recomputed_association = dict(sorted(association.items()))
    recomputed_final = dict(sorted(final.items()))

    require(
        recomputed_preoutcome == EXPECTED_PREOUTCOME,
        f"independently recomputed preoutcome counts changed: {recomputed_preoutcome}",
    )
    require(
        recomputed_association == EXPECTED_ASSOCIATION,
        f"independently recomputed association counts changed: {recomputed_association}",
    )
    require(
        recomputed_final == EXPECTED_FINAL,
        f"independently recomputed final-status counts changed: {recomputed_final}",
    )

    expected_aggregate = {
        "candidate_rows_total": EXPECTED_CANDIDATE_ROWS_TOTAL,
        "raw_candidate_rows_total": EXPECTED_RAW_CANDIDATE_ROWS_TOTAL,
        "sampled_grid_points_total": EXPECTED_SAMPLED_GRID_POINTS_TOTAL,
        "recovered_local_inconsistency_count_total":
            EXPECTED_RECOVERED_LOCAL_TOTAL,
        "rejected_local_inconsistency_count_total":
            EXPECTED_REJECTED_LOCAL_TOTAL,
        "unresolved_boundary_count_total":
            EXPECTED_UNRESOLVED_BOUNDARY_TOTAL,
    }
    require(
        aggregate == expected_aggregate,
        f"independently recomputed diagnostic sums changed: {aggregate}",
    )

    total_mismatches = sum(counters.values())

    return {
        "rows_audited": row_count,
        "unique_replay_identities": len(seen),
        "mismatch_counts": counters,
        "total_integrity_mismatches": total_mismatches,
        "mismatch_examples": mismatch_examples,
        "recomputed_preoutcome_status_counts": recomputed_preoutcome,
        "recomputed_association_status_counts": recomputed_association,
        "recomputed_final_status_counts": recomputed_final,
        "recomputed_diagnostic_sums": aggregate,
    }


def verify_summary_recounts(summary: dict, row_audit: dict) -> None:
    require(
        summary["preoutcome_status_counts"]
        == row_audit["recomputed_preoutcome_status_counts"],
        "summary preoutcome counts disagree with independent row audit",
    )
    require(
        summary["association_status_counts"]
        == row_audit["recomputed_association_status_counts"],
        "summary association counts disagree with independent row audit",
    )
    require(
        summary["final_status_counts"]
        == row_audit["recomputed_final_status_counts"],
        "summary final-status counts disagree with independent row audit",
    )

    for key, value in row_audit["recomputed_diagnostic_sums"].items():
        require(
            int(summary[key]) == int(value),
            f"summary diagnostic disagrees with independent row audit: {key}",
        )


def main() -> int:
    print("A2_INDEPENDENT_FULL_PRODUCTION_OUTPUT_AUDIT_START = true")
    print("ORBITAL_PROPAGATION_PERFORMED = false")
    print("CANDIDATE_GENERATION_PERFORMED = false")
    print("REFERENCE_ASSOCIATION_RECOMPUTED = false")
    print("RAW_GP_HISTORY_OPENED = false")
    print("A2_METRICS_COMPUTED = false")
    print("A0_EXECUTION_AUTHORIZED = false")

    frozen_hashes = verify_lineage_and_hashes()
    linked = verify_json_linkages()

    materialized_sha, materialized_counts = verify_materialized_db()
    print("A2_POSTPRODUCTION_MATERIALIZED_DB = PASS", flush=True)

    work_sha, checkpoint_metadata, completion = verify_checkpoint_structure()
    print("A2_POSTPRODUCTION_CHECKPOINT_STRUCTURE = PASS", flush=True)

    row_audit = audit_all_rows(completion)
    require(
        row_audit["total_integrity_mismatches"] == 0,
        "independent production row audit found integrity mismatches",
    )
    print("A2_POSTPRODUCTION_FULL_ROW_AUDIT = PASS", flush=True)

    verify_summary_recounts(linked["summary"], row_audit)
    print("A2_POSTPRODUCTION_SUMMARY_RECOUNT = PASS", flush=True)

    result = {
        "status": "G7R6_A2_PRODUCTION_INDEPENDENT_AUDIT_PASS",
        "authorization_commit": AUTHORIZATION_COMMIT,
        "authorization_parent_commit": AUTHORIZATION_PARENT,
        "authorization_tag": AUTHORIZATION_TAG,
        "frozen_artifact_hashes": frozen_hashes,
        "materialized_db_sha256": materialized_sha,
        "production_checkpoint_sha256": work_sha,
        "materialized_schedule_counts": materialized_counts,
        "checkpoint_metadata": checkpoint_metadata,
        "checkpoint_completed_events": len(completion),
        "row_audit": row_audit,
        "cryptographic_linkage": {
            "result_csv_sha256": EXPECTED_RESULT_CSV_SHA256,
            "summary_sha256": EXPECTED_SUMMARY_SHA256,
            "executor_audit_sha256": EXPECTED_EXECUTOR_AUDIT_SHA256,
            "authorization_audit_sha256":
                EXPECTED_AUTHORIZATION_AUDIT_SHA256,
            "all_linkages_verified": True,
        },
        "operations_performed_by_independent_audit": {
            "materialized_SQLite_read": True,
            "production_checkpoint_read": True,
            "result_CSV_read": True,
            "orbital_propagation_performed": False,
            "candidate_generation_performed": False,
            "candidate_refinement_performed": False,
            "reference_association_recomputed": False,
            "reference_miss_distance_accessed": False,
            "raw_GP_HISTORY_opened": False,
            "CAL0_refit_performed": False,
            "network_requests": 0,
            "A2_metrics_computed": False,
        },
        "interpretation": (
            "PASS establishes cryptographic, membership, checkpoint, row-level, "
            "status-semantic, and aggregate integrity for the completed frozen "
            "107,740-replay A2 production execution. It does not independently "
            "re-run orbital propagation and does not constitute scientific "
            "interpretation of S05/S06/S07 or Q01-Q05."
        ),
        "A0_execution_authorized": False,
        "next_safe_action":
            "FREEZE_A2_PRODUCTION_EXECUTION_AND_INDEPENDENT_AUDIT_EVIDENCE",
    }

    atomic_json(OUT, result)

    print(json.dumps(
        {
            "status": result["status"],
            "rows_audited": row_audit["rows_audited"],
            "unique_replay_identities":
                row_audit["unique_replay_identities"],
            "total_integrity_mismatches":
                row_audit["total_integrity_mismatches"],
            "recomputed_preoutcome_status_counts":
                row_audit["recomputed_preoutcome_status_counts"],
            "recomputed_association_status_counts":
                row_audit["recomputed_association_status_counts"],
            "recomputed_final_status_counts":
                row_audit["recomputed_final_status_counts"],
            "recomputed_diagnostic_sums":
                row_audit["recomputed_diagnostic_sums"],
            "materialized_db_sha256": materialized_sha,
            "production_checkpoint_sha256": work_sha,
            "result_csv_sha256": EXPECTED_RESULT_CSV_SHA256,
            "summary_sha256": EXPECTED_SUMMARY_SHA256,
            "executor_audit_sha256": EXPECTED_EXECUTOR_AUDIT_SHA256,
            "authorization_audit_sha256":
                EXPECTED_AUTHORIZATION_AUDIT_SHA256,
            "audit_output":
                OUT.relative_to(ROOT).as_posix(),
            "audit_output_sha256": sha256(OUT),
            "orbital_propagation_performed": False,
            "A2_metrics_computed": False,
            "A0_execution_authorized": False,
            "next_safe_action": result["next_safe_action"],
        },
        indent=2,
        sort_keys=True,
    ))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
