#!/usr/bin/env python3
"""Authorization wrapper for the frozen 28-replay G7 R6 A2 pilot.

Before the later authorization freeze:
  * --offline-validate is permitted.
  * --execute-pilot MUST fail because the authorization tag does not exist.

After a separate reviewed authorization commit/tag:
  * --execute-pilot may execute exactly the pilot identities already frozen
    in G7R6_A2_Predetermined_Pilot_Manifest.json through the frozen controller.

This wrapper has no full-production entry point.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PARENT_COMMIT = "e9882c325306d0f7b779237a49b7bd0ed0bf05fd"
PARENT_TAG = "g7-r6-a2-controller-pilot-frozen"
AUTH_TAG = "g7-r6-a2-pilot-authorized"

CONTROLLER = ROOT / "scripts/g7/run_g7r6_a2_controller.py"
MANIFEST = ROOT / "results/g7/G7R6_A2_Predetermined_Pilot_Manifest.json"
DR0059 = ROOT / "docs/research/DR-0059_G7_R6_A2_Controller_and_Predetermined_Pilot_Freeze.md"
CONTROLLER_OFFLINE = ROOT / "results/g7/G7R6_A2_Controller_Offline_Validation.json"
CONTROLLER_REVIEW = ROOT / "results/g7/G7R6_A2_Controller_Implementation_Independent_Review.json"

PILOT_OUTPUT = ROOT / "results/g7/G7R6_A2_Pilot_Execution.json"
AUTH_OFFLINE = ROOT / "results/g7/G7R6_A2_Pilot_Authorization_Offline_Validation.json"
AUTH_EXECUTION_AUDIT = ROOT / "results/g7/G7R6_A2_Pilot_Authorization_Execution_Audit.json"

EXPECTED_HASHES = {
    CONTROLLER:
        "806aff3b06c6efd82726a4d93f0a0ad9e3ab8314e9f5904e4707e38680c4f8de",
    MANIFEST:
        "c3c803e9957a1241854fa81f3e51bbe9923d7710c866fa34e08d79d907ac8cac",
    DR0059:
        "b4500f40fc2a0ce367b0148aa2162a172e5906d0935c4bc04cffd6ddf539e755",
    CONTROLLER_OFFLINE:
        "931b36dbd42cf38c7b49c974fcc5c8c205a9094f1dde8487e9e2ecf6329046f5",
    CONTROLLER_REVIEW:
        "6912c5c673e5f2579be6cee894d4fc13aab8819f1d03765ac0f02088263d75a0",
}

EXPECTED_DB_SHA256 = (
    "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"
)
EXPECTED_PILOT_EVENTS = 12
EXPECTED_PILOT_REPLAYS = 28
EXPECTED_UNIQUE_PAYLOADS = 56


class PilotAuthorizationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PilotAuthorizationError(message)


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


def verify_parent_freeze() -> dict[str, str]:
    require(
        tag_target(PARENT_TAG) == PARENT_COMMIT,
        "controller/pilot freeze tag target changed",
    )

    observed = {}
    for path, expected in EXPECTED_HASHES.items():
        require(path.is_file(), f"missing frozen authorization dependency: {path}")
        actual = sha256(path)
        require(
            actual == expected,
            "frozen authorization dependency SHA mismatch\n"
            f"path={path}\nactual={actual}\nexpected={expected}",
        )
        observed[path.relative_to(ROOT).as_posix()] = actual

    with MANIFEST.open("r", encoding="utf-8") as stream:
        manifest = json.load(stream)

    require(
        manifest["status"] == "G7R6_A2_PREDETERMINED_PILOT_MANIFEST",
        "pilot manifest status changed",
    )
    require(
        int(manifest["selected_event_count"]) == EXPECTED_PILOT_EVENTS,
        "pilot event count changed",
    )
    require(
        int(manifest["selected_pilot_replay_count"]) == EXPECTED_PILOT_REPLAYS,
        "pilot replay count changed",
    )
    require(
        manifest["materialized_db_sha256"] == EXPECTED_DB_SHA256,
        "pilot manifest materialized-DB binding changed",
    )

    with CONTROLLER_OFFLINE.open("r", encoding="utf-8") as stream:
        controller_offline = json.load(stream)
    with CONTROLLER_REVIEW.open("r", encoding="utf-8") as stream:
        controller_review = json.load(stream)

    require(
        controller_offline["status"]
        == "G7R6_A2_CONTROLLER_OFFLINE_VALIDATION_PASS",
        "frozen controller offline validation lost PASS",
    )
    require(
        controller_review["status"]
        == "G7R6_A2_CONTROLLER_IMPLEMENTATION_INDEPENDENT_REVIEW_PASS",
        "frozen controller independent review lost PASS",
    )
    require(
        controller_offline["manifest_check"]["unique_selected_payloads"]
        == EXPECTED_UNIQUE_PAYLOADS,
        "frozen unique pilot payload count changed",
    )
    require(
        controller_offline["pilot_execution_blocked_without_authorization"] is True,
        "frozen controller no longer blocks unauthorized pilot execution",
    )
    require(
        controller_offline["full_production_blocked"] is True,
        "frozen controller no longer blocks full production",
    )
    require(
        controller_review["independent_pilot_selection_exact_match"] is True,
        "frozen independent pilot-selection match changed",
    )

    return observed


def import_controller():
    g7_dir = str(ROOT / "scripts/g7")
    if g7_dir not in sys.path:
        sys.path.insert(0, g7_dir)

    controller = importlib.import_module("run_g7r6_a2_controller")
    require(
        Path(controller.__file__).resolve() == CONTROLLER.resolve(),
        "unexpected A2 controller module imported",
    )
    require(
        sha256(Path(controller.__file__))
        == EXPECTED_HASHES[CONTROLLER],
        "imported A2 controller hash mismatch",
    )
    return controller


def verify_offline_boundary() -> dict:
    require(
        git_value("rev-parse", "HEAD") == PARENT_COMMIT,
        "offline authorization review must run at frozen controller parent",
    )
    require(
        tag_target(AUTH_TAG) is None,
        "pilot authorization tag already exists before authorization freeze",
    )
    require(
        not PILOT_OUTPUT.exists(),
        "pilot output exists before authorization freeze",
    )
    require(
        not AUTH_EXECUTION_AUDIT.exists(),
        "pilot authorization execution audit exists before authorization freeze",
    )

    frozen_hashes = verify_parent_freeze()
    controller = import_controller()

    pilot_blocked = False
    try:
        controller.execute_pilot(authorization=False)
    except controller.A2PilotExecutionNotAuthorized:
        pilot_blocked = True
    require(pilot_blocked, "controller did not block unauthorized pilot execution")

    production_blocked = False
    try:
        controller.execute_production(authorization=False)
    except controller.A2ProductionExecutionNotAuthorized:
        production_blocked = True
    require(production_blocked, "controller did not block full production")

    return {
        "frozen_hashes": frozen_hashes,
        "controller_pilot_guard_verified": pilot_blocked,
        "controller_production_guard_verified": production_blocked,
    }


def offline_validate() -> dict:
    require(
        not AUTH_OFFLINE.exists(),
        "pilot authorization offline-validation output already exists",
    )

    boundary = verify_offline_boundary()

    result = {
        "status": "G7R6_A2_PILOT_AUTHORIZATION_OFFLINE_VALIDATION_PASS",
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "authorization_tag": AUTH_TAG,
        "authorization_tag_present": False,
        "launcher_sha256": sha256(Path(__file__)),
        "controller_sha256": EXPECTED_HASHES[CONTROLLER],
        "pilot_manifest_sha256": EXPECTED_HASHES[MANIFEST],
        "materialized_db_expected_sha256": EXPECTED_DB_SHA256,
        "pilot_event_count": EXPECTED_PILOT_EVENTS,
        "pilot_replay_count": EXPECTED_PILOT_REPLAYS,
        "unique_selected_payload_count": EXPECTED_UNIQUE_PAYLOADS,
        **boundary,
        "raw_GP_HISTORY_opened": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_performed": False,
        "pilot_execution_performed": False,
        "full_production_authorized": False,
        "network_requests": 0,
        "next_safe_action":
            "INDEPENDENT_REVIEW_THEN_FREEZE_28_REPLAY_PILOT_AUTHORIZATION",
    }

    atomic_json(AUTH_OFFLINE, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def verify_execution_authorization() -> dict:
    verify_parent_freeze()

    current_head = git_value("rev-parse", "HEAD")
    auth_target = tag_target(AUTH_TAG)

    require(
        auth_target is not None,
        "A2_PILOT_AUTHORIZATION_TAG_MISSING",
    )
    require(
        auth_target == current_head,
        "A2 pilot authorization tag does not target current HEAD",
    )

    parent_of_current = git_value("rev-parse", "HEAD^")
    require(
        parent_of_current == PARENT_COMMIT,
        "A2 pilot authorization commit is not direct child of frozen controller commit",
    )

    require(
        not git_value("status", "--porcelain=v1", "--untracked-files=no"),
        "tracked working tree is not clean before pilot execution",
    )

    require(
        not PILOT_OUTPUT.exists(),
        "refusing to overwrite existing A2 pilot execution output",
    )
    require(
        not AUTH_EXECUTION_AUDIT.exists(),
        "refusing to overwrite existing A2 pilot authorization execution audit",
    )

    return {
        "authorization_commit": current_head,
        "authorization_tag": AUTH_TAG,
        "authorization_tag_target": auth_target,
        "authorization_parent_commit": parent_of_current,
    }


def execute_authorized_pilot() -> dict:
    authorization = verify_execution_authorization()

    controller = import_controller()

    # This is the only point in the authorization wrapper where true is passed.
    # The frozen controller itself contains no full-production execution path
    # reachable through this wrapper.
    result = controller.execute_pilot(authorization=True)

    require(
        result["pilot_replays_executed"] == EXPECTED_PILOT_REPLAYS,
        "pilot controller did not execute exactly 28 frozen replays",
    )
    require(
        result["materialized_db_sha256"] == EXPECTED_DB_SHA256,
        "pilot output materialized-DB identity changed",
    )
    require(
        result["pilot_manifest_sha256"] == EXPECTED_HASHES[MANIFEST],
        "pilot output manifest identity changed",
    )
    require(
        result["controller_sha256"] == EXPECTED_HASHES[CONTROLLER],
        "pilot output controller identity changed",
    )
    require(
        result["operations"]["raw_GP_HISTORY_opened"] is False,
        "pilot unexpectedly opened raw GP_HISTORY",
    )
    require(
        result["operations"]["network_requests"] == 0,
        "pilot unexpectedly used network access",
    )
    require(
        result["operations"]["CAL0_refit_performed"] is False,
        "pilot unexpectedly refit CAL0",
    )

    output_sha = sha256(PILOT_OUTPUT)

    execution_audit = {
        "status": "G7R6_A2_PILOT_AUTHORIZATION_EXECUTION_COMPLETED",
        **authorization,
        "launcher_sha256": sha256(Path(__file__)),
        "controller_sha256": EXPECTED_HASHES[CONTROLLER],
        "pilot_manifest_sha256": EXPECTED_HASHES[MANIFEST],
        "pilot_output_sha256": output_sha,
        "pilot_controller_status": result["status"],
        "pilot_replays_executed": result["pilot_replays_executed"],
        "implementation_equivalence_mismatches":
            result["implementation_equivalence_mismatches"],
        "raw_GP_HISTORY_opened":
            result["operations"]["raw_GP_HISTORY_opened"],
        "orbital_propagation_performed":
            result["operations"]["SGP4_orbital_propagation_performed"],
        "candidate_generation_performed":
            result["operations"]["candidate_generation_performed"],
        "reference_association_performed":
            result["operations"]["reference_association_performed"],
        "reference_miss_distance_accessed":
            result["operations"]["reference_miss_distance_accessed"],
        "CAL0_refit_performed":
            result["operations"]["CAL0_refit_performed"],
        "network_requests":
            result["operations"]["network_requests"],
        "full_production_authorized": False,
        "next_safe_action":
            "INDEPENDENT_PILOT_OUTPUT_AUDIT_BEFORE_ANY_PRODUCTION_AUTHORIZATION",
    }

    atomic_json(AUTH_EXECUTION_AUDIT, execution_audit)

    print(json.dumps(
        {
            **execution_audit,
            "authorization_execution_audit_sha256":
                sha256(AUTH_EXECUTION_AUDIT),
        },
        indent=2,
        sort_keys=True,
    ))

    return execution_audit


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--offline-validate", action="store_true")
    group.add_argument("--execute-pilot", action="store_true")
    args = parser.parse_args()

    if args.offline_validate:
        offline_validate()
        return 0

    if args.execute_pilot:
        execute_authorized_pilot()
        return 0

    raise PilotAuthorizationError("unreachable command state")


if __name__ == "__main__":
    raise SystemExit(main())
