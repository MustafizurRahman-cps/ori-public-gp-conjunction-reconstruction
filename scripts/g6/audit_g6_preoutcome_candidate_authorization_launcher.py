#!/usr/bin/env python3
"""Independent review of G6 pre-outcome candidate authorization launcher.

No G6 orbital propagation or candidate generation is executed.
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
    / "scripts/g6/run_g6_preoutcome_candidate_generation.py"
)

VALIDATION = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Authorization_Launcher_Offline_Validation.json"
)

OUT = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Authorization_Launcher_Review.json"
)

PARENT_COMMIT = (
    "6ee71bcc5069638535d3f2f77329c35de5ec1aba"
)

PARENT_TAG = (
    "g6-preoutcome-candidate-implementation-frozen"
)

EXECUTION_TAG = (
    "g6-preoutcome-candidate-generation-authorized"
)

TOKEN = (
    "DR-0028_G6_PREOUTCOME_CANDIDATE_GENERATION"
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

    digest = hashlib.sha256()

    with path.open("rb") as stream:

        for block in iter(
            lambda: stream.read(
                1024 * 1024
            ),
            b"",
        ):
            digest.update(
                block
            )

    return digest.hexdigest()


def git_value(
    *args: str,
) -> str:

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


def calls_in(function):

    return {
        call_name(
            node
        )

        for node in ast.walk(
            function
        )

        if isinstance(
            node,
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
            "review HEAD is not DR-0027"
        )

    if (
        git_value(
            "rev-parse",
            f"refs/tags/{PARENT_TAG}^{{commit}}",
        )
        != PARENT_COMMIT
    ):
        raise ReviewError(
            "DR-0027 tag target changed"
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
        "verify_local_checkpoint_governance",
        "verify_final_outputs_absent",
        "offline_validate",
        "execute_or_resume",
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
            "launcher function set incomplete: "
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

        if isinstance(
            node,
            ast.Import,
        ):

            modules = [
                alias.name
                for alias
                in node.names
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
        "target.run_generation_to_work_db"
        in offline_calls
    ):

        raise ReviewError(
            "offline validation can execute G6 candidate generation"
        )

    execution_calls = calls_in(
        functions[
            "execute_or_resume"
        ]
    )

    if (
        "target.run_generation_to_work_db"
        not in execution_calls
    ):

        raise ReviewError(
            "authorized execution does not call frozen generation function"
        )

    forbidden_scientific_calls = {
        "target.process_event",
        "target.satrec_from_record",
        "target.enumerate_candidates_redundancy_safe",
        "target.g4.associate_reference_event",
        "associate_reference_event",
    }

    overlap = (
        execution_calls
        & forbidden_scientific_calls
    )

    if overlap:

        raise ReviewError(
            "authorization launcher reimplements scientific logic: "
            + repr(
                sorted(
                    overlap
                )
            )
        )

    # The launcher must not include final outcome operations.
    forbidden_tokens = (
        "min_range",
        "mdistance",
        "VALID_ASSOCIATED",
        "UNASSOCIATED_REFERENCE_EVENT",
        "E_d",
        "E_t",
        "associate_reference_event",
        "IVV_Releasable_Dataset",
        "data/raw/g2",
    )

    for token in forbidden_tokens:

        if token in source:

            raise ReviewError(
                "protected/final-association token found in launcher: "
                + token
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

    expected_scalars = {
        "status":
            "G6_PREOUTCOME_CANDIDATE_AUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS",

        "events_verified":
            6927,

        "objects_verified":
            13854,

        "candidate_work_db_gitignored":
            True,

        "candidate_work_db_tracked":
            False,

        "candidate_work_db_exists":
            False,

        "generation_executed":
            False,

        "G6_orbital_propagation_performed":
            False,

        "G6_candidate_generation_executed":
            False,

        "reference_association_performed":
            False,

        "TraCSS_reference_rows_opened":
            0,

        "protected_outcome_access":
            False,

        "G6_error_metrics_computed":
            False,

        "full_GP_HISTORY_reread":
            False,

        "network_requests":
            0,

        "execution_authorized_in_this_mode":
            False,
    }

    for key, expected in (
        expected_scalars.items()
    ):

        if validation.get(
            key
        ) != expected:

            raise ReviewError(
                f"launcher validation invariant mismatch: {key}"
            )

    result = {
        "status":
            "G6_PREOUTCOME_CANDIDATE_AUTHORIZATION_LAUNCHER_REVIEW_PASS",

        "launcher_sha256":
            args.expected_launcher_sha,

        "launcher_offline_validation_sha256":
            args.expected_validation_sha,

        "DR0027_boundary_verified":
            True,

        "network_capable_imports_absent":
            True,

        "offline_path_generation_absent":
            True,

        "authorized_path_calls_frozen_generation_function":
            True,

        "authorization_launcher_scientific_reimplementation_absent":
            True,

        "reference_association_logic_absent":
            True,

        "protected_outcome_logic_absent":
            True,

        "explicit_authorization_tag_required":
            True,

        "explicit_authorization_token_required":
            True,

        "resume_checkpoint_supported":
            True,

        "G6_orbital_propagation_performed":
            False,

        "G6_candidate_generation_executed":
            False,

        "TraCSS_reference_rows_opened":
            0,

        "reference_association_performed":
            False,

        "protected_outcome_access":
            False,

        "G6_error_metrics_computed":
            False,

        "full_GP_HISTORY_reread":
            False,

        "network_requests":
            0,

        "next_safe_action":
            "FREEZE_DR0028_AUTHORIZATION_LAYER_WITHOUT_EXECUTING_G6_CANDIDATE_GENERATION",
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
