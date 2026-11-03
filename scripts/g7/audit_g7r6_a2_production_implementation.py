#!/usr/bin/env python3
"""Independent offline review of the G7 R6 A2 production implementation.

No real A2 orbital propagation is performed.

The review verifies:
  * frozen DR-0061 lineage and source bindings;
  * exact 107,740-row temporal-support workload contract;
  * direct-CLI production blocking;
  * downstream-only reference association structure;
  * absence of a reference-miss-distance path;
  * deterministic complete-event checkpoint/resume behavior in a temporary DB;
  * checkpoint metadata mismatch rejection;
  * finalized-output paths are absent before authorization.
"""

from __future__ import annotations

import ast
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

PARENT_COMMIT = "b257cc1ba6942d6ed0fc9b32502e8ed493650856"
PARENT_TAG = "g7-r6-a2-pilot-execution-frozen"

PRODUCTION = ROOT / "scripts/g7/run_g7r6_a2_production.py"
OFFLINE = ROOT / "results/g7/G7R6_A2_Production_Implementation_Offline_Validation.json"
OUT = ROOT / "results/g7/G7R6_A2_Production_Implementation_Independent_Review.json"

WORK_DB = ROOT / "data/raw/g7/G7R6_A2_Production_Work.sqlite3"
FINAL_RESULTS = ROOT / "results/g7/G7R6_A2_Production_Replay_Results.csv"
FINAL_SUMMARY = ROOT / "results/g7/G7R6_A2_Production_Summary.json"
FINAL_EXECUTION_AUDIT = ROOT / "results/g7/G7R6_A2_Production_Execution_Audit.json"

EXPECTED_PRODUCTION_SHA256 = "4a07fc8d1c96b3c82a71f7431cbe5b4b89d24ba5d984185a300286e14af5b185"
EXPECTED_DB_SHA256 = (
    "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"
)
EXPECTED_SUPPORT_REPLAYS = 107_740


class ProductionReviewError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProductionReviewError(message)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
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
    spec = importlib.util.spec_from_file_location(
        "g7r6_a2_production_under_review",
        PRODUCTION,
    )
    require(
        spec is not None and spec.loader is not None,
        "cannot load production implementation",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def source_structure_audit(source: str) -> dict:
    tree = ast.parse(source)

    process_event_calls = 0
    associate_calls = 0
    true_execution_calls = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "process_event":
                process_event_calls += 1
            if node.func.attr == "associate_reference_event":
                associate_calls += 1
            if node.func.attr == "execute_production":
                for kw in node.keywords:
                    if (
                        kw.arg == "authorization"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        true_execution_calls += 1

    require(
        process_event_calls == 1,
        f"expected one frozen G6 process_event call, found {process_event_calls}",
    )
    require(
        associate_calls == 1,
        f"expected one downstream association call, found {associate_calls}",
    )
    require(
        true_execution_calls == 0,
        "production implementation self-authorizes production",
    )

    prohibited_terms = (
        "reference_distance_km",
        "requests.",
        "urllib.request",
        "http.client",
        "spacetrack",
        "Space-Track",
    )
    prohibited_hits = [term for term in prohibited_terms if term in source]
    require(
        prohibited_hits == [],
        f"prohibited production source terms found: {prohibited_hits}",
    )

    candidate_marker = source.find("g6.process_event(")
    association_marker = source.find("associate_reference_event(")
    require(
        candidate_marker >= 0
        and association_marker > candidate_marker,
        "reference association is not textually downstream of candidate generation",
    )

    require(
        "--run-production" in source,
        "direct blocked production CLI is missing",
    )
    require(
        "execute_production(authorization=False)" in source,
        "direct CLI does not explicitly invoke unauthorized execution",
    )

    return {
        "g6_process_event_call_count": process_event_calls,
        "associate_reference_event_call_count": associate_calls,
        "authorization_true_call_count": true_execution_calls,
        "reference_association_textually_downstream": True,
        "prohibited_source_hits": prohibited_hits,
    }


def checkpoint_smoke(module) -> dict:
    with tempfile.TemporaryDirectory(prefix="g7r6_a2_checkpoint_review_") as tmp:
        path = Path(tmp) / "work.sqlite3"

        connection = module.open_checkpoint_at(
            path,
            EXPECTED_PRODUCTION_SHA256,
        )
        try:
            initial = module.checkpoint_state(connection)
            require(
                initial == {
                    "completed_events": 0,
                    "completed_replays": 0,
                    "orphan_replay_rows": 0,
                    "event_count_mismatches": 0,
                },
                f"unexpected initial checkpoint state: {initial}",
            )

            rows = [
                {
                    field: ""
                    for field in module.RESULT_FIELDS
                }
                for _ in range(2)
            ]

            rows[0].update(
                {
                    "event_index": 123,
                    "replay_sequence": 7,
                    "replay_us": 1000,
                }
            )
            rows[1].update(
                {
                    "event_index": 123,
                    "replay_sequence": 8,
                    "replay_us": 2000,
                }
            )

            digest = module.commit_event_results(
                connection,
                123,
                2,
                rows,
            )

            after = module.checkpoint_state(connection)
            require(
                after["completed_events"] == 1
                and after["completed_replays"] == 2,
                "checkpoint did not atomically record one completed event",
            )
            require(
                module.completed_event_indices(connection) == {123},
                "completed-event resume index changed",
            )

            persisted = list(module.iter_checkpoint_results(connection))
            require(
                [int(row["replay_sequence"]) for row in persisted] == [7, 8],
                "checkpoint replay ordering changed",
            )
        finally:
            connection.close()

        # Reopen with the same frozen executor identity: must preserve state.
        connection = module.open_checkpoint_at(
            path,
            EXPECTED_PRODUCTION_SHA256,
        )
        try:
            reopened = module.checkpoint_state(connection)
            require(
                reopened["completed_events"] == 1
                and reopened["completed_replays"] == 2,
                "checkpoint resume lost committed event state",
            )
        finally:
            connection.close()

        # Reopen with a different implementation identity: must fail.
        mismatch_rejected = False
        try:
            module.open_checkpoint_at(path, "0" * 64)
        except module.A2ProductionError:
            mismatch_rejected = True
        require(
            mismatch_rejected,
            "checkpoint accepted a different executor SHA",
        )

        return {
            "synthetic_completed_events": 1,
            "synthetic_completed_replays": 2,
            "same_executor_resume_preserved": True,
            "different_executor_metadata_rejected": True,
            "synthetic_event_result_sha256": digest,
        }


def main() -> int:
    require(
        git_value("rev-parse", "HEAD") == PARENT_COMMIT,
        "production implementation review must run at DR-0061 freeze",
    )
    require(
        git_value("rev-list", "-n", "1", PARENT_TAG) == PARENT_COMMIT,
        "DR-0061 freeze tag target changed",
    )

    require(PRODUCTION.is_file(), "production implementation missing")
    require(OFFLINE.is_file(), "production offline validation missing")
    require(not OUT.exists(), "production independent review already exists")

    require(
        sha256(PRODUCTION) == EXPECTED_PRODUCTION_SHA256,
        "production implementation SHA mismatch",
    )

    with OFFLINE.open("r", encoding="utf-8") as stream:
        offline = json.load(stream)

    require(
        offline["status"]
        == "G7R6_A2_PRODUCTION_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",
        "production offline validation is not PASS",
    )
    require(
        offline["executor_sha256"] == EXPECTED_PRODUCTION_SHA256,
        "offline validation/executor hash binding changed",
    )
    require(
        offline["materialized_db_expected_sha256"] == EXPECTED_DB_SHA256,
        "offline validation/DB binding changed",
    )
    require(
        int(offline["materialized_schedule"]["temporal_support_replays"])
        == EXPECTED_SUPPORT_REPLAYS,
        "offline validation support workload changed",
    )
    require(
        offline["production_execution_blocked_without_authorization"] is True,
        "offline validation did not confirm production guard",
    )
    require(
        offline["orbital_propagation_performed"] is False
        and offline["candidate_generation_performed"] is False
        and offline["reference_association_performed"] is False,
        "production offline validation performed real science execution",
    )

    for path in (
        WORK_DB,
        Path(str(WORK_DB) + "-wal"),
        Path(str(WORK_DB) + "-shm"),
        FINAL_RESULTS,
        FINAL_SUMMARY,
        FINAL_EXECUTION_AUDIT,
    ):
        require(
            not path.exists(),
            f"real production/checkpoint artifact exists before authorization: {path}",
        )

    # The real checkpoint must be ignored before authorization.
    ignore = git(
        "check-ignore",
        "-q",
        WORK_DB.relative_to(ROOT).as_posix(),
        check=False,
    )
    require(
        ignore.returncode == 0,
        "production checkpoint DB is not Git-ignored",
    )

    source = PRODUCTION.read_text(encoding="utf-8")
    structure = source_structure_audit(source)

    module = load_production()

    blocked = False
    try:
        module.execute_production(authorization=False)
    except module.A2ProductionExecutionNotAuthorized:
        blocked = True
    require(
        blocked,
        "production implementation did not block authorization=False",
    )

    smoke = checkpoint_smoke(module)

    result = {
        "status": "G7R6_A2_PRODUCTION_IMPLEMENTATION_INDEPENDENT_REVIEW_PASS",
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "executor_sha256": EXPECTED_PRODUCTION_SHA256,
        "materialized_db_expected_sha256": EXPECTED_DB_SHA256,
        "expected_support_replays": EXPECTED_SUPPORT_REPLAYS,
        "source_structure_audit": structure,
        "checkpoint_resume_smoke": smoke,
        "production_execution_blocked_without_authorization": blocked,
        "real_checkpoint_created": False,
        "real_production_outputs_created": False,
        "raw_GP_HISTORY_opened": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_performed": False,
        "network_requests": 0,
        "next_safe_action":
            "FREEZE_FULL_A2_PRODUCTION_IMPLEMENTATION_BEFORE_EXECUTION_AUTHORIZATION",
    }

    atomic_json(OUT, result)

    print(json.dumps(
        {
            **result,
            "review_output_sha256": sha256(OUT),
        },
        indent=2,
        sort_keys=True,
    ))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
