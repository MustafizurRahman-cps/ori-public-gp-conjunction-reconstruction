#!/usr/bin/env python3
"""Independent review of inactive G6 pre-outcome candidate implementation.

No G6 orbit is propagated.  No G6 candidate is generated.  No TraCSS
reference quantity is opened.  Behavioral branch tests use synthetic stubs.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np


ROOT = Path(__file__).resolve().parents[2]

IMPLEMENTATION = (
    ROOT / "scripts/g6/generate_g6_preoutcome_candidates.py"
)

VALIDATION = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Implementation_Offline_Validation.json"
)

OUT = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Implementation_Independent_Review.json"
)

EXPECTED_HEAD = (
    "d12b51a8ebdcc16b6c9d728d63e4ae08f3c7cecd"
)

EXPECTED_TAG = (
    "g6-dr0025-corrective-verification-recorded"
)

EXPECTED_IMPLEMENTATION_SHA = (
    "bce2a4b5e4dab8d0c4e461e2acb4dbac8afe22d3ac412922ba3670a832adcecb"
)

EXPECTED_VALIDATION_SHA = (
    "f605f66c8525413ff0e62ad4a0456e9485051513805d9eb58b7263b64a4c6ba2"
)

EXPECTED_ALLOWED_STATUSES = {
    "GP_PARSE_FAILURE",
    "PROPAGATION_FAILURE",
    "UNRECOVERED_REFINEMENT_FAILURE",
    "NO_VALID_CANDIDATE",
    "CANDIDATE_SET_READY",
}

FORBIDDEN_NETWORK_ROOTS = {
    "requests",
    "urllib",
    "httpx",
    "aiohttp",
    "socket",
    "ftplib",
}

FORBIDDEN_PROTECTED_SOURCE_TOKENS = (
    "IVV_Releasable_Dataset",
    "data/raw/g2",
    "min_range",
    "mdistance",
    "associate_reference_event",
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
    return git(*args).stdout.strip()


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


def function_map(tree):
    return {
        node.name: node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    }


def literal_subscript_key(node):

    if not isinstance(
        node,
        ast.Subscript,
    ):
        return None

    key = node.slice

    if isinstance(
        key,
        ast.Constant,
    ):
        return key.value

    return None


# ------------------------------------------------------------
# Repository and hashes
# ------------------------------------------------------------

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
        "DR-0026 tag target changed"
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


# ------------------------------------------------------------
# AST/static review
# ------------------------------------------------------------

source = IMPLEMENTATION.read_text(
    encoding="utf-8-sig"
)

tree = ast.parse(
    source,
    filename=str(IMPLEMENTATION),
)

functions = function_map(
    tree
)

required_functions = {
    "verify_repository_boundary",
    "verify_frozen_hashes",
    "verify_environment",
    "import_frozen_controlflow",
    "verify_compact_inputs",
    "make_gp_record",
    "build_events",
    "process_event",
    "open_work_db",
    "run_generation_to_work_db",
    "verify_numerical_constants",
    "synthetic_controlflow_test",
    "offline_validate",
    "generate",
    "main",
}

missing = (
    required_functions
    - set(functions)
)

if missing:
    raise ReviewError(
        "required functions missing: "
        + repr(
            sorted(missing)
        )
    )


# No network-capable imports.
for node in ast.walk(tree):

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


# No protected G6 outcome source or association operation.
for token in FORBIDDEN_PROTECTED_SOURCE_TOKENS:

    if token in source:
        raise ReviewError(
            "protected/reference token found in implementation: "
            + token
        )


# Direct --generate path must remain a single hard stop.
generate_fn = functions[
    "generate"
]

if (
    len(generate_fn.body) != 1
    or not isinstance(
        generate_fn.body[0],
        ast.Raise,
    )
):
    raise ReviewError(
        "generate() is no longer a single hard-disable raise"
    )

generate_raise = (
    generate_fn.body[0]
)

if not isinstance(
    generate_raise.exc,
    ast.Call,
):
    raise ReviewError(
        "generate() raise shape changed"
    )

if (
    not generate_raise.exc.args
    or not isinstance(
        generate_raise.exc.args[0],
        ast.Constant,
    )
    or generate_raise.exc.args[0].value
    != "G6_CANDIDATE_GENERATION_INACTIVE_UNTIL_INDEPENDENT_REVIEW_AND_AUTHORIZATION"
):
    raise ReviewError(
        "generate() authorization sentinel changed"
    )


# process_event must call the already-frozen primitives.
process_fn = functions[
    "process_event"
]

process_calls = {
    call_name(node)
    for node in ast.walk(
        process_fn
    )
    if isinstance(
        node,
        ast.Call,
    )
}

for required_call in (
    "make_gp_record",
    "s.satrec_from_record",
    "s.g4.Sgp4PairQuantity",
    "s.enumerate_candidates_redundancy_safe",
):
    if required_call not in process_calls:
        raise ReviewError(
            "process_event missing frozen primitive call: "
            + required_call
        )

if any(
    name is not None
    and name.endswith(
        "associate_reference_event"
    )
    for name in process_calls
):
    raise ReviewError(
        "reference association is present in pre-outcome process_event"
    )


# process_event must not read a reference TCA.
for node in ast.walk(
    process_fn
):

    key = literal_subscript_key(
        node
    )

    if key == "reference_tca_utc":
        raise ReviewError(
            "process_event reads reference_tca_utc"
        )


# Extract only explicit preoutcome terminal assignments.
assigned_statuses = set()

for node in ast.walk(
    process_fn
):

    if not isinstance(
        node,
        ast.Assign,
    ):
        continue

    if len(
        node.targets
    ) != 1:
        continue

    target = node.targets[
        0
    ]

    if not isinstance(
        target,
        ast.Subscript,
    ):
        continue

    if not (
        isinstance(
            target.value,
            ast.Name,
        )
        and target.value.id
        == "event_row"
    ):
        continue

    if (
        literal_subscript_key(
            target
        )
        != "preoutcome_status"
    ):
        continue

    if isinstance(
        node.value,
        ast.Constant,
    ):

        value = node.value.value

        if value:
            assigned_statuses.add(
                value
            )


if assigned_statuses != EXPECTED_ALLOWED_STATUSES:
    raise ReviewError(
        "pre-outcome status assignments differ: "
        + repr(
            sorted(
                assigned_statuses
            )
        )
    )


# Later association terminal statuses must not be emitted.
if (
    "UNASSOCIATED_REFERENCE_EVENT"
    in assigned_statuses
    or "VALID_ASSOCIATED"
    in assigned_statuses
):
    raise ReviewError(
        "final association status emitted before association stage"
    )


# Work DB must be local-only under data/raw/g6.
if (
    'data/raw/g6/G6_PreOutcome_Candidate_Work.sqlite3'
    not in source.replace(
        "\\",
        "/",
    )
):
    # Path is constructed compositionally in the implementation; verify
    # dynamically below instead of accepting source absence as failure.
    pass


# ------------------------------------------------------------
# Import target without invoking CLI
# ------------------------------------------------------------

spec = importlib.util.spec_from_file_location(
    "g6_candidate_review_target",
    IMPLEMENTATION,
)

if (
    spec is None
    or spec.loader is None
):
    raise ReviewError(
        "could not create implementation module spec"
    )

target = importlib.util.module_from_spec(
    spec
)

sys.modules[
    spec.name
] = target

spec.loader.exec_module(
    target
)


# Explicit path/governance checks.
if (
    target.WORK_DB.relative_to(
        ROOT
    ).as_posix()
    != "data/raw/g6/G6_PreOutcome_Candidate_Work.sqlite3"
):
    raise ReviewError(
        "work DB path changed"
    )

ignore = git(
    "check-ignore",
    "-v",
    target.WORK_DB.relative_to(
        ROOT
    ).as_posix(),
    check=False,
)

if ignore.returncode != 0:
    raise ReviewError(
        "candidate work DB is not Git-ignored"
    )

if git_value(
    "ls-files",
    "--",
    target.WORK_DB.relative_to(
        ROOT
    ).as_posix(),
):
    raise ReviewError(
        "candidate work DB is unexpectedly tracked"
    )


if target.ALLOWED_PREOUTCOME_STATUSES != EXPECTED_ALLOWED_STATUSES:
    raise ReviewError(
        "runtime allowed pre-outcome status set changed"
    )


# Calling generate() must only raise the authorization sentinel.
try:
    target.generate()

except target.CandidateImplementationError as exc:

    if str(
        exc
    ) != (
        "G6_CANDIDATE_GENERATION_INACTIVE_UNTIL_"
        "INDEPENDENT_REVIEW_AND_AUTHORIZATION"
    ):
        raise ReviewError(
            "generate() raised unexpected sentinel"
        ) from exc

else:
    raise ReviewError(
        "generate() unexpectedly returned"
    )


# ------------------------------------------------------------
# Independent frozen enumerator synthetic test
# ------------------------------------------------------------

g5_dir = (
    ROOT
    / "scripts/g5"
)

if str(
    g5_dir
) not in sys.path:
    sys.path.insert(
        0,
        str(
            g5_dir
        ),
    )

import select_lstar_controlflow_amended as frozen


class AnalyticQuantity:

    def __init__(
        self,
        t0: float,
        distance: float,
    ):
        self.t0 = float(
            t0
        )

        self.distance = float(
            distance
        )

    def evaluate(
        self,
        time_s: float,
    ):
        x = float(
            time_s
        ) - self.t0

        return (
            x * x
            + self.distance
            * self.distance,

            2.0 * x,
        )

    def evaluate_many(
        self,
        times_s,
    ):
        times = np.asarray(
            times_s,
            dtype=float,
        )

        x = (
            times
            - self.t0
        )

        return (
            x * x
            + self.distance
            * self.distance,

            2.0 * x,
        )


independent_target_time = 234567.891
independent_target_distance = 7.25

enumeration = (
    frozen.enumerate_candidates_redundancy_safe(
        AnalyticQuantity(
            independent_target_time,
            independent_target_distance,
        )
    )
)

if len(
    enumeration.candidates
) != 1:
    raise ReviewError(
        "independent off-grid enumerator test "
        "did not produce exactly one candidate"
    )

candidate = enumeration.candidates[
    0
]

independent_time_error = abs(
    float(
        candidate.time_s
    )
    - independent_target_time
)

independent_distance_error = abs(
    float(
        candidate.distance_km
    )
    - independent_target_distance
)

if (
    independent_time_error
    > frozen.REFINEMENT_TOLERANCE_S
):
    raise ReviewError(
        "independent enumerator TCA error exceeds tolerance"
    )

if (
    independent_distance_error
    > frozen.DEDUP_DISTANCE_TOLERANCE_KM
):
    raise ReviewError(
        "independent enumerator distance error exceeds tolerance"
    )


# ------------------------------------------------------------
# Synthetic process_event branch testing
# ------------------------------------------------------------

class FakeParseError(Exception):
    pass


class FakeVerificationError(Exception):
    pass


class FakeOptimizationError(
    FakeVerificationError
):
    pass


class FakeUnrecovered(
    FakeVerificationError
):
    pass


class FakeGPRecord:

    def __init__(
        self,
        object_id,
        epoch_utc,
        creation_date_utc,
        stable_tie_key,
        payload=None,
    ):
        self.object_id = object_id
        self.epoch_utc = epoch_utc
        self.creation_date_utc = creation_date_utc
        self.stable_tie_key = stable_tie_key
        self.payload = payload


def fake_enumeration(
    candidates,
):
    return SimpleNamespace(
        raw_candidates=tuple(
            candidates
        ),
        candidates=tuple(
            candidates
        ),
        unresolved_boundary_count=0,
        sampled_count=43201,
        rejected_local_inconsistency_count=0,
        recovered_local_inconsistency_count=0,
    )


fake_candidate = SimpleNamespace(
    time_s=100.25,
    distance_km=2.5,
    bracket_s=(
        100.0,
        110.0,
    ),
    bracket_index=10,
    converged=True,
    iterations=5,
    function_evaluations=8,
    termination="SYNTHETIC",
)


def fake_s(
    mode: str,
):

    g4 = SimpleNamespace(
        ParseError=
            FakeParseError,

        VerificationError=
            FakeVerificationError,

        OptimizationError=
            FakeOptimizationError,

        GPRecord=
            FakeGPRecord,

        Sgp4PairQuantity=
            lambda *_args, **_kwargs:
                object(),
    )

    def satrec_from_record(
        _record,
    ):
        if mode == "parse":
            raise FakeParseError(
                "synthetic parse failure"
            )

        return object()

    def enumerate_candidates(
        _quantity,
    ):
        if mode == "propagation":
            raise FakeVerificationError(
                "synthetic propagation failure"
            )

        if mode == "unrecovered":
            raise FakeUnrecovered(
                "synthetic unrecovered refinement"
            )

        if mode == "optimization":
            raise FakeOptimizationError(
                "synthetic optimization failure"
            )

        if mode == "empty":
            return fake_enumeration(
                []
            )

        if mode == "ready":
            return fake_enumeration(
                [
                    fake_candidate
                ]
            )

        raise RuntimeError(
            "unknown synthetic mode"
        )

    return SimpleNamespace(
        g4=g4,
        UnrecoveredRefinementFailure=
            FakeUnrecovered,
        satrec_from_record=
            satrec_from_record,
        enumerate_candidates_redundancy_safe=
            enumerate_candidates,
    )


synthetic_event = {
    "final_g6_sample_index":
        "1",

    "g6_temporal_stratum":
        "G6-T1",

    "conj_id":
        "SYNTHETIC-CONJ",

    "obj1":
        "10001",

    "obj2":
        "10002",

    "replay_time":
        target.parse_utc(
            "2025-01-01T00:00:00Z"
        ),
}

synthetic_cache = {
    "10001": {
        "NORAD_CAT_ID":
            "10001",
        "GP_ID":
            "1",
        "stable_tie_key":
            "1",
        "EPOCH":
            "2024-12-31T12:00:00Z",
        "CREATION_DATE":
            "2024-12-31T23:00:00Z",
        "TLE_LINE1":
            "1 SYNTHETIC",
        "TLE_LINE2":
            "2 SYNTHETIC",
    },

    "10002": {
        "NORAD_CAT_ID":
            "10002",
        "GP_ID":
            "2",
        "stable_tie_key":
            "2",
        "EPOCH":
            "2024-12-31T12:30:00Z",
        "CREATION_DATE":
            "2024-12-31T23:30:00Z",
        "TLE_LINE1":
            "1 SYNTHETIC",
        "TLE_LINE2":
            "2 SYNTHETIC",
    },
}


expected_modes = {
    "parse":
        "GP_PARSE_FAILURE",

    "propagation":
        "PROPAGATION_FAILURE",

    "unrecovered":
        "UNRECOVERED_REFINEMENT_FAILURE",

    "optimization":
        "UNRECOVERED_REFINEMENT_FAILURE",

    "empty":
        "NO_VALID_CANDIDATE",

    "ready":
        "CANDIDATE_SET_READY",
}


branch_results = {}

for mode, expected_status in (
    expected_modes.items()
):

    (
        event_result,
        candidate_rows,
    ) = target.process_event(
        fake_s(
            mode
        ),
        synthetic_event,
        synthetic_cache,
    )

    actual_status = (
        event_result[
            "preoutcome_status"
        ]
    )

    if actual_status != expected_status:
        raise ReviewError(
            f"synthetic branch {mode}: "
            f"{actual_status} != {expected_status}"
        )

    if (
        mode == "ready"
        and len(
            candidate_rows
        ) != 1
    ):
        raise ReviewError(
            "CANDIDATE_SET_READY did not emit one synthetic candidate"
        )

    if (
        mode != "ready"
        and candidate_rows
    ):
        raise ReviewError(
            f"failure branch {mode} emitted candidate rows"
        )

    branch_results[
        mode
    ] = actual_status


# ------------------------------------------------------------
# Validate offline-validation record
# ------------------------------------------------------------

validation = json.loads(
    VALIDATION.read_text(
        encoding="utf-8"
    )
)

expected_validation_scalars = {
    "status":
        "G6_PREOUTCOME_CANDIDATE_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",

    "implementation_sha256":
        EXPECTED_IMPLEMENTATION_SHA,

    "events_verified":
        6927,

    "objects_verified":
        13854,

    "candidate_horizon_days":
        5.0,

    "candidate_horizon_seconds":
        432000.0,

    "gravity_constants":
        "WGS-72",

    "coordinate_frame":
        "TEME",

    "time_scale":
        "UTC",

    "generation_authorized":
        False,

    "G6_orbital_propagation_performed":
        False,

    "G6_candidate_generation_executed":
        False,

    "reference_association_performed":
        False,

    "final_association_statuses_emitted":
        False,

    "TraCSS_reference_rows_opened":
        0,

    "protected_outcome_access":
        False,

    "G6_error_metrics_computed":
        False,

    "network_requests":
        0,

    "full_GP_HISTORY_reread":
        False,

    "candidate_work_db_gitignored":
        True,

    "candidate_work_db_tracked":
        False,
}

for key, expected in (
    expected_validation_scalars.items()
):

    if validation.get(
        key
    ) != expected:
        raise ReviewError(
            "offline-validation invariant mismatch: "
            f"{key}={validation.get(key)!r}"
        )


nominal = (
    validation[
        "synthetic_controlflow_test"
    ][
        "nominal_offgrid_case"
    ]
)

if (
    nominal[
        "candidate_count"
    ]
    != 1
):
    raise ReviewError(
        "offline nominal synthetic case is not single-candidate"
    )

if (
    nominal[
        "absolute_time_error_s"
    ]
    > 0.01
):
    raise ReviewError(
        "offline nominal TCA error exceeds tolerance"
    )

if (
    nominal[
        "absolute_distance_error_km"
    ]
    > 1.0e-6
):
    raise ReviewError(
        "offline nominal distance error exceeds tolerance"
    )


grid_case = (
    validation[
        "synthetic_controlflow_test"
    ][
        "grid_aligned_edge_case"
    ]
)

if (
    grid_case[
        "candidate_count"
    ]
    < 1
):
    raise ReviewError(
        "grid-aligned edge case lost all candidates"
    )


# ------------------------------------------------------------
# Result
# ------------------------------------------------------------

result = {
    "status":
        "G6_PREOUTCOME_CANDIDATE_IMPLEMENTATION_INDEPENDENT_REVIEW_PASS",

    "implementation_sha256":
        EXPECTED_IMPLEMENTATION_SHA,

    "offline_validation_sha256":
        EXPECTED_VALIDATION_SHA,

    "review_scope":
        "AST_STATIC_PLUS_SYNTHETIC_BRANCH_TESTS_NO_G6_PROPAGATION",

    "network_capable_imports_absent":
        True,

    "protected_reference_source_paths_absent":
        True,

    "reference_association_call_absent":
        True,

    "reference_tca_access_in_process_event_absent":
        True,

    "direct_generation_hard_disabled":
        True,

    "allowed_preoutcome_status_set_verified":
        True,

    "process_event_frozen_primitive_calls_verified":
        True,

    "synthetic_branch_statuses":
        branch_results,

    "independent_analytic_candidate_count":
        len(
            enumeration.candidates
        ),

    "independent_analytic_time_error_s":
        independent_time_error,

    "independent_analytic_distance_error_km":
        independent_distance_error,

    "candidate_work_db_gitignored":
        True,

    "candidate_work_db_untracked":
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
        "FREEZE_INACTIVE_PREOUTCOME_CANDIDATE_IMPLEMENTATION_BEFORE_EXECUTION_AUTHORIZATION",
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
