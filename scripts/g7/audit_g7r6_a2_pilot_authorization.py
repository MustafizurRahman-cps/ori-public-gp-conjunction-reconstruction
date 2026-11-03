#!/usr/bin/env python3
"""Independent pre-freeze review of the G7 R6 A2 28-replay pilot authorization.

This reviewer performs no orbital propagation or candidate generation.
It verifies that the authorization wrapper is bound to the frozen controller,
exact pilot manifest, and controller/pilot freeze, and that real execution is
impossible before the future authorization tag is created.
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

PARENT_COMMIT = "e9882c325306d0f7b779237a49b7bd0ed0bf05fd"
PARENT_TAG = "g7-r6-a2-controller-pilot-frozen"
AUTH_TAG = "g7-r6-a2-pilot-authorized"

LAUNCHER = ROOT / "scripts/g7/run_g7r6_a2_pilot_authorization.py"
CONTROLLER = ROOT / "scripts/g7/run_g7r6_a2_controller.py"
MANIFEST = ROOT / "results/g7/G7R6_A2_Predetermined_Pilot_Manifest.json"
DR0059 = ROOT / "docs/research/DR-0059_G7_R6_A2_Controller_and_Predetermined_Pilot_Freeze.md"
OFFLINE = ROOT / "results/g7/G7R6_A2_Pilot_Authorization_Offline_Validation.json"
PILOT_OUTPUT = ROOT / "results/g7/G7R6_A2_Pilot_Execution.json"
EXECUTION_AUDIT = ROOT / "results/g7/G7R6_A2_Pilot_Authorization_Execution_Audit.json"
OUT = ROOT / "results/g7/G7R6_A2_Pilot_Authorization_Independent_Review.json"

EXPECTED_LAUNCHER_SHA256 = "91d8c7358bed926340d72fa5d912cb9c5cc64bc19157d0e88e4a7ae570cc30a5"
EXPECTED_CONTROLLER_SHA256 = (
    "806aff3b06c6efd82726a4d93f0a0ad9e3ab8314e9f5904e4707e38680c4f8de"
)
EXPECTED_MANIFEST_SHA256 = (
    "c3c803e9957a1241854fa81f3e51bbe9923d7710c866fa34e08d79d907ac8cac"
)
EXPECTED_DR0059_SHA256 = (
    "b4500f40fc2a0ce367b0148aa2162a172e5906d0935c4bc04cffd6ddf539e755"
)
EXPECTED_DB_SHA256 = (
    "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"
)


class AuthorizationReviewError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuthorizationReviewError(message)


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


def load_launcher():
    spec = importlib.util.spec_from_file_location(
        "g7r6_a2_pilot_authorization_under_review",
        LAUNCHER,
    )
    require(spec is not None and spec.loader is not None, "cannot load authorization launcher")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ast_authorization_audit(source: str) -> dict:
    tree = ast.parse(source)

    true_pilot_calls = 0
    true_production_calls = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue

        if node.func.attr == "execute_pilot":
            for keyword in node.keywords:
                if (
                    keyword.arg == "authorization"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ):
                    true_pilot_calls += 1

        if node.func.attr == "execute_production":
            for keyword in node.keywords:
                if (
                    keyword.arg == "authorization"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ):
                    true_production_calls += 1

    require(
        true_pilot_calls == 1,
        f"expected exactly one authorization=True pilot call, found {true_pilot_calls}",
    )
    require(
        true_production_calls == 0,
        "authorization wrapper contains a production authorization=True call",
    )

    return {
        "authorization_true_pilot_call_count": true_pilot_calls,
        "authorization_true_production_call_count": true_production_calls,
    }


def main() -> int:
    require(
        git_value("rev-parse", "HEAD") == PARENT_COMMIT,
        "authorization review must run at frozen controller commit",
    )
    require(
        tag_target(PARENT_TAG) == PARENT_COMMIT,
        "controller/pilot freeze tag target changed",
    )
    require(
        tag_target(AUTH_TAG) is None,
        "authorization tag already exists before review",
    )

    require(LAUNCHER.is_file(), "authorization launcher missing")
    require(CONTROLLER.is_file(), "frozen controller missing")
    require(MANIFEST.is_file(), "frozen pilot manifest missing")
    require(DR0059.is_file(), "DR-0059 missing")
    require(OFFLINE.is_file(), "authorization offline validation missing")
    require(not OUT.exists(), "authorization independent-review output already exists")
    require(not PILOT_OUTPUT.exists(), "pilot output exists before authorization freeze")
    require(not EXECUTION_AUDIT.exists(), "pilot execution audit exists before authorization freeze")

    require(
        sha256(LAUNCHER) == EXPECTED_LAUNCHER_SHA256,
        "authorization launcher SHA mismatch",
    )
    require(
        sha256(CONTROLLER) == EXPECTED_CONTROLLER_SHA256,
        "frozen controller SHA mismatch",
    )
    require(
        sha256(MANIFEST) == EXPECTED_MANIFEST_SHA256,
        "frozen pilot manifest SHA mismatch",
    )
    require(
        sha256(DR0059) == EXPECTED_DR0059_SHA256,
        "DR-0059 SHA mismatch",
    )

    with OFFLINE.open("r", encoding="utf-8") as stream:
        offline = json.load(stream)

    require(
        offline["status"]
        == "G7R6_A2_PILOT_AUTHORIZATION_OFFLINE_VALIDATION_PASS",
        "authorization offline validation is not PASS",
    )
    require(
        offline["launcher_sha256"] == EXPECTED_LAUNCHER_SHA256,
        "offline validation/launcher binding changed",
    )
    require(
        offline["controller_sha256"] == EXPECTED_CONTROLLER_SHA256,
        "offline validation/controller binding changed",
    )
    require(
        offline["pilot_manifest_sha256"] == EXPECTED_MANIFEST_SHA256,
        "offline validation/manifest binding changed",
    )
    require(
        offline["materialized_db_expected_sha256"] == EXPECTED_DB_SHA256,
        "offline validation/DB binding changed",
    )
    require(offline["pilot_event_count"] == 12, "pilot event count changed")
    require(offline["pilot_replay_count"] == 28, "pilot replay count changed")
    require(
        offline["unique_selected_payload_count"] == 56,
        "unique selected-payload count changed",
    )
    require(
        offline["controller_pilot_guard_verified"] is True,
        "controller pilot guard was not verified",
    )
    require(
        offline["controller_production_guard_verified"] is True,
        "controller production guard was not verified",
    )
    require(
        offline["orbital_propagation_performed"] is False,
        "offline authorization validation performed propagation",
    )

    source = LAUNCHER.read_text(encoding="utf-8")
    source_audit = ast_authorization_audit(source)

    require(
        "--execute-pilot" in source,
        "authorization launcher lacks execute-pilot CLI",
    )
    require(
        "--run-production" not in source
        and "--execute-production" not in source,
        "authorization launcher exposes a production CLI",
    )
    require(
        "A2_PILOT_AUTHORIZATION_TAG_MISSING" in source,
        "authorization-tag absence guard missing",
    )
    require(
        "HEAD^" in source,
        "direct-parent authorization-commit guard missing",
    )

    launcher = load_launcher()

    blocked = False
    try:
        launcher.execute_authorized_pilot()
    except launcher.PilotAuthorizationError as exc:
        require(
            "A2_PILOT_AUTHORIZATION_TAG_MISSING" in str(exc),
            "pre-freeze execution failed for an unexpected reason",
        )
        blocked = True

    require(
        blocked,
        "authorization launcher did not block execution before authorization tag",
    )
    require(not PILOT_OUTPUT.exists(), "blocked authorization attempt created pilot output")
    require(not EXECUTION_AUDIT.exists(), "blocked authorization attempt created execution audit")

    result = {
        "status": "G7R6_A2_PILOT_AUTHORIZATION_INDEPENDENT_REVIEW_PASS",
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "authorization_tag": AUTH_TAG,
        "launcher_sha256": EXPECTED_LAUNCHER_SHA256,
        "controller_sha256": EXPECTED_CONTROLLER_SHA256,
        "pilot_manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "materialized_db_expected_sha256": EXPECTED_DB_SHA256,
        "pilot_event_count": 12,
        "pilot_replay_count": 28,
        "unique_selected_payload_count": 56,
        **source_audit,
        "authorization_tag_absence_guard_verified": blocked,
        "production_authorization_true_call_count": 0,
        "production_cli_exposed": False,
        "pilot_execution_performed": False,
        "raw_GP_HISTORY_opened": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_performed": False,
        "network_requests": 0,
        "next_safe_action":
            "FREEZE_EXPLICIT_28_REPLAY_PILOT_AUTHORIZATION",
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
