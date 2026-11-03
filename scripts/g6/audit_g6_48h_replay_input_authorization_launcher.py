#!/usr/bin/env python3
"""Independent static review of G6 replay-input authorization launcher."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

LAUNCHER = (
    ROOT / "scripts/g6/run_g6_48h_replay_input_preparation.py"
)

VALIDATION = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Authorization_Launcher_Offline_Validation.json"
)

OUT = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Authorization_Launcher_Review.json"
)

PARENT_COMMIT = (
    "138ab478495b7ef106bd0f3078fe92a572758099"
)

PARENT_TAG = (
    "g6-48h-replay-input-implementation-frozen"
)

EXECUTION_TAG = (
    "g6-48h-replay-input-preparation-authorized"
)

FORBIDDEN_NETWORK_IMPORTS = {
    "requests",
    "urllib",
    "httpx",
    "aiohttp",
    "socket",
    "ftplib",
}


class ReviewError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def git_value(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def call_name(node) -> str | None:

    if not isinstance(
        node,
        ast.Call,
    ):
        return None

    current = node.func

    if isinstance(
        current,
        ast.Name,
    ):
        return current.id

    if isinstance(
        current,
        ast.Attribute,
    ):
        parts = []

        while isinstance(
            current,
            ast.Attribute,
        ):
            parts.append(
                current.attr
            )
            current = current.value

        if isinstance(
            current,
            ast.Name,
        ):
            parts.append(
                current.id
            )

            return ".".join(
                reversed(parts)
            )

    return None


def functions(tree):
    return {
        node.name: node
        for node in tree.body
        if isinstance(
            node,
            ast.FunctionDef,
        )
    }


def ordered_call_lines(
    fn,
    required,
):
    found = {}

    for node in ast.walk(fn):

        if not isinstance(
            node,
            ast.Call,
        ):
            continue

        name = call_name(
            node
        )

        if (
            name in required
            and name not in found
        ):
            found[
                name
            ] = node.lineno

    missing = [
        name
        for name in required
        if name not in found
    ]

    if missing:
        raise ReviewError(
            "required execution calls missing: "
            + repr(missing)
        )

    lines = [
        found[name]
        for name in required
    ]

    if lines != sorted(lines):
        raise ReviewError(
            "frozen helper execution sequence is out of order"
        )

    return found


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--expected-launcher-sha",
        required=True,
    )

    parser.add_argument(
        "--expected-validation-sha",
        required=True,
    )

    args = parser.parse_args()

    if git_value(
        "rev-parse",
        "HEAD",
    ) != PARENT_COMMIT:
        raise ReviewError(
            "review HEAD is not DR-0023 commit"
        )

    if git_value(
        "rev-parse",
        f"refs/tags/{PARENT_TAG}^{{commit}}",
    ) != PARENT_COMMIT:
        raise ReviewError(
            "DR-0023 tag target mismatch"
        )

    if sha256(
        LAUNCHER
    ) != args.expected_launcher_sha:
        raise ReviewError(
            "launcher SHA mismatch"
        )

    if sha256(
        VALIDATION
    ) != args.expected_validation_sha:
        raise ReviewError(
            "launcher validation SHA mismatch"
        )

    source = LAUNCHER.read_text(
        encoding="utf-8-sig"
    )

    tree = ast.parse(
        source,
        filename=str(LAUNCHER),
    )

    funcs = functions(
        tree
    )

    for required in (
        "verify_frozen_dependencies",
        "verify_parent_boundary",
        "verify_execution_boundary",
        "import_target",
        "offline_validate",
        "validate_selected_tles",
        "execute_once",
        "main",
    ):
        if required not in funcs:
            raise ReviewError(
                "launcher function missing: "
                + required
            )

    for node in ast.walk(
        tree
    ):

        if isinstance(
            node,
            ast.Import,
        ):
            modules = [
                alias.name
                for alias in node.names
            ]

        elif isinstance(
            node,
            ast.ImportFrom,
        ):
            modules = (
                [node.module]
                if node.module
                else []
            )

        else:
            continue

        for module in modules:
            if (
                module.split(
                    "."
                )[0]
                in FORBIDDEN_NETWORK_IMPORTS
            ):
                raise ReviewError(
                    "network-capable import found: "
                    + module
                )

    offline_calls = {
        call_name(node)
        for node in ast.walk(
            funcs[
                "offline_validate"
            ]
        )
        if isinstance(
            node,
            ast.Call,
        )
    }

    forbidden_offline_calls = {
        "target.load_execution_inputs",
        "target.stream_main_first_pass",
        "target.add_queried_predecessors",
        "target.derive_cutoff_and_replay_times",
        "target.stream_main_reselection_pass",
        "target.finalize_reselection",
        "target.write_prepared_outputs",
    }

    if (
        offline_calls
        & forbidden_offline_calls
    ):
        raise ReviewError(
            "offline validation can enter raw execution path"
        )

    execute = funcs[
        "execute_once"
    ]

    execution_calls = {
        call_name(node)
        for node in ast.walk(
            execute
        )
        if isinstance(
            node,
            ast.Call,
        )
    }

    if (
        "target.verify_repository_boundary"
        in execution_calls
    ):
        raise ReviewError(
            "launcher incorrectly calls stale frozen target boundary checker"
        )

    required_order = (
        "verify_execution_boundary",
        "verify_frozen_dependencies",
        "import_target",
        "target.verify_small_inputs",
        "target.verify_environment",
        "target.validate_frozen_metadata",
        "target.load_execution_inputs",
        "target.stream_main_first_pass",
        "target.add_queried_predecessors",
        "target.derive_cutoff_and_replay_times",
        "target.stream_main_reselection_pass",
        "target.finalize_reselection",
        "validate_selected_tles",
        "target.write_prepared_outputs",
    )

    ordered_call_lines(
        execute,
        required_order,
    )

    if (
        EXECUTION_TAG
        not in source
    ):
        raise ReviewError(
            "execution authorization tag literal missing"
        )

    if (
        "DR-0024_G6_48H_REPLAY_INPUT_PREPARATION"
        not in source
    ):
        raise ReviewError(
            "explicit authorization token missing"
        )

    for forbidden in (
        "TraCSS/",
        "tracss/",
        "answer_key",
        "min_range",
        "mdistance",
        "reconstructed_miss_distance",
    ):
        if forbidden in source:
            raise ReviewError(
                "protected-source token found in launcher: "
                + forbidden
            )

    validation = json.loads(
        VALIDATION.read_text(
            encoding="utf-8"
        )
    )

    if (
        validation.get(
            "status"
        )
        != "G6_48H_REPLAY_INPUT_AUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS"
    ):
        raise ReviewError(
            "launcher offline validation is not PASS"
        )

    for key, expected in {
        "network_requests": 0,
        "SpaceTrack_raw_files_opened": 0,
        "TraCSS_reference_rows_opened": 0,
        "protected_outcome_access": False,
        "propagation_performed": False,
        "reconstruction_performed": False,
        "replay_input_preparation_executed": False,
        "execution_authorized_in_this_mode": False,
    }.items():

        if validation.get(
            key
        ) != expected:
            raise ReviewError(
                f"launcher validation invariant mismatch: {key}"
            )

    result = {
        "status":
            "G6_48H_REPLAY_INPUT_AUTHORIZATION_LAUNCHER_REVIEW_PASS",

        "launcher_sha256":
            args.expected_launcher_sha,

        "launcher_offline_validation_sha256":
            args.expected_validation_sha,

        "network_requests":
            0,

        "SpaceTrack_raw_files_opened":
            0,

        "TraCSS_reference_rows_opened":
            0,

        "protected_outcome_access":
            False,

        "propagation_performed":
            False,

        "reconstruction_performed":
            False,

        "offline_path_raw_execution_absent":
            True,

        "stale_target_boundary_checker_not_called":
            True,

        "DR0024_execution_tag_required":
            True,

        "explicit_authorization_token_required":
            True,

        "frozen_helper_call_order_verified":
            True,

        "selected_TLE_integrity_guard_present":
            True,

        "network_capable_imports_absent":
            True,

        "next_safe_action":
            "FREEZE_DR0024_AUTHORIZATION_LAYER_WITHOUT_EXECUTING_RAW_PREPARATION",
    }

    OUT.write_text(
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(
        json.dumps(
            {
                **result,
                "review_script_sha256":
                    sha256(
                        Path(__file__)
                    ),
                "review_artifact_sha256":
                    sha256(
                        OUT
                    ),
            },
            indent=2,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
