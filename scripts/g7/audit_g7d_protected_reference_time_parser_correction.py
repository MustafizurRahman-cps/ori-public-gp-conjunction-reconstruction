from __future__ import annotations

import csv
import gzip
import hashlib
import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]

EVALUATOR = (
    ROOT
    / "scripts/g7/run_g7d_horizon_association.py"
)

VALIDATION = (
    ROOT
    / "results/g7/G7D_Horizon_Association_Offline_Validation.json"
)

SAMPLE = (
    ROOT
    / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"
)

TRACSS = (
    ROOT
    / "data/raw/g2/IVV_Releasable_Dataset_SFSH_DiscreteHBR.csv.gz"
)

G7C_EVENT = (
    ROOT
    / "results/g7/G7C_Association_Tolerance_Event.csv"
)

AUDIT_OUT = (
    ROOT
    / "results/g7/"
      "G7D_Protected_Reference_Time_Parser_Correction_Audit.json"
)

EXPECTED_EVALUATOR_SHA = (
    "4ebc7f1d887b9d56cf43d41f9822bd32cb189b1fd4ffe543bdcc67ad81ecd769"
)

EXPECTED_VALIDATION_SHA = (
    "2de8432b8e832cdad14aaf3fcbb7d7e38ca8ee25bdbf760ebd0127751b979e94"
)

EXPECTED_SAMPLE_SHA = (
    "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef"
)

EXPECTED_TRACSS_SHA = (
    "b8e49b9c40f974e5ff37e26ade3a855813436bb4c6dab4d7ddeccc8ff5752d39"
)

EXPECTED_G7C_EVENT_SHA = (
    "f848fbc1327456fa6051c1bafb5f9f602b0f00e2d62ebc6b19857a468f0b57e6"
)

EXPECTED_EVENTS = 6927
EXPECTED_REFERENCE_ROWS = 283595
EXPECTED_G7C_5MIN_ASSOCIATED = 6839

UTC = timezone.utc


class AuditError(RuntimeError):
    pass


def require(condition, message):

    if not condition:
        raise AuditError(message)


def sha256(path: Path) -> str:

    digest = hashlib.sha256()

    with path.open("rb") as stream:

        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def parse_aware_utc(value: str) -> datetime:

    text = str(value).strip().replace(
        " ",
        "T",
    )

    if not text:
        raise AuditError(
            "empty aware timestamp"
        )

    if text.endswith(("Z", "z")):
        text = (
            text[:-1]
            + "+00:00"
        )

    try:
        result = datetime.fromisoformat(
            text
        )

    except ValueError as exc:
        raise AuditError(
            f"malformed aware timestamp: {value!r}"
        ) from exc

    if result.tzinfo is None:
        raise AuditError(
            f"unexpected naive aware-source timestamp: {value!r}"
        )

    return result.astimezone(
        UTC
    )


def parse_tracss_epoch_utc(value: str) -> datetime:

    text = str(value).strip().replace(
        " ",
        "T",
    )

    if not text:
        raise AuditError(
            "empty TraCSS epoch"
        )

    if text.endswith(("Z", "z")):
        text = (
            text[:-1]
            + "+00:00"
        )

    try:
        result = datetime.fromisoformat(
            text
        )

    except ValueError as exc:
        raise AuditError(
            f"malformed TraCSS epoch: {value!r}"
        ) from exc

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=UTC
        )

    return result.astimezone(
        UTC
    )


def parse_bool(value: str) -> bool:

    text = str(value).strip().upper()

    if text == "TRUE":
        return True

    if text == "FALSE":
        return False

    raise AuditError(
        f"invalid boolean: {value!r}"
    )


# ================================================================
# 1. Frozen/corrected artifact identities
# ================================================================

for path in (
    EVALUATOR,
    VALIDATION,
    SAMPLE,
    TRACSS,
    G7C_EVENT,
):

    require(
        path.is_file(),
        f"missing required artifact: {path}",
    )


require(
    sha256(EVALUATOR)
    == EXPECTED_EVALUATOR_SHA,
    "corrected evaluator SHA mismatch",
)

require(
    sha256(VALIDATION)
    == EXPECTED_VALIDATION_SHA,
    "corrected validation SHA mismatch",
)

require(
    sha256(SAMPLE)
    == EXPECTED_SAMPLE_SHA,
    "sample-register SHA mismatch",
)

require(
    sha256(TRACSS)
    == EXPECTED_TRACSS_SHA,
    "protected TraCSS SHA mismatch",
)

require(
    sha256(G7C_EVENT)
    == EXPECTED_G7C_EVENT_SHA,
    "frozen G7-C event SHA mismatch",
)


# ================================================================
# 2. Import corrected evaluator
# ================================================================

spec = importlib.util.spec_from_file_location(
    "g7d_corrected_reference_loader",
    EVALUATOR,
)

require(
    spec is not None
    and spec.loader is not None,
    "cannot import corrected evaluator",
)

module = importlib.util.module_from_spec(
    spec
)

spec.loader.exec_module(
    module
)


# ================================================================
# 3. Exact failed timestamp regression
# ================================================================

failed_value = (
    "2025-01-04 12:00:13.371034"
)

expected_failed = parse_aware_utc(
    "2025-01-04T12:00:13.371034Z"
)

require(
    module.tracss_epoch_utc(
        failed_value
    )
    == expected_failed,
    "corrected evaluator does not reproduce TraCSS UTC convention",
)

require(
    parse_tracss_epoch_utc(
        failed_value
    )
    == expected_failed,
    "independent TraCSS parser disagrees on failed timestamp",
)


generic_naive_rejected = False

try:
    module.utc(
        failed_value
    )

except module.AssociationError:
    generic_naive_rejected = True


require(
    generic_naive_rejected,
    "generic evaluator utc() was weakened",
)


# ================================================================
# 4. Independently load frozen sample register
# ================================================================

sample = {}
conj_to_index = {}

with SAMPLE.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(
        stream
    )

    required = {
        "final_g6_sample_index",
        "conj_id",
        "reference_tca_utc",
    }

    require(
        required.issubset(
            set(
                reader.fieldnames
                or ()
            )
        ),
        "sample-register schema changed",
    )

    for row in reader:

        index = int(
            row[
                "final_g6_sample_index"
            ]
        )

        require(
            index not in sample,
            f"duplicate sample index: {index}",
        )

        conj_id = row[
            "conj_id"
        ].strip()

        require(
            bool(conj_id),
            f"empty conjunction ID: {index}",
        )

        require(
            conj_id not in conj_to_index,
            f"duplicate sample conjunction ID: {conj_id}",
        )

        reference_tca = parse_aware_utc(
            row[
                "reference_tca_utc"
            ]
        )

        sample[index] = {
            "conj_id":
                conj_id,

            "reference_tca":
                reference_tca,
        }

        conj_to_index[
            conj_id
        ] = index


require(
    len(sample)
    == EXPECTED_EVENTS,
    "sample event census mismatch",
)

require(
    set(sample)
    == set(
        range(
            1,
            EXPECTED_EVENTS + 1,
        )
    ),
    "sample index set is not exact",
)


# ================================================================
# 5. Independent raw TraCSS reference reconstruction
# ================================================================

independent_reference = {}

reference_rows_scanned = 0
matched_reference_rows = 0
naive_matched_epochs = 0
aware_matched_epochs = 0

with gzip.open(
    TRACSS,
    "rt",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(
        stream
    )

    required = {
        "conj_id",
        "epoch",
        "min_range",
    }

    require(
        required.issubset(
            set(
                reader.fieldnames
                or ()
            )
        ),
        "protected TraCSS schema changed",
    )

    for row in reader:

        reference_rows_scanned += 1

        conj_id = row[
            "conj_id"
        ].strip()

        index = conj_to_index.get(
            conj_id
        )

        if index is None:
            continue

        require(
            index not in independent_reference,
            (
                "duplicate protected reference "
                f"event: {index}"
            ),
        )

        raw_epoch = row[
            "epoch"
        ].strip()

        epoch = parse_tracss_epoch_utc(
            raw_epoch
        )

        # Count the actual source representations encountered.
        normalized = raw_epoch.replace(
            " ",
            "T",
        )

        if normalized.endswith(
            ("Z", "z")
        ):
            aware_matched_epochs += 1

        else:
            try:
                parsed_form = datetime.fromisoformat(
                    normalized
                )
            except ValueError as exc:
                raise AuditError(
                    f"malformed raw TraCSS epoch: {raw_epoch!r}"
                ) from exc

            if parsed_form.tzinfo is None:
                naive_matched_epochs += 1
            else:
                aware_matched_epochs += 1

        require(
            epoch
            == sample[
                index
            ][
                "reference_tca"
            ],
            (
                "independent protected-reference "
                f"TCA mismatch: {index}"
            ),
        )

        try:
            distance = float(
                row[
                    "min_range"
                ]
            )
        except ValueError as exc:
            raise AuditError(
                (
                    "invalid protected-reference "
                    f"min_range: event {index}"
                )
            ) from exc

        require(
            math.isfinite(
                distance
            ),
            (
                "nonfinite protected-reference "
                f"distance: event {index}"
            ),
        )

        independent_reference[
            index
        ] = {
            "tca":
                epoch,

            "distance":
                distance,
        }

        matched_reference_rows += 1


require(
    reference_rows_scanned
    == EXPECTED_REFERENCE_ROWS,
    "protected-reference full row census mismatch",
)

require(
    matched_reference_rows
    == EXPECTED_EVENTS,
    "protected-reference matched-row census mismatch",
)

require(
    len(independent_reference)
    == EXPECTED_EVENTS,
    "protected-reference sample coverage mismatch",
)

require(
    naive_matched_epochs > 0,
    "audit did not encounter any naive TraCSS sample epoch",
)


# ================================================================
# 6. Corrected evaluator full-reference loader
# ================================================================

module_sample = module.indexed(
    SAMPLE
)

module_reference, module_rows_scanned = (
    module.load_protected_reference(
        module_sample
    )
)

require(
    module_rows_scanned
    == EXPECTED_REFERENCE_ROWS,
    "corrected evaluator reference-row census mismatch",
)

require(
    len(module_reference)
    == EXPECTED_EVENTS,
    "corrected evaluator reference coverage mismatch",
)


# ================================================================
# 7. Full 6,927-event module-vs-independent equivalence
# ================================================================

module_independent_tca_matches = 0
module_independent_distance_matches = 0

maximum_module_independent_distance_residual_km = 0.0

for index in range(
    1,
    EXPECTED_EVENTS + 1,
):

    module_tca = parse_aware_utc(
        module_reference[
            index
        ][
            "reference_tca_utc"
        ]
    )

    independent_tca = (
        independent_reference[
            index
        ][
            "tca"
        ]
    )

    require(
        module_tca
        == independent_tca,
        (
            "module/independent reference "
            f"TCA mismatch: {index}"
        ),
    )

    module_independent_tca_matches += 1

    module_distance = float(
        module_reference[
            index
        ][
            "reference_miss_distance_km"
        ]
    )

    independent_distance = float(
        independent_reference[
            index
        ][
            "distance"
        ]
    )

    residual = abs(
        module_distance
        - independent_distance
    )

    maximum_module_independent_distance_residual_km = max(
        maximum_module_independent_distance_residual_km,
        residual,
    )

    require(
        residual <= 1.0e-12,
        (
            "module/independent reference "
            f"distance mismatch: {index}"
        ),
    )

    module_independent_distance_matches += 1


require(
    module_independent_tca_matches
    == EXPECTED_EVENTS,
    "not all module/reference TCAs matched",
)

require(
    module_independent_distance_matches
    == EXPECTED_EVENTS,
    "not all module/reference distances matched",
)


# ================================================================
# 8. External equivalence against frozen G7-C 5-min rows
#
# G7-C deliberately writes reference miss distance only for
# associated events. Thus:
# - TCA can be compared for all 6,927 events;
# - miss distance can be compared for the 6,839 associated events;
# - unassociated rows MUST have blank reference distance.
# ================================================================

g7c_rows = {}

with G7C_EVENT.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(
        stream
    )

    required = {
        "final_g6_sample_index",
        "tolerance_min",
        "associated",
        "reference_tca_utc",
        "reference_miss_distance_km",
    }

    require(
        required.issubset(
            set(
                reader.fieldnames
                or ()
            )
        ),
        "G7-C event schema changed",
    )

    for row in reader:

        if int(
            row[
                "tolerance_min"
            ]
        ) != 5:
            continue

        index = int(
            row[
                "final_g6_sample_index"
            ]
        )

        require(
            index not in g7c_rows,
            (
                "duplicate frozen G7-C "
                f"5-min event: {index}"
            ),
        )

        g7c_rows[
            index
        ] = row


require(
    len(g7c_rows)
    == EXPECTED_EVENTS,
    "frozen G7-C 5-min event census mismatch",
)


g7c_tca_matches = 0
g7c_distance_comparable = 0
g7c_distance_matches = 0
g7c_blank_distance_unassociated = 0

maximum_g7c_distance_residual_km = 0.0


for index in range(
    1,
    EXPECTED_EVENTS + 1,
):

    row = g7c_rows[
        index
    ]

    frozen_tca = parse_aware_utc(
        row[
            "reference_tca_utc"
        ]
    )

    independent_tca = (
        independent_reference[
            index
        ][
            "tca"
        ]
    )

    require(
        frozen_tca
        == independent_tca,
        (
            "frozen G7-C/reference "
            f"TCA mismatch: {index}"
        ),
    )

    g7c_tca_matches += 1

    associated = parse_bool(
        row[
            "associated"
        ]
    )

    frozen_distance_text = (
        row[
            "reference_miss_distance_km"
        ].strip()
    )

    if associated:

        require(
            bool(
                frozen_distance_text
            ),
            (
                "associated G7-C event has blank "
                f"reference distance: {index}"
            ),
        )

        frozen_distance = float(
            frozen_distance_text
        )

        independent_distance = (
            independent_reference[
                index
            ][
                "distance"
            ]
        )

        residual = abs(
            frozen_distance
            - independent_distance
        )

        maximum_g7c_distance_residual_km = max(
            maximum_g7c_distance_residual_km,
            residual,
        )

        require(
            residual <= 1.0e-12,
            (
                "frozen G7-C/reference "
                f"distance mismatch: {index}"
            ),
        )

        g7c_distance_comparable += 1
        g7c_distance_matches += 1

    else:

        require(
            frozen_distance_text == "",
            (
                "unassociated G7-C event unexpectedly "
                f"contains reference distance: {index}"
            ),
        )

        g7c_blank_distance_unassociated += 1


require(
    g7c_tca_matches
    == EXPECTED_EVENTS,
    "not all frozen G7-C TCAs matched",
)

require(
    g7c_distance_comparable
    == EXPECTED_G7C_5MIN_ASSOCIATED,
    (
        "frozen G7-C comparable reference-distance "
        "census mismatch"
    ),
)

require(
    g7c_distance_matches
    == EXPECTED_G7C_5MIN_ASSOCIATED,
    (
        "not all comparable frozen G7-C "
        "reference distances matched"
    ),
)

require(
    g7c_blank_distance_unassociated
    == (
        EXPECTED_EVENTS
        - EXPECTED_G7C_5MIN_ASSOCIATED
    ),
    "unexpected G7-C blank-distance census",
)


# ================================================================
# 9. Corrected offline-validation semantics
# ================================================================

validation = json.loads(
    VALIDATION.read_text(
        encoding="utf-8"
    )
)

require(
    validation.get(
        "status"
    )
    == "G7D_HORIZON_ASSOCIATION_OFFLINE_VALIDATION_PASS",
    "corrected offline validation is not PASS",
)

require(
    validation.get(
        "tracss_naive_epoch_interpreted_as_UTC"
    )
    is True,
    "TraCSS UTC semantic missing from validation",
)

require(
    validation.get(
        "generic_utc_naive_timestamp_rejected"
    )
    is True,
    "strict generic UTC semantic missing from validation",
)

for key in (
    "H5_exact_G6_metric_reproduction",
    "H5_exact_G6_selected_candidate_reproduction",
    "H5_exact_G6_status_reproduction",
    "H5_exact_G7C_5min_reproduction",
):

    require(
        validation.get(
            key
        )
        is True,
        f"corrected H5 regression failure: {key}",
    )


# ================================================================
# 10. No H3/H7 result may exist
# ================================================================

real_outputs = [
    ROOT / "results/g7/G7D_H3_Horizon_Association_Event.csv",
    ROOT / "results/g7/G7D_H3_Horizon_Association_Summary.json",
    ROOT / "results/g7/G7D_H3_Horizon_Association_Execution_Audit.json",
    ROOT / "results/g7/G7D_H7_Horizon_Association_Event.csv",
    ROOT / "results/g7/G7D_H7_Horizon_Association_Summary.json",
    ROOT / "results/g7/G7D_H7_Horizon_Association_Execution_Audit.json",
]

for path in real_outputs:

    require(
        not path.exists(),
        f"scientific horizon result exists: {path}",
    )


# ================================================================
# 11. Emit audit
# ================================================================

payload = {
    "status":
        "G7D_PROTECTED_REFERENCE_TIME_PARSER_CORRECTION_AUDIT_PASS",

    "correction_class":
        "SOURCE_SPECIFIC_TIMESTAMP_PARSER_WIRING_DEFECT",

    "failed_H3_attempt_result_produced":
        False,

    "failed_attempt_error":
        (
            "naive timestamp: "
            "'2025-01-04 12:00:13.371034'"
        ),

    "corrected_evaluator_sha256":
        sha256(
            EVALUATOR
        ),

    "corrected_offline_validation_sha256":
        sha256(
            VALIDATION
        ),

    "generic_utc_parser_remains_strict":
        True,

    "generic_naive_timestamp_rejected":
        True,

    "TraCSS_naive_epoch_interpreted_as_UTC":
        True,

    "TraCSS_semantics_match_frozen_G7C":
        True,

    "protected_reference_rows_scanned_independently":
        reference_rows_scanned,

    "protected_reference_rows_scanned_by_corrected_evaluator":
        module_rows_scanned,

    "protected_reference_sample_events":
        len(
            independent_reference
        ),

    "sample_events_with_naive_TraCSS_epoch":
        naive_matched_epochs,

    "sample_events_with_aware_TraCSS_epoch":
        aware_matched_epochs,

    "module_vs_independent_TCA_matches":
        module_independent_tca_matches,

    "module_vs_independent_distance_matches":
        module_independent_distance_matches,

    "maximum_module_vs_independent_distance_residual_km":
        maximum_module_independent_distance_residual_km,

    "frozen_G7C_5min_TCA_matches":
        g7c_tca_matches,

    "frozen_G7C_5min_reference_distance_comparable_events":
        g7c_distance_comparable,

    "frozen_G7C_5min_reference_distance_matches":
        g7c_distance_matches,

    "frozen_G7C_5min_unassociated_blank_reference_distances":
        g7c_blank_distance_unassociated,

    "maximum_frozen_G7C_reference_distance_residual_km":
        maximum_g7c_distance_residual_km,

    "H5_exact_G6_and_G7C_regression_preserved":
        True,

    "H3_candidate_file_accessed_by_correction_audit":
        False,

    "H7_candidate_file_accessed_by_correction_audit":
        False,

    "H3_reference_association_performed":
        False,

    "H7_reference_association_performed":
        False,

    "protected_TraCSS_accessed_for_parser_validation":
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

    "automatic_retry_performed":
        False,

    "previous_audit_failure_class":
        "AUDIT_HARNESS_ASSUMED_REFERENCE_DISTANCE_PRESENT_FOR_UNASSOCIATED_G7C_ROWS",

    "next_safe_action":
        (
            "FREEZE_DR0049_CORRECTED_EVALUATOR_"
            "BEFORE_NEW_EXECUTION_AUTHORIZATION"
        ),
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