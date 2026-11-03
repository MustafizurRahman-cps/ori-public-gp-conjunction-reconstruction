from __future__ import annotations

import ast
import csv
import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]

EVALUATOR = (
    ROOT
    / "scripts/g7/run_g7d_horizon_association.py"
)

VALIDATION = (
    ROOT
    / "results/g7/"
      "G7D_Horizon_Association_Offline_Validation.json"
)

AUDIT_OUT = (
    ROOT
    / "results/g7/"
      "G7D_Horizon_Association_Independent_Audit.json"
)

LOCK = (
    ROOT
    / "docs/research/"
      "G7_Robustness_Sensitivity_Analysis_Lock.md"
)

SAMPLE = (
    ROOT
    / "docs/research/"
      "G6_Final_Confirmatory_Sample_Register.csv"
)

G7C_SUMMARY = (
    ROOT
    / "results/g7/"
      "G7C_Association_Tolerance_Summary.csv"
)


EXPECTED_EVALUATOR_SHA = (
    "ee9c867ed6b70c40fec23de0f8ddbb2c5a6e46885aec2dc99e66a8f8440987c6"
)

EXPECTED_VALIDATION_SHA = (
    "cac8b94587f49a8e51fa61723fdbbd33ea84df9df9e9cec21e94e7634c8199b4"
)

EXPECTED_LOCK_SHA = (
    "9323799c51520da1d9bf39a386b7b9ff3c9dbd171dd8d175dca3408efae004b6"
)

EXPECTED_SAMPLE_SHA = (
    "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef"
)

EXPECTED_G7C_SUMMARY_SHA = (
    "bc5d66904dc2dfc1ebaaa7ff8a0a6161e3ff26f0a4853a59d5bc6e88ffea6e47"
)


class AuditError(RuntimeError):
    pass


def sha256(path: Path) -> str:

    digest = hashlib.sha256()

    with path.open("rb") as stream:

        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def require(
    condition: bool,
    message: str,
):

    if not condition:
        raise AuditError(message)


# ================================================================
# 1. Frozen tracked/control artifacts
# ================================================================

for path in (
    EVALUATOR,
    VALIDATION,
    LOCK,
    SAMPLE,
    G7C_SUMMARY,
):

    require(
        path.is_file(),
        f"missing required artifact: {path}",
    )


require(
    sha256(EVALUATOR)
    == EXPECTED_EVALUATOR_SHA,
    "evaluator SHA mismatch",
)

require(
    sha256(VALIDATION)
    == EXPECTED_VALIDATION_SHA,
    "validation SHA mismatch",
)

require(
    sha256(LOCK)
    == EXPECTED_LOCK_SHA,
    "G7 lock SHA mismatch",
)

require(
    sha256(SAMPLE)
    == EXPECTED_SAMPLE_SHA,
    "sample-register SHA mismatch",
)

require(
    sha256(G7C_SUMMARY)
    == EXPECTED_G7C_SUMMARY_SHA,
    "G7-C summary SHA mismatch",
)


# ================================================================
# 2. Import evaluator without invoking main()
# ================================================================

spec = importlib.util.spec_from_file_location(
    "g7d_horizon_association_frozen_candidate",
    EVALUATOR,
)

if (
    spec is None
    or spec.loader is None
):
    raise AuditError(
        "cannot construct evaluator import spec"
    )

module = importlib.util.module_from_spec(
    spec
)

spec.loader.exec_module(
    module
)


# ================================================================
# 3. Frozen configuration
# ================================================================

require(
    module.N_EVENTS == 6927,
    "event census constant changed",
)

require(
    module.H5_READY == 6906,
    "H5 ready-event constant changed",
)

require(
    module.H5_CANDIDATE_ROWS == 929821,
    "H5 candidate-row constant changed",
)

require(
    module.ASSOC_TOL_MIN == 5,
    "association tolerance changed",
)

require(
    module.ASSOC_TOL_S == 300.0,
    "association tolerance seconds changed",
)

require(
    module.MISS_RADIUS_KM
    == 78.75554652312711,
    "miss-distance radius changed",
)

require(
    module.TCA_RADIUS_S
    == 7.105892,
    "TCA radius changed",
)

require(
    set(module.HORIZONS.keys())
    == {3, 7},
    "authorized sensitivity horizon set changed",
)

require(
    module.HORIZONS[3][
        "candidate_rows"
    ]
    == 558469,
    "H3 candidate census changed",
)

require(
    module.HORIZONS[7][
        "candidate_rows"
    ]
    == 1299383,
    "H7 candidate census changed",
)

require(
    module.HORIZONS[3][
        "ready"
    ]
    == 6915,
    "H3 ready census changed",
)

require(
    module.HORIZONS[7][
        "ready"
    ]
    == 6889,
    "H7 ready census changed",
)

require(
    module.TRACSS_SHA256
    == (
        "b8e49b9c40f974e5ff37e26ade3a855813436bb4c6dab4d7ddeccc8ff5752d39"
    ),
    "protected-reference SHA constant changed",
)

require(
    module.EXPECTED_REFERENCE_ROWS
    == 283595,
    "protected-reference row census changed",
)


# ================================================================
# 4. Static isolation of H5-only offline validation
# ================================================================

offline_source = inspect.getsource(
    module.offline_validate
)

offline_tree = ast.parse(
    offline_source
)

offline_names = {
    node.id
    for node in ast.walk(
        offline_tree
    )
    if isinstance(
        node,
        ast.Name,
    )
}

for forbidden_name in (
    "H3_EVENT",
    "H3_CAND",
    "H7_EVENT",
    "H7_CAND",
    "TRACSS",
    "load_protected_reference",
    "evaluate_horizon",
):

    require(
        forbidden_name
        not in offline_names,
        (
            "offline validation has forbidden "
            f"dependency: {forbidden_name}"
        ),
    )


# ================================================================
# 5. Static authorized-path structure
# ================================================================

execution_source = inspect.getsource(
    module.evaluate_horizon
)

required_execution_fragments = (
    "authorization",
    "HORIZONS",
    "load_protected_reference",
    "sample_rows",
    "scan(",
    "evaluate(",
)

for fragment in required_execution_fragments:

    require(
        fragment in execution_source,
        (
            "authorized execution structure "
            f"missing: {fragment}"
        ),
    )


protected_source = inspect.getsource(
    module.load_protected_reference
)

for fragment in (
    "TRACSS",
    "gzip.open",
    "conj_id",
    "epoch",
    "min_range",
    "reference_tca_utc",
    "EXPECTED_REFERENCE_ROWS",
):

    require(
        fragment in protected_source,
        (
            "protected reference loader "
            f"missing required semantic: {fragment}"
        ),
    )


# ================================================================
# 6. Runtime authorization guards
#
# These calls must fail BEFORE opening any H3/H7 candidate or
# protected-reference source.
# ================================================================

unauthorized_guards = {}

for horizon in (3, 7):

    try:

        module.evaluate_horizon(
            horizon,
            authorization=False,
        )

    except module.AssociationError as exc:

        require(
            "unauthorized"
            in str(exc).lower(),
            (
                "unexpected unauthorized-guard "
                f"message for H{horizon}"
            ),
        )

        unauthorized_guards[
            f"H{horizon}"
        ] = True

    else:

        raise AuditError(
            f"unauthorized H{horizon} execution was allowed"
        )


try:

    module.evaluate_horizon(
        5,
        authorization=True,
    )

except module.AssociationError as exc:

    require(
        (
            "exactly 3 or 7"
            in str(exc)
        ),
        "H5 execution was not explicitly blocked",
    )

    h5_execution_blocked = True

else:

    raise AuditError(
        "H5 execution was incorrectly allowed"
    )


# ================================================================
# 7. Direct CLI guard
# ================================================================

environment = dict(
    os.environ
)

environment[
    "PYTHONDONTWRITEBYTECODE"
] = "1"

direct = subprocess.run(
    [
        sys.executable,
        str(EVALUATOR),
    ],
    cwd=ROOT,
    env=environment,
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
)

require(
    direct.returncode != 0,
    "direct evaluator CLI unexpectedly succeeded",
)

direct_text = (
    direct.stdout
    + "\n"
    + direct.stderr
)

require(
    (
        "direct real H3/H7 association "
        "is disabled"
    )
    in direct_text,
    "direct CLI guard message missing",
)


# ================================================================
# 8. Phase-A validation result
# ================================================================

validation = json.loads(
    VALIDATION.read_text(
        encoding="utf-8"
    )
)

require(
    validation[
        "status"
    ]
    == (
        "G7D_HORIZON_ASSOCIATION_"
        "OFFLINE_VALIDATION_PASS"
    ),
    "offline validation status mismatch",
)

expected_validation_exact = {
    "analysis_role":
        "POST_CONFIRMATORY_PROPAGATION_HORIZON_SENSITIVITY",

    "association_tolerance_min":
        5,

    "association_threshold_inclusive":
        True,

    "H5_control_events":
        6927,

    "H5_control_candidate_rows_scanned":
        929821,

    "H5_control_candidate_ready_events":
        6906,

    "H5_control_valid_associated_events":
        6839,

    "H5_exact_G6_status_reproduction":
        True,

    "H5_exact_G6_selected_candidate_reproduction":
        True,

    "H5_exact_G6_metric_reproduction":
        True,

    "H5_exact_G7C_5min_reproduction":
        True,

    "direct_H3_H7_execution_authorized":
        False,

    "H3_candidate_or_event_file_accessed":
        False,

    "H7_candidate_or_event_file_accessed":
        False,

    "frozen_sample_register_accessed":
        True,

    "raw_TraCSS_reference_accessed":
        False,

    "frozen_G6_reference_outputs_accessed":
        True,

    "orbital_propagation_performed":
        False,

    "candidate_generation_performed":
        False,

    "CAL0_refit_performed":
        False,

    "method_retuning_performed":
        False,

    "network_requests":
        0,
}

for key, expected in expected_validation_exact.items():

    require(
        validation.get(key)
        == expected,
        (
            "offline validation field mismatch: "
            f"{key}"
        ),
    )


expected_ordering = [
    "minimum absolute reference-TCA offset",
    "earlier candidate time",
    "smaller candidate distance",
    "lower stable candidate index",
]

require(
    validation[
        "association_ordering"
    ]
    == expected_ordering,
    "association ordering changed",
)

require(
    all(
        validation[
            "synthetic_tests"
        ].values()
    ),
    "one or more synthetic tests failed",
)


# ================================================================
# 9. Independent external H5 control from frozen G7-C summary
# ================================================================

with G7C_SUMMARY.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    rows = [
        row
        for row
        in csv.DictReader(
            stream
        )
        if int(
            row[
                "tolerance_min"
            ]
        ) == 5
    ]

require(
    len(rows) == 1,
    "G7-C 5-minute control row missing/duplicated",
)

five = rows[0]

expected_integer_control = {
    "total_events":
        6927,

    "candidate_ready_events":
        6906,

    "associated_events":
        6839,

    "unassociated_candidate_ready_events":
        67,

    "Ed_successes":
        5609,

    "Ed_denominator":
        6839,

    "Et_successes":
        5125,

    "Et_denominator":
        6839,
}

for key, expected in expected_integer_control.items():

    require(
        int(
            five[key]
        )
        == expected,
        (
            "frozen G7-C 5-min external "
            f"control mismatch: {key}"
        ),
    )


# ================================================================
# 10. Emit independent audit
# ================================================================

payload = {
    "status":
        "G7D_HORIZON_ASSOCIATION_IMPLEMENTATION_INDEPENDENT_AUDIT_PASS",

    "analysis_role":
        "POST_CONFIRMATORY_PROPAGATION_HORIZON_SENSITIVITY",

    "evaluator_sha256":
        sha256(
            EVALUATOR
        ),

    "offline_validation_sha256":
        sha256(
            VALIDATION
        ),

    "association_tolerance_min":
        5,

    "association_threshold_inclusive":
        True,

    "association_ordering":
        expected_ordering,

    "authorized_horizons_days":
        [3, 7],

    "H5_primary_execution_blocked":
        h5_execution_blocked,

    "H3_unauthorized_execution_blocked":
        unauthorized_guards[
            "H3"
        ],

    "H7_unauthorized_execution_blocked":
        unauthorized_guards[
            "H7"
        ],

    "direct_CLI_real_execution_blocked":
        True,

    "offline_validation_static_H3_H7_isolation_verified":
        True,

    "protected_reference_loader_isolated_to_authorized_path":
        True,

    "H5_external_control_source":
        "G7C_Association_Tolerance_Summary.csv",

    "H5_external_control_total_events":
        6927,

    "H5_external_control_candidate_ready_events":
        6906,

    "H5_external_control_valid_associated_events":
        6839,

    "H5_external_control_Ed_successes":
        5609,

    "H5_external_control_Et_successes":
        5125,

    "H5_exact_G6_reproduction_in_offline_validation":
        True,

    "H5_exact_G7C_5min_reproduction_in_offline_validation":
        True,

    "H3_candidate_or_event_file_accessed_by_audit":
        False,

    "H7_candidate_or_event_file_accessed_by_audit":
        False,

    "raw_TraCSS_reference_accessed_by_audit":
        False,

    "H3_reference_association_performed":
        False,

    "H7_reference_association_performed":
        False,

    "orbital_propagation_performed":
        False,

    "candidate_generation_performed":
        False,

    "CAL0_refit_performed":
        False,

    "method_retuning_performed":
        False,

    "network_requests":
        0,

    "next_safe_action":
        "FREEZE_G7D_HORIZON_ASSOCIATION_EVALUATOR_BEFORE_H3_H7_EXECUTION",
}

AUDIT_OUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

with AUDIT_OUT.open(
    "w",
    encoding="utf-8",
    newline="\n",
) as stream:

    json.dump(
        payload,
        stream,
        indent=2,
        sort_keys=True,
    )

    stream.write("\n")


print(
    json.dumps(
        payload,
        indent=2,
        sort_keys=True,
    )
)