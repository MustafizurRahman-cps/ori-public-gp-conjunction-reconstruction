#!/usr/bin/env python3
"""Authorization wrapper for frozen G7 R6 A2 primary metric derivation.

Before a later authorization freeze:
  * --offline-validate is permitted.
  * --execute-primary-metrics MUST fail because the authorization tag is absent.

After a separately reviewed authorization commit/tag:
  * --execute-primary-metrics may execute/resume exactly the frozen primary
    A2 metric derivation through the frozen executor.

The wrapper does not authorize orbital propagation, candidate regeneration,
reference reassociation, raw GP_HISTORY access, secondary 14/60-day execution,
or A0 execution.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PARENT_COMMIT = "0e7cdecfa22b0c8130d1409f82306d816a3f651f"
PARENT_TAG = "g7-r6-a2-metric-implementation-v2-frozen"
AUTH_TAG = "g7-r6-a2-primary-metric-execution-authorized"

EXECUTOR = ROOT / "scripts/g7/run_g7r6_a2_metric_analysis.py"
DR0066 = ROOT / "docs/research/DR-0066_G7_R6_A2_Metric_Implementation_v2_Freeze.md"
IMPLEMENTATION_OFFLINE = ROOT / "results/g7/G7R6_A2_Metric_Implementation_Offline_Validation.json"
IMPLEMENTATION_REVIEW = ROOT / "results/g7/G7R6_A2_Metric_Implementation_Independent_Review.json"
GITIGNORE = ROOT / ".gitignore"

MATERIALIZED_DB = ROOT / "data/raw/g7/G7R6_A2_Materialized.sqlite3"
PRODUCTION_CSV = ROOT / "results/g7/G7R6_A2_Production_Replay_Results.csv"

WORK_DB = ROOT / "data/raw/g7/G7R6_A2_Metric_Derived.sqlite3"
S05_S06_CHANGES = ROOT / "results/g7/G7R6_A2_Metric_S05_S06_Changes.csv"
S05_S06_EVENT_STATUS = ROOT / "results/g7/G7R6_A2_Metric_S05_S06_Event_Status.csv"
S07_EVENT = ROOT / "results/g7/G7R6_A2_Metric_S07_Event_Lead.csv"
FINAL_SUMMARY = ROOT / "results/g7/G7R6_A2_Metric_Derivation_Summary.json"
FINAL_EXECUTION_AUDIT = ROOT / "results/g7/G7R6_A2_Metric_Derivation_Execution_Audit.json"

AUTH_OFFLINE = ROOT / "results/g7/G7R6_A2_Metric_Execution_Authorization_Offline_Validation.json"
AUTH_EXECUTION_AUDIT = ROOT / "results/g7/G7R6_A2_Metric_Execution_Authorization_Audit.json"

EXPECTED_EXECUTOR_SHA256 = "6fc04230f230c79053bc1e9e5a9f2887bd9842fa07f234667122a1f051b2cc51"
EXPECTED_DR0066_SHA256 = "5c439514104453b45ee1c63e5328f5ec51e88ac4c328640966776553e987fd5b"
EXPECTED_IMPLEMENTATION_OFFLINE_SHA256 = "d82e949a671f84703564cc0f78715b78a0e2045e6e5475404fa01debe53728cf"
EXPECTED_IMPLEMENTATION_REVIEW_SHA256 = "3ed7a2e84167ff09ee1cbe5abbf504b933c7210c14f447321dfcc08b0131482b"
EXPECTED_GITIGNORE_SHA256 = "ccc4ceeae94257080505551e5c188fdea70a21a3f52781c3237e4979b4eca663"
EXPECTED_MATERIALIZED_DB_SHA256 = "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"
EXPECTED_PRODUCTION_CSV_SHA256 = "a0217011e0695c240cbc1209f671c2ad7a8cb1948093b1f8c075146819e9efdd"

EXPECTED_EVENTS = 6_927
EXPECTED_OBJECTS = 13_854
EXPECTED_PUBLICATION_OPPORTUNITIES = 2_020_081
EXPECTED_SUPPORT_REPLAYS = 107_740
EXPECTED_NON_SUPPORT_REPLAYS = 1_912_341
EXPECTED_Q_OBJECT_ROWS = 4_040_162


class AuthorizationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuthorizationError(message)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
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


def tag_target(tag: str) -> str | None:
    result = git("rev-list", "-n", "1", tag, check=False)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def load_executor():
    require(EXECUTOR.is_file(), "frozen metric executor missing")
    require(
        sha256(EXECUTOR) == EXPECTED_EXECUTOR_SHA256,
        "frozen metric executor SHA mismatch",
    )
    spec = importlib.util.spec_from_file_location(
        "g7r6_a2_frozen_metric_executor_authorized_dependency",
        EXECUTOR,
    )
    require(spec is not None and spec.loader is not None, "cannot load metric executor")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    require(
        Path(module.__file__).resolve() == EXECUTOR.resolve(),
        "unexpected metric executor imported",
    )
    return module


def verify_frozen_implementation() -> dict:
    require(
        tag_target(PARENT_TAG) == PARENT_COMMIT,
        "metric-implementation freeze tag target changed",
    )

    expected = {
        EXECUTOR: EXPECTED_EXECUTOR_SHA256,
        DR0066: EXPECTED_DR0066_SHA256,
        IMPLEMENTATION_OFFLINE: EXPECTED_IMPLEMENTATION_OFFLINE_SHA256,
        IMPLEMENTATION_REVIEW: EXPECTED_IMPLEMENTATION_REVIEW_SHA256,
        GITIGNORE: EXPECTED_GITIGNORE_SHA256,
    }

    observed = {}
    for path, expected_sha in expected.items():
        require(path.is_file(), f"frozen implementation dependency missing: {path}")
        actual = sha256(path)
        require(
            actual == expected_sha,
            "frozen implementation dependency SHA mismatch\n"
            f"path={path.relative_to(ROOT).as_posix()}\n"
            f"actual={actual}\nexpected={expected_sha}",
        )
        observed[path.relative_to(ROOT).as_posix()] = actual

    with IMPLEMENTATION_OFFLINE.open("r", encoding="utf-8") as stream:
        offline = json.load(stream)
    with IMPLEMENTATION_REVIEW.open("r", encoding="utf-8") as stream:
        review = json.load(stream)

    require(
        offline["status"] == "G7R6_A2_METRIC_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",
        "implementation offline validation lost PASS",
    )
    require(
        review["status"] == "G7R6_A2_METRIC_IMPLEMENTATION_INDEPENDENT_REVIEW_PASS",
        "implementation independent review lost PASS",
    )
    require(
        offline["metric_execution_blocked_without_authorization"] is True,
        "implementation evidence lost execution guard",
    )
    require(
        review["execution_guard_independently_verified"] is True,
        "independent review lost execution guard",
    )
    require(
        review["source_structure_audit"]["authorization_true_call_count"] == 0,
        "frozen executor now self-authorizes",
    )

    counts = offline["frozen_inputs"]["counts"]
    require(int(counts["events"]) == EXPECTED_EVENTS, "event census changed")
    require(
        int(counts["distinct_event_objects"]) == EXPECTED_OBJECTS,
        "object census changed",
    )
    require(
        int(counts["publication_opportunities"]) == EXPECTED_PUBLICATION_OPPORTUNITIES,
        "publication-opportunity schedule changed",
    )
    require(
        int(counts["support_replays"]) == EXPECTED_SUPPORT_REPLAYS,
        "support replay census changed",
    )
    require(
        int(counts["non_support_replays"]) == EXPECTED_NON_SUPPORT_REPLAYS,
        "non-support replay census changed",
    )
    require(
        int(offline["output_plan"]["q_object_update_rows_expected"])
        == EXPECTED_Q_OBJECT_ROWS,
        "planned Q object-update cardinality changed",
    )

    boundary = offline["frozen_inputs"]["materialized_boundary_semantics"]
    require(boundary["report_start"] == "t_ref-90d", "report boundary changed")
    require(boundary["quality_start"] == "t_ref-120d", "quality boundary changed")
    require(
        int(boundary["primary_quality_guard_days"]) == 30,
        "primary quality guard changed",
    )
    require(
        boundary["primary_30d_windows_fully_supported"] is True,
        "primary 30 d support guarantee lost",
    )
    require(
        boundary["secondary_14d_60d_execution"] == "DEFERRED",
        "secondary quality-window boundary changed",
    )

    return observed


def verify_large_input_identities() -> dict:
    require(MATERIALIZED_DB.is_file(), "materialized A2 DB missing")
    require(PRODUCTION_CSV.is_file(), "frozen production replay CSV missing")

    materialized_sha = sha256(MATERIALIZED_DB)
    production_sha = sha256(PRODUCTION_CSV)

    require(
        materialized_sha == EXPECTED_MATERIALIZED_DB_SHA256,
        "materialized A2 DB SHA mismatch",
    )
    require(
        production_sha == EXPECTED_PRODUCTION_CSV_SHA256,
        "production replay CSV SHA mismatch",
    )
    return {
        MATERIALIZED_DB.relative_to(ROOT).as_posix(): materialized_sha,
        PRODUCTION_CSV.relative_to(ROOT).as_posix(): production_sha,
    }


def expected_checkpoint_metadata() -> dict[str, str]:
    return {
        "schema_version": "1",
        "executor_sha256": EXPECTED_EXECUTOR_SHA256,
        "parent_commit": "68e1a188db9ecc18d80c65655e75e05c4cb123b1",
        "parent_tag": "g7-r6-a2-metric-semantics-frozen",
        "materialized_db_sha256": EXPECTED_MATERIALIZED_DB_SHA256,
        "production_csv_sha256": EXPECTED_PRODUCTION_CSV_SHA256,
        "events": str(EXPECTED_EVENTS),
        "objects": str(EXPECTED_OBJECTS),
        "publication_opportunities": str(EXPECTED_PUBLICATION_OPPORTUNITIES),
        "support_replays": str(EXPECTED_SUPPORT_REPLAYS),
        "s05_s06_sequence": "FILTERED_VALID_ASSOCIATED_CHRONOLOGICAL",
        "intervening_failure_resets_sequence": "false",
        "s07": "EARLIEST_PRE_TCA_VALID_ASSOCIATED",
        "quality_windows_days_executed": "30",
        "primary_quality_window_days": "30",
        "secondary_quality_windows_days_deferred": "14,60",
        "q06_requires_valid_associated": "true",
        "event_transaction_atomic": "true",
    }


def inspect_existing_checkpoint_readonly() -> dict:
    if not WORK_DB.exists():
        return {
            "checkpoint_exists": False,
            "completed_events": 0,
            "q_object_rows": 0,
            "change_rows": 0,
        }

    uri = WORK_DB.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        require(
            connection.execute("PRAGMA quick_check").fetchone()[0] == "ok",
            "existing metric checkpoint quick_check failed",
        )
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {
            "metadata",
            "event_completion",
            "s05_s06_changes",
            "s05_s06_event_status",
            "s07_event_lead",
            "q_object_updates",
        }
        require(required.issubset(tables), "existing metric checkpoint schema incomplete")

        observed_metadata = dict(
            connection.execute("SELECT key, value FROM metadata").fetchall()
        )
        require(
            observed_metadata == expected_checkpoint_metadata(),
            "existing metric checkpoint metadata mismatch",
        )

        completed_events = int(
            connection.execute("SELECT COUNT(*) FROM event_completion").fetchone()[0]
        )
        q_object_rows = int(
            connection.execute("SELECT COUNT(*) FROM q_object_updates").fetchone()[0]
        )
        change_rows = int(
            connection.execute("SELECT COUNT(*) FROM s05_s06_changes").fetchone()[0]
        )

        require(
            0 <= completed_events <= EXPECTED_EVENTS,
            "checkpoint completed-event count outside frozen workload",
        )
        require(
            0 <= q_object_rows <= EXPECTED_Q_OBJECT_ROWS,
            "checkpoint Q row count outside frozen workload",
        )

        return {
            "checkpoint_exists": True,
            "completed_events": completed_events,
            "q_object_rows": q_object_rows,
            "change_rows": change_rows,
        }
    finally:
        connection.close()


def verify_offline_boundary() -> dict:
    require(
        git_value("rev-parse", "HEAD") == PARENT_COMMIT,
        "authorization review must run at frozen metric-implementation commit",
    )
    require(
        tag_target(AUTH_TAG) is None,
        "primary metric execution authorization tag already exists",
    )

    for path in (
        WORK_DB,
        Path(str(WORK_DB) + "-wal"),
        Path(str(WORK_DB) + "-shm"),
        S05_S06_CHANGES,
        S05_S06_EVENT_STATUS,
        S07_EVENT,
        FINAL_SUMMARY,
        FINAL_EXECUTION_AUDIT,
        AUTH_EXECUTION_AUDIT,
    ):
        require(
            not path.exists(),
            f"metric execution artifact exists before authorization freeze: {path}",
        )

    frozen = verify_frozen_implementation()

    # Deliberately do not re-hash the 1.48 GB materialized DB here.
    # Its real hash was verified during the frozen implementation review.
    # The execution path re-hashes both large scientific inputs immediately
    # before authorized execution.
    executor = load_executor()
    blocked = False
    try:
        executor.execute_metrics(authorization=False)
    except executor.A2MetricExecutionNotAuthorized:
        blocked = True
    require(blocked, "frozen metric executor did not block authorization=False")

    return {
        "frozen_implementation_hashes": frozen,
        "large_input_execution_time_rehash_required": True,
        "metric_execution_guard_verified": blocked,
    }


def offline_validate() -> dict:
    require(
        not AUTH_OFFLINE.exists(),
        "metric execution authorization offline-validation output already exists",
    )

    boundary = verify_offline_boundary()

    result = {
        "status": "G7R6_A2_METRIC_EXECUTION_AUTHORIZATION_OFFLINE_VALIDATION_PASS",
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "authorization_tag": AUTH_TAG,
        "authorization_tag_present": False,
        "wrapper_sha256": sha256(Path(__file__)),
        "metric_executor_sha256": EXPECTED_EXECUTOR_SHA256,
        "materialized_db_expected_sha256": EXPECTED_MATERIALIZED_DB_SHA256,
        "production_csv_expected_sha256": EXPECTED_PRODUCTION_CSV_SHA256,
        "events": EXPECTED_EVENTS,
        "objects": EXPECTED_OBJECTS,
        "publication_replay_opportunities": EXPECTED_PUBLICATION_OPPORTUNITIES,
        "production_support_replays": EXPECTED_SUPPORT_REPLAYS,
        "expected_q_object_update_rows": EXPECTED_Q_OBJECT_ROWS,
        "primary_q03_q05_window_days": 30,
        "secondary_14d_60d_execution_authorized": False,
        "checkpoint_resume_permitted_after_authorization": True,
        **boundary,
        "real_metric_execution_performed": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_recomputed": False,
        "raw_GP_HISTORY_opened": False,
        "network_requests": 0,
        "A0_execution_authorized": False,
        "next_safe_action":
            "INDEPENDENT_REVIEW_THEN_FREEZE_PRIMARY_A2_METRIC_EXECUTION_AUTHORIZATION",
    }

    AUTH_OFFLINE.parent.mkdir(parents=True, exist_ok=True)
    with AUTH_OFFLINE.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")

    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def verify_execution_authorization() -> dict:
    frozen = verify_frozen_implementation()

    current_head = git_value("rev-parse", "HEAD")
    auth_target = tag_target(AUTH_TAG)

    require(auth_target is not None, "A2_PRIMARY_METRIC_EXECUTION_AUTHORIZATION_TAG_MISSING")
    require(
        auth_target == current_head,
        "primary metric execution authorization tag does not target current HEAD",
    )
    require(
        git_value("rev-parse", "HEAD^") == PARENT_COMMIT,
        "metric execution authorization commit is not a direct child of DR-0066",
    )
    require(
        not git_value("status", "--porcelain=v1", "--untracked-files=no"),
        "tracked working tree is not clean before/resuming metric execution",
    )

    for path in (
        S05_S06_CHANGES,
        S05_S06_EVENT_STATUS,
        S07_EVENT,
        FINAL_SUMMARY,
        FINAL_EXECUTION_AUDIT,
        AUTH_EXECUTION_AUDIT,
    ):
        require(
            not path.exists(),
            f"final metric artifact already exists; refusing overwrite: {path}",
        )

    checkpoint = inspect_existing_checkpoint_readonly()
    large_inputs = verify_large_input_identities()

    return {
        "authorization_commit": current_head,
        "authorization_tag": AUTH_TAG,
        "authorization_tag_target": auth_target,
        "authorization_parent_commit": PARENT_COMMIT,
        "frozen_implementation_hashes": frozen,
        "large_input_hashes": large_inputs,
        "checkpoint_before_invocation": checkpoint,
    }


def execute_authorized_primary_metrics() -> dict:
    authorization = verify_execution_authorization()
    executor = load_executor()

    summary = executor.execute_metrics(authorization=True)

    require(
        summary["status"] == "G7R6_A2_METRIC_DERIVATION_COMPLETED",
        "metric executor did not report completed status",
    )
    require(int(summary["events"]) == EXPECTED_EVENTS, "completed event count changed")
    require(
        int(summary["publication_replay_opportunities"])
        == EXPECTED_PUBLICATION_OPPORTUNITIES,
        "completed replay-opportunity count changed",
    )
    require(
        int(summary["q_object_update_rows"]) == EXPECTED_Q_OBJECT_ROWS,
        "completed Q object-update count changed",
    )

    operations = summary["operations"]
    require(
        operations["orbital_propagation_performed"] is False,
        "metric derivation unexpectedly reports orbital propagation",
    )
    require(
        operations["candidate_generation_performed"] is False,
        "metric derivation unexpectedly reports candidate generation",
    )
    require(
        operations["reference_association_recomputed"] is False,
        "metric derivation unexpectedly reports reassociation",
    )
    require(
        operations["raw_GP_HISTORY_opened"] is False,
        "metric derivation unexpectedly reports raw GP_HISTORY access",
    )
    require(
        operations["network_requests"] == 0,
        "metric derivation unexpectedly reports network access",
    )
    require(
        operations["A0_execution_authorized"] is False,
        "metric derivation unexpectedly authorized A0",
    )

    for path in (
        WORK_DB,
        S05_S06_CHANGES,
        S05_S06_EVENT_STATUS,
        S07_EVENT,
        FINAL_SUMMARY,
        FINAL_EXECUTION_AUDIT,
    ):
        require(path.is_file(), f"completed metric artifact missing: {path}")

    with FINAL_EXECUTION_AUDIT.open("r", encoding="utf-8") as stream:
        executor_audit = json.load(stream)

    require(
        executor_audit["status"] == "G7R6_A2_METRIC_DERIVATION_EXECUTION_AUDIT",
        "executor audit status changed",
    )
    require(
        int(executor_audit["completed_events"]) == EXPECTED_EVENTS,
        "executor audit event count changed",
    )
    require(
        int(executor_audit["q_object_update_rows"]) == EXPECTED_Q_OBJECT_ROWS,
        "executor audit Q row count changed",
    )

    result_hashes = {
        S05_S06_CHANGES.relative_to(ROOT).as_posix(): sha256(S05_S06_CHANGES),
        S05_S06_EVENT_STATUS.relative_to(ROOT).as_posix(): sha256(S05_S06_EVENT_STATUS),
        S07_EVENT.relative_to(ROOT).as_posix(): sha256(S07_EVENT),
        FINAL_SUMMARY.relative_to(ROOT).as_posix(): sha256(FINAL_SUMMARY),
        FINAL_EXECUTION_AUDIT.relative_to(ROOT).as_posix(): sha256(FINAL_EXECUTION_AUDIT),
    }

    execution_audit = {
        "status": "G7R6_A2_PRIMARY_METRIC_AUTHORIZED_EXECUTION_COMPLETED",
        **authorization,
        "wrapper_sha256": sha256(Path(__file__)),
        "metric_executor_sha256": EXPECTED_EXECUTOR_SHA256,
        "result_hashes": result_hashes,
        "work_db_sha256": sha256(WORK_DB),
        "events": int(summary["events"]),
        "publication_replay_opportunities":
            int(summary["publication_replay_opportunities"]),
        "q_object_update_rows": int(summary["q_object_update_rows"]),
        "secondary_14d_60d_execution_performed": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_recomputed": False,
        "raw_GP_HISTORY_opened": False,
        "network_requests": 0,
        "A0_execution_authorized": False,
        "interpretation_authorized": False,
        "next_safe_action":
            "INDEPENDENT_POST_EXECUTION_A2_METRIC_AUDIT_BEFORE_INTERPRETATION",
    }

    with AUTH_EXECUTION_AUDIT.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(execution_audit, stream, indent=2, sort_keys=True)
        stream.write("\n")

    print(json.dumps(
        {
            "status": execution_audit["status"],
            "authorization_commit": execution_audit["authorization_commit"],
            "events": execution_audit["events"],
            "publication_replay_opportunities":
                execution_audit["publication_replay_opportunities"],
            "q_object_update_rows": execution_audit["q_object_update_rows"],
            "result_hashes": execution_audit["result_hashes"],
            "work_db_sha256": execution_audit["work_db_sha256"],
            "authorization_execution_audit_sha256": sha256(AUTH_EXECUTION_AUDIT),
            "secondary_14d_60d_execution_performed": False,
            "orbital_propagation_performed": False,
            "reference_association_recomputed": False,
            "raw_GP_HISTORY_opened": False,
            "A0_execution_authorized": False,
            "interpretation_authorized": False,
            "next_safe_action":
                execution_audit["next_safe_action"],
        },
        indent=2,
        sort_keys=True,
    ))

    return execution_audit


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--offline-validate", action="store_true")
    group.add_argument("--execute-primary-metrics", action="store_true")
    args = parser.parse_args()

    if args.offline_validate:
        offline_validate()
        return 0

    if args.execute_primary_metrics:
        execute_authorized_primary_metrics()
        return 0

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
