"""Independent inactive review of the G7-D execution-authorization launcher."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

LAUNCHER = ROOT / "scripts/g7/run_g7d_horizon_execution.py"

VALIDATION = (
    ROOT
    / "results/g7/G7D_Horizon_Authorization_Launcher_Offline_Validation.json"
)

AUDIT_OUT = (
    ROOT
    / "results/g7/G7D_Horizon_Authorization_Launcher_Independent_Audit.json"
)

EXPECTED_EXECUTOR_SHA256 = (
    "488c99aec614cd51c6ae3682fb8ef404fc767d6417681b09c82e2f5b1c4aa48d"
)

EXPECTED_EXECUTOR_FREEZE_COMMIT = (
    "cd382da06986c71c0466e299ffb8b13d56eb3d64"
)


class AuditError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_launcher():
    spec = importlib.util.spec_from_file_location(
        "g7d_authorization_launcher_audited",
        LAUNCHER,
    )
    if spec is None or spec.loader is None:
        raise AuditError("cannot load authorization launcher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    if not LAUNCHER.is_file():
        raise AuditError("authorization launcher is missing")
    if not VALIDATION.is_file():
        raise AuditError("offline validation is missing")

    source = LAUNCHER.read_text(encoding="utf-8")

    required_tokens = (
        "g7d-horizon-executor-frozen",
        "g7d-horizon-execution-authorized",
        "DR0045_G7D_HORIZON_EXECUTION_AUTHORIZED",
        "executor.execute_horizon(",
        "authorization=True",
        "H=5 was accepted",
        "H=7 execution requires complete H=3 final outputs first",
    )

    missing = [
        token for token in required_tokens
        if token not in source
    ]
    if missing:
        raise AuditError(
            "required authorization control missing: "
            + ", ".join(missing)
        )

    forbidden_tokens = (
        "IVV_Releasable_Dataset_SFSH_DiscreteHBR",
        "G6_Reference_TCA_Association.csv",
        "G6_Confirmatory_Event_Metrics.csv",
        "G6_Confirmatory_Metric_Summary.json",
        "requests.",
        "urllib.request",
        "socket.",
    )

    present_forbidden = [
        token for token in forbidden_tokens
        if token in source
    ]
    if present_forbidden:
        raise AuditError(
            "forbidden authorization-layer coupling found: "
            + ", ".join(present_forbidden)
        )

    launcher = load_launcher()

    if tuple(launcher.AUTHORIZED_HORIZONS_DAYS) != (3, 7):
        raise AuditError(
            "authorization horizon set is not exactly {3,7}"
        )

    freeze = launcher.verify_executor_freeze()

    if freeze["executor_sha256"] != EXPECTED_EXECUTOR_SHA256:
        raise AuditError("frozen executor SHA mismatch")

    if (
        freeze["executor_freeze_commit"]
        != EXPECTED_EXECUTOR_FREEZE_COMMIT
    ):
        raise AuditError("executor freeze commit mismatch")

    h5_blocked = False
    try:
        launcher.validate_horizon(5)
    except launcher.AuthorizationLauncherError:
        h5_blocked = True

    if not h5_blocked:
        raise AuditError("H=5 authorization was not blocked")

    invalid_token_blocked = False
    try:
        launcher.verify_token("INVALID")
    except launcher.AuthorizationLauncherError:
        invalid_token_blocked = True

    if not invalid_token_blocked:
        raise AuditError("invalid token was not blocked")

    absent_tag_blocked = False
    try:
        launcher.require_authorization_tag_at_head()
    except launcher.AuthorizationLauncherError:
        absent_tag_blocked = True

    if not absent_tag_blocked:
        raise AuditError(
            "real execution is open before authorization freeze"
        )

    for path in (
        *launcher.H3_OUTPUTS,
        *launcher.H7_OUTPUTS,
        launcher.H3_DB,
        launcher.H7_DB,
    ):
        if path.exists():
            raise AuditError(
                "inactive review found real execution artifact: "
                + launcher.relative(path)
            )

    validation = json.loads(
        VALIDATION.read_text(encoding="utf-8")
    )

    if (
        validation.get("status")
        != "G7D_HORIZON_AUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS"
    ):
        raise AuditError("offline validation is not PASS")

    if validation.get("H5_execution_blocked") is not True:
        raise AuditError("offline validation did not block H5")

    if validation.get("invalid_token_blocked") is not True:
        raise AuditError(
            "offline validation did not block invalid token"
        )

    if (
        validation.get("authorization_tag_absence_blocks_real_execution")
        is not True
    ):
        raise AuditError(
            "offline validation did not require authorization tag"
        )

    payload = {
        "status": "G7D_HORIZON_AUTHORIZATION_LAUNCHER_INDEPENDENT_AUDIT_PASS",
        "launcher_sha256": sha256(LAUNCHER),
        "offline_validation_sha256": sha256(VALIDATION),
        "frozen_executor_sha256": EXPECTED_EXECUTOR_SHA256,
        "frozen_executor_commit": EXPECTED_EXECUTOR_FREEZE_COMMIT,
        "authorized_horizons_days": [3, 7],
        "H5_execution_blocked": True,
        "invalid_token_blocked": True,
        "authorization_tag_required": True,
        "authorization_tag_absence_blocks_real_execution": True,
        "H3_must_precede_H7": True,
        "protected_reference_coupling_detected": False,
        "network_capability_detected": False,
        "real_G6_events_processed": 0,
        "orbital_propagation_performed": False,
        "real_candidate_generation_performed": False,
        "reference_association_performed": False,
        "GP_HISTORY_accessed": False,
        "CAL0_refit_performed": False,
        "method_retuning_performed": False,
        "network_requests": 0,
        "real_H3_H7_execution_authorized": False,
        "next_safe_action": (
            "FREEZE_AND_AUTHORIZE_DR0045_WITHOUT_EXECUTING_PROPAGATION"
        ),
    }

    AUDIT_OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with AUDIT_OUT.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())