#!/usr/bin/env python3
"""Independent post-outcome audit of frozen G6 confirmatory metrics.

This program independently reconstructs the already-authorized protected
metrics from frozen candidate identities and the protected TraCSS source.

It performs no orbital propagation, candidate generation, reassociation,
CAL-0 refitting, bootstrap, threshold selection, or method retuning.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import subprocess

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

SAMPLE = ROOT / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"

EVENT_STATUS = ROOT / "results/g6/G6_PreOutcome_Candidate_Event_Status.csv"

ASSOCIATION = ROOT / "results/g6/G6_Reference_TCA_Association.csv"

CANDIDATES = ROOT / "results/g6/G6_PreOutcome_Candidates.csv"

TRACSS = ROOT / "data/raw/g2/IVV_Releasable_Dataset_SFSH_DiscreteHBR.csv.gz"

EVENT_METRICS = ROOT / "results/g6/G6_Confirmatory_Event_Metrics.csv"

TEMPORAL = ROOT / "results/g6/G6_Confirmatory_Temporal_Summary.csv"

SUMMARY = ROOT / "results/g6/G6_Confirmatory_Metric_Summary.json"

EXECUTION = ROOT / "results/g6/G6_Confirmatory_Metric_Execution_Audit.json"

OUTPUT = ROOT / "results/g6/G6_Confirmatory_Metric_Output_Independent_Audit.json"


EXPECTED_HEAD = "bdefb1ab3ffbe2506f9759133d55250f86ce428f"

EXPECTED_TAG = "g6-confirmatory-metric-execution-authorized"

EXPECTED_HASHES = {
    SAMPLE:
        "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef",

    EVENT_STATUS:
        "844a4c3f90a1b5bb738f13911b5e9f99f22bfe72e01bdb43dd6242e292b7b308",

    ASSOCIATION:
        "31327a65448908791928aaa41a822f02086f97662c2c89ed4ea337fec2cfa622",

    CANDIDATES:
        "21b8a9f5c8aab4b3427fad5cd3f3c2823ffc0219bd2a36aeccafc6a0aaa80e36",

    TRACSS:
        "b8e49b9c40f974e5ff37e26ade3a855813436bb4c6dab4d7ddeccc8ff5752d39",

    EVENT_METRICS:
        "e92553698deba0643e5c11ad48ddccab33c2f9a0fd9ce4298a6ee167860c0a79",

    TEMPORAL:
        "84daf7ce19e4a1ea69946a093ab72826ab0eb7671d4bb45494f9a25972322108",

    SUMMARY:
        "0b057d16ee25ce8beb6a729fc51d0aa330f980375bb23dcb7a5be51160dbd976",

    EXECUTION:
        "b4324954074bc5713499bcce91c5919966033733bc554c63d5764dc7f2611a46",
}


EXPECTED_EVENTS = 6927
EXPECTED_EVALUABLE = 6906
EXPECTED_VALID = 6839
EXPECTED_CANDIDATE_ROWS = 929821
EXPECTED_TRACSS_ROWS = 283595

MISS_RADIUS_KM = 78.75554652312711
TCA_RADIUS_S = 7.105892
NOMINAL = 0.90
MIN_SUPPORT = 141
Z95 = 1.959963984540054


EXPECTED_STRATA = {
    "G6-T1": 1386,
    "G6-T2": 1386,
    "G6-T3": 1385,
    "G6-T4": 1385,
    "G6-T5": 1385,
}


EVENT_FIELDS = (
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


def git_value(*args: str) -> str:

    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def parse_utc(
    value: str,
    *,
    naive_is_utc: bool = False,
) -> datetime:

    text = str(value).strip()

    if not text:
        raise AuditError("empty timestamp")

    text = text.replace(" ", "T")

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    try:
        result = datetime.fromisoformat(text)

    except ValueError as exc:
        raise AuditError(
            f"invalid timestamp {value!r}"
        ) from exc

    if result.tzinfo is None:

        if not naive_is_utc:
            raise AuditError(
                f"naive timestamp without UTC contract: {value!r}"
            )

        result = result.replace(tzinfo=UTC)

    return result.astimezone(UTC)


def parse_bool(value: str) -> bool:

    value = value.strip().upper()

    if value == "TRUE":
        return True

    if value == "FALSE":
        return False

    raise AuditError(
        f"invalid serialized boolean: {value!r}"
    )


def number(value: str) -> float:

    result = float(value)

    if not math.isfinite(result):
        raise AuditError(
            f"nonfinite number: {value!r}"
        )

    return result


def same(
    actual: float,
    expected: float,
    *,
    tol: float = 1.0e-10,
):

    if not math.isclose(
        float(actual),
        float(expected),
        rel_tol=1.0e-12,
        abs_tol=tol,
    ):
        raise AuditError(
            f"numeric mismatch: actual={actual}, expected={expected}"
        )


def linear_quantile(
    values,
    q,
):

    ordered = sorted(
        float(value)
        for value in values
    )

    if not ordered:
        raise AuditError(
            "empty quantile population"
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


def wilson(
    successes: int,
    denominator: int,
):

    if not (
        0
        <= successes
        <= denominator
    ):
        raise AuditError(
            "invalid binary count"
        )

    if denominator <= 0:
        raise AuditError(
            "zero Wilson denominator"
        )

    n = float(denominator)

    p = successes / n

    z2 = Z95 * Z95

    denom = 1.0 + z2 / n

    center = (
        p
        + z2
        / (
            2.0 * n
        )
    ) / denom

    half = (
        Z95
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


def binary_summary(
    successes,
    denominator,
):

    lower, upper = wilson(
        successes,
        denominator,
    )

    return {
        "successes":
            successes,

        "denominator":
            denominator,

        "proportion":
            successes / denominator,

        "wilson_95_lower":
            lower,

        "wilson_95_upper":
            upper,
    }


def error_summary(
    values,
    radius,
):

    values = [
        float(value)
        for value in values
    ]

    if not values:
        raise AuditError(
            "empty error population"
        )

    successes = sum(
        value <= radius
        for value in values
    )

    binary = binary_summary(
        successes,
        len(values),
    )

    return {
        "n":
            len(values),

        "median":
            linear_quantile(
                values,
                0.50,
            ),

        "p90":
            linear_quantile(
                values,
                0.90,
            ),

        "p95":
            linear_quantile(
                values,
                0.95,
            ),

        "p99":
            linear_quantile(
                values,
                0.99,
            ),

        "maximum":
            max(values),

        "fixed_radius":
            radius,

        "coverage_successes":
            successes,

        "coverage_denominator":
            len(values),

        "observed_coverage":
            binary["proportion"],

        "wilson_95_lower":
            binary["wilson_95_lower"],

        "wilson_95_upper":
            binary["wilson_95_upper"],

        "absolute_deviation_from_0p90":
            abs(
                binary["proportion"]
                - NOMINAL
            ),
    }


def interpretation(primary):

    n = primary[
        "coverage_denominator"
    ]

    if n < MIN_SUPPORT:

        return {
            "classification":
                "INSUFFICIENT CAUSAL/EVALUABLE SUPPORT",

            "direction":
                None,
        }

    lower = primary[
        "wilson_95_lower"
    ]

    upper = primary[
        "wilson_95_upper"
    ]

    observed = primary[
        "observed_coverage"
    ]

    if lower <= NOMINAL <= upper:

        return {
            "classification":
                "CONSISTENT WITH NOMINAL 90% COVERAGE",

            "direction":
                None,
        }

    return {
        "classification":
            "NOMINAL 90% COVERAGE NOT SUPPORTED",

        "direction":
            (
                "UNDERCOVERAGE"
                if observed < NOMINAL
                else
                "CONSERVATIVE OVERCOVERAGE"
            ),
    }


# ============================================================
# Repository and immutable inputs
# ============================================================

if (
    git_value(
        "rev-parse",
        "HEAD",
    )
    != EXPECTED_HEAD
):
    raise AuditError(
        "unexpected HEAD"
    )

if (
    git_value(
        "rev-parse",
        f"refs/tags/{EXPECTED_TAG}^{{commit}}",
    )
    != EXPECTED_HEAD
):
    raise AuditError(
        "authorization tag target mismatch"
    )


for path, expected in EXPECTED_HASHES.items():

    if not path.is_file():
        raise AuditError(
            f"required file missing: {path}"
        )

    actual = sha256(path)

    if actual != expected:
        raise AuditError(
            f"SHA mismatch: {path}\n"
            f"actual={actual}\n"
            f"expected={expected}"
        )


# ============================================================
# Frozen allocation / replay / association metadata
# ============================================================

sample = {}

with SAMPLE.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    for expected_index, row in enumerate(
        reader,
        start=1,
    ):

        index = int(
            row[
                "final_g6_sample_index"
            ]
        )

        if index != expected_index:
            raise AuditError(
                "sample-register ordering mismatch"
            )

        sample[index] = row


if len(sample) != EXPECTED_EVENTS:
    raise AuditError(
        "sample-register event count mismatch"
    )


event_status = {}

with EVENT_STATUS.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    for expected_index, row in enumerate(
        reader,
        start=1,
    ):

        index = int(
            row[
                "final_g6_sample_index"
            ]
        )

        if index != expected_index:
            raise AuditError(
                "event-status ordering mismatch"
            )

        event_status[index] = row


association = {}
status_counts = Counter()

with ASSOCIATION.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    for expected_index, row in enumerate(
        reader,
        start=1,
    ):

        index = int(
            row[
                "final_g6_sample_index"
            ]
        )

        if index != expected_index:
            raise AuditError(
                "association ordering mismatch"
            )

        association[index] = row

        status_counts[
            row[
                "final_status"
            ].strip()
        ] += 1


if status_counts != Counter(
    {
        "VALID_ASSOCIATED": 6839,
        "UNASSOCIATED_REFERENCE_EVENT": 67,
        "UNRECOVERED_REFINEMENT_FAILURE": 20,
        "PROPAGATION_FAILURE": 1,
    }
):
    raise AuditError(
        f"association census mismatch: {dict(status_counts)}"
    )


wanted = {
    index:
        int(
            row[
                "selected_candidate_index"
            ]
        )

    for index, row
    in association.items()

    if (
        row[
            "final_status"
        ].strip()
        == "VALID_ASSOCIATED"
    )
}


if len(wanted) != EXPECTED_VALID:
    raise AuditError(
        "VALID_ASSOCIATED membership count mismatch"
    )


# ============================================================
# Independently recover exact frozen selected candidates
# ============================================================

selected = {}
candidate_rows = 0

with CANDIDATES.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    for row in reader:

        candidate_rows += 1

        index = int(
            row[
                "final_g6_sample_index"
            ]
        )

        if index not in wanted:
            continue

        candidate_index = int(
            row[
                "candidate_index"
            ]
        )

        if (
            candidate_index
            != wanted[index]
        ):
            continue

        if index in selected:
            raise AuditError(
                f"duplicate selected candidate: {index}"
            )

        selected[index] = {
            "candidate_index":
                candidate_index,

            "time_from_replay_s":
                number(
                    row[
                        "time_from_replay_s"
                    ]
                ),

            "distance_km":
                number(
                    row[
                        "distance_km"
                    ]
                ),
        }


if candidate_rows != EXPECTED_CANDIDATE_ROWS:
    raise AuditError(
        f"candidate row count mismatch: {candidate_rows}"
    )


if set(selected) != set(wanted):

    missing = sorted(
        set(wanted)
        - set(selected)
    )

    raise AuditError(
        "selected candidate recovery incomplete: "
        + repr(
            missing[:10]
        )
    )


# ============================================================
# Independently recover protected TraCSS references
# ============================================================

wanted_conj_ids = {
    sample[index][
        "conj_id"
    ].strip()

    for index in wanted
}


references = {}
raw_rows = 0

with gzip.open(
    TRACSS,
    "rt",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    required = {
        "conj_id",
        "epoch",
        "min_range",
    }

    if not required.issubset(
        set(
            reader.fieldnames
            or ()
        )
    ):
        raise AuditError(
            "protected TraCSS schema mismatch"
        )

    for row in reader:

        raw_rows += 1

        conj_id = (
            row[
                "conj_id"
            ].strip()
        )

        if conj_id not in wanted_conj_ids:
            continue

        if conj_id in references:
            raise AuditError(
                f"duplicate protected conjunction: {conj_id}"
            )

        miss = number(
            row[
                "min_range"
            ]
        )

        if miss < 0.0:
            raise AuditError(
                f"negative min_range: {conj_id}"
            )

        references[
            conj_id
        ] = {
            "epoch":
                row[
                    "epoch"
                ].strip(),

            "min_range":
                miss,
        }


if raw_rows != EXPECTED_TRACSS_ROWS:
    raise AuditError(
        f"protected TraCSS row count mismatch: {raw_rows}"
    )


if set(references) != wanted_conj_ids:

    missing = sorted(
        wanted_conj_ids
        - set(references)
    )

    raise AuditError(
        "protected reference recovery incomplete: "
        + repr(
            missing[:10]
        )
    )


# ============================================================
# Independently reconstruct every event-level result
# ============================================================

event_rows = {}

with EVENT_METRICS.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    if tuple(
        reader.fieldnames or ()
    ) != EVENT_FIELDS:
        raise AuditError(
            "event-metric schema mismatch"
        )

    for expected_index, row in enumerate(
        reader,
        start=1,
    ):

        index = int(
            row[
                "final_g6_sample_index"
            ]
        )

        if index != expected_index:
            raise AuditError(
                "event-metric ordering mismatch"
            )

        event_rows[index] = row


if len(event_rows) != EXPECTED_EVENTS:
    raise AuditError(
        "event metric row count mismatch"
    )


errors_d = []
errors_t = []

strata = defaultdict(
    lambda: {
        "rows": 0,
        "evaluable": 0,
        "valid": 0,
        "errors_d": [],
    }
)

valid_rows_verified = 0
nonvalid_rows_verified = 0


protected_fields = (
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


for index in range(
    1,
    EXPECTED_EVENTS + 1,
):

    sample_row = sample[index]
    status_row = event_status[index]
    association_row = association[index]
    metric_row = event_rows[index]

    conj_id = (
        sample_row[
            "conj_id"
        ].strip()
    )

    stratum = (
        sample_row[
            "g6_temporal_stratum"
        ].strip()
    )

    final_status = (
        association_row[
            "final_status"
        ].strip()
    )

    if (
        metric_row[
            "conj_id"
        ].strip()
        != conj_id
    ):
        raise AuditError(
            f"event conjunction mismatch: {index}"
        )

    if (
        metric_row[
            "g6_temporal_stratum"
        ].strip()
        != stratum
    ):
        raise AuditError(
            f"event stratum mismatch: {index}"
        )

    if (
        metric_row[
            "final_status"
        ].strip()
        != final_status
    ):
        raise AuditError(
            f"event final-status mismatch: {index}"
        )

    expected_evaluable = (
        final_status
        in {
            "VALID_ASSOCIATED",
            "UNASSOCIATED_REFERENCE_EVENT",
        }
    )

    expected_valid = (
        final_status
        == "VALID_ASSOCIATED"
    )

    if (
        parse_bool(
            metric_row[
                "evaluable"
            ]
        )
        != expected_evaluable
    ):
        raise AuditError(
            f"evaluable flag mismatch: {index}"
        )

    if (
        parse_bool(
            metric_row[
                "valid_associated"
            ]
        )
        != expected_valid
    ):
        raise AuditError(
            f"valid-associated flag mismatch: {index}"
        )

    strata[
        stratum
    ][
        "rows"
    ] += 1

    if expected_evaluable:

        strata[
            stratum
        ][
            "evaluable"
        ] += 1

    if not expected_valid:

        if (
            metric_row[
                "selected_candidate_index"
            ].strip()
        ):
            raise AuditError(
                f"non-valid row exports selected candidate: {index}"
            )

        for field in protected_fields:

            if (
                metric_row[
                    field
                ].strip()
            ):
                raise AuditError(
                    f"non-valid row exports protected metric field "
                    f"{field}: {index}"
                )

        nonvalid_rows_verified += 1
        continue


    strata[
        stratum
    ][
        "valid"
    ] += 1


    selected_candidate = selected[index]

    if (
        int(
            metric_row[
                "selected_candidate_index"
            ]
        )
        != selected_candidate[
            "candidate_index"
        ]
    ):
        raise AuditError(
            f"selected candidate mismatch: {index}"
        )


    replay = parse_utc(
        status_row[
            "replay_time_utc"
        ]
    )

    reference_tca = parse_utc(
        sample_row[
            "reference_tca_utc"
        ]
    )

    raw_reference = references[
        conj_id
    ]

    raw_epoch = parse_utc(
        raw_reference[
            "epoch"
        ],
        naive_is_utc=True,
    )

    if raw_epoch != reference_tca:
        raise AuditError(
            f"raw/reference TCA mismatch: {index}"
        )


    predicted_tca = (
        replay
        + timedelta(
            seconds=
                selected_candidate[
                    "time_from_replay_s"
                ]
        )
    )


    emitted_predicted_tca = parse_utc(
        metric_row[
            "predicted_tca_utc"
        ]
    )

    emitted_reference_tca = parse_utc(
        metric_row[
            "reference_tca_utc"
        ]
    )

    if (
        emitted_predicted_tca
        != predicted_tca
    ):
        raise AuditError(
            f"predicted TCA mismatch: {index}"
        )

    if (
        emitted_reference_tca
        != reference_tca
    ):
        raise AuditError(
            f"reference TCA mismatch: {index}"
        )


    signed_t = (
        predicted_tca
        - reference_tca
    ).total_seconds()

    absolute_t = abs(
        signed_t
    )


    predicted_miss = (
        selected_candidate[
            "distance_km"
        ]
    )

    reference_miss = (
        raw_reference[
            "min_range"
        ]
    )

    signed_d = (
        predicted_miss
        - reference_miss
    )

    absolute_d = abs(
        signed_d
    )


    same(
        number(
            metric_row[
                "signed_tca_residual_s"
            ]
        ),
        signed_t,
    )

    same(
        number(
            metric_row[
                "absolute_tca_error_s"
            ]
        ),
        absolute_t,
    )

    same(
        number(
            metric_row[
                "tca_radius_s"
            ]
        ),
        TCA_RADIUS_S,
    )

    if (
        parse_bool(
            metric_row[
                "tca_covered"
            ]
        )
        != (
            absolute_t
            <= TCA_RADIUS_S
        )
    ):
        raise AuditError(
            f"TCA coverage mismatch: {index}"
        )


    same(
        number(
            metric_row[
                "predicted_miss_distance_km"
            ]
        ),
        predicted_miss,
    )

    same(
        number(
            metric_row[
                "reference_miss_distance_km"
            ]
        ),
        reference_miss,
    )

    same(
        number(
            metric_row[
                "signed_miss_distance_residual_km"
            ]
        ),
        signed_d,
    )

    same(
        number(
            metric_row[
                "absolute_miss_distance_error_km"
            ]
        ),
        absolute_d,
    )

    same(
        number(
            metric_row[
                "miss_distance_radius_km"
            ]
        ),
        MISS_RADIUS_KM,
    )

    if (
        parse_bool(
            metric_row[
                "miss_distance_covered"
            ]
        )
        != (
            absolute_d
            <= MISS_RADIUS_KM
        )
    ):
        raise AuditError(
            f"miss-distance coverage mismatch: {index}"
        )


    errors_t.append(
        absolute_t
    )

    errors_d.append(
        absolute_d
    )

    strata[
        stratum
    ][
        "errors_d"
    ].append(
        absolute_d
    )

    valid_rows_verified += 1


if valid_rows_verified != EXPECTED_VALID:
    raise AuditError(
        "not all VALID_ASSOCIATED rows were independently verified"
    )


if (
    valid_rows_verified
    + nonvalid_rows_verified
    != EXPECTED_EVENTS
):
    raise AuditError(
        "event-level verification count mismatch"
    )


if len(errors_d) != EXPECTED_VALID:
    raise AuditError(
        "E_d denominator mismatch"
    )


if len(errors_t) != EXPECTED_VALID:
    raise AuditError(
        "E_t denominator mismatch"
    )


# ============================================================
# Independently recompute pooled metrics
# ============================================================

evaluable_count = sum(
    1
    for row in association.values()
    if (
        row[
            "final_status"
        ].strip()
        in {
            "VALID_ASSOCIATED",
            "UNASSOCIATED_REFERENCE_EVENT",
        }
    )
)


valid_count = sum(
    1
    for row in association.values()
    if (
        row[
            "final_status"
        ].strip()
        == "VALID_ASSOCIATED"
    )
)


if evaluable_count != EXPECTED_EVALUABLE:
    raise AuditError(
        "evaluable count mismatch"
    )


if valid_count != EXPECTED_VALID:
    raise AuditError(
        "valid-associated count mismatch"
    )


evaluable_summary = binary_summary(
    evaluable_count,
    EXPECTED_EVENTS,
)

association_summary = binary_summary(
    valid_count,
    EXPECTED_EVENTS,
)

primary = error_summary(
    errors_d,
    MISS_RADIUS_KM,
)

secondary = error_summary(
    errors_t,
    TCA_RADIUS_S,
)

primary_interpretation = interpretation(
    primary
)


# ============================================================
# Independently recompute temporal metrics
# ============================================================

temporal_recomputed = []

for name in (
    "G6-T1",
    "G6-T2",
    "G6-T3",
    "G6-T4",
    "G6-T5",
):

    data = strata[name]

    if (
        data[
            "rows"
        ]
        != EXPECTED_STRATA[
            name
        ]
    ):
        raise AuditError(
            f"stratum size mismatch: {name}"
        )

    eval_binary = binary_summary(
        data[
            "evaluable"
        ],
        data[
            "rows"
        ],
    )

    assoc_binary = binary_summary(
        data[
            "valid"
        ],
        data[
            "rows"
        ],
    )

    ed = error_summary(
        data[
            "errors_d"
        ],
        MISS_RADIUS_KM,
    )

    temporal_recomputed.append(
        {
            "g6_temporal_stratum":
                name,

            "total_events":
                data[
                    "rows"
                ],

            "evaluable_count":
                data[
                    "evaluable"
                ],

            "evaluable_rate":
                eval_binary[
                    "proportion"
                ],

            "evaluable_wilson_95_lower":
                eval_binary[
                    "wilson_95_lower"
                ],

            "evaluable_wilson_95_upper":
                eval_binary[
                    "wilson_95_upper"
                ],

            "valid_associated_count":
                data[
                    "valid"
                ],

            "valid_association_rate":
                assoc_binary[
                    "proportion"
                ],

            "valid_association_wilson_95_lower":
                assoc_binary[
                    "wilson_95_lower"
                ],

            "valid_association_wilson_95_upper":
                assoc_binary[
                    "wilson_95_upper"
                ],

            "Ed_successes":
                ed[
                    "coverage_successes"
                ],

            "Ed_denominator":
                ed[
                    "coverage_denominator"
                ],

            "Ed_coverage":
                ed[
                    "observed_coverage"
                ],

            "Ed_wilson_95_lower":
                ed[
                    "wilson_95_lower"
                ],

            "Ed_wilson_95_upper":
                ed[
                    "wilson_95_upper"
                ],

            "Ed_absolute_deviation_from_0p90":
                ed[
                    "absolute_deviation_from_0p90"
                ],

            "Ed_median_km":
                ed[
                    "median"
                ],

            "Ed_p90_km":
                ed[
                    "p90"
                ],

            "Ed_p95_km":
                ed[
                    "p95"
                ],

            "Ed_p99_km":
                ed[
                    "p99"
                ],

            "Ed_maximum_km":
                ed[
                    "maximum"
                ],
        }
    )


# ============================================================
# Compare temporal CSV against independent reconstruction
# ============================================================

with TEMPORAL.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    temporal_actual = list(
        csv.DictReader(
            stream
        )
    )


if len(temporal_actual) != 5:
    raise AuditError(
        "temporal output row count mismatch"
    )


int_fields = {
    "total_events",
    "evaluable_count",
    "valid_associated_count",
    "Ed_successes",
    "Ed_denominator",
}


for actual, expected in zip(
    temporal_actual,
    temporal_recomputed,
):

    if (
        actual[
            "g6_temporal_stratum"
        ]
        != expected[
            "g6_temporal_stratum"
        ]
    ):
        raise AuditError(
            "temporal stratum ordering mismatch"
        )

    for key, expected_value in expected.items():

        if key == "g6_temporal_stratum":
            continue

        if key in int_fields:

            if int(
                actual[
                    key
                ]
            ) != expected_value:
                raise AuditError(
                    f"temporal integer mismatch: "
                    f"{actual['g6_temporal_stratum']} {key}"
                )

        else:

            same(
                number(
                    actual[
                        key
                    ]
                ),
                expected_value,
            )


temporal_coverages = [
    row[
        "Ed_coverage"
    ]
    for row in temporal_recomputed
]


temporal_flag = any(
    not (
        row[
            "Ed_wilson_95_lower"
        ]
        <= NOMINAL
        <= row[
            "Ed_wilson_95_upper"
        ]
    )
    for row in temporal_recomputed
)


temporal_excluding_nominal = sum(
    not (
        row[
            "Ed_wilson_95_lower"
        ]
        <= NOMINAL
        <= row[
            "Ed_wilson_95_upper"
        ]
    )
    for row in temporal_recomputed
)


# ============================================================
# Compare JSON metric summary
# ============================================================

summary = json.loads(
    SUMMARY.read_text(
        encoding="utf-8"
    )
)


if (
    summary.get("status")
    != "G6_CONFIRMATORY_METRICS_PASS"
):
    raise AuditError(
        "metric summary status is not PASS"
    )


def check_binary(
    actual,
    expected,
):

    if (
        int(
            actual[
                "successes"
            ]
        )
        != expected[
            "successes"
        ]
        or int(
            actual[
                "denominator"
            ]
        )
        != expected[
            "denominator"
        ]
    ):
        raise AuditError(
            "binary count mismatch"
        )

    for key in (
        "proportion",
        "wilson_95_lower",
        "wilson_95_upper",
    ):
        same(
            float(
                actual[
                    key
                ]
            ),
            expected[
                key
            ],
        )


check_binary(
    summary[
        "process_estimands"
    ][
        "evaluable"
    ],
    evaluable_summary,
)

check_binary(
    summary[
        "process_estimands"
    ][
        "valid_association"
    ],
    association_summary,
)


def check_error(
    actual,
    expected,
):

    for key in (
        "n",
        "coverage_successes",
        "coverage_denominator",
    ):

        if int(
            actual[
                key
            ]
        ) != int(
            expected[
                key
            ]
        ):
            raise AuditError(
                f"error-summary count mismatch: {key}"
            )

    for key in (
        "median",
        "p90",
        "p95",
        "p99",
        "maximum",
        "fixed_radius",
        "observed_coverage",
        "wilson_95_lower",
        "wilson_95_upper",
        "absolute_deviation_from_0p90",
    ):
        same(
            float(
                actual[
                    key
                ]
            ),
            expected[
                key
            ],
        )


check_error(
    summary[
        "primary_Ed"
    ],
    primary,
)

check_error(
    summary[
        "secondary_Et"
    ],
    secondary,
)


if (
    summary[
        "primary_Ed"
    ][
        "interpretation"
    ]
    != primary_interpretation
):
    raise AuditError(
        "primary interpretation mismatch"
    )


same(
    summary[
        "temporal_reporting"
    ][
        "Ed_coverage_min"
    ],
    min(
        temporal_coverages
    ),
)

same(
    summary[
        "temporal_reporting"
    ][
        "Ed_coverage_max"
    ],
    max(
        temporal_coverages
    ),
)


if (
    summary[
        "temporal_reporting"
    ][
        "TEMPORAL_INSTABILITY_FLAG"
    ]
    is not temporal_flag
):
    raise AuditError(
        "temporal instability flag mismatch"
    )


for key in (
    "orbital_propagation_performed",
    "candidate_generation_performed",
    "association_recomputed",
    "CAL0_refit_performed",
    "method_retuning_performed",
    "bootstrap_performed",
    "covariance_access",
    "Pc_access",
    "HBR_access",
):

    if summary.get(key) is not False:
        raise AuditError(
            f"summary safety invariant failed: {key}"
        )


if summary.get("network_requests") != 0:
    raise AuditError(
        "network activity reported"
    )


# ============================================================
# Compare execution audit
# ============================================================

execution = json.loads(
    EXECUTION.read_text(
        encoding="utf-8"
    )
)


if (
    execution.get("status")
    != "G6_CONFIRMATORY_METRIC_EXECUTION_PASS"
):
    raise AuditError(
        "execution audit is not PASS"
    )


if (
    execution.get(
        "authorization_commit"
    )
    != EXPECTED_HEAD
):
    raise AuditError(
        "execution authorization commit mismatch"
    )


if (
    execution.get(
        "authorization_tag"
    )
    != EXPECTED_TAG
):
    raise AuditError(
        "execution authorization tag mismatch"
    )


if (
    execution.get(
        "event_metrics_sha256"
    )
    != EXPECTED_HASHES[
        EVENT_METRICS
    ]
    or execution.get(
        "temporal_summary_sha256"
    )
    != EXPECTED_HASHES[
        TEMPORAL
    ]
    or execution.get(
        "metric_summary_sha256"
    )
    != EXPECTED_HASHES[
        SUMMARY
    ]
):
    raise AuditError(
        "execution audit output-hash linkage mismatch"
    )


if (
    int(
        execution[
            "primary_Ed_coverage_successes"
        ]
    )
    != primary[
        "coverage_successes"
    ]
):
    raise AuditError(
        "execution primary success count mismatch"
    )


if (
    int(
        execution[
            "primary_Ed_coverage_denominator"
        ]
    )
    != primary[
        "coverage_denominator"
    ]
):
    raise AuditError(
        "execution primary denominator mismatch"
    )


same(
    execution[
        "primary_Ed_observed_coverage"
    ],
    primary[
        "observed_coverage"
    ],
)


same(
    execution[
        "primary_Ed_wilson_95_lower"
    ],
    primary[
        "wilson_95_lower"
    ],
)


same(
    execution[
        "primary_Ed_wilson_95_upper"
    ],
    primary[
        "wilson_95_upper"
    ],
)


if (
    execution[
        "primary_interpretation"
    ]
    != primary_interpretation
):
    raise AuditError(
        "execution interpretation mismatch"
    )


if (
    int(
        execution[
            "secondary_Et_coverage_successes"
        ]
    )
    != secondary[
        "coverage_successes"
    ]
    or int(
        execution[
            "secondary_Et_coverage_denominator"
        ]
    )
    != secondary[
        "coverage_denominator"
    ]
):
    raise AuditError(
        "execution secondary coverage mismatch"
    )


if (
    execution[
        "temporal_instability_flag"
    ]
    is not temporal_flag
):
    raise AuditError(
        "execution temporal flag mismatch"
    )


# ============================================================
# Final independent audit record
# ============================================================

result = {
    "status":
        "G6_CONFIRMATORY_METRIC_OUTPUT_INDEPENDENT_AUDIT_PASS",

    "audit_scope":
        "FULL_POST_OUTCOME_INDEPENDENT_RECONSTRUCTION_NO_REPROPAGATION_NO_REASSOCIATION",

    "protected_source_sha256":
        EXPECTED_HASHES[
            TRACSS
        ],

    "protected_source_rows_parsed":
        raw_rows,

    "events_verified":
        EXPECTED_EVENTS,

    "VALID_ASSOCIATED_rows_independently_reconstructed":
        valid_rows_verified,

    "non_VALID_ASSOCIATED_rows_verified":
        nonvalid_rows_verified,

    "candidate_rows_scanned":
        candidate_rows,

    "selected_candidate_identities_verified":
        len(
            selected
        ),

    "primary_Ed": {
        **primary,

        "interpretation":
            primary_interpretation,
    },

    "secondary_Et":
        secondary,

    "evaluable_process_estimand":
        evaluable_summary,

    "valid_association_process_estimand":
        association_summary,

    "temporal_Ed_coverage_ordered": [
        {
            "stratum":
                row[
                    "g6_temporal_stratum"
                ],

            "coverage":
                row[
                    "Ed_coverage"
                ],

            "wilson_95_lower":
                row[
                    "Ed_wilson_95_lower"
                ],

            "wilson_95_upper":
                row[
                    "Ed_wilson_95_upper"
                ],
        }

        for row in temporal_recomputed
    ],

    "temporal_Ed_coverage_min":
        min(
            temporal_coverages
        ),

    "temporal_Ed_coverage_max":
        max(
            temporal_coverages
        ),

    "temporal_strata_excluding_nominal_0p90":
        temporal_excluding_nominal,

    "TEMPORAL_INSTABILITY_FLAG":
        temporal_flag,

    "event_metrics_sha256":
        EXPECTED_HASHES[
            EVENT_METRICS
        ],

    "temporal_summary_sha256":
        EXPECTED_HASHES[
            TEMPORAL
        ],

    "metric_summary_sha256":
        EXPECTED_HASHES[
            SUMMARY
        ],

    "execution_audit_sha256":
        EXPECTED_HASHES[
            EXECUTION
        ],

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

    "network_requests":
        0,

    "next_safe_action":
        "FREEZE_CONFIRMATORY_METRIC_OUTPUTS_WITHOUT_RETUNING",
}


OUTPUT.write_text(
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

            "audit_script_sha256":
                sha256(
                    Path(__file__)
                ),

            "audit_artifact_sha256":
                sha256(
                    OUTPUT
                ),
        },
        indent=2,
        sort_keys=True,
    )
)
