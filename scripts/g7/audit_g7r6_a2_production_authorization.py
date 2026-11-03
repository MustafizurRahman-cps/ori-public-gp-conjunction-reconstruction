#!/usr/bin/env python3
"""Independent pre-freeze review of full A2 production authorization.

No real A2 production propagation is performed. The reviewer verifies the
authorization wrapper's frozen bindings, static authorization surface,
resume semantics, and missing-tag guard.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PARENT_COMMIT = "44b9e8c41142774db03797b58f423b623b227862"
PARENT_TAG = "g7-r6-a2-production-implementation-frozen"
AUTH_TAG = "g7-r6-a2-production-authorized"

LAUNCHER = ROOT / "scripts/g7/run_g7r6_a2_production_authorization.py"
PRODUCTION = ROOT / "scripts/g7/run_g7r6_a2_production.py"
OFFLINE = ROOT / "results/g7/G7R6_A2_Production_Authorization_Offline_Validation.json"
OUT = ROOT / "results/g7/G7R6_A2_Production_Authorization_Independent_Review.json"

WORK_DB = ROOT / "data/raw/g7/G7R6_A2_Production_Work.sqlite3"
FINAL_RESULTS = ROOT / "results/g7/G7R6_A2_Production_Replay_Results.csv"
FINAL_SUMMARY = ROOT / "results/g7/G7R6_A2_Production_Summary.json"
FINAL_EXECUTION_AUDIT = ROOT / "results/g7/G7R6_A2_Production_Execution_Audit.json"
AUTH_EXECUTION_AUDIT = ROOT / "results/g7/G7R6_A2_Production_Authorization_Execution_Audit.json"

EXPECTED_LAUNCHER_SHA256 = "e6828b6e89c2c7e369349f3cc57fceeb9ee643500d5787aa3c6ddc67d10069ec"
EXPECTED_PRODUCTION_SHA256 = (
    "4a07fc8d1c96b3c82a71f7431cbe5b4b89d24ba5d984185a300286e14af5b185"
)
EXPECTED_DB_SHA256 = (
    "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"
)
EXPECTED_SUPPORT_REPLAYS = 107_740


class AuthorizationReviewError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuthorizationReviewError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
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


def load_launcher():
    spec = importlib.util.spec_from_file_location(
        "g7r6_a2_production_authorization_under_review",
        LAUNCHER,
    )
    require(
        spec is not None and spec.loader is not None,
        "cannot load production authorization launcher",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def source_authorization_audit(source: str) -> dict:
    tree = ast.parse(source)

    production_true_calls = 0
    pilot_true_calls = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue

        if node.func.attr == "execute_production":
            for kw in node.keywords:
                if (
                    kw.arg == "authorization"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    production_true_calls += 1

        if node.func.attr == "execute_pilot":
            for kw in node.keywords:
                if (
                    kw.arg == "authorization"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    pilot_true_calls += 1

    require(
        production_true_calls == 1,
        f"expected exactly one production authorization=True call, found {production_true_calls}",
    )
    require(
        pilot_true_calls == 0,
        "production authorization wrapper contains pilot authorization=True call",
    )

    require(
        "--execute-production" in source,
        "production authorization CLI missing",
    )
    require(
        "--execute-pilot" not in source,
        "production authorization wrapper exposes pilot execution CLI",
    )
    require(
        "A2_PRODUCTION_AUTHORIZATION_TAG_MISSING" in source,
        "authorization-tag guard missing",
    )
    require(
        "inspect_existing_checkpoint_readonly" in source,
        "read-only resume checkpoint inspection is missing",
    )
    require(
        '"--untracked-files=no"' in source,
        "tracked-cleanliness check does not permit ignored checkpoint resume",
    )

    prohibited = (
        "requests.",
        "urllib.request",
        "http.client",
        "Space-Track",
        "spacetrack",
    )
    prohibited_hits = [term for term in prohibited if term in source]
    require(
        prohibited_hits == [],
        f"prohibited authorization-wrapper source terms: {prohibited_hits}",
    )

    return {
        "production_authorization_true_call_count": production_true_calls,
        "pilot_authorization_true_call_count": pilot_true_calls,
        "production_cli_exposed": True,
        "pilot_cli_exposed": False,
        "read_only_checkpoint_resume_inspection_present": True,
        "tracked_cleanliness_allows_ignored_checkpoint": True,
        "prohibited_source_hits": prohibited_hits,
    }


def main() -> int:
    require(
        git_value("rev-parse", "HEAD") == PARENT_COMMIT,
        "authorization review must run at DR-0062 production freeze",
    )
    require(
        tag_target(PARENT_TAG) == PARENT_COMMIT,
        "production-implementation freeze tag target changed",
    )
    require(
        tag_target(AUTH_TAG) is None,
        "production authorization tag already exists before review",
    )

    require(LAUNCHER.is_file(), "production authorization launcher missing")
    require(PRODUCTION.is_file(), "frozen production executor missing")
    require(OFFLINE.is_file(), "production authorization offline validation missing")
    require(not OUT.exists(), "production authorization review output already exists")

    require(
        sha256(LAUNCHER) == EXPECTED_LAUNCHER_SHA256,
        "production authorization launcher SHA mismatch",
    )
    require(
        sha256(PRODUCTION) == EXPECTED_PRODUCTION_SHA256,
        "frozen production executor SHA mismatch",
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
            f"real production artifact exists before authorization freeze: {path}",
        )

    with OFFLINE.open("r", encoding="utf-8") as stream:
        offline = json.load(stream)

    require(
        offline["status"]
        == "G7R6_A2_PRODUCTION_AUTHORIZATION_OFFLINE_VALIDATION_PASS",
        "production authorization offline validation is not PASS",
    )
    require(
        offline["launcher_sha256"] == EXPECTED_LAUNCHER_SHA256,
        "offline authorization validation/launcher binding changed",
    )
    require(
        offline["production_executor_sha256"] == EXPECTED_PRODUCTION_SHA256,
        "offline authorization validation/executor binding changed",
    )
    require(
        offline["materialized_db_expected_sha256"] == EXPECTED_DB_SHA256,
        "offline authorization validation/DB binding changed",
    )
    require(
        int(offline["production_support_replays"]) == EXPECTED_SUPPORT_REPLAYS,
        "offline authorization validation workload changed",
    )
    require(
        offline["production_execution_guard_verified"] is True,
        "offline authorization validation did not verify production guard",
    )
    require(
        offline["checkpoint_resume_permitted_after_authorization"] is True,
        "offline authorization validation does not preserve resume semantics",
    )
    require(
        offline["production_execution_performed"] is False
        and offline["orbital_propagation_performed"] is False
        and offline["candidate_generation_performed"] is False
        and offline["reference_association_performed"] is False,
        "authorization offline validation performed real production work",
    )

    source = LAUNCHER.read_text(encoding="utf-8")
    source_audit = source_authorization_audit(source)

    launcher = load_launcher()

    blocked = False
    try:
        launcher.execute_authorized_production()
    except launcher.A2ProductionAuthorizationError as exc:
        require(
            "A2_PRODUCTION_AUTHORIZATION_TAG_MISSING" in str(exc),
            "pre-freeze production execution failed for an unexpected reason",
        )
        blocked = True

    require(
        blocked,
        "production authorization launcher did not block execution before authorization tag",
    )

    for path in (
        WORK_DB,
        FINAL_RESULTS,
        FINAL_SUMMARY,
        FINAL_EXECUTION_AUDIT,
        AUTH_EXECUTION_AUDIT,
    ):
        require(
            not path.exists(),
            f"blocked authorization attempt created real production state: {path}",
        )

    result = {
        "status": "G7R6_A2_PRODUCTION_AUTHORIZATION_INDEPENDENT_REVIEW_PASS",
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "authorization_tag": AUTH_TAG,
        "launcher_sha256": EXPECTED_LAUNCHER_SHA256,
        "production_executor_sha256": EXPECTED_PRODUCTION_SHA256,
        "materialized_db_expected_sha256": EXPECTED_DB_SHA256,
        "production_support_replays": EXPECTED_SUPPORT_REPLAYS,
        "source_authorization_audit": source_audit,
        "authorization_tag_absence_guard_verified": blocked,
        "checkpoint_resume_permitted_after_authorization": True,
        "real_checkpoint_created": False,
        "real_production_outputs_created": False,
        "production_execution_performed": False,
        "raw_GP_HISTORY_opened": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_performed": False,
        "reference_miss_distance_accessed": False,
        "CAL0_refit_performed": False,
        "network_requests": 0,
        "A0_execution_authorized": False,
        "next_safe_action":
            "FREEZE_EXPLICIT_FULL_A2_PRODUCTION_EXECUTION_AUTHORIZATION",
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
