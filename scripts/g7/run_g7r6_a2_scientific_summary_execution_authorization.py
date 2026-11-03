#!/usr/bin/env python3
"""Execution-authorization wrapper for the frozen G7 R6 A2 scientific summary.

Before the later authorization commit/tag:
  * --offline-validate is permitted.
  * --execute-summary must fail because the authorization tag is absent.

After the reviewed authorization commit/tag:
  * --execute-summary invokes exactly the frozen scientific-summary executor.

This wrapper does not change scientific methods, output schemas, bootstrap
settings, quality-descriptor families, or interpretation boundaries.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PARENT_COMMIT = "04854de46a4b663a2ccf92b7f15984a2b100b51d"
PARENT_TAG = "g7-r6-a2-scientific-summary-implementation-frozen"
AUTH_TAG = "g7-r6-a2-scientific-summary-execution-authorized"

EXECUTOR = ROOT / "scripts/g7/run_g7r6_a2_scientific_summary.py"
DR0070 = ROOT / "docs/research/DR-0070_G7_R6_A2_Scientific_Summary_Implementation_Freeze.md"
SPEC = ROOT / "docs/research/G7R6_A2_Scientific_Summary_Interpretation_Spec.md"
DR0069 = ROOT / "docs/research/DR-0069_G7_R6_A2_Scientific_Summary_Interpretation_Spec_Freeze.md"

OFFLINE = ROOT / "results/g7/G7R6_A2_Scientific_Summary_Execution_Authorization_Offline_Validation.json"

EXPECTED_EXECUTOR_SHA256 = "de19c6c9f5d473ae62d1e84e8421eb94b1cf5812074162d21c2f44922dd03782"
EXPECTED_DR0070_SHA256 = "0118739e1b7ca491f048548173f0ff05bd175165553f15cebfb40a288d7683c7"
EXPECTED_SPEC_SHA256 = "7fd8befe8f7e24e990b4e513d8dbd50b0f62e776000aa86b949fd53b33204615"
EXPECTED_DR0069_SHA256 = "090e34525332bb6f8194b8dc535ade96467c4f1c930e7e5f6d6113972bed518e"

EXPECTED_EVENTS = 6_927
EXPECTED_PUBLICATION_OPPORTUNITIES = 2_020_081
EXPECTED_Q_ROWS = 4_040_162
EXPECTED_CHANGE_ROWS = 99_545
EXPECTED_ASSOCIATIONS = 8
EXPECTED_BOOTSTRAP_REPLICATES = 2_000
EXPECTED_BOOTSTRAP_SEED = 20_260_908

REAL_OUTPUTS = (
    ROOT / "results/g7/G7R6_A2_Scientific_Summary.json",
    ROOT / "results/g7/G7R6_A2_S05_S06_EventEqual_Summary.csv",
    ROOT / "results/g7/G7R6_A2_S07_Summary.csv",
    ROOT / "results/g7/G7R6_A2_Quality_Descriptor_Summary.csv",
    ROOT / "results/g7/G7R6_A2_Quality_Stability_Associations.csv",
    ROOT / "results/g7/G7R6_A2_Scientific_Summary_Execution_Audit.json",
)


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
    return result.stdout.strip() or None


def load_executor():
    require(EXECUTOR.is_file(), "frozen scientific-summary executor missing")
    require(sha256(EXECUTOR) == EXPECTED_EXECUTOR_SHA256, "scientific-summary executor SHA mismatch")
    spec = importlib.util.spec_from_file_location(
        "g7r6_a2_frozen_scientific_summary_executor_for_authorization",
        EXECUTOR,
    )
    require(spec is not None and spec.loader is not None, "cannot load scientific-summary executor")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    require(Path(module.__file__).resolve() == EXECUTOR.resolve(), "unexpected executor imported")
    return module


def verify_frozen_boundary() -> dict:
    require(tag_target(PARENT_TAG) == PARENT_COMMIT, "implementation freeze tag target changed")
    expected = {
        EXECUTOR: EXPECTED_EXECUTOR_SHA256,
        DR0070: EXPECTED_DR0070_SHA256,
        SPEC: EXPECTED_SPEC_SHA256,
        DR0069: EXPECTED_DR0069_SHA256,
    }
    observed = {}
    for path, expected_sha in expected.items():
        require(path.is_file(), f"frozen authorization dependency missing: {path}")
        actual = sha256(path)
        require(actual == expected_sha, f"frozen authorization dependency SHA mismatch: {path}")
        observed[path.relative_to(ROOT).as_posix()] = actual
    return observed


def verify_offline_boundary() -> dict:
    require(
        git_value("rev-parse", "HEAD") == PARENT_COMMIT,
        "authorization review must run at DR-0070 implementation-freeze commit",
    )
    require(tag_target(AUTH_TAG) is None, "summary execution authorization tag already exists")
    for output in REAL_OUTPUTS:
        require(not output.exists(), f"real scientific-summary output exists before authorization: {output}")
    return verify_frozen_boundary()


def offline_validate() -> dict:
    require(not OFFLINE.exists(), "authorization offline-validation output already exists")
    frozen = verify_offline_boundary()
    executor = load_executor()

    blocked_false = False
    try:
        executor.execute_scientific_summary(authorization=False)
    except executor.A2SummaryExecutionNotAuthorized:
        blocked_false = True
    require(blocked_false, "frozen executor did not block authorization=False")

    blocked_missing_tag = False
    try:
        executor.execute_scientific_summary(authorization=True)
    except executor.A2SummaryExecutionNotAuthorized as exc:
        blocked_missing_tag = "AUTHORIZATION_TAG_MISSING" in str(exc)
    require(blocked_missing_tag, "frozen executor did not block missing execution-authorization tag")

    result = {
        "status": "G7R6_A2_SCIENTIFIC_SUMMARY_EXECUTION_AUTHORIZATION_OFFLINE_VALIDATION_PASS",
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "authorization_tag": AUTH_TAG,
        "authorization_tag_present": False,
        "wrapper_sha256": sha256(Path(__file__)),
        "executor_sha256": EXPECTED_EXECUTOR_SHA256,
        "frozen_dependency_hashes": frozen,
        "expected_counts": {
            "events": EXPECTED_EVENTS,
            "publication_replay_opportunities": EXPECTED_PUBLICATION_OPPORTUNITIES,
            "q_object_update_rows": EXPECTED_Q_ROWS,
            "s05_s06_change_rows": EXPECTED_CHANGE_ROWS,
            "quality_stability_associations": EXPECTED_ASSOCIATIONS,
        },
        "bootstrap_replicates": EXPECTED_BOOTSTRAP_REPLICATES,
        "bootstrap_seed": EXPECTED_BOOTSTRAP_SEED,
        "execution_guard_authorization_false_verified": blocked_false,
        "execution_guard_missing_tag_verified": blocked_missing_tag,
        "real_result_data_opened": False,
        "derived_metric_database_opened": False,
        "scientific_summary_values_computed": False,
        "scientific_interpretation_performed": False,
        "secondary_14d_60d_execution_performed": False,
        "A0_execution_performed": False,
        "orbital_propagation_performed": False,
        "reference_association_recomputed": False,
        "raw_GP_HISTORY_opened": False,
        "network_requests": 0,
        "next_safe_action":
            "INDEPENDENT_REVIEW_THEN_FREEZE_A2_SCIENTIFIC_SUMMARY_EXECUTION_AUTHORIZATION",
    }

    OFFLINE.parent.mkdir(parents=True, exist_ok=True)
    with OFFLINE.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")

    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def verify_execution_authorization() -> dict:
    frozen = verify_frozen_boundary()
    current_head = git_value("rev-parse", "HEAD")
    auth_target = tag_target(AUTH_TAG)

    require(auth_target is not None, "A2_SCIENTIFIC_SUMMARY_EXECUTION_AUTHORIZATION_TAG_MISSING")
    require(auth_target == current_head, "summary execution authorization tag must target current HEAD")
    require(
        git_value("rev-parse", "HEAD^") == PARENT_COMMIT,
        "summary execution authorization commit must be a direct child of DR-0070",
    )
    require(
        not git_value("status", "--porcelain=v1", "--untracked-files=no"),
        "tracked working tree not clean before summary execution",
    )
    for output in REAL_OUTPUTS:
        require(not output.exists(), f"real scientific-summary output already exists: {output}")

    return {
        "authorization_commit": current_head,
        "authorization_tag": AUTH_TAG,
        "authorization_tag_target": auth_target,
        "authorization_parent_commit": PARENT_COMMIT,
        "frozen_dependency_hashes": frozen,
    }


def execute_authorized_summary() -> dict:
    authorization = verify_execution_authorization()
    executor = load_executor()

    summary = executor.execute_scientific_summary(authorization=True)
    require(
        summary["status"] == "G7R6_A2_SCIENTIFIC_SUMMARY_EXECUTION_COMPLETED",
        "scientific-summary executor did not report completion",
    )
    require(int(summary["events"]) == EXPECTED_EVENTS, "summary event count changed")
    require(
        int(summary["publication_replay_opportunities"]) == EXPECTED_PUBLICATION_OPPORTUNITIES,
        "summary publication-opportunity count changed",
    )
    require(
        int(summary["q_object_update_rows"]) == EXPECTED_Q_ROWS,
        "summary Q-row count changed",
    )
    require(
        int(summary["s05_s06_change_rows"]) == EXPECTED_CHANGE_ROWS,
        "summary S05/S06 change count changed",
    )
    require(
        int(summary["quality_stability_associations"]) == EXPECTED_ASSOCIATIONS,
        "summary association-family count changed",
    )
    require(
        int(summary["bootstrap_replicates"]) == EXPECTED_BOOTSTRAP_REPLICATES,
        "summary bootstrap replicate count changed",
    )
    require(int(summary["bootstrap_seed"]) == EXPECTED_BOOTSTRAP_SEED, "summary bootstrap seed changed")
    require(summary["scientific_interpretation_performed"] is False, "executor performed interpretation")
    require(summary["secondary_14d_60d_execution_performed"] is False, "executor ran secondary windows")
    require(summary["A0_execution_performed"] is False, "executor ran A0")
    require(summary["orbital_propagation_performed"] is False, "executor ran orbital propagation")
    require(summary["reference_association_recomputed"] is False, "executor recomputed association")
    require(summary["raw_GP_HISTORY_opened"] is False, "executor opened raw GP_HISTORY")
    require(int(summary["network_requests"]) == 0, "executor reports network access")

    for output in REAL_OUTPUTS:
        require(output.is_file(), f"expected scientific-summary output missing: {output}")

    print(json.dumps(
        {
            "status": "G7R6_A2_SCIENTIFIC_SUMMARY_AUTHORIZED_EXECUTION_COMPLETED",
            "authorization": authorization,
            "events": int(summary["events"]),
            "publication_replay_opportunities": int(summary["publication_replay_opportunities"]),
            "q_object_update_rows": int(summary["q_object_update_rows"]),
            "s05_s06_change_rows": int(summary["s05_s06_change_rows"]),
            "quality_stability_associations": int(summary["quality_stability_associations"]),
            "bootstrap_replicates": int(summary["bootstrap_replicates"]),
            "bootstrap_seed": int(summary["bootstrap_seed"]),
            "output_hashes": {
                output.relative_to(ROOT).as_posix(): sha256(output)
                for output in REAL_OUTPUTS
            },
            "scientific_interpretation_performed": False,
            "secondary_14d_60d_execution_performed": False,
            "A0_execution_performed": False,
            "next_safe_action":
                "INDEPENDENT_POST_EXECUTION_A2_SCIENTIFIC_SUMMARY_AUDIT_BEFORE_INTERPRETATION",
        },
        indent=2,
        sort_keys=True,
    ))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--offline-validate", action="store_true")
    group.add_argument("--execute-summary", action="store_true")
    args = parser.parse_args()

    if args.offline_validate:
        offline_validate()
        return 0
    if args.execute_summary:
        execute_authorized_summary()
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
