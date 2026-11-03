#!/usr/bin/env python3
"""Authorization wrapper for frozen G7 R6 A2 full production execution.

Before the later authorization freeze:
  * --offline-validate is permitted.
  * --execute-production MUST fail because the authorization tag is absent.

After a separately reviewed authorization commit/tag:
  * --execute-production may execute or resume exactly the frozen 107,740
    temporal-support replay workload through the frozen production executor.

The ignored production checkpoint may persist across interrupted invocations.
Final outputs are never overwritten. Full execution remains bound to the
frozen materialized database, executor, numerical settings, and reference
sequencing.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PARENT_COMMIT = "44b9e8c41142774db03797b58f423b623b227862"
PARENT_TAG = "g7-r6-a2-production-implementation-frozen"
AUTH_TAG = "g7-r6-a2-production-authorized"

PRODUCTION = ROOT / "scripts/g7/run_g7r6_a2_production.py"
DR0062 = ROOT / "docs/research/DR-0062_G7_R6_A2_Full_Production_Implementation_Freeze.md"
IMPLEMENTATION_OFFLINE = ROOT / "results/g7/G7R6_A2_Production_Implementation_Offline_Validation.json"
IMPLEMENTATION_REVIEW = ROOT / "results/g7/G7R6_A2_Production_Implementation_Independent_Review.json"
GITIGNORE = ROOT / ".gitignore"

WORK_DB = ROOT / "data/raw/g7/G7R6_A2_Production_Work.sqlite3"
FINAL_RESULTS = ROOT / "results/g7/G7R6_A2_Production_Replay_Results.csv"
FINAL_SUMMARY = ROOT / "results/g7/G7R6_A2_Production_Summary.json"
FINAL_EXECUTION_AUDIT = ROOT / "results/g7/G7R6_A2_Production_Execution_Audit.json"

AUTH_OFFLINE = ROOT / "results/g7/G7R6_A2_Production_Authorization_Offline_Validation.json"
AUTH_EXECUTION_AUDIT = ROOT / "results/g7/G7R6_A2_Production_Authorization_Execution_Audit.json"

EXPECTED_PRODUCTION_SHA256 = (
    "4a07fc8d1c96b3c82a71f7431cbe5b4b89d24ba5d984185a300286e14af5b185"
)
EXPECTED_DR0062_SHA256 = (
    "c18f609bfb7faaecb0d96d48ea5f6a34bd7cdad24ecbdeebf5a0fe30308c34f2"
)
EXPECTED_IMPLEMENTATION_OFFLINE_SHA256 = (
    "b055f5041810f48445fc011190496a5b2799745d4dcced7106c8f45fe2df5424"
)
EXPECTED_IMPLEMENTATION_REVIEW_SHA256 = (
    "de2bb752afdad6975af162773de2cde471cc414cbd5f56c2ff43e66f18435f2f"
)
EXPECTED_GITIGNORE_SHA256 = (
    "68c9baea31a81df98d368c5088b85298a7247f563f8cd597e1dcd52c9ac23c9e"
)
EXPECTED_DB_SHA256 = (
    "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"
)

EXPECTED_EVENTS = 6_927
EXPECTED_POSITIVE_SUPPORT_EVENTS = 6_926
EXPECTED_ZERO_SUPPORT_EVENTS = 1
EXPECTED_PUBLICATION_REPLAY_OPPORTUNITIES = 2_020_081
EXPECTED_TEMPORAL_NON_SUPPORT = 1_912_341
EXPECTED_SUPPORT_REPLAYS = 107_740


class A2ProductionAuthorizationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise A2ProductionAuthorizationError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def load_production():
    require(PRODUCTION.is_file(), "frozen production executor is missing")
    require(
        sha256(PRODUCTION) == EXPECTED_PRODUCTION_SHA256,
        "frozen production executor SHA mismatch",
    )

    spec = importlib.util.spec_from_file_location(
        "g7r6_a2_frozen_production_authorized_dependency",
        PRODUCTION,
    )
    require(
        spec is not None and spec.loader is not None,
        "cannot load frozen A2 production executor",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    require(
        Path(module.__file__).resolve() == PRODUCTION.resolve(),
        "unexpected production module imported",
    )
    return module


def verify_frozen_implementation() -> dict[str, str]:
    require(
        tag_target(PARENT_TAG) == PARENT_COMMIT,
        "production-implementation freeze tag target changed",
    )

    expected = {
        PRODUCTION: EXPECTED_PRODUCTION_SHA256,
        DR0062: EXPECTED_DR0062_SHA256,
        IMPLEMENTATION_OFFLINE: EXPECTED_IMPLEMENTATION_OFFLINE_SHA256,
        IMPLEMENTATION_REVIEW: EXPECTED_IMPLEMENTATION_REVIEW_SHA256,
        GITIGNORE: EXPECTED_GITIGNORE_SHA256,
    }

    observed = {}
    for path, expected_sha in expected.items():
        require(path.is_file(), f"frozen production dependency missing: {path}")
        actual = sha256(path)
        require(
            actual == expected_sha,
            "frozen production dependency SHA mismatch\n"
            f"path={path.relative_to(ROOT).as_posix()}\n"
            f"actual={actual}\nexpected={expected_sha}",
        )
        observed[path.relative_to(ROOT).as_posix()] = actual

    with IMPLEMENTATION_OFFLINE.open("r", encoding="utf-8") as stream:
        offline = json.load(stream)
    with IMPLEMENTATION_REVIEW.open("r", encoding="utf-8") as stream:
        review = json.load(stream)

    require(
        offline["status"]
        == "G7R6_A2_PRODUCTION_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",
        "frozen production offline validation lost PASS",
    )
    require(
        review["status"]
        == "G7R6_A2_PRODUCTION_IMPLEMENTATION_INDEPENDENT_REVIEW_PASS",
        "frozen production independent review lost PASS",
    )

    schedule = offline["materialized_schedule"]
    require(int(schedule["events"]) == EXPECTED_EVENTS, "event census changed")
    require(
        int(schedule["positive_support_events"])
        == EXPECTED_POSITIVE_SUPPORT_EVENTS,
        "positive-support event count changed",
    )
    require(
        int(schedule["zero_support_events"]) == EXPECTED_ZERO_SUPPORT_EVENTS,
        "zero-support event count changed",
    )
    require(
        int(schedule["publication_replay_opportunities"])
        == EXPECTED_PUBLICATION_REPLAY_OPPORTUNITIES,
        "publication replay-opportunity count changed",
    )
    require(
        int(schedule["temporal_non_support_replays"])
        == EXPECTED_TEMPORAL_NON_SUPPORT,
        "temporal non-support count changed",
    )
    require(
        int(schedule["temporal_support_replays"]) == EXPECTED_SUPPORT_REPLAYS,
        "temporal-support production workload changed",
    )
    require(
        int(schedule["non_parse_ready_support_replays"]) == 0,
        "production support set no longer remains fully parse-ready",
    )

    require(
        offline["production_execution_blocked_without_authorization"] is True,
        "frozen offline evidence no longer confirms production guard",
    )
    require(
        review["production_execution_blocked_without_authorization"] is True,
        "frozen independent review no longer confirms production guard",
    )
    require(
        int(review["source_structure_audit"]["authorization_true_call_count"]) == 0,
        "frozen production executor now self-authorizes",
    )

    return observed


def inspect_existing_checkpoint_readonly(production) -> dict:
    if not WORK_DB.exists():
        return {
            "checkpoint_exists": False,
            "completed_events": 0,
            "completed_replays": 0,
        }

    uri = WORK_DB.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                """
            )
        }
        require(
            {"metadata", "replay_results", "event_completion"}.issubset(tables),
            "existing production checkpoint schema is incomplete",
        )

        expected_metadata = production.expected_checkpoint_metadata(
            EXPECTED_PRODUCTION_SHA256
        )
        observed_metadata = dict(
            connection.execute("SELECT key, value FROM metadata").fetchall()
        )
        require(
            observed_metadata == expected_metadata,
            "existing production checkpoint metadata does not match frozen executor",
        )

        state = production.checkpoint_state(connection)
        require(
            int(state["completed_events"]) <= EXPECTED_POSITIVE_SUPPORT_EVENTS,
            "checkpoint completed-event count exceeds frozen workload",
        )
        require(
            int(state["completed_replays"]) <= EXPECTED_SUPPORT_REPLAYS,
            "checkpoint completed-replay count exceeds frozen workload",
        )

        return {
            "checkpoint_exists": True,
            **state,
        }
    finally:
        connection.close()


def verify_offline_boundary() -> dict:
    require(
        git_value("rev-parse", "HEAD") == PARENT_COMMIT,
        "authorization review must run at frozen production implementation commit",
    )
    require(
        tag_target(AUTH_TAG) is None,
        "production authorization tag already exists before authorization freeze",
    )

    for path in (
        WORK_DB,
        Path(str(WORK_DB) + "-wal"),
        Path(str(WORK_DB) + "-shm"),
        FINAL_RESULTS,
        FINAL_SUMMARY,
        FINAL_EXECUTION_AUDIT,
        AUTH_EXECUTION_AUDIT,
    ):
        require(
            not path.exists(),
            f"production execution artifact exists before authorization freeze: {path}",
        )

    frozen_hashes = verify_frozen_implementation()
    production = load_production()

    blocked = False
    try:
        production.execute_production(authorization=False)
    except production.A2ProductionExecutionNotAuthorized:
        blocked = True

    require(
        blocked,
        "frozen production executor did not block authorization=False",
    )

    return {
        "frozen_hashes": frozen_hashes,
        "production_execution_guard_verified": blocked,
    }


def offline_validate() -> dict:
    require(
        not AUTH_OFFLINE.exists(),
        "production authorization offline-validation output already exists",
    )

    boundary = verify_offline_boundary()

    result = {
        "status": "G7R6_A2_PRODUCTION_AUTHORIZATION_OFFLINE_VALIDATION_PASS",
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "authorization_tag": AUTH_TAG,
        "authorization_tag_present": False,
        "launcher_sha256": sha256(Path(__file__)),
        "production_executor_sha256": EXPECTED_PRODUCTION_SHA256,
        "materialized_db_expected_sha256": EXPECTED_DB_SHA256,
        "events": EXPECTED_EVENTS,
        "positive_support_events": EXPECTED_POSITIVE_SUPPORT_EVENTS,
        "zero_support_events": EXPECTED_ZERO_SUPPORT_EVENTS,
        "publication_replay_opportunities":
            EXPECTED_PUBLICATION_REPLAY_OPPORTUNITIES,
        "temporal_non_support_replays": EXPECTED_TEMPORAL_NON_SUPPORT,
        "production_support_replays": EXPECTED_SUPPORT_REPLAYS,
        "checkpoint_resume_permitted_after_authorization": True,
        **boundary,
        "real_checkpoint_created": False,
        "production_execution_performed": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_performed": False,
        "reference_miss_distance_accessed": False,
        "CAL0_refit_performed": False,
        "network_requests": 0,
        "A0_execution_authorized": False,
        "next_safe_action":
            "INDEPENDENT_REVIEW_THEN_FREEZE_FULL_A2_PRODUCTION_AUTHORIZATION",
    }

    atomic_json(AUTH_OFFLINE, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def verify_execution_authorization() -> dict:
    frozen_hashes = verify_frozen_implementation()
    production = load_production()

    current_head = git_value("rev-parse", "HEAD")
    auth_target = tag_target(AUTH_TAG)

    require(
        auth_target is not None,
        "A2_PRODUCTION_AUTHORIZATION_TAG_MISSING",
    )
    require(
        auth_target == current_head,
        "A2 production authorization tag does not target current HEAD",
    )
    require(
        git_value("rev-parse", "HEAD^") == PARENT_COMMIT,
        "A2 production authorization commit is not a direct child of DR-0062",
    )

    # Ignored checkpoint state is permitted. Tracked changes are not.
    require(
        not git_value("status", "--porcelain=v1", "--untracked-files=no"),
        "tracked working tree is not clean before/resuming production execution",
    )

    for path in (
        FINAL_RESULTS,
        FINAL_SUMMARY,
        FINAL_EXECUTION_AUDIT,
        AUTH_EXECUTION_AUDIT,
    ):
        require(
            not path.exists(),
            f"final production artifact already exists; refusing overwrite: {path}",
        )

    checkpoint = inspect_existing_checkpoint_readonly(production)

    return {
        "authorization_commit": current_head,
        "authorization_tag": AUTH_TAG,
        "authorization_tag_target": auth_target,
        "authorization_parent_commit": PARENT_COMMIT,
        "frozen_hashes": frozen_hashes,
        "checkpoint_before_invocation": checkpoint,
    }


def execute_authorized_production() -> dict:
    authorization = verify_execution_authorization()
    production = load_production()

    # This is the only authorization=True call in this wrapper.
    summary = production.execute_production(authorization=True)

    require(
        summary["status"] == "G7R6_A2_PRODUCTION_EXECUTION_COMPLETED",
        "production executor did not report completed status",
    )
    require(
        int(summary["replay_results_written"]) == EXPECTED_SUPPORT_REPLAYS,
        "production executor did not finalize exactly 107,740 replay rows",
    )
    require(
        summary["executor_sha256"] == EXPECTED_PRODUCTION_SHA256,
        "production summary executor identity changed",
    )
    require(
        summary["materialized_db_sha256"] == EXPECTED_DB_SHA256,
        "production summary materialized-DB identity changed",
    )

    operations = summary["operations"]
    require(
        operations["raw_GP_HISTORY_opened"] is False,
        "production unexpectedly reports raw GP_HISTORY access",
    )
    require(
        operations["SGP4_orbital_propagation_performed"] is True,
        "production did not report authorized SGP4 propagation",
    )
    require(
        operations["candidate_generation_performed"] is True,
        "production did not report candidate generation",
    )
    require(
        operations["reference_association_performed"] is True,
        "production did not report downstream association",
    )
    require(
        operations["reference_miss_distance_accessed"] is False,
        "production unexpectedly accessed reference miss distance",
    )
    require(
        operations["CAL0_refit_performed"] is False,
        "production unexpectedly refit CAL0",
    )
    require(
        int(operations["network_requests"]) == 0,
        "production unexpectedly used network access",
    )

    require(FINAL_RESULTS.is_file(), "final production replay CSV missing")
    require(FINAL_SUMMARY.is_file(), "final production summary missing")
    require(
        FINAL_EXECUTION_AUDIT.is_file(),
        "final production execution audit missing",
    )

    result_sha = sha256(FINAL_RESULTS)
    summary_sha = sha256(FINAL_SUMMARY)
    executor_audit_sha = sha256(FINAL_EXECUTION_AUDIT)

    with FINAL_EXECUTION_AUDIT.open("r", encoding="utf-8") as stream:
        executor_audit = json.load(stream)

    require(
        executor_audit["result_csv_sha256"] == result_sha,
        "executor audit/result CSV hash linkage mismatch",
    )
    require(
        executor_audit["summary_json_sha256"] == summary_sha,
        "executor audit/summary hash linkage mismatch",
    )
    require(
        int(executor_audit["completed_replays"]) == EXPECTED_SUPPORT_REPLAYS,
        "executor audit replay count changed",
    )

    execution_audit = {
        "status": "G7R6_A2_PRODUCTION_AUTHORIZATION_EXECUTION_COMPLETED",
        **authorization,
        "launcher_sha256": sha256(Path(__file__)),
        "production_executor_sha256": EXPECTED_PRODUCTION_SHA256,
        "materialized_db_sha256": EXPECTED_DB_SHA256,
        "production_result_csv_sha256": result_sha,
        "production_summary_sha256": summary_sha,
        "production_executor_audit_sha256": executor_audit_sha,
        "replay_results_written": int(summary["replay_results_written"]),
        "raw_GP_HISTORY_opened": False,
        "reference_miss_distance_accessed": False,
        "CAL0_refit_performed": False,
        "network_requests": 0,
        "A0_execution_authorized": False,
        "next_safe_action":
            "INDEPENDENT_FULL_PRODUCTION_OUTPUT_AUDIT_BEFORE_A2_METRICS",
    }

    atomic_json(AUTH_EXECUTION_AUDIT, execution_audit)

    print(json.dumps(
        {
            "status": execution_audit["status"],
            "authorization_commit":
                execution_audit["authorization_commit"],
            "checkpoint_before_invocation":
                execution_audit["checkpoint_before_invocation"],
            "replay_results_written":
                execution_audit["replay_results_written"],
            "production_result_csv_sha256":
                execution_audit["production_result_csv_sha256"],
            "production_summary_sha256":
                execution_audit["production_summary_sha256"],
            "production_executor_audit_sha256":
                execution_audit["production_executor_audit_sha256"],
            "authorization_execution_audit_sha256":
                sha256(AUTH_EXECUTION_AUDIT),
            "raw_GP_HISTORY_opened": False,
            "reference_miss_distance_accessed": False,
            "CAL0_refit_performed": False,
            "network_requests": 0,
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
    group.add_argument("--execute-production", action="store_true")
    args = parser.parse_args()

    if args.offline_validate:
        offline_validate()
        return 0

    if args.execute_production:
        execute_authorized_production()
        return 0

    raise A2ProductionAuthorizationError("unreachable command state")


if __name__ == "__main__":
    raise SystemExit(main())
