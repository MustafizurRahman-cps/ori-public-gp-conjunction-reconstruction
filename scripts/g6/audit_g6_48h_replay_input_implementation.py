#!/usr/bin/env python3
"""Semantic/static + pure-synthetic review of frozen G6 48 h input code."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_HEAD = (
    "8b108a2585f37d35c33b4f38ee3e18ed81c8256b"
)

FREEZE_TAG = (
    "g6-causal-history-manifest-frozen"
)

IMPLEMENTATION = (
    ROOT / "scripts/g6/prepare_g6_48h_replay_inputs.py"
)

VALIDATION = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Implementation_Offline_Validation.json"
)

MAIN_STATE = (
    ROOT / "data/raw/g6/G6_SpaceTrack_Acquisition_State.json"
)

PRED_STATE = (
    ROOT
    / "data/raw/g6/G6_SpaceTrack_Predecessor_Acquisition_State.json"
)

OUT = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Implementation_Independent_Review.json"
)

EXPECTED_IMPLEMENTATION_SHA = (
    "87ed8e4139d6d8de9324d621acfc2d283ffccb716fe57914814f5890a90bfc3f"
)

EXPECTED_VALIDATION_SHA = (
    "7e09cb9f5e95efc804af1ea170a1b6efb647b79ab485a99ed991cf4f736dafb6"
)

FORBIDDEN_IMPORT_ROOTS = {
    "requests",
    "urllib",
    "httpx",
    "aiohttp",
    "socket",
    "ftplib",
}

REQUIRED_FUNCTIONS = {
    "sha256",
    "parse_utc",
    "iso_utc",
    "canonical_row_sha",
    "normalize_id",
    "record_key",
    "choose_later",
    "selected_identity",
    "make_record",
    "verify_repository_boundary",
    "verify_small_inputs",
    "verify_environment",
    "validate_frozen_metadata",
    "offline_validate",
    "load_execution_inputs",
    "load_main_state_by_path",
    "stream_main_first_pass",
    "add_queried_predecessors",
    "derive_cutoff_and_replay_times",
    "stream_main_reselection_pass",
    "finalize_reselection",
    "write_prepared_outputs",
    "prepare_inputs",
    "main",
}


class ReviewError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def git(*args: str, check: bool = True):
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def git_value(*args: str) -> str:
    return git(*args).stdout.strip()


def function_map(tree: ast.Module):
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


def is_name(node, name: str) -> bool:
    return (
        isinstance(node, ast.Name)
        and node.id == name
    )


def call_name(node) -> str | None:
    if not isinstance(node, ast.Call):
        return None

    if isinstance(node.func, ast.Name):
        return node.func.id

    if isinstance(node.func, ast.Attribute):
        parts = []
        current = node.func

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


def function_calls(fn: ast.AST) -> set[str]:
    return {
        name
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        for name in [call_name(node)]
        if name is not None
    }


def has_simple_compare(
    fn: ast.AST,
    operands: tuple[str, ...],
    operators: tuple[type, ...],
) -> bool:

    for node in ast.walk(fn):

        if not isinstance(
            node,
            ast.Compare,
        ):
            continue

        nodes = (
            node.left,
            *node.comparators,
        )

        if len(nodes) != len(
            operands
        ):
            continue

        if not all(
            is_name(item, expected)
            for item, expected
            in zip(
                nodes,
                operands,
            )
        ):
            continue

        if tuple(
            type(op)
            for op in node.ops
        ) == operators:
            return True

    return False


def compare_mentions_name_with_not_equal(
    fn: ast.AST,
    name: str,
) -> bool:

    for node in ast.walk(fn):

        if not isinstance(
            node,
            ast.Compare,
        ):
            continue

        if not any(
            isinstance(op, ast.NotEq)
            for op in node.ops
        ):
            continue

        involved = [
            n.id
            for n in ast.walk(node)
            if isinstance(n, ast.Name)
        ]

        if name in involved:
            return True

    return False


# ------------------------------------------------------------
# Repository boundary
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
        f"refs/tags/{FREEZE_TAG}^{{commit}}",
    )
    != EXPECTED_HEAD
):
    raise ReviewError(
        "causal-history freeze tag changed"
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
        "validation SHA mismatch"
    )


# ------------------------------------------------------------
# Parse implementation
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

missing = (
    REQUIRED_FUNCTIONS
    - set(functions)
)

if missing:
    raise ReviewError(
        "required function set incomplete: "
        + repr(
            sorted(missing)
        )
    )


# ------------------------------------------------------------
# No network-capable imports
# ------------------------------------------------------------

for node in ast.walk(tree):

    if isinstance(
        node,
        ast.Import,
    ):
        names = [
            alias.name
            for alias in node.names
        ]

    elif isinstance(
        node,
        ast.ImportFrom,
    ):
        names = (
            [node.module]
            if node.module
            else []
        )

    else:
        continue

    for name in names:
        root = name.split(".")[0]

        if root in FORBIDDEN_IMPORT_ROOTS:
            raise ReviewError(
                "network-capable import found: "
                + name
            )


# ------------------------------------------------------------
# prepare_inputs must still be hard-disabled
# ------------------------------------------------------------

prepare_fn = functions[
    "prepare_inputs"
]

if len(
    prepare_fn.body
) != 1 or not isinstance(
    prepare_fn.body[0],
    ast.Raise,
):
    raise ReviewError(
        "prepare_inputs is no longer a single hard-disable raise"
    )

raise_node = (
    prepare_fn.body[0]
)

if not isinstance(
    raise_node.exc,
    ast.Call,
):
    raise ReviewError(
        "prepare_inputs guard shape changed"
    )

if call_name(
    raise_node.exc
) != "ReplayInputError":
    raise ReviewError(
        "prepare_inputs no longer raises ReplayInputError"
    )

if (
    not raise_node.exc.args
    or not isinstance(
        raise_node.exc.args[0],
        ast.Constant,
    )
    or raise_node.exc.args[0].value
    != "PREPARE_INPUTS_INACTIVE_UNTIL_IMPLEMENTATION_IS_REVIEWED_AND_AUTHORIZED"
):
    raise ReviewError(
        "prepare_inputs authorization sentinel changed"
    )


# ------------------------------------------------------------
# Semantic review of first streaming pass
# ------------------------------------------------------------

first_pass = functions[
    "stream_main_first_pass"
]

# Exact local causal history:
# lower <= creation < upper
if not has_simple_compare(
    first_pass,
    (
        "lower",
        "creation",
        "upper",
    ),
    (
        ast.LtE,
        ast.Lt,
    ),
):
    raise ReviewError(
        "local causal-window semantics missing"
    )

# Causal 48 h cutoff:
# creation <= cutoff
if not has_simple_compare(
    first_pass,
    (
        "creation",
        "cutoff",
    ),
    (
        ast.LtE,
    ),
):
    raise ReviewError(
        "48 h cutoff admissibility comparison missing"
    )

first_calls = function_calls(
    first_pass
)

for required_call in (
    "hashlib.sha256",
    "make_record",
    "choose_later",
):
    if required_call not in first_calls:
        raise ReviewError(
            "first pass missing call: "
            + required_call
        )

# Semantic raw-integrity checks.
if not has_simple_compare(
    first_pass,
    (
        "actual_sha",
        "expected_sha",
    ),
    (
        ast.NotEq,
    ),
):
    raise ReviewError(
        "main raw SHA comparison missing"
    )

if not compare_mentions_name_with_not_equal(
    first_pass,
    "row_count",
):
    raise ReviewError(
        "main raw row-count comparison missing"
    )

if not compare_mentions_name_with_not_equal(
    first_pass,
    "byte_count",
):
    raise ReviewError(
        "main raw byte-count comparison missing"
    )


# ------------------------------------------------------------
# Semantic replay-time derivation
# ------------------------------------------------------------

derive_fn = functions[
    "derive_cutoff_and_replay_times"
]

replay_max_assignment = False

for node in ast.walk(
    derive_fn
):

    if not isinstance(
        node,
        ast.Assign,
    ):
        continue

    if len(
        node.targets
    ) != 1 or not is_name(
        node.targets[0],
        "replay",
    ):
        continue

    if not isinstance(
        node.value,
        ast.Call,
    ):
        continue

    if call_name(
        node.value
    ) != "max":
        continue

    if len(
        node.value.args
    ) != 2:
        continue

    attrs = []

    for arg in node.value.args:
        if (
            isinstance(
                arg,
                ast.Attribute,
            )
            and arg.attr
            == "creation_utc"
            and isinstance(
                arg.value,
                ast.Name,
            )
        ):
            attrs.append(
                arg.value.id
            )

    if set(attrs) == {
        "first",
        "second",
    }:
        replay_max_assignment = True
        break

if not replay_max_assignment:
    raise ReviewError(
        "publication-driven replay=max(CREATION_DATE_1, CREATION_DATE_2) missing"
    )

if not has_simple_compare(
    derive_fn,
    (
        "replay",
        "cutoff",
    ),
    (
        ast.Gt,
    ),
):
    raise ReviewError(
        "replay-time upper-bound guard missing"
    )


# ------------------------------------------------------------
# Semantic second-pass / identity review
# ------------------------------------------------------------

second_pass = functions[
    "stream_main_reselection_pass"
]

second_calls = function_calls(
    second_pass
)

for required_call in (
    "make_record",
    "choose_later",
):
    if required_call not in second_calls:
        raise ReviewError(
            "reselection pass missing call: "
            + required_call
        )


write_fn = functions[
    "write_prepared_outputs"
]

identity_guard_found = False

for node in ast.walk(
    write_fn
):

    if not isinstance(
        node,
        ast.Compare,
    ):
        continue

    if (
        len(node.ops) != 1
        or not isinstance(
            node.ops[0],
            ast.NotEq,
        )
        or len(
            node.comparators
        ) != 1
    ):
        continue

    left = node.left
    right = node.comparators[
        0
    ]

    if (
        isinstance(
            left,
            ast.Call,
        )
        and isinstance(
            right,
            ast.Call,
        )
        and call_name(
            left
        )
        == "selected_identity"
        and call_name(
            right
        )
        == "selected_identity"
    ):
        identity_guard_found = True
        break

if not identity_guard_found:
    raise ReviewError(
        "reselection identity guard missing"
    )


# ------------------------------------------------------------
# Local cache governance
# ------------------------------------------------------------

if "LOCAL_ONLY_UNDER_DATA_RAW_G6" not in source:
    raise ReviewError(
        "local-cache governance marker missing"
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
            "protected-source token found: "
            + forbidden
        )


# ------------------------------------------------------------
# Import module without invoking CLI/main
# ------------------------------------------------------------

spec = importlib.util.spec_from_file_location(
    "g6_replay_input_review_target",
    IMPLEMENTATION,
)

if (
    spec is None
    or spec.loader is None
):
    raise ReviewError(
        "module spec creation failed"
    )

module = importlib.util.module_from_spec(
    spec
)

sys.modules[
    spec.name
] = module

spec.loader.exec_module(
    module
)


# ------------------------------------------------------------
# Pure synthetic helper tests
# ------------------------------------------------------------

def dt(text: str):
    return datetime.fromisoformat(
        text.replace(
            "Z",
            "+00:00",
        )
    ).astimezone(UTC)


base = module.SelectedGP(
    object_id="1",
    creation_utc=dt(
        "2025-01-01T00:00:00Z"
    ),
    epoch_utc=dt(
        "2024-12-31T23:00:00Z"
    ),
    stable_tie_key="100",
    gp_id="100",
    tle_line1="1 synthetic",
    tle_line2="2 synthetic",
    source_class="TEST",
)

later_creation = module.SelectedGP(
    object_id="1",
    creation_utc=dt(
        "2025-01-01T00:00:01Z"
    ),
    epoch_utc=dt(
        "2024-01-01T00:00:00Z"
    ),
    stable_tie_key="001",
    gp_id="001",
    tle_line1="1 synthetic",
    tle_line2="2 synthetic",
    source_class="TEST",
)

if (
    module.choose_later(
        base,
        later_creation,
    )
    is not later_creation
):
    raise ReviewError(
        "CREATION_DATE ordering synthetic failed"
    )


later_epoch = module.SelectedGP(
    object_id="1",
    creation_utc=base.creation_utc,
    epoch_utc=dt(
        "2025-01-01T00:00:00Z"
    ),
    stable_tie_key="001",
    gp_id="001",
    tle_line1="1 synthetic",
    tle_line2="2 synthetic",
    source_class="TEST",
)

if (
    module.choose_later(
        base,
        later_epoch,
    )
    is not later_epoch
):
    raise ReviewError(
        "EPOCH tie-break synthetic failed"
    )


later_key = module.SelectedGP(
    object_id="1",
    creation_utc=base.creation_utc,
    epoch_utc=base.epoch_utc,
    stable_tie_key="999",
    gp_id="999",
    tle_line1="1 synthetic",
    tle_line2="2 synthetic",
    source_class="TEST",
)

if (
    module.choose_later(
        base,
        later_key,
    )
    is not later_key
):
    raise ReviewError(
        "stable-key tie-break synthetic failed"
    )


row = {
    "NORAD_CAT_ID": "12345",
    "CREATION_DATE":
        "2025-01-01T00:00:00Z",
    "EPOCH":
        "2024-12-31T12:00:00Z",
    "GP_ID":
        "777",
    "TLE_LINE1":
        "1 synthetic",
    "TLE_LINE2":
        "2 synthetic",
}

record = module.make_record(
    row,
    tuple(
        row.keys()
    ),
    "TEST",
)

if (
    record.stable_tie_key
    != "777"
):
    raise ReviewError(
        "GP_ID preference synthetic failed"
    )


fallback = dict(
    row
)

fallback[
    "GP_ID"
] = ""

fallback1 = module.make_record(
    fallback,
    tuple(
        fallback.keys()
    ),
    "TEST",
)

fallback2 = module.make_record(
    fallback,
    tuple(
        fallback.keys()
    ),
    "TEST",
)

if (
    fallback1.stable_tie_key
    != fallback2.stable_tie_key
    or len(
        fallback1.stable_tie_key
    )
    != 64
):
    raise ReviewError(
        "canonical SHA fallback synthetic failed"
    )


# Publication-driven replay test.
t_ref = dt(
    "2025-01-10T00:00:00Z"
)

first = module.SelectedGP(
    object_id="101",
    creation_utc=dt(
        "2025-01-07T20:00:00Z"
    ),
    epoch_utc=dt(
        "2025-01-07T00:00:00Z"
    ),
    stable_tie_key="A",
    gp_id="A",
    tle_line1="1 synthetic",
    tle_line2="2 synthetic",
    source_class="MAIN_WINDOW",
)

second = module.SelectedGP(
    object_id="202",
    creation_utc=dt(
        "2025-01-07T22:00:00Z"
    ),
    epoch_utc=dt(
        "2025-01-07T01:00:00Z"
    ),
    stable_tie_key="B",
    gp_id="B",
    tle_line1="1 synthetic",
    tle_line2="2 synthetic",
    source_class="MAIN_WINDOW",
)

sample_rows = [
    {
        "final_g6_sample_index":
            "1",
        "obj1":
            "101",
        "obj2":
            "202",
        "reference_tca_utc":
            "2025-01-10T00:00:00Z",
    },
    {
        "final_g6_sample_index":
            "2",
        "obj1":
            "303",
        "obj2":
            "404",
        "reference_tca_utc":
            "2025-01-11T00:00:00Z",
    },
]

event_by_object = {
    "101": {
        "reference_tca_utc":
            t_ref,
    },
    "202": {
        "reference_tca_utc":
            t_ref,
    },
    "303": {
        "reference_tca_utc":
            dt(
                "2025-01-11T00:00:00Z"
            ),
    },
    "404": {
        "reference_tca_utc":
            dt(
                "2025-01-11T00:00:00Z"
            ),
    },
}

cutoff_selected = {
    "101":
        first,
    "202":
        second,
    "303":
        None,
    "404":
        second,
}

event_replay, replay_by_object, availability = (
    module.derive_cutoff_and_replay_times(
        sample_rows,
        event_by_object,
        cutoff_selected,
        {},
    )
)

expected_replay = max(
    first.creation_utc,
    second.creation_utc,
)

if (
    event_replay.get(
        "1"
    )
    != expected_replay
):
    raise ReviewError(
        "publication-driven replay-time synthetic failed"
    )

if set(
    replay_by_object
) != {
    "101",
    "202",
}:
    raise ReviewError(
        "pair replay membership synthetic failed"
    )

if availability != Counter(
    {
        "CAUSAL_GP_AVAILABLE_BOTH":
            1,

        "NO_CAUSAL_GP_OBJECT_1":
            1,
    }
):
    raise ReviewError(
        "pair-unavailability synthetic failed: "
        + repr(
            dict(availability)
        )
    )


# Older predecessor must not displace later selected main GP.
older_pred = module.SelectedGP(
    object_id="101",
    creation_utc=dt(
        "2025-01-07T19:00:00Z"
    ),
    epoch_utc=dt(
        "2025-01-07T02:00:00Z"
    ),
    stable_tie_key="P",
    gp_id="P",
    tle_line1="1 synthetic",
    tle_line2="2 synthetic",
    source_class="QUERIED_PREDECESSOR",
)

working = {
    "101":
        first,
}

module.finalize_reselection(
    working,
    {
        "101":
            expected_replay,
    },
    {
        "101":
            older_pred,
    },
)

if (
    module.selected_identity(
        working[
            "101"
        ]
    )
    != module.selected_identity(
        first
    )
):
    raise ReviewError(
        "ordinary predecessor ordering synthetic failed"
    )


# ------------------------------------------------------------
# Realized dataset: GP_ID fallback not exercised
# State JSON only; no raw history read.
# ------------------------------------------------------------

main_state = json.loads(
    MAIN_STATE.read_text(
        encoding="utf-8"
    )
)

main_success = [
    r
    for r in main_state.get(
        "records",
        [],
    )
    if (
        r.get(
            "kind"
        )
        == "main"
        and r.get(
            "status"
        )
        == "SUCCESS"
    )
]

main_missing_gp = sum(
    int(
        r.get(
            "gp_id_missing_value_count"
        )
        or 0
    )
    for r in main_success
)

pred_state = json.loads(
    PRED_STATE.read_text(
        encoding="utf-8"
    )
)

pred_success = [
    r
    for r in pred_state.get(
        "records",
        [],
    )
    if r.get(
        "status"
    )
    == "SUCCESS"
]

pred_missing_gp = sum(
    1
    for r in pred_success
    if not str(
        r.get(
            "GP_ID"
        )
        or ""
    ).strip()
)

if (
    len(
        main_success
    )
    != 1386
    or main_missing_gp
    != 0
):
    raise ReviewError(
        "realized main GP_ID census changed"
    )

if (
    len(
        pred_success
    )
    != 5032
    or pred_missing_gp
    != 0
):
    raise ReviewError(
        "realized predecessor GP_ID census changed"
    )


# ------------------------------------------------------------
# Local cache must be ignored
# ------------------------------------------------------------

cache_rel = (
    "data/raw/g6/"
    "G6_48H_Replay_Selected_GP_Cache.jsonl"
)

ignore = git(
    "check-ignore",
    "-v",
    cache_rel,
    check=False,
)

if ignore.returncode != 0:
    raise ReviewError(
        "local selected-TLE cache is not Git-ignored"
    )


# ------------------------------------------------------------
# Existing offline-validation artifact
# ------------------------------------------------------------

validation = json.loads(
    VALIDATION.read_text(
        encoding="utf-8"
    )
)

expected_validation = {
    "status":
        "G6_48H_REPLAY_INPUT_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",

    "SpaceTrack_raw_files_opened":
        0,

    "TraCSS_reference_rows_opened":
        0,

    "network_requests":
        0,

    "protected_outcome_access":
        False,

    "propagation_performed":
        False,

    "reconstruction_performed":
        False,

    "replay_input_selection_executed":
        False,

    "future_prepare_mode_authorized":
        False,

    "selected_L_star_hours":
        48,

    "implementation_sha256":
        EXPECTED_IMPLEMENTATION_SHA,
}

for key, expected in (
    expected_validation.items()
):
    if (
        validation.get(
            key
        )
        != expected
    ):
        raise ReviewError(
            "offline-validation invariant mismatch: "
            f"{key}={validation.get(key)!r}"
        )


# ------------------------------------------------------------
# Review artifact
# ------------------------------------------------------------

result = {
    "status":
        "G6_48H_REPLAY_INPUT_IMPLEMENTATION_INDEPENDENT_REVIEW_PASS",

    "review_revision":
        "AST_SEMANTIC_V2",

    "prior_review_failures":
        [
            "BRITTLE_WHITESPACE_SOURCE_MATCH_CAUSAL_WINDOW",
            "BRITTLE_WHITESPACE_SOURCE_MATCH_SHA_GUARD",
        ],

    "implementation_sha256":
        EXPECTED_IMPLEMENTATION_SHA,

    "offline_validation_sha256":
        EXPECTED_VALIDATION_SHA,

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

    "prepare_inputs_active":
        False,

    "inactive_authorization_guard_verified":
        True,

    "network_capable_imports_absent":
        True,

    "required_function_set_verified":
        True,

    "semantic_local_causal_window_verified":
        True,

    "semantic_48h_cutoff_verified":
        True,

    "semantic_main_raw_sha_guard_verified":
        True,

    "semantic_main_row_count_guard_verified":
        True,

    "semantic_main_byte_count_guard_verified":
        True,

    "semantic_publication_driven_replay_time_verified":
        True,

    "semantic_reselection_identity_guard_verified":
        True,

    "synthetic_creation_date_ordering_pass":
        True,

    "synthetic_epoch_tiebreak_pass":
        True,

    "synthetic_stable_key_tiebreak_pass":
        True,

    "synthetic_gp_id_preference_pass":
        True,

    "synthetic_sha_fallback_determinism_pass":
        True,

    "synthetic_publication_replay_rule_pass":
        True,

    "synthetic_pair_unavailability_rule_pass":
        True,

    "synthetic_predecessor_ordinary_ordering_pass":
        True,

    "realized_dataset_main_missing_gp_id":
        0,

    "realized_dataset_success_predecessor_missing_gp_id":
        0,

    "fallback_tie_key_exercised_by_realized_dataset":
        False,

    "local_TLE_cache_gitignored":
        True,

    "review_scope":
        "AST_SEMANTIC_PLUS_PURE_SYNTHETIC_ZERO_RAW_HISTORY_READ",

    "next_safe_action":
        "FREEZE_INACTIVE_REPLAY_INPUT_IMPLEMENTATION_AND_REVIEW",
}

OUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

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
