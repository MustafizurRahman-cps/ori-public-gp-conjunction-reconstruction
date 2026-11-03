"""Authorization launcher for G7-D H=3/H=7 candidate generation.

This layer is reviewed and frozen before any real propagation.  The frozen
executor remains unable to execute from its own direct CLI.  Real execution
through this launcher additionally requires:

1. the frozen executor SHA-256 and freeze tag,
2. the future authorization tag at the current HEAD,
3. the exact authorization token,
4. a horizon in {3, 7},
5. deterministic execution-order and working-tree preconditions.

Offline validation never calls execute_horizon(..., authorization=True).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EXECUTOR = ROOT / "scripts/g7/run_g7d_horizon_candidates.py"
OFFLINE_VALIDATION = (
    ROOT
    / "results/g7/G7D_Horizon_Authorization_Launcher_Offline_Validation.json"
)

EXPECTED_EXECUTOR_SHA256 = (
    "488c99aec614cd51c6ae3682fb8ef404fc767d6417681b09c82e2f5b1c4aa48d"
)

EXECUTOR_FREEZE_COMMIT = (
    "cd382da06986c71c0466e299ffb8b13d56eb3d64"
)

EXECUTOR_FREEZE_TAG = "g7d-horizon-executor-frozen"

# Created only after independent review / DR-0045.
AUTHORIZATION_TAG = "g7d-horizon-execution-authorized"

AUTHORIZATION_TOKEN = (
    "DR0045_G7D_HORIZON_EXECUTION_AUTHORIZED"
)

AUTHORIZED_HORIZONS_DAYS = (3, 7)

H3_OUTPUTS = (
    ROOT / "results/g7/G7D_H3_Event_Status.csv",
    ROOT / "results/g7/G7D_H3_Candidates.csv",
    ROOT / "results/g7/G7D_H3_Candidate_Summary.json",
)

H7_OUTPUTS = (
    ROOT / "results/g7/G7D_H7_Event_Status.csv",
    ROOT / "results/g7/G7D_H7_Candidates.csv",
    ROOT / "results/g7/G7D_H7_Candidate_Summary.json",
)

H3_SUMMARY = ROOT / "results/g7/G7D_H3_Candidate_Summary.json"
H7_SUMMARY = ROOT / "results/g7/G7D_H7_Candidate_Summary.json"

H3_DB = ROOT / "data/raw/g7/G7D_H3_Candidate_Work.sqlite3"
H7_DB = ROOT / "data/raw/g7/G7D_H7_Candidate_Work.sqlite3"


class AuthorizationLauncherError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise AuthorizationLauncherError(
            "git command failed: git "
            + " ".join(args)
            + "\n"
            + result.stderr.strip()
        )
    return result


def git_text(*args: str) -> str:
    return git(*args).stdout.strip()


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def load_executor():
    spec = importlib.util.spec_from_file_location(
        "g7d_frozen_horizon_executor_authorized",
        EXECUTOR,
    )
    if spec is None or spec.loader is None:
        raise AuthorizationLauncherError("cannot load frozen executor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_executor_freeze() -> dict[str, str]:
    if not EXECUTOR.is_file():
        raise AuthorizationLauncherError("frozen executor is missing")

    executor_sha = sha256(EXECUTOR)
    if executor_sha != EXPECTED_EXECUTOR_SHA256:
        raise AuthorizationLauncherError(
            "frozen executor SHA-256 mismatch: " + executor_sha
        )

    tag_target = git_text(
        "rev-parse",
        f"refs/tags/{EXECUTOR_FREEZE_TAG}^{{commit}}",
    )

    if tag_target != EXECUTOR_FREEZE_COMMIT:
        raise AuthorizationLauncherError(
            "executor freeze tag target changed: " + tag_target
        )

    return {
        "executor_sha256": executor_sha,
        "executor_freeze_tag": EXECUTOR_FREEZE_TAG,
        "executor_freeze_commit": tag_target,
    }


def validate_horizon(horizon_days: int) -> int:
    if (
        isinstance(horizon_days, bool)
        or horizon_days not in AUTHORIZED_HORIZONS_DAYS
    ):
        raise AuthorizationLauncherError(
            "authorization launcher permits only H=3 d or H=7 d"
        )
    return int(horizon_days)


def verify_token(token: str) -> None:
    if token != AUTHORIZATION_TOKEN:
        raise AuthorizationLauncherError(
            "G7D_REAL_HORIZON_EXECUTION_AUTHORIZATION_TOKEN_INVALID"
        )


def require_authorization_tag_at_head() -> str:
    head = git_text("rev-parse", "HEAD")

    tag = git(
        "rev-parse",
        f"refs/tags/{AUTHORIZATION_TAG}^{{commit}}",
        check=False,
    )

    if tag.returncode != 0:
        raise AuthorizationLauncherError(
            "authorization tag is absent; real H3/H7 execution remains unauthorized"
        )

    tag_target = tag.stdout.strip()

    if tag_target != head:
        raise AuthorizationLauncherError(
            "current HEAD does not equal the frozen authorization tag target"
        )

    return head


def status_paths() -> list[str]:
    lines = git_text(
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ).splitlines()
    return [line for line in lines if line]


def expected_untracked_for_h3_complete() -> set[str]:
    return {
        "?? " + relative(path)
        for path in H3_OUTPUTS
    }


def verify_h3_complete() -> dict:
    if not all(path.is_file() for path in H3_OUTPUTS):
        raise AuthorizationLauncherError(
            "H=7 execution requires complete H=3 final outputs first"
        )

    payload = json.loads(
        H3_SUMMARY.read_text(encoding="utf-8")
    )

    if payload.get("status") != "G7D_H3_CANDIDATE_GENERATION_PASS":
        raise AuthorizationLauncherError(
            "H=3 summary is not PASS"
        )

    if int(payload.get("horizon_days", -1)) != 3:
        raise AuthorizationLauncherError(
            "H=3 summary horizon mismatch"
        )

    for key in (
        "reference_association_performed",
        "protected_reference_accessed",
        "GP_HISTORY_accessed",
        "CAL0_refit_performed",
        "method_retuning_performed",
    ):
        if payload.get(key) is not False:
            raise AuthorizationLauncherError(
                "H=3 summary violates scientific boundary: " + key
            )

    if int(payload.get("network_requests", -1)) != 0:
        raise AuthorizationLauncherError(
            "H=3 summary reports network requests"
        )

    return payload


def verify_execution_preconditions(horizon_days: int) -> dict[str, object]:
    horizon_days = validate_horizon(horizon_days)

    head = require_authorization_tag_at_head()
    freeze = verify_executor_freeze()

    if horizon_days == 3:
        if any(path.exists() for path in H3_OUTPUTS):
            raise AuthorizationLauncherError(
                "H=3 final output already exists; rerun is prohibited"
            )
        if any(path.exists() for path in H7_OUTPUTS):
            raise AuthorizationLauncherError(
                "H=7 output exists before H=3 execution"
            )

        status = set(status_paths())
        if status:
            raise AuthorizationLauncherError(
                "H=3 execution requires a clean tracked/untracked working tree; "
                "ignored checkpoint DBs are permitted for manual resume"
            )

        prior_h3 = None

    else:
        if any(path.exists() for path in H7_OUTPUTS):
            raise AuthorizationLauncherError(
                "H=7 final output already exists; rerun is prohibited"
            )

        prior_h3 = verify_h3_complete()

        status = set(status_paths())
        expected = expected_untracked_for_h3_complete()

        if status != expected:
            raise AuthorizationLauncherError(
                "H=7 execution permits only the three completed H=3 final outputs "
                "as untracked working-tree files"
            )

    return {
        "current_head": head,
        "horizon_days": horizon_days,
        "executor_freeze": freeze,
        "H3_checkpoint_exists": H3_DB.exists(),
        "H7_checkpoint_exists": H7_DB.exists(),
        "prior_H3_complete": prior_h3 is not None,
    }


def execute_authorized(horizon_days: int, token: str) -> dict:
    horizon_days = validate_horizon(horizon_days)
    verify_token(token)

    preflight = verify_execution_preconditions(horizon_days)

    executor = load_executor()

    if tuple(executor.AUTHORIZED_HORIZONS_DAYS) != (3, 7):
        raise AuthorizationLauncherError(
            "frozen executor horizon set changed"
        )

    result = executor.execute_horizon(
        horizon_days,
        authorization=True,
    )

    expected_status = (
        f"G7D_H{horizon_days}_CANDIDATE_GENERATION_PASS"
    )

    if result.get("status") != expected_status:
        raise AuthorizationLauncherError(
            "authorized executor did not return expected PASS status"
        )

    if int(result.get("horizon_days", -1)) != horizon_days:
        raise AuthorizationLauncherError(
            "authorized executor returned wrong horizon"
        )

    if result.get("frozen_H5_anchor_rerun") is not False:
        raise AuthorizationLauncherError(
            "authorized result reports H5 rerun"
        )

    for key in (
        "reference_association_performed",
        "protected_reference_accessed",
        "GP_HISTORY_accessed",
        "CAL0_refit_performed",
        "method_retuning_performed",
        "automatic_retry_performed",
    ):
        if result.get(key) is not False:
            raise AuthorizationLauncherError(
                "authorized result violates scientific boundary: " + key
            )

    if int(result.get("network_requests", -1)) != 0:
        raise AuthorizationLauncherError(
            "authorized result reports network requests"
        )

    payload = {
        "status": (
            f"G7D_H{horizon_days}_AUTHORIZED_EXECUTION_PASS"
        ),
        "horizon_days": horizon_days,
        "preflight": preflight,
        "executor_result": result,
    }

    print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def offline_validate() -> dict[str, object]:
    freeze = verify_executor_freeze()

    if tuple(AUTHORIZED_HORIZONS_DAYS) != (3, 7):
        raise AuthorizationLauncherError(
            "authorization horizon set changed"
        )

    h5_blocked = False
    try:
        validate_horizon(5)
    except AuthorizationLauncherError:
        h5_blocked = True

    if not h5_blocked:
        raise AuthorizationLauncherError(
            "H=5 was accepted by authorization launcher"
        )

    bad_token_blocked = False
    try:
        verify_token("NOT_AUTHORIZED")
    except AuthorizationLauncherError:
        bad_token_blocked = True

    if not bad_token_blocked:
        raise AuthorizationLauncherError(
            "invalid authorization token was accepted"
        )

    tag_absent_blocks_real_execution = False
    try:
        require_authorization_tag_at_head()
    except AuthorizationLauncherError:
        tag_absent_blocks_real_execution = True

    if not tag_absent_blocks_real_execution:
        raise AuthorizationLauncherError(
            "real execution boundary unexpectedly open before DR-0045"
        )

    for path in (*H3_OUTPUTS, *H7_OUTPUTS, H3_DB, H7_DB):
        if path.exists():
            raise AuthorizationLauncherError(
                "offline authorization review found real execution artifact: "
                + relative(path)
            )

    payload = {
        "status": "G7D_HORIZON_AUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS",
        "executor_freeze": freeze,
        "authorized_horizons_days": [3, 7],
        "H5_execution_blocked": True,
        "invalid_token_blocked": True,
        "authorization_tag_required": True,
        "authorization_tag_present_before_freeze": False,
        "authorization_tag_absence_blocks_real_execution": True,
        "H3_must_precede_H7": True,
        "H3_manual_checkpoint_resume_supported": True,
        "H7_manual_checkpoint_resume_supported": True,
        "automatic_retry_performed": False,
        "real_G6_events_processed": 0,
        "orbital_propagation_performed": False,
        "real_candidate_generation_performed": False,
        "reference_association_performed": False,
        "protected_reference_accessed": False,
        "GP_HISTORY_accessed": False,
        "CAL0_refit_performed": False,
        "method_retuning_performed": False,
        "network_requests": 0,
        "real_H3_H7_execution_authorized": False,
        "next_safe_action": (
            "INDEPENDENT_AUDIT_AND_FREEZE_DR0045_AUTHORIZATION_LAYER"
        ),
    }

    OFFLINE_VALIDATION.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OFFLINE_VALIDATION.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)

    group.add_argument(
        "--offline-validate",
        action="store_true",
    )

    group.add_argument(
        "--execute",
        action="store_true",
    )

    parser.add_argument(
        "--horizon-days",
        type=int,
        choices=(3, 7),
    )

    parser.add_argument(
        "--authorization-token",
        type=str,
    )

    args = parser.parse_args()

    if args.offline_validate:
        if args.horizon_days is not None:
            raise AuthorizationLauncherError(
                "--horizon-days is invalid with --offline-validate"
            )
        if args.authorization_token is not None:
            raise AuthorizationLauncherError(
                "--authorization-token is invalid with --offline-validate"
            )
        offline_validate()
        return 0

    if args.horizon_days is None:
        raise AuthorizationLauncherError(
            "--execute requires --horizon-days"
        )

    if args.authorization_token is None:
        raise AuthorizationLauncherError(
            "--execute requires --authorization-token"
        )

    execute_authorized(
        args.horizon_days,
        args.authorization_token,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())