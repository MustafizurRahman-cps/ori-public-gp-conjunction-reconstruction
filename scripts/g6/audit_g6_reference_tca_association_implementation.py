#!/usr/bin/env python3
"""Independent review of inactive G6 reference-TCA association implementation.

No real G6 association is executed.
No G6 propagation or candidate generation is executed.
No physical TraCSS outcome quantity is opened.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

IMPLEMENTATION = (
    ROOT
    / "scripts/g6/associate_g6_reference_tca.py"
)

VALIDATION = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Implementation_Offline_Validation.json"
)

OUT = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Implementation_Independent_Review.json"
)

G4_NUMERICS = (
    ROOT
    / "scripts/g4/numerics.py"
)

ASSOCIATION_OUT = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association.csv"
)

ASSOCIATION_SUMMARY = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Summary.json"
)


EXPECTED_HEAD = (
    "cb0981bfea448a45beefed2112df16d4ee6f8c4c"
)

EXPECTED_TAG = (
    "g6-preoutcome-candidate-outputs-frozen"
)

EXPECTED_IMPLEMENTATION_SHA = (
    "4bc879120ca6d55769847e3d52e54f00bf3ed74e905112abee0367ffbbee3c79"
)

EXPECTED_VALIDATION_SHA = (
    "a629efed90cd8d23d908bc1511c3df315eb21066353db8d2de60c5a6f4df7138"
)

EXPECTED_G4_SHA = (
    "dc8de410b89e05ffd829563b4996b06703c31236e9a10fb56bd7f4e690048406"
)

EXPECTED_OUTPUT_FIELDS = (
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "input_preoutcome_status",
    "association_status",
    "final_status",
    "selected_candidate_index",
)

FORBIDDEN_NETWORK_ROOTS = {
    "requests",
    "urllib",
    "httpx",
    "aiohttp",
    "socket",
    "ftplib",
}

FORBIDDEN_REAL_PATH_FIELDS = {
    "min_range",
    "mdistance",
    "Pc",
    "HBR",
    "covariance",
    "E_d",
    "E_t",
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


def git(
    *args: str,
    check: bool = True,
):

    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def git_value(*args: str) -> str:

    return git(
        *args
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
                reversed(parts)
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


def subscript_key(node):

    if not isinstance(
        node,
        ast.Subscript,
    ):
        return None

    if isinstance(
        node.slice,
        ast.Constant,
    ):
        return node.slice.value

    return None


# ============================================================
# Repository / immutable boundary
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
        "DR-0029 tag target changed"
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
        "association implementation SHA mismatch"
    )

if sha256(
    VALIDATION
) != EXPECTED_VALIDATION_SHA:
    raise ReviewError(
        "offline validation SHA mismatch"
    )

if sha256(
    G4_NUMERICS
) != EXPECTED_G4_SHA:
    raise ReviewError(
        "frozen G4 numerics SHA mismatch"
    )

if (
    ASSOCIATION_OUT.exists()
    or ASSOCIATION_SUMMARY.exists()
):
    raise ReviewError(
        "real association output already exists"
    )


# ============================================================
# AST review
# ============================================================

source = IMPLEMENTATION.read_text(
    encoding="utf-8-sig"
)

tree = ast.parse(
    source,
    filename=str(
        IMPLEMENTATION
    ),
)

functions = function_map(
    tree
)

required_functions = {
    "verify_repository_boundary",
    "verify_frozen_hashes",
    "import_g4",
    "verify_association_signature",
    "read_sample",
    "read_event_status",
    "verify_sample_event_alignment",
    "verify_local_manifest",
    "make_candidate",
    "associate_one_ready_event",
    "run_association",
    "synthetic_association_tests",
    "offline_validate",
    "associate",
    "main",
}

missing = (
    required_functions
    - set(
        functions
    )
)

if missing:
    raise ReviewError(
        "required implementation functions missing: "
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
            module.split(".")[0]
            in FORBIDDEN_NETWORK_ROOTS
        ):
            raise ReviewError(
                "network-capable import found: "
                + module
            )


# CLI association path must remain hard-disabled.
associate_fn = functions[
    "associate"
]

if (
    len(
        associate_fn.body
    )
    != 1
    or not isinstance(
        associate_fn.body[0],
        ast.Raise,
    )
):
    raise ReviewError(
        "associate() is no longer a single hard-disable raise"
    )

raise_node = associate_fn.body[0]

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
    != "G6_REFERENCE_TCA_ASSOCIATION_INACTIVE_UNTIL_INDEPENDENT_REVIEW_AND_AUTHORIZATION"
):
    raise ReviewError(
        "association authorization sentinel changed"
    )


# The real per-event path must call frozen G4 association exactly once.
per_event_fn = functions[
    "associate_one_ready_event"
]

association_calls = [
    node
    for node in ast.walk(
        per_event_fn
    )
    if (
        isinstance(
            node,
            ast.Call,
        )
        and call_name(
            node
        )
        == "g4.associate_reference_event"
    )
]

if len(
    association_calls
) != 1:
    raise ReviewError(
        "real event path must contain exactly one frozen association call"
    )

association_call = (
    association_calls[0]
)

keywords = {
    keyword.arg:
        keyword.value
    for keyword in association_call.keywords
}

if (
    "reference_distance_km"
    not in keywords
):
    raise ReviewError(
        "real association call does not explicitly suppress reference distance"
    )

reference_distance_value = (
    keywords[
        "reference_distance_km"
    ]
)

if not (
    isinstance(
        reference_distance_value,
        ast.Constant,
    )
    and reference_distance_value.value
    is None
):
    raise ReviewError(
        "real association call supplies a physical reference distance"
    )

if (
    "time_equality_tolerance_s"
    in keywords
):
    raise ReviewError(
        "real association call introduced an equality/tie band"
    )


# Real G6 path must never inspect returned residual fields.
real_path_functions = (
    "associate_one_ready_event",
    "run_association",
)

for function_name in real_path_functions:

    node = functions[
        function_name
    ]

    attributes = {
        child.attr
        for child in ast.walk(
            node
        )
        if isinstance(
            child,
            ast.Attribute,
        )
    }

    forbidden_attributes = {
        "tca_error_s",
        "miss_distance_error_km",
    }

    overlap = (
        attributes
        & forbidden_attributes
    )

    if overlap:
        raise ReviewError(
            f"{function_name} reads association residual field(s): "
            + repr(
                sorted(
                    overlap
                )
            )
        )


# Real path must not read protected physical outcome columns.
for function_name in (
    "read_sample",
    "read_event_status",
    "verify_sample_event_alignment",
    "make_candidate",
    "associate_one_ready_event",
    "run_association",
):

    node = functions[
        function_name
    ]

    accessed = {
        subscript_key(
            child
        )
        for child in ast.walk(
            node
        )
        if isinstance(
            child,
            ast.Subscript,
        )
    }

    accessed.discard(
        None
    )

    overlap = (
        accessed
        & FORBIDDEN_REAL_PATH_FIELDS
    )

    if overlap:
        raise ReviewError(
            f"{function_name} accesses protected field(s): "
            + repr(
                sorted(
                    overlap
                )
            )
        )


# run_association must use the per-event association wrapper,
# not reimplement/reference G4 directly.
run_fn = functions[
    "run_association"
]

run_calls = {
    call_name(
        node
    )
    for node in ast.walk(
        run_fn
    )
    if isinstance(
        node,
        ast.Call,
    )
}

if (
    "associate_one_ready_event"
    not in run_calls
):
    raise ReviewError(
        "run_association does not use reviewed per-event wrapper"
    )

if (
    "g4.associate_reference_event"
    in run_calls
):
    raise ReviewError(
        "run_association bypasses reviewed per-event association wrapper"
    )


# ============================================================
# Import implementation without invoking CLI
# ============================================================

spec = (
    importlib.util.spec_from_file_location(
        "g6_association_review_target",
        IMPLEMENTATION,
    )
)

if (
    spec is None
    or spec.loader is None
):
    raise ReviewError(
        "could not import association implementation"
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


if tuple(
    target.OUTPUT_FIELDS
) != EXPECTED_OUTPUT_FIELDS:
    raise ReviewError(
        "association output schema changed"
    )

for forbidden in (
    "reference_tca_utc",
    "replay_time_utc",
    "candidate_time_utc",
    "time_from_replay_s",
    "distance_km",
    "signed_tca_residual_s",
    "absolute_tca_error_s",
    "reference_miss_km",
    "signed_miss_residual_km",
    "absolute_miss_error_km",
):
    if forbidden in target.OUTPUT_FIELDS:
        raise ReviewError(
            "metric/geometry field entered association output: "
            + forbidden
        )


# Direct associate() invocation must remain impossible.
try:
    target.associate()

except target.AssociationImplementationError as exc:

    if str(
        exc
    ) != (
        "G6_REFERENCE_TCA_ASSOCIATION_INACTIVE_UNTIL_"
        "INDEPENDENT_REVIEW_AND_AUTHORIZATION"
    ):
        raise ReviewError(
            "associate() raised wrong authorization sentinel"
        ) from exc

else:
    raise ReviewError(
        "associate() unexpectedly executed"
    )


# ============================================================
# Independent G4 signature review
# ============================================================

g4 = target.import_g4()

signature = inspect.signature(
    g4.associate_reference_event
)

parameters = list(
    signature.parameters
)

if parameters[:3] != [
    "frozen_candidates",
    "reference_time_s",
    "delta_assoc_min",
]:
    raise ReviewError(
        "frozen G4 association positional interface changed"
    )

if (
    "time_equality_tolerance_s"
    in signature.parameters
):
    raise ReviewError(
        "frozen G4 association unexpectedly contains equality band"
    )

if (
    "reference_distance_km"
    not in signature.parameters
):
    raise ReviewError(
        "frozen G4 optional reference-distance interface missing"
    )


# ============================================================
# Independent synthetic per-event behavioral tests
# ============================================================

def candidate_row(
    candidate_index,
    time_s,
    distance_km,
    bracket_index,
):

    return {
        "candidate_index":
            str(
                candidate_index
            ),

        "time_from_replay_s":
            str(
                time_s
            ),

        "distance_km":
            str(
                distance_km
            ),

        "bracket_lower_s":
            str(
                max(
                    0.0,
                    float(time_s) - 1.0,
                )
            ),

        "bracket_upper_s":
            str(
                float(time_s) + 1.0
            ),

        "bracket_index":
            str(
                bracket_index
            ),

        "converged":
            "True",

        "iterations":
            "1",

        "function_evaluations":
            "1",

        "termination":
            "INDEPENDENT_SYNTHETIC",
    }


event_two = {
    "preoutcome_status":
        "CANDIDATE_SET_READY",

    "candidate_count":
        "2",

    "replay_time_utc":
        "2025-01-01T00:00:00Z",
}


# Equal absolute offset: earlier candidate must win.
sample_equal_offset = {
    "reference_tca_utc":
        "2025-01-01T00:02:30Z",
}

equal_offset_rows = [
    candidate_row(
        1,
        100.0,
        9.0,
        10,
    ),
    candidate_row(
        2,
        200.0,
        1.0,
        20,
    ),
]

result_equal_offset = (
    target.associate_one_ready_event(
        g4,
        sample_equal_offset,
        event_two,
        equal_offset_rows,
    )
)

if (
    result_equal_offset[
        "association_status"
    ]
    != "VALID_ASSOCIATED"
    or result_equal_offset[
        "selected_candidate_index"
    ]
    != "1"
):
    raise ReviewError(
        "independent equal-offset earlier-time test failed"
    )


# Equal time/distance: lower stable bracket index must win.
sample_exact = {
    "reference_tca_utc":
        "2025-01-01T00:01:40Z",
}

stable_index_rows = [
    candidate_row(
        1,
        100.0,
        2.0,
        20,
    ),
    candidate_row(
        2,
        100.0,
        2.0,
        10,
    ),
]

result_stable_index = (
    target.associate_one_ready_event(
        g4,
        sample_exact,
        event_two,
        stable_index_rows,
    )
)

if (
    result_stable_index[
        "association_status"
    ]
    != "VALID_ASSOCIATED"
    or result_stable_index[
        "selected_candidate_index"
    ]
    != "2"
):
    raise ReviewError(
        "independent stable-index tie-break test failed"
    )


event_one = {
    "preoutcome_status":
        "CANDIDATE_SET_READY",

    "candidate_count":
        "1",

    "replay_time_utc":
        "2025-01-01T00:00:00Z",
}


# Exactly 300 s must associate.
sample_zero = {
    "reference_tca_utc":
        "2025-01-01T00:00:00Z",
}

boundary_result = (
    target.associate_one_ready_event(
        g4,
        sample_zero,
        event_one,
        [
            candidate_row(
                1,
                300.0,
                1.0,
                1,
            )
        ],
    )
)

if (
    boundary_result[
        "association_status"
    ]
    != "VALID_ASSOCIATED"
):
    raise ReviewError(
        "independent inclusive 5-minute boundary test failed"
    )


# Just outside 300 s must not associate.
outside_result = (
    target.associate_one_ready_event(
        g4,
        sample_zero,
        event_one,
        [
            candidate_row(
                1,
                300.000001,
                1.0,
                1,
            )
        ],
    )
)

if (
    outside_result[
        "association_status"
    ]
    != "UNASSOCIATED_REFERENCE_EVENT"
    or outside_result[
        "selected_candidate_index"
    ]
    != ""
):
    raise ReviewError(
        "independent outside-boundary test failed"
    )


# ============================================================
# Validate offline-validation artifact
# ============================================================

validation = json.loads(
    VALIDATION.read_text(
        encoding="utf-8"
    )
)

expected_scalars = {
    "status":
        "G6_REFERENCE_TCA_ASSOCIATION_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",

    "implementation_sha256":
        EXPECTED_IMPLEMENTATION_SHA,

    "events_verified":
        6927,

    "candidate_set_ready_events":
        6906,

    "preoutcome_terminal_events":
        21,

    "candidate_rows_frozen":
        929821,

    "candidate_csv_byte_count":
        128418422,

    "reference_TCA_allowed_metadata_verified":
        True,

    "reference_distance_km_used":
        False,

    "real_G6_association_executed":
        False,

    "orbital_propagation_performed":
        False,

    "candidate_generation_performed":
        False,

    "TraCSS_physical_outcome_rows_opened":
        0,

    "selected_candidate_distance_exported":
        False,

    "selected_candidate_time_exported":
        False,

    "signed_TCA_residual_exported":
        False,

    "absolute_TCA_error_E_t_computed":
        False,

    "miss_distance_error_E_d_computed":
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

    "association_authorized":
        False,
}

for key, expected in (
    expected_scalars.items()
):

    if validation.get(
        key
    ) != expected:
        raise ReviewError(
            "offline-validation invariant mismatch: "
            f"{key}={validation.get(key)!r}"
        )


association_signature = validation.get(
    "association_signature",
    {}
)

if (
    association_signature.get(
        "association_tolerance_min"
    )
    != 5.0
):
    raise ReviewError(
        "association tolerance changed"
    )

if (
    association_signature.get(
        "no_equality_band"
    )
    is not True
):
    raise ReviewError(
        "association equality-band invariant changed"
    )

expected_ordering = (
    "MIN_ABSOLUTE_REFERENCE_TCA_OFFSET__"
    "EARLIER_CANDIDATE_TIME__"
    "SMALLER_CANDIDATE_DISTANCE__"
    "LOWER_STABLE_CANDIDATE_INDEX"
)

if (
    association_signature.get(
        "ordering"
    )
    != expected_ordering
):
    raise ReviewError(
        "association ordering changed"
    )


synthetic = validation.get(
    "synthetic_association_tests",
    {}
)

for key in (
    "empty_candidate_set_unassociated",
    "equal_absolute_offset_selects_earlier_time",
    "equal_time_distance_selects_lower_stable_index",
    "equal_time_selects_smaller_distance",
    "five_minute_boundary_inclusive",
    "outside_five_minute_boundary_unassociated",
):

    if synthetic.get(
        key
    ) is not True:
        raise ReviewError(
            "offline synthetic association test not PASS: "
            + key
        )

if synthetic.get(
    "reference_distance_supplied"
) is not False:
    raise ReviewError(
        "offline synthetic test supplied reference distance"
    )

if synthetic.get(
    "miss_distance_error_returned"
) is not False:
    raise ReviewError(
        "offline synthetic test returned miss-distance error"
    )


# ============================================================
# Result
# ============================================================

result = {
    "status":
        "G6_REFERENCE_TCA_ASSOCIATION_IMPLEMENTATION_INDEPENDENT_REVIEW_PASS",

    "review_scope":
        "STATIC_AND_SYNTHETIC_NO_REAL_G6_ASSOCIATION",

    "implementation_sha256":
        EXPECTED_IMPLEMENTATION_SHA,

    "offline_validation_sha256":
        EXPECTED_VALIDATION_SHA,

    "frozen_G4_numerics_sha256":
        EXPECTED_G4_SHA,

    "network_capable_imports_absent":
        True,

    "direct_association_hard_disabled":
        True,

    "real_path_uses_frozen_G4_association":
        True,

    "real_path_reference_distance_is_none":
        True,

    "real_path_equality_band_absent":
        True,

    "real_path_TCA_residual_field_access_absent":
        True,

    "real_path_miss_distance_residual_field_access_absent":
        True,

    "real_path_protected_physical_field_access_absent":
        True,

    "safe_association_output_schema_verified":
        True,

    "independent_equal_offset_earlier_time_test":
        True,

    "independent_stable_index_tiebreak_test":
        True,

    "independent_five_minute_boundary_test":
        True,

    "independent_outside_boundary_test":
        True,

    "events_verified_by_offline_validation":
        6927,

    "candidate_set_ready_events":
        6906,

    "preoutcome_terminal_events":
        21,

    "candidate_rows_frozen":
        929821,

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

    "selected_candidate_geometry_exported":
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
        "FREEZE_INACTIVE_REFERENCE_TCA_ASSOCIATION_IMPLEMENTATION_BEFORE_EXECUTION_AUTHORIZATION",
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
