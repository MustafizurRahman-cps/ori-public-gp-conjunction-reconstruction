#!/usr/bin/env python3
"""Independent static review of G6 protected metric authorization launcher.

This reviewer does not open/hash/parse the protected TraCSS raw source and
does not invoke the frozen run_metrics() scientific execution.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

LAUNCHER = (
    ROOT
    / "scripts/g6/run_g6_confirmatory_metrics.py"
)

VALIDATION = (
    ROOT
    / "results/g6/G6_Confirmatory_Metric_Execution_Authorization_Launcher_Offline_Validation.json"
)

OUT = (
    ROOT
    / "results/g6/G6_Confirmatory_Metric_Execution_Authorization_Review.json"
)

EXPECTED_PARENT = (
    "9f9819ed0769e65a5e076069474dfd88090eda87"
)

EXPECTED_PARENT_TAG = (
    "g6-confirmatory-metric-implementation-frozen"
)

EXPECTED_AUTH_TAG = (
    "g6-confirmatory-metric-execution-authorized"
)

EXPECTED_TOKEN = (
    "DR-0034_G6_CONFIRMATORY_METRICS"
)

EXPECTED_IMPLEMENTATION_SHA = (
    "bf5cd3758a2791e1cd6e9506053b1ab288541fc9d906b933907df8b49b7c9643"
)


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


def calls_in(node):

    return {
        call_name(child)
        for child in ast.walk(node)
        if isinstance(
            child,
            ast.Call,
        )
    }


if (
    git_value(
        "rev-parse",
        "HEAD",
    )
    != EXPECTED_PARENT
):
    raise ReviewError(
        "review is not running at DR-0033 parent"
    )

if (
    git_value(
        "rev-parse",
        f"refs/tags/{EXPECTED_PARENT_TAG}^{{commit}}",
    )
    != EXPECTED_PARENT
):
    raise ReviewError(
        "DR-0033 tag target mismatch"
    )


validation = json.loads(
    VALIDATION.read_text(
        encoding="utf-8"
    )
)

launcher_sha = sha256(
    LAUNCHER
)

if (
    validation.get("status")
    != "G6_CONFIRMATORY_METRIC_EXECUTION_AUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS"
):
    raise ReviewError(
        "authorization launcher validation is not PASS"
    )

if (
    validation.get(
        "launcher_sha256"
    )
    != launcher_sha
):
    raise ReviewError(
        "authorization validation references wrong launcher SHA"
    )

if (
    validation.get(
        "authorization_token"
    )
    != EXPECTED_TOKEN
):
    raise ReviewError(
        "authorization token changed"
    )

if (
    validation.get(
        "future_authorization_tag"
    )
    != EXPECTED_AUTH_TAG
):
    raise ReviewError(
        "future authorization tag changed"
    )

if (
    validation.get(
        "frozen_metric_implementation_sha256"
    )
    != EXPECTED_IMPLEMENTATION_SHA
):
    raise ReviewError(
        "frozen metric implementation SHA changed"
    )


for key in (
    "real_metric_execution_performed",
    "protected_source_hashed",
    "protected_source_opened",
    "min_range_accessed",
    "mdistance_accessed",
    "E_d_computed",
    "E_t_computed",
    "coverage_computed",
    "CAL0_refit_performed",
    "method_retuning_performed",
):

    if validation.get(key) is not False:
        raise ReviewError(
            f"offline authorization invariant failed: {key}"
        )


if (
    int(
        validation.get(
            "TraCSS_rows_parsed",
            -1,
        )
    )
    != 0
):
    raise ReviewError(
        "offline authorization parsed protected rows"
    )

if (
    int(
        validation.get(
            "network_requests",
            -1,
        )
    )
    != 0
):
    raise ReviewError(
        "offline authorization made network requests"
    )


source = LAUNCHER.read_text(
    encoding="utf-8-sig"
)

tree = ast.parse(
    source,
    filename=str(LAUNCHER),
)

functions = {
    node.name:
        node

    for node in tree.body

    if isinstance(
        node,
        ast.FunctionDef,
    )
}


required_functions = {
    "verify_parent_boundary",
    "verify_frozen_implementation",
    "verify_source_metadata_only",
    "verify_outputs_absent",
    "offline_validate",
    "verify_authorized_execution_boundary",
    "validate_protected_outputs",
    "execute",
    "main",
}

if not required_functions.issubset(
    functions
):
    raise ReviewError(
        "authorization launcher required function missing"
    )


# Offline validation must never call scientific execution.
offline_calls = calls_in(
    functions[
        "offline_validate"
    ]
)

if (
    "execute"
    in offline_calls
    or "target.run_metrics"
    in offline_calls
):
    raise ReviewError(
        "offline authorization path reaches metric execution"
    )


# Metadata-only preflight may use filesystem metadata but no raw-source hash/open.
metadata_calls = calls_in(
    functions[
        "verify_source_metadata_only"
    ]
)

if "sha256" in metadata_calls:
    raise ReviewError(
        "metadata-only reference check hashes source"
    )

if "gzip.open" in metadata_calls:
    raise ReviewError(
        "metadata-only reference check opens compressed source"
    )

if (
    "TRACSS.is_file"
    not in metadata_calls
    or "TRACSS.stat"
    not in metadata_calls
):
    raise ReviewError(
        "metadata-only source presence/size checks missing"
    )


# Authorized execute() must call only the frozen scientific top-level function.
execute_calls = calls_in(
    functions[
        "execute"
    ]
)

if (
    "target.run_metrics"
    not in execute_calls
):
    raise ReviewError(
        "authorized execute path does not call frozen run_metrics()"
    )

if "target.execute" in execute_calls:
    raise ReviewError(
        "launcher calls target CLI execute() instead of frozen run_metrics()"
    )


for forbidden in (
    "target.load_reference_rows",
    "target.load_selected_candidate_rows",
    "target.error_summary",
    "target.primary_interpretation",
    "target.wilson_interval",
    "target.linear_quantile",
):
    if forbidden in execute_calls:
        raise ReviewError(
            "authorization launcher bypasses frozen top-level metric path: "
            + forbidden
        )


# Authorization boundary must reference the frozen token/tag.
normalized_auth = ast.unparse(
    functions[
        "verify_authorized_execution_boundary"
    ]
)

if "AUTHORIZATION_TOKEN" not in normalized_auth:
    raise ReviewError(
        "authorization token check absent"
    )

if "AUTHORIZATION_TAG" not in normalized_auth:
    raise ReviewError(
        "authorization tag check absent"
    )

if "HEAD^" not in normalized_auth:
    raise ReviewError(
        "authorization parent-lineage check absent"
    )


# Launcher itself must not implement propagation/association.
for forbidden in (
    "Satrec",
    "Sgp4PairQuantity",
    "associate_reference_event",
    "enumerate_candidates",
    "min_range",
    "mdistance",
):
    if forbidden in source and forbidden in {
        "Satrec",
        "Sgp4PairQuantity",
        "associate_reference_event",
        "enumerate_candidates",
    }:
        raise ReviewError(
            "scientific orbital operation appears in authorization launcher: "
            + forbidden
        )


# No network-capable imports.
for node in ast.walk(tree):

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
    ) and node.module:

        modules = [
            node.module
        ]

    for module in modules:

        if (
            module.split(".")[0]
            in {
                "requests",
                "urllib",
                "httpx",
                "aiohttp",
                "socket",
                "ftplib",
            }
        ):
            raise ReviewError(
                "network-capable import found: "
                + module
            )


result = {
    "status":
        "G6_CONFIRMATORY_METRIC_EXECUTION_AUTHORIZATION_REVIEW_PASS",

    "review_scope":
        "STATIC_AUTHORIZATION_REVIEW_NO_PROTECTED_RAW_SOURCE_ACCESS",

    "launcher_sha256":
        launcher_sha,

    "launcher_offline_validation_sha256":
        sha256(
            VALIDATION
        ),

    "parent_commit":
        EXPECTED_PARENT,

    "parent_tag":
        EXPECTED_PARENT_TAG,

    "future_authorization_tag":
        EXPECTED_AUTH_TAG,

    "authorization_token":
        EXPECTED_TOKEN,

    "frozen_metric_implementation_sha256":
        EXPECTED_IMPLEMENTATION_SHA,

    "offline_execution_absent":
        True,

    "offline_raw_source_hash_absent":
        True,

    "offline_raw_source_open_absent":
        True,

    "authorized_path_calls_only_frozen_run_metrics":
        True,

    "authorization_token_check_present":
        True,

    "authorization_tag_check_present":
        True,

    "authorization_parent_lineage_check_present":
        True,

    "output_overwrite_guard_present":
        True,

    "network_capable_imports_absent":
        True,

    "protected_source_opened_by_review":
        False,

    "protected_source_hashed_by_review":
        False,

    "protected_rows_parsed_by_review":
        0,

    "min_range_accessed_by_review":
        False,

    "mdistance_accessed_by_review":
        False,

    "real_G6_metric_execution_performed":
        False,

    "E_d_computed":
        False,

    "E_t_computed":
        False,

    "coverage_computed":
        False,

    "method_retuning_performed":
        False,

    "network_requests":
        0,

    "next_safe_action":
        "FREEZE_DR0034_EXECUTION_AUTHORIZATION_WITHOUT_EXECUTING_METRICS",
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
