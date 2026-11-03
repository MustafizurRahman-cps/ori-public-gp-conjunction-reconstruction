#!/usr/bin/env python3
"""Independent review of G6 reference-TCA association authorization layer.

No real G6 association is performed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

LAUNCHER = (
    ROOT
    / "scripts/g6/run_g6_reference_tca_association.py"
)

VALIDATION = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Authorization_Launcher_Offline_Validation.json"
)

OUT = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Authorization_Launcher_Review.json"
)

PARENT_COMMIT = (
    "ea0d92d7f1080bcdfb7831adcd41e96f0e70eeff"
)

PARENT_TAG = (
    "g6-reference-tca-association-implementation-frozen"
)

EXECUTION_TAG = (
    "g6-reference-tca-association-authorized"
)

TOKEN = (
    "DR-0031_G6_REFERENCE_TCA_ASSOCIATION"
)

FORBIDDEN_NETWORK_ROOTS = {
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


def call_name(node):

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

            current = (
                current.value
            )

        if isinstance(
            current,
            ast.Name,
        ):

            parts.append(
                current.id
            )

            return ".".join(
                reversed(
                    parts
                )
            )

    return None


def function_map(tree):

    return {
        node.name:
            node

        for node in tree.body

        if isinstance(
            node,
            ast.FunctionDef,
        )
    }


def calls_in(node):

    return {
        call_name(
            child
        )
        for child in ast.walk(
            node
        )
        if isinstance(
            child,
            ast.Call,
        )
    }


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

    if (
        git_value(
            "rev-parse",
            "HEAD",
        )
        != PARENT_COMMIT
    ):
        raise ReviewError(
            "review HEAD is not DR-0030"
        )

    if (
        git_value(
            "rev-parse",
            f"refs/tags/{PARENT_TAG}^{{commit}}",
        )
        != PARENT_COMMIT
    ):
        raise ReviewError(
            "DR-0030 tag target changed"
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
        filename=str(
            LAUNCHER
        ),
    )

    functions = function_map(
        tree
    )

    required = {
        "verify_frozen_dependencies",
        "verify_parent_boundary",
        "verify_execution_boundary",
        "import_target",
        "verify_outputs_absent",
        "offline_validate",
        "execute",
        "main",
    }

    missing = (
        required
        - set(
            functions
        )
    )

    if missing:
        raise ReviewError(
            "authorization launcher function set incomplete: "
            + repr(
                sorted(
                    missing
                )
            )
        )

    # No network-capable imports.
    for node in ast.walk(
        tree
    ):

        modules = []

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

        for module in modules:

            if (
                module.split(".")[0]
                in FORBIDDEN_NETWORK_ROOTS
            ):
                raise ReviewError(
                    "network-capable import found: "
                    + module
                )

    offline_calls = calls_in(
        functions[
            "offline_validate"
        ]
    )

    if (
        "target.run_association"
        in offline_calls
    ):
        raise ReviewError(
            "offline validation can execute real G6 association"
        )

    execute_calls = calls_in(
        functions[
            "execute"
        ]
    )

    if (
        "target.run_association"
        not in execute_calls
    ):
        raise ReviewError(
            "authorized execution does not call frozen run_association()"
        )

    forbidden_scientific_calls = {
        "target.associate_one_ready_event",
        "target.make_candidate",
        "target.import_g4",
        "target.read_sample",
        "target.read_event_status",
    }

    overlap = (
        execute_calls
        & forbidden_scientific_calls
    )

    if overlap:
        raise ReviewError(
            "launcher reimplements/bypasses frozen scientific association path: "
            + repr(
                sorted(
                    overlap
                )
            )
        )

    forbidden_tokens = (
        "min_range",
        "mdistance",
        "signed_tca_residual",
        "absolute_tca_error",
        "signed_miss_residual",
        "absolute_miss_error",
        "associate_reference_event",
        "reference_distance_km",
    )

    # These terms may appear only in invariant/status key names where
    # explicitly proving absence, not as physical-data access logic.
    # Reject subscript access to physical reference fields.
    execute_fn = functions[
        "execute"
    ]

    for node in ast.walk(
        execute_fn
    ):

        if not isinstance(
            node,
            ast.Subscript,
        ):
            continue

        key = node.slice

        if isinstance(
            key,
            ast.Constant,
        ):

            if key.value in {
                "min_range",
                "mdistance",
                "reference_distance_km",
                "E_d",
                "E_t",
            }:
                raise ReviewError(
                    "authorized execution reads prohibited physical outcome field: "
                    + str(
                        key.value
                    )
                )

    if EXECUTION_TAG not in source:
        raise ReviewError(
            "authorization tag literal missing"
        )

    if TOKEN not in source:
        raise ReviewError(
            "explicit authorization token missing"
        )

    validation = json.loads(
        VALIDATION.read_text(
            encoding="utf-8"
        )
    )

    expected = {
        "status":
            "G6_REFERENCE_TCA_ASSOCIATION_AUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS",

        "events":
            6927,

        "association_eligible_events":
            6906,

        "preoutcome_terminal_events":
            21,

        "association_tolerance_min":
            5.0,

        "association_execution_performed":
            False,

        "orbital_propagation_performed":
            False,

        "candidate_generation_performed":
            False,

        "reference_miss_distance_access":
            False,

        "TraCSS_physical_outcome_rows_opened":
            0,

        "E_t_computed":
            False,

        "E_d_computed":
            False,

        "coverage_computed":
            False,

        "covariance_access":
            False,

        "Pc_access":
            False,

        "HBR_access":
            False,

        "method_retuning_performed":
            False,

        "network_requests":
            0,

        "execution_authorized_in_this_mode":
            False,
    }

    for key, expected_value in expected.items():

        if validation.get(
            key
        ) != expected_value:
            raise ReviewError(
                f"offline-validation invariant mismatch: {key}"
            )

    result = {
        "status":
            "G6_REFERENCE_TCA_ASSOCIATION_AUTHORIZATION_LAUNCHER_REVIEW_PASS",

        "launcher_sha256":
            args.expected_launcher_sha,

        "launcher_offline_validation_sha256":
            args.expected_validation_sha,

        "DR0030_boundary_verified":
            True,

        "network_capable_imports_absent":
            True,

        "offline_path_real_association_absent":
            True,

        "authorized_path_calls_only_frozen_run_association":
            True,

        "scientific_association_reimplementation_absent":
            True,

        "physical_reference_outcome_access_absent":
            True,

        "explicit_authorization_tag_required":
            True,

        "explicit_authorization_token_required":
            True,

        "real_G6_association_executed":
            False,

        "orbital_propagation_performed":
            False,

        "candidate_generation_performed":
            False,

        "TraCSS_physical_outcome_rows_opened":
            0,

        "reference_miss_distance_access":
            False,

        "error_metrics_computed":
            False,

        "coverage_computed":
            False,

        "method_retuning_performed":
            False,

        "network_requests":
            0,

        "next_safe_action":
            "FREEZE_DR0031_AUTHORIZATION_LAYER_WITHOUT_EXECUTING_REFERENCE_TCA_ASSOCIATION",
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
