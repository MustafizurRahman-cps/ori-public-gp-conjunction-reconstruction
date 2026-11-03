#!/usr/bin/env python3
"""Independent static/synthetic review of inactive G6 metric implementation.

No TraCSS raw file is opened, hashed, decompressed, or parsed.
No real G6 E_d, E_t, coverage, or interpretation result is computed.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import json
import math
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

IMPLEMENTATION = (
    ROOT
    / "scripts/g6/compute_g6_confirmatory_metrics.py"
)

VALIDATION = (
    ROOT
    / "results/g6/G6_Confirmatory_Metric_Implementation_Offline_Validation.json"
)

OUT = (
    ROOT
    / "results/g6/G6_Confirmatory_Metric_Implementation_Independent_Review.json"
)

EXPECTED_HEAD = (
    "5141ce45b2aec0f64c2fc367794a607de86b0f30"
)

EXPECTED_TAG = (
    "g6-reference-tca-association-outputs-frozen"
)

EXPECTED_IMPLEMENTATION_SHA = (
    "bf5cd3758a2791e1cd6e9506053b1ab288541fc9d906b933907df8b49b7c9643"
)

EXPECTED_VALIDATION_SHA = (
    "673769cd31c8a955497261320bd975136f6ede5809a35cb15b27b65af6e2b7f3"
)

EXPECTED_EVENTS = 6927
EXPECTED_EVALUABLE = 6906
EXPECTED_VALID = 6839

EXPECTED_MISS_RADIUS_KM = 78.75554652312711
EXPECTED_TCA_RADIUS_S = 7.105892
EXPECTED_NOMINAL = 0.90
EXPECTED_SUPPORT = 141
EXPECTED_Z95 = 1.959963984540054

EXPECTED_TRACSS_SHA = (
    "b8e49b9c40f974e5ff37e26ade3a855813436bb4c6dab4d7ddeccc8ff5752d39"
)

EXPECTED_TRACSS_ROWS = 283595
EXPECTED_TRACSS_BYTES = 65406532

EXPECTED_STATUS_COUNTS = {
    "PROPAGATION_FAILURE": 1,
    "UNASSOCIATED_REFERENCE_EVENT": 67,
    "UNRECOVERED_REFINEMENT_FAILURE": 20,
    "VALID_ASSOCIATED": 6839,
}

EXPECTED_STRATA = {
    "G6-T1": 1386,
    "G6-T2": 1386,
    "G6-T3": 1385,
    "G6-T4": 1385,
    "G6-T5": 1385,
}

EXPECTED_EVENT_FIELDS = (
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "final_status",
    "evaluable",
    "valid_associated",
    "selected_candidate_index",
    "predicted_tca_utc",
    "reference_tca_utc",
    "signed_tca_residual_s",
    "absolute_tca_error_s",
    "tca_radius_s",
    "tca_covered",
    "predicted_miss_distance_km",
    "reference_miss_distance_km",
    "signed_miss_distance_residual_km",
    "absolute_miss_distance_error_km",
    "miss_distance_radius_km",
    "miss_distance_covered",
)

EXPECTED_TEMPORAL_FIELDS = (
    "g6_temporal_stratum",
    "total_events",
    "evaluable_count",
    "evaluable_rate",
    "evaluable_wilson_95_lower",
    "evaluable_wilson_95_upper",
    "valid_associated_count",
    "valid_association_rate",
    "valid_association_wilson_95_lower",
    "valid_association_wilson_95_upper",
    "Ed_successes",
    "Ed_denominator",
    "Ed_coverage",
    "Ed_wilson_95_lower",
    "Ed_wilson_95_upper",
    "Ed_absolute_deviation_from_0p90",
    "Ed_median_km",
    "Ed_p90_km",
    "Ed_p95_km",
    "Ed_p99_km",
    "Ed_maximum_km",
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


def call_name(node):

    if not isinstance(node, ast.Call):
        return None

    current = node.func

    if isinstance(current, ast.Name):
        return current.id

    if isinstance(current, ast.Attribute):

        parts = []

        while isinstance(
            current,
            ast.Attribute,
        ):
            parts.append(current.attr)
            current = current.value

        if isinstance(current, ast.Name):
            parts.append(current.id)

            return ".".join(
                reversed(parts)
            )

    return None


def function_map(tree):

    return {
        node.name: node
        for node in tree.body
        if isinstance(
            node,
            ast.FunctionDef,
        )
    }


def calls_in(node):

    return {
        call_name(child)
        for child in ast.walk(node)
        if isinstance(
            child,
            ast.Call,
        )
    }


def subscript_string_keys(node):

    result = set()

    for child in ast.walk(node):

        if not isinstance(
            child,
            ast.Subscript,
        ):
            continue

        key = child.slice

        if (
            isinstance(key, ast.Constant)
            and isinstance(
                key.value,
                str,
            )
        ):
            result.add(key.value)

    return result


# ============================================================
# Repository and byte-identity boundary
# ============================================================

if (
    git_value(
        "rev-parse",
        "HEAD",
    )
    != EXPECTED_HEAD
):
    raise ReviewError(
        "repository HEAD changed"
    )

if (
    git_value(
        "rev-parse",
        f"refs/tags/{EXPECTED_TAG}^{{commit}}",
    )
    != EXPECTED_HEAD
):
    raise ReviewError(
        "DR-0032 tag target changed"
    )

if git_value(
    "status",
    "--porcelain",
    "--untracked-files=no",
):
    raise ReviewError(
        "tracked working tree is not clean"
    )

if sha256(
    IMPLEMENTATION
) != EXPECTED_IMPLEMENTATION_SHA:
    raise ReviewError(
        "implementation SHA mismatch"
    )

if sha256(
    VALIDATION
) != EXPECTED_VALIDATION_SHA:
    raise ReviewError(
        "offline-validation SHA mismatch"
    )


# ============================================================
# Static implementation review
# ============================================================

source = IMPLEMENTATION.read_text(
    encoding="utf-8-sig"
)

tree = ast.parse(
    source,
    filename=str(IMPLEMENTATION),
)

functions = function_map(tree)

required_functions = {
    "linear_quantile",
    "wilson_interval",
    "binary_summary",
    "error_summary",
    "primary_interpretation",
    "verify_offline_repository_boundary",
    "verify_frozen_nonprotected_inputs",
    "verify_reference_source_metadata_only",
    "read_frozen_context",
    "verify_protected_source_integrity_for_execution",
    "load_selected_candidate_rows",
    "load_reference_rows",
    "run_metrics",
    "synthetic_statistical_validation",
    "offline_validate",
    "execute",
    "main",
}

missing = (
    required_functions
    - set(functions)
)

if missing:
    raise ReviewError(
        "required implementation function(s) missing: "
        + repr(sorted(missing))
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
    ):
        modules = (
            [node.module]
            if node.module
            else []
        )

    for module in modules:

        if (
            module.split(".")[0]
            in FORBIDDEN_NETWORK_IMPORTS
        ):
            raise ReviewError(
                "network-capable import found: "
                + module
            )


# Direct CLI execution must remain hard-disabled.
execute_fn = functions["execute"]

if (
    len(execute_fn.body) != 1
    or not isinstance(
        execute_fn.body[0],
        ast.Raise,
    )
):
    raise ReviewError(
        "execute() is no longer a single hard-disable raise"
    )

raise_node = execute_fn.body[0]

if (
    not isinstance(
        raise_node.exc,
        ast.Call,
    )
    or not raise_node.exc.args
    or not isinstance(
        raise_node.exc.args[0],
        ast.Constant,
    )
    or raise_node.exc.args[0].value
    != (
        "G6_CONFIRMATORY_METRICS_INACTIVE_UNTIL_"
        "INDEPENDENT_REVIEW_AND_EXPLICIT_AUTHORIZATION"
    )
):
    raise ReviewError(
        "metric execution authorization sentinel changed"
    )


# Offline path must not reach any protected execution helper.
offline_calls = calls_in(
    functions["offline_validate"]
)

for prohibited in (
    "run_metrics",
    "verify_protected_source_integrity_for_execution",
    "load_selected_candidate_rows",
    "load_reference_rows",
):
    if prohibited in offline_calls:
        raise ReviewError(
            "offline validation reaches protected execution helper: "
            + prohibited
        )


# Metadata-only source check must not open/hash/decompress the raw source.
metadata_calls = calls_in(
    functions[
        "verify_reference_source_metadata_only"
    ]
)

for prohibited in (
    "gzip.open",
    "TRACSS.open",
    "sha256",
):
    if prohibited in metadata_calls:
        raise ReviewError(
            "metadata-only source verifier performs raw-source access: "
            + prohibited
        )

if (
    "TRACSS.is_file"
    not in metadata_calls
    or "TRACSS.stat"
    not in metadata_calls
):
    raise ReviewError(
        "metadata-only source verifier lacks presence/size checks"
    )


# Protected execution must perform source-integrity verification.
run_calls = calls_in(
    functions["run_metrics"]
)

for required in (
    "verify_frozen_nonprotected_inputs",
    "verify_protected_source_integrity_for_execution",
    "read_frozen_context",
    "load_selected_candidate_rows",
    "load_reference_rows",
    "binary_summary",
    "error_summary",
    "primary_interpretation",
    "atomic_csv",
    "atomic_json",
):
    if required not in run_calls:
        raise ReviewError(
            "run_metrics missing required operation: "
            + required
        )


# No propagation / candidate-generation implementation may appear here.
for forbidden in (
    "Satrec.twoline2rv",
    "Sgp4PairQuantity",
    "enumerate_candidates_redundancy_safe",
    "associate_reference_event",
):
    if forbidden in source:
        raise ReviewError(
            "post-association metric implementation contains "
            "forbidden orbital/association operation: "
            + forbidden
        )


# Protected loader may use min_range but never mdistance.
reference_loader_keys = (
    subscript_string_keys(
        functions["load_reference_rows"]
    )
)

if "min_range" not in reference_loader_keys:
    raise ReviewError(
        "protected reference loader does not read min_range"
    )

if "mdistance" in reference_loader_keys:
    raise ReviewError(
        "protected reference loader accesses mdistance"
    )

if (
    "conj_id"
    not in reference_loader_keys
    or "epoch"
    not in reference_loader_keys
):
    raise ReviewError(
        "protected reference loader lacks required identity/TCA fields"
    )


# Check the core residual semantics syntactically.
run_normalized = "".join(
    ast.unparse(
        functions["run_metrics"]
    ).split()
)

required_semantic_fragments = (
    "signed_t=(predicted_tca-reference_tca).total_seconds()",
    "absolute_t=abs(signed_t)",
    "signed_d=predicted_miss-reference_miss",
    "absolute_d=abs(signed_d)",
    "absolute_t<=TCA_RADIUS_S",
    "absolute_d<=MISS_RADIUS_KM",
    "raw_epoch!=reference_tca",
)

for fragment in required_semantic_fragments:

    if fragment not in run_normalized:
        raise ReviewError(
            "required residual/coverage semantic fragment missing: "
            + fragment
        )


# Selected candidate identity must come from frozen association identity.
#
# Inspect AST subscript keys directly rather than comparing ast.unparse()
# text, because Python's unparser may choose single or double quotes.
selected_keys = subscript_string_keys(
    functions[
        "load_selected_candidate_rows"
    ]
)

if (
    "selected_candidate_index"
    not in selected_keys
):
    raise ReviewError(
        "selected candidate identity is not sourced from frozen association output"
    )

if (
    "candidate_index"
    not in selected_keys
):
    raise ReviewError(
        "candidate CSV identity field is not checked"
    )


# ============================================================
# Import without executing CLI
# ============================================================

spec = (
    importlib.util.spec_from_file_location(
        "g6_metric_review_target",
        IMPLEMENTATION,
    )
)

if (
    spec is None
    or spec.loader is None
):
    raise ReviewError(
        "could not import metric implementation"
    )

target = (
    importlib.util.module_from_spec(
        spec
    )
)

sys.modules[
    spec.name
] = target

spec.loader.exec_module(
    target
)


# Frozen constants.
constant_checks = {
    "EXPECTED_EVENTS":
        EXPECTED_EVENTS,

    "EXPECTED_EVALUABLE":
        EXPECTED_EVALUABLE,

    "EXPECTED_VALID_ASSOCIATED":
        EXPECTED_VALID,

    "MISS_RADIUS_KM":
        EXPECTED_MISS_RADIUS_KM,

    "TCA_RADIUS_S":
        EXPECTED_TCA_RADIUS_S,

    "NOMINAL_COVERAGE":
        EXPECTED_NOMINAL,

    "MIN_CONFIRMATORY_SUPPORT":
        EXPECTED_SUPPORT,

    "Z95":
        EXPECTED_Z95,

    "EXPECTED_TRACSS_SHA":
        EXPECTED_TRACSS_SHA,

    "EXPECTED_TRACSS_ROWS":
        EXPECTED_TRACSS_ROWS,

    "EXPECTED_TRACSS_BYTES":
        EXPECTED_TRACSS_BYTES,
}

for name, expected in constant_checks.items():

    actual = getattr(
        target,
        name,
    )

    if isinstance(
        expected,
        float,
    ):

        if not math.isclose(
            float(actual),
            expected,
            rel_tol=0.0,
            abs_tol=1.0e-15,
        ):
            raise ReviewError(
                f"frozen constant changed: {name}"
            )

    elif actual != expected:
        raise ReviewError(
            f"frozen constant changed: {name}"
        )


if (
    target.EXPECTED_STRATUM_TOTALS
    != EXPECTED_STRATA
):
    raise ReviewError(
        "frozen temporal strata changed"
    )

if tuple(
    target.EVENT_OUTPUT_FIELDS
) != EXPECTED_EVENT_FIELDS:
    raise ReviewError(
        "event metric output schema changed"
    )

if tuple(
    target.TEMPORAL_FIELDS
) != EXPECTED_TEMPORAL_FIELDS:
    raise ReviewError(
        "temporal summary schema changed"
    )


# Direct execute() must fail without touching protected source.
try:
    target.execute()

except target.MetricError as exc:

    expected_message = (
        "G6_CONFIRMATORY_METRICS_INACTIVE_UNTIL_"
        "INDEPENDENT_REVIEW_AND_EXPLICIT_AUTHORIZATION"
    )

    if str(exc) != expected_message:
        raise ReviewError(
            "execute() raised wrong authorization sentinel"
        ) from exc

else:
    raise ReviewError(
        "execute() unexpectedly executed"
    )


# ============================================================
# Independent statistical reference implementations
# ============================================================

def independent_linear_quantile(
    values,
    q,
):

    ordered = sorted(
        float(value)
        for value in values
    )

    h = (
        (len(ordered) - 1)
        * float(q)
    )

    lo = math.floor(h)
    hi = math.ceil(h)

    if lo == hi:
        return ordered[lo]

    return (
        ordered[lo]
        + (
            h - lo
        )
        * (
            ordered[hi]
            - ordered[lo]
        )
    )


def independent_wilson(
    successes,
    denominator,
):

    p = (
        successes
        / denominator
    )

    z = EXPECTED_Z95
    z2 = z * z
    n = float(denominator)

    denom = (
        1.0
        + z2 / n
    )

    center = (
        p
        + z2
        / (
            2.0 * n
        )
    ) / denom

    half = (
        z
        / denom
        * math.sqrt(
            p
            * (
                1.0 - p
            )
            / n
            + z2
            / (
                4.0
                * n
                * n
            )
        )
    )

    return (
        center - half,
        center + half,
    )


# ============================================================
# Independent synthetic tests
# ============================================================

quantile_cases = (
    (
        [0, 10, 20, 30, 40],
        0.50,
    ),
    (
        [0, 10, 20, 30, 40],
        0.90,
    ),
    (
        [5, 1, 9, 3, 7, 11],
        0.95,
    ),
    (
        [2, 8, 4, 6],
        0.99,
    ),
)

for values, q in quantile_cases:

    expected = (
        independent_linear_quantile(
            values,
            q,
        )
    )

    actual = (
        target.linear_quantile(
            values,
            q,
        )
    )

    if not math.isclose(
        actual,
        expected,
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise ReviewError(
            f"independent quantile mismatch at q={q}"
        )


wilson_cases = (
    (90, 100),
    (100, 150),
    (135, 150),
    (149, 150),
    (6839, 6927),
    (6839, 6839),
)

for successes, denominator in wilson_cases:

    expected = (
        independent_wilson(
            successes,
            denominator,
        )
    )

    actual = (
        target.wilson_interval(
            successes,
            denominator,
        )
    )

    for a, b in zip(
        actual,
        expected,
    ):
        if not math.isclose(
            a,
            b,
            rel_tol=0.0,
            abs_tol=1.0e-15,
        ):
            raise ReviewError(
                "independent Wilson mismatch: "
                f"{successes}/{denominator}"
            )


# Inclusive fixed-radius boundary.
summary = target.error_summary(
    [
        0.0,
        EXPECTED_MISS_RADIUS_KM,
        EXPECTED_MISS_RADIUS_KM + 1.0e-9,
    ],
    EXPECTED_MISS_RADIUS_KM,
)

if (
    summary[
        "coverage_successes"
    ]
    != 2
    or summary[
        "coverage_denominator"
    ]
    != 3
):
    raise ReviewError(
        "fixed-radius inclusive-boundary semantics failed"
    )


# Minimum-support precedence.
low, high = target.wilson_interval(
    100,
    100,
)

insufficient = (
    target.primary_interpretation(
        100,
        1.0,
        low,
        high,
    )
)

if (
    insufficient[
        "classification"
    ]
    != "INSUFFICIENT CAUSAL/EVALUABLE SUPPORT"
):
    raise ReviewError(
        "minimum-support precedence failed"
    )


# Undercoverage.
low, high = target.wilson_interval(
    100,
    150,
)

under = target.primary_interpretation(
    150,
    100 / 150,
    low,
    high,
)

if (
    under[
        "classification"
    ]
    != "NOMINAL 90% COVERAGE NOT SUPPORTED"
    or under[
        "direction"
    ]
    != "UNDERCOVERAGE"
):
    raise ReviewError(
        "undercoverage classification failed"
    )


# Conservative overcoverage.
low, high = target.wilson_interval(
    149,
    150,
)

over = target.primary_interpretation(
    150,
    149 / 150,
    low,
    high,
)

if (
    over[
        "classification"
    ]
    != "NOMINAL 90% COVERAGE NOT SUPPORTED"
    or over[
        "direction"
    ]
    != "CONSERVATIVE OVERCOVERAGE"
):
    raise ReviewError(
        "conservative-overcoverage classification failed"
    )


# Nominal consistency.
low, high = target.wilson_interval(
    135,
    150,
)

consistent = (
    target.primary_interpretation(
        150,
        0.90,
        low,
        high,
    )
)

if (
    consistent[
        "classification"
    ]
    != "CONSISTENT WITH NOMINAL 90% COVERAGE"
):
    raise ReviewError(
        "nominal-consistency classification failed"
    )


# ============================================================
# Offline-validation artifact audit
# ============================================================

validation = json.loads(
    VALIDATION.read_text(
        encoding="utf-8"
    )
)

if (
    validation.get("status")
    != "G6_CONFIRMATORY_METRIC_IMPLEMENTATION_OFFLINE_VALIDATION_PASS"
):
    raise ReviewError(
        "offline-validation status is not PASS"
    )

if (
    validation.get(
        "implementation_sha256"
    )
    != EXPECTED_IMPLEMENTATION_SHA
):
    raise ReviewError(
        "validation references wrong implementation SHA"
    )

expected_scalars = {
    "events_verified":
        6927,

    "evaluable_events_frozen":
        6906,

    "VALID_ASSOCIATED_frozen":
        6839,

    "UNASSOCIATED_REFERENCE_EVENT_frozen":
        67,

    "UNRECOVERED_REFINEMENT_FAILURE_frozen":
        20,

    "PROPAGATION_FAILURE_frozen":
        1,

    "miss_distance_radius_km":
        EXPECTED_MISS_RADIUS_KM,

    "TCA_radius_s":
        EXPECTED_TCA_RADIUS_S,

    "nominal_coverage":
        EXPECTED_NOMINAL,

    "minimum_confirmatory_support":
        EXPECTED_SUPPORT,

    "Wilson_z":
        EXPECTED_Z95,

    "quantile_rule":
        "LINEAR_EMPIRICAL_QUANTILE_H=(N-1)Q",

    "primary_reference_field":
        "min_range",

    "mdistance_authorized":
        False,

    "real_G6_metric_execution_performed":
        False,

    "TraCSS_raw_bytes_hashed":
        False,

    "TraCSS_raw_file_opened":
        False,

    "TraCSS_rows_parsed":
        0,

    "physical_reference_min_range_access":
        False,

    "mdistance_access":
        False,

    "E_d_computed_on_real_G6":
        False,

    "E_t_computed_on_real_G6":
        False,

    "coverage_computed_on_real_G6":
        False,

    "orbital_propagation_performed":
        False,

    "candidate_generation_performed":
        False,

    "association_recomputed":
        False,

    "CAL0_refit_performed":
        False,

    "bootstrap_performed":
        False,

    "method_retuning_performed":
        False,

    "covariance_access":
        False,

    "Pc_access":
        False,

    "HBR_access":
        False,

    "network_requests":
        0,

    "direct_execution_authorized":
        False,
}

for key, expected in expected_scalars.items():

    actual = validation.get(key)

    if isinstance(
        expected,
        float,
    ):

        if not math.isclose(
            float(actual),
            expected,
            rel_tol=0.0,
            abs_tol=1.0e-15,
        ):
            raise ReviewError(
                f"offline-validation invariant mismatch: {key}"
            )

    elif actual != expected:
        raise ReviewError(
            f"offline-validation invariant mismatch: {key}"
        )


if (
    validation.get(
        "frozen_final_status_counts"
    )
    != EXPECTED_STATUS_COUNTS
):
    raise ReviewError(
        "offline-validation frozen status census mismatch"
    )

if (
    validation.get(
        "temporal_stratum_totals"
    )
    != EXPECTED_STRATA
):
    raise ReviewError(
        "offline-validation stratum census mismatch"
    )


metadata = validation.get(
    "reference_source_metadata",
    {}
)

metadata_expected = {
    "exists":
        True,

    "byte_count":
        EXPECTED_TRACSS_BYTES,

    "recorded_row_count":
        EXPECTED_TRACSS_ROWS,

    "recorded_sha256":
        EXPECTED_TRACSS_SHA,

    "raw_bytes_hashed_during_offline_validation":
        False,

    "raw_file_opened_during_offline_validation":
        False,

    "raw_rows_parsed_during_offline_validation":
        0,

    "min_range_accessed":
        False,

    "mdistance_accessed":
        False,
}

for key, expected in metadata_expected.items():

    if metadata.get(key) != expected:
        raise ReviewError(
            "reference-source metadata invariant mismatch: "
            + key
        )


synthetic = validation.get(
    "synthetic_statistical_validation",
    {}
)

for key in (
    "Wilson_two_sided_95pct",
    "conservative_overcoverage_classification",
    "fixed_radius_inclusive_boundary",
    "linear_quantile_median",
    "linear_quantile_p90",
    "linear_quantile_p95",
    "linear_quantile_p99",
    "minimum_support_precedence",
    "nominal_consistency_classification",
    "undercoverage_classification",
):
    if synthetic.get(key) is not True:
        raise ReviewError(
            "offline synthetic validation not PASS: "
            + key
        )


# ============================================================
# Protected-source nonaccess assertion for THIS reviewer
# ============================================================

# Deliberately no existence/stat/hash/open call on target.TRACSS occurs here.
# Its provenance is verified solely from the already-created offline record.

result = {
    "status":
        "G6_CONFIRMATORY_METRIC_IMPLEMENTATION_INDEPENDENT_REVIEW_PASS",

    "review_scope":
        "STATIC_AND_SYNTHETIC_NO_PROTECTED_RAW_SOURCE_ACCESS",

    "implementation_sha256":
        EXPECTED_IMPLEMENTATION_SHA,

    "offline_validation_sha256":
        EXPECTED_VALIDATION_SHA,

    "frozen_event_count":
        EXPECTED_EVENTS,

    "frozen_evaluable_count":
        EXPECTED_EVALUABLE,

    "frozen_VALID_ASSOCIATED_count":
        EXPECTED_VALID,

    "fixed_miss_distance_radius_km":
        EXPECTED_MISS_RADIUS_KM,

    "fixed_TCA_radius_s":
        EXPECTED_TCA_RADIUS_S,

    "nominal_coverage":
        EXPECTED_NOMINAL,

    "minimum_confirmatory_support":
        EXPECTED_SUPPORT,

    "Wilson_z":
        EXPECTED_Z95,

    "quantile_rule":
        "LINEAR_EMPIRICAL_QUANTILE_H=(N-1)Q",

    "direct_metric_execution_hard_disabled":
        True,

    "offline_path_protected_execution_helpers_absent":
        True,

    "offline_metadata_path_raw_open_absent":
        True,

    "offline_metadata_path_raw_hash_absent":
        True,

    "protected_execution_requires_source_hash_verification":
        True,

    "protected_reference_field":
        "min_range",

    "mdistance_field_access_in_reference_loader":
        False,

    "frozen_selected_candidate_identity_reused":
        True,

    "reference_epoch_equality_check_present":
        True,

    "signed_TCA_residual_definition_verified":
        True,

    "absolute_TCA_error_definition_verified":
        True,

    "signed_miss_residual_definition_verified":
        True,

    "absolute_miss_error_definition_verified":
        True,

    "fixed_radius_inclusive_semantics_verified":
        True,

    "linear_quantile_independent_tests_passed":
        True,

    "Wilson_independent_tests_passed":
        True,

    "minimum_support_precedence_verified":
        True,

    "undercoverage_interpretation_verified":
        True,

    "conservative_overcoverage_interpretation_verified":
        True,

    "nominal_consistency_interpretation_verified":
        True,

    "temporal_strata_frozen":
        True,

    "orbital_propagation_path_absent":
        True,

    "candidate_generation_path_absent":
        True,

    "association_recomputation_path_absent":
        True,

    "bootstrap_not_authorized":
        True,

    "network_capable_imports_absent":
        True,

    "real_G6_metric_execution_performed":
        False,

    "TraCSS_raw_source_opened_by_review":
        False,

    "TraCSS_raw_source_hashed_by_review":
        False,

    "TraCSS_rows_parsed_by_review":
        0,

    "physical_reference_min_range_access_by_review":
        False,

    "E_d_computed_on_real_G6":
        False,

    "E_t_computed_on_real_G6":
        False,

    "coverage_computed_on_real_G6":
        False,

    "CAL0_refit_performed":
        False,

    "method_retuning_performed":
        False,

    "network_requests":
        0,

    "next_safe_action":
        "FREEZE_INACTIVE_CONFIRMATORY_METRIC_IMPLEMENTATION_BEFORE_PROTECTED_OUTCOME_AUTHORIZATION",
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
