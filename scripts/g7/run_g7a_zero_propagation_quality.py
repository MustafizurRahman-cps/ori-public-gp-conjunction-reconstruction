#!/usr/bin/env python3
"""G7-A zero-propagation age/quality robustness analysis.

Post-confirmatory descriptive analysis of the frozen G6 sample.

Inputs:
- frozen 48 h replay-input selection;
- frozen confirmatory event metrics.

No raw GP_HISTORY read.
No SGP4 propagation.
No candidate generation.
No reassociation.
No CAL-0 refit.
No method retuning.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr


ROOT = Path.cwd()

SELECTION = ROOT / "results/g6/G6_48H_Replay_Input_Selection.csv"
METRICS = ROOT / "results/g6/G6_Confirmatory_Event_Metrics.csv"
G7_LOCK = ROOT / "docs/research/G7_Robustness_Sensitivity_Analysis_Lock.md"
G5_IMPL = ROOT / "scripts/g5/run_g5_calibration_v2.py"

EVENT_OUT = ROOT / "results/g7/G7A_Event_Quality.csv"
AGE_OUT = ROOT / "results/g7/G7A_Age_Sensitivity.csv"
QUALITY_OUT = ROOT / "results/g7/G7A_StateAge_Association.csv"
SUMMARY_OUT = ROOT / "results/g7/G7A_Zero_Propagation_Quality_Summary.json"


EXPECTED_HASHES = {
    G7_LOCK:
        "9323799c51520da1d9bf39a386b7b9ff3c9dbd171dd8d175dca3408efae004b6",

    SELECTION:
        "6d03f0b4c7104b7cbac76b649bedc87e9f497f297dccc19f2270e7fec5552095",

    METRICS:
        "e92553698deba0643e5c11ad48ddccab33c2f9a0fd9ce4298a6ee167860c0a79",

    G5_IMPL:
        "2ceae52cd0e4ffa6c1e9dea2df38c49d2c4bb983e7dfe7f8cdbc8dbeaf202c0d",
}

EXPECTED_EVENTS = 6927
EXPECTED_OBJECT_ROWS = 13854
EXPECTED_EVALUABLE = 6906
EXPECTED_VALID = 6839

MISS_RADIUS_KM = 78.75554652312711
Z95 = 1.959963984540054

BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20260908
SECONDS_PER_DAY = 86400.0

AGE_BINS = (
    ("LT1", "<1 d"),
    ("1_LT2", "1–<2 d"),
    ("2_LT3", "2–<3 d"),
    ("3_LT5", "3–<5 d"),
    ("5_LT7", "5–<7 d"),
    ("GE7", ">=7 d"),
)

TEMPORAL = (
    "G6-T1",
    "G6-T2",
    "G6-T3",
    "G6-T4",
    "G6-T5",
)


class AnalysisError(RuntimeError):
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


def parse_utc(value: str) -> datetime:

    text = value.strip()

    if not text:
        raise AnalysisError("empty timestamp")

    text = text.replace(" ", "T")

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    try:
        result = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AnalysisError(
            f"malformed UTC timestamp: {value!r}"
        ) from exc

    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)

    return result.astimezone(UTC)


def finite(value) -> float:

    result = float(value)

    if not math.isfinite(result):
        raise AnalysisError(
            f"nonfinite value: {value!r}"
        )

    return result


def parse_bool(value: str) -> bool:

    text = value.strip().upper()

    if text == "TRUE":
        return True

    if text == "FALSE":
        return False

    raise AnalysisError(
        f"invalid boolean serialization: {value!r}"
    )


def age_bin(value: float) -> tuple[str, str]:

    x = finite(value)

    # Exact inherited G5 boundaries.
    if x < 1.0:
        return AGE_BINS[0]

    if x < 2.0:
        return AGE_BINS[1]

    if x < 3.0:
        return AGE_BINS[2]

    if x < 5.0:
        return AGE_BINS[3]

    if x < 7.0:
        return AGE_BINS[4]

    return AGE_BINS[5]


def wilson(successes: int, denominator: int):

    if denominator <= 0:
        return None, None

    if not 0 <= successes <= denominator:
        raise AnalysisError("invalid Wilson count")

    p = successes / denominator
    z2 = Z95 * Z95

    scale = 1.0 + z2 / denominator

    center = (
        p
        + z2 / (2.0 * denominator)
    ) / scale

    half = (
        Z95
        / scale
        * math.sqrt(
            p * (1.0 - p) / denominator
            + z2
            / (
                4.0
                * denominator
                * denominator
            )
        )
    )

    return (
        max(0.0, center - half),
        min(1.0, center + half),
    )


def linear_quantile(values, q):

    array = np.asarray(
        list(values),
        dtype=float,
    )

    if array.size == 0:
        return None

    return float(
        np.quantile(
            array,
            q,
        )
    )


def error_summary(values):

    values = [
        finite(value)
        for value in values
    ]

    if not values:
        return {
            "n": 0,
            "median": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "maximum": None,
            "coverage_successes": 0,
            "coverage_denominator": 0,
            "observed_coverage": None,
            "wilson_95_lower": None,
            "wilson_95_upper": None,
            "absolute_deviation_from_0p90": None,
        }

    successes = sum(
        value <= MISS_RADIUS_KM
        for value in values
    )

    lower, upper = wilson(
        successes,
        len(values),
    )

    coverage = successes / len(values)

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

        "coverage_successes":
            successes,

        "coverage_denominator":
            len(values),

        "observed_coverage":
            coverage,

        "wilson_95_lower":
            lower,

        "wilson_95_upper":
            upper,

        "absolute_deviation_from_0p90":
            abs(
                coverage - 0.90
            ),
    }


def spearman_value(rows, field):

    pairs = [
        (
            row[field],
            row[
                "absolute_miss_distance_error_km"
            ],
        )
        for row in rows
        if (
            row[field] is not None
            and row[
                "absolute_miss_distance_error_km"
            ] is not None
        )
    ]

    n = len(pairs)

    if n < 3:
        return None, n

    x = np.asarray(
        [p[0] for p in pairs],
        dtype=float,
    )

    y = np.asarray(
        [p[1] for p in pairs],
        dtype=float,
    )

    if (
        len(np.unique(x)) < 2
        or len(np.unique(y)) < 2
    ):
        return None, n

    rho = float(
        spearmanr(
            x,
            y,
        ).statistic
    )

    if not math.isfinite(rho):
        return None, n

    return rho, n


def bootstrap_state_age_spearman(rows):

    pairs = [
        (
            float(
                row[
                    "pair_max_state_age_days"
                ]
            ),
            float(
                row[
                    "absolute_miss_distance_error_km"
                ]
            ),
        )
        for row in rows
        if (
            row[
                "pair_max_state_age_days"
            ]
            is not None
            and row[
                "absolute_miss_distance_error_km"
            ]
            is not None
        )
    ]

    n = len(pairs)

    if n < 3:
        return None, None, 0

    x = np.asarray(
        [pair[0] for pair in pairs],
        dtype=float,
    )

    y = np.asarray(
        [pair[1] for pair in pairs],
        dtype=float,
    )

    # Exact inherited first-variable G5 bootstrap stream.
    rng = np.random.default_rng(
        BOOTSTRAP_SEED
    )

    values = []

    for _ in range(
        BOOTSTRAP_REPLICATES
    ):

        indices = rng.integers(
            0,
            n,
            size=n,
        )

        bx = x[indices]
        by = y[indices]

        if (
            len(np.unique(bx)) < 2
            or len(np.unique(by)) < 2
        ):
            continue

        rho = float(
            spearmanr(
                bx,
                by,
            ).statistic
        )

        if math.isfinite(rho):
            values.append(rho)

    if not values:
        return None, None, 0

    array = np.asarray(
        values,
        dtype=float,
    )

    return (
        float(
            np.quantile(
                array,
                0.025,
            )
        ),
        float(
            np.quantile(
                array,
                0.975,
            )
        ),
        len(values),
    )


def write_csv(path, fields, rows):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as stream:

        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
            lineterminator="\n",
        )

        writer.writeheader()
        writer.writerows(rows)


# ------------------------------------------------------------
# Frozen-input integrity
# ------------------------------------------------------------

for path, expected in EXPECTED_HASHES.items():

    if not path.is_file():
        raise AnalysisError(
            f"required input missing: {path}"
        )

    actual = sha256(path)

    if actual != expected:
        raise AnalysisError(
            f"input SHA mismatch: {path}"
        )


# ------------------------------------------------------------
# Load exact replay selections: two object rows/event
# ------------------------------------------------------------

selection_by_event = defaultdict(dict)
selection_rows = 0

with SELECTION.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    for row in reader:

        selection_rows += 1

        index = int(
            row[
                "final_g6_sample_index"
            ]
        )

        role = row[
            "object_role"
        ].strip()

        if role not in {
            "obj1",
            "obj2",
        }:
            raise AnalysisError(
                f"unexpected object role: {role}"
            )

        if role in selection_by_event[index]:
            raise AnalysisError(
                f"duplicate selection role: {index}/{role}"
            )

        if (
            row[
                "selection_status"
            ].strip()
            != "CAUSAL_GP_SELECTED"
        ):
            raise AnalysisError(
                f"non-selected replay input: {index}/{role}"
            )

        selection_by_event[
            index
        ][
            role
        ] = row


if selection_rows != EXPECTED_OBJECT_ROWS:
    raise AnalysisError(
        f"selection row count {selection_rows} != {EXPECTED_OBJECT_ROWS}"
    )

if set(selection_by_event) != set(
    range(
        1,
        EXPECTED_EVENTS + 1,
    )
):
    raise AnalysisError(
        "selection event membership mismatch"
    )


# ------------------------------------------------------------
# Load frozen confirmatory event metrics
# ------------------------------------------------------------

metrics = {}

with METRICS.open(
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
            raise AnalysisError(
                "metric ordering mismatch"
            )

        metrics[index] = row


if len(metrics) != EXPECTED_EVENTS:
    raise AnalysisError(
        "metric event count mismatch"
    )


# ------------------------------------------------------------
# Derive event-level quality variables
# ------------------------------------------------------------

event_rows = []

negative_state_age_objects = 0
negative_state_age_events = 0

for index in range(
    1,
    EXPECTED_EVENTS + 1,
):

    pair = selection_by_event[
        index
    ]

    row1 = pair[
        "obj1"
    ]

    row2 = pair[
        "obj2"
    ]

    metric = metrics[
        index
    ]

    replay1 = parse_utc(
        row1[
            "replay_time_utc"
        ]
    )

    replay2 = parse_utc(
        row2[
            "replay_time_utc"
        ]
    )

    if replay1 != replay2:
        raise AnalysisError(
            f"pair replay times differ: {index}"
        )

    replay = replay1

    for source in (
        row1,
        row2,
    ):
        if (
            source[
                "conj_id"
            ].strip()
            != metric[
                "conj_id"
            ].strip()
        ):
            raise AnalysisError(
                f"selection/metric conjunction mismatch: {index}"
            )

        if (
            source[
                "g6_temporal_stratum"
            ].strip()
            != metric[
                "g6_temporal_stratum"
            ].strip()
        ):
            raise AnalysisError(
                f"selection/metric stratum mismatch: {index}"
            )


    def object_quality(source):

        epoch = parse_utc(
            source[
                "replay_EPOCH"
            ]
        )

        creation = parse_utc(
            source[
                "replay_CREATION_DATE"
            ]
        )

        state_age = (
            replay - epoch
        ).total_seconds() / SECONDS_PER_DAY

        publication_age = (
            replay - creation
        ).total_seconds() / SECONDS_PER_DAY

        if publication_age < -1.0e-12:
            raise AnalysisError(
                f"negative publication age: {index}"
            )

        if publication_age < 0.0:
            publication_age = 0.0

        return (
            state_age,
            publication_age,
            epoch,
        )


    (
        state1,
        pub1,
        epoch1,
    ) = object_quality(
        row1
    )

    (
        state2,
        pub2,
        epoch2,
    ) = object_quality(
        row2
    )


    object_negative_count = sum(
        value < 0.0
        for value in (
            state1,
            state2,
        )
    )

    negative_state_age_objects += (
        object_negative_count
    )

    if object_negative_count:
        negative_state_age_events += 1


    pair_state_age = max(
        state1,
        state2,
    )

    pair_publication_age = max(
        pub1,
        pub2,
    )

    age_id, age_label = age_bin(
        pair_state_age
    )


    evaluable = parse_bool(
        metric[
            "evaluable"
        ]
    )

    valid = parse_bool(
        metric[
            "valid_associated"
        ]
    )


    propagation1 = None
    propagation2 = None
    pair_propagation = None

    ed = None
    et = None
    ed_covered = None


    if valid:

        predicted_tca = parse_utc(
            metric[
                "predicted_tca_utc"
            ]
        )

        propagation1 = abs(
            (
                predicted_tca
                - epoch1
            ).total_seconds()
            / SECONDS_PER_DAY
        )

        propagation2 = abs(
            (
                predicted_tca
                - epoch2
            ).total_seconds()
            / SECONDS_PER_DAY
        )

        pair_propagation = max(
            propagation1,
            propagation2,
        )

        ed = finite(
            metric[
                "absolute_miss_distance_error_km"
            ]
        )

        et = finite(
            metric[
                "absolute_tca_error_s"
            ]
        )

        emitted_radius = finite(
            metric[
                "miss_distance_radius_km"
            ]
        )

        if not math.isclose(
            emitted_radius,
            MISS_RADIUS_KM,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ):
            raise AnalysisError(
                f"miss-distance radius changed: {index}"
            )

        ed_covered = (
            ed <= MISS_RADIUS_KM
        )

        if (
            parse_bool(
                metric[
                    "miss_distance_covered"
                ]
            )
            != ed_covered
        ):
            raise AnalysisError(
                f"miss-distance coverage mismatch: {index}"
            )


    event_rows.append(
        {
            "final_g6_sample_index":
                index,

            "g6_temporal_stratum":
                metric[
                    "g6_temporal_stratum"
                ].strip(),

            "conj_id":
                metric[
                    "conj_id"
                ].strip(),

            "final_status":
                metric[
                    "final_status"
                ].strip(),

            "evaluable":
                evaluable,

            "valid_associated":
                valid,

            "replay_time_utc":
                row1[
                    "replay_time_utc"
                ].strip(),

            "obj1_NORAD_CAT_ID":
                row1[
                    "NORAD_CAT_ID"
                ].strip(),

            "obj2_NORAD_CAT_ID":
                row2[
                    "NORAD_CAT_ID"
                ].strip(),

            "obj1_state_age_days":
                state1,

            "obj2_state_age_days":
                state2,

            "pair_max_state_age_days":
                pair_state_age,

            "age_bin":
                age_id,

            "age_bin_label":
                age_label,

            "obj1_publication_age_days":
                pub1,

            "obj2_publication_age_days":
                pub2,

            "pair_max_publication_age_days":
                pair_publication_age,

            "obj1_propagation_span_days":
                propagation1,

            "obj2_propagation_span_days":
                propagation2,

            "pair_max_propagation_span_days":
                pair_propagation,

            "absolute_miss_distance_error_km":
                ed,

            "miss_distance_covered":
                ed_covered,

            "absolute_tca_error_s":
                et,
        }
    )


if sum(
    row[
        "evaluable"
    ]
    for row in event_rows
) != EXPECTED_EVALUABLE:
    raise AnalysisError(
        "evaluable count mismatch"
    )


if sum(
    row[
        "valid_associated"
    ]
    for row in event_rows
) != EXPECTED_VALID:
    raise AnalysisError(
        "valid-associated count mismatch"
    )


# ------------------------------------------------------------
# Prespecified age-stratum sensitivity
# ------------------------------------------------------------

age_rows = []

for age_id, age_label in AGE_BINS:

    subset = [
        row
        for row in event_rows
        if row[
            "age_bin"
        ] == age_id
    ]

    total = len(
        subset
    )

    evaluable = sum(
        row[
            "evaluable"
        ]
        for row in subset
    )

    valid = sum(
        row[
            "valid_associated"
        ]
        for row in subset
    )

    eval_lo, eval_hi = wilson(
        evaluable,
        total,
    )

    valid_lo, valid_hi = wilson(
        valid,
        total,
    )

    errors = [
        row[
            "absolute_miss_distance_error_km"
        ]
        for row in subset
        if row[
            "absolute_miss_distance_error_km"
        ] is not None
    ]

    ed = error_summary(
        errors
    )

    age_rows.append(
        {
            "age_bin":
                age_id,

            "age_bin_label":
                age_label,

            "total_events":
                total,

            "evaluable_count":
                evaluable,

            "evaluable_rate":
                (
                    evaluable / total
                    if total
                    else None
                ),

            "evaluable_wilson_95_lower":
                eval_lo,

            "evaluable_wilson_95_upper":
                eval_hi,

            "valid_associated_count":
                valid,

            "valid_association_rate":
                (
                    valid / total
                    if total
                    else None
                ),

            "valid_association_wilson_95_lower":
                valid_lo,

            "valid_association_wilson_95_upper":
                valid_hi,

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


if sum(
    row[
        "total_events"
    ]
    for row in age_rows
) != EXPECTED_EVENTS:
    raise AnalysisError(
        "age-stratum total does not equal frozen sample"
    )


if sum(
    row[
        "Ed_denominator"
    ]
    for row in age_rows
) != EXPECTED_VALID:
    raise AnalysisError(
        "age-stratum E_d denominator does not equal VALID_ASSOCIATED count"
    )


# ------------------------------------------------------------
# State-age association: inherited exact G5 procedure
# ------------------------------------------------------------

valid_rows = [
    row
    for row in event_rows
    if row[
        "valid_associated"
    ]
]

rho, rho_n = spearman_value(
    valid_rows,
    "pair_max_state_age_days",
)

lower, upper, valid_boot = (
    bootstrap_state_age_spearman(
        valid_rows
    )
)


quality_rows = [
    {
        "scope":
            "ALL_G7_G6_SAMPLE",

        "temporal_stratum":
            "ALL",

        "quality_variable":
            "pair_max_state_age_days",

        "n":
            rho_n,

        "spearman_rho":
            rho,

        "bootstrap_95_lower":
            lower,

        "bootstrap_95_upper":
            upper,

        "bootstrap_replicates_requested":
            BOOTSTRAP_REPLICATES,

        "bootstrap_replicates_valid":
            valid_boot,

        "bootstrap_seed":
            BOOTSTRAP_SEED,
    }
]


for stratum in TEMPORAL:

    subset = [
        row
        for row in valid_rows
        if row[
            "g6_temporal_stratum"
        ] == stratum
    ]

    stratum_rho, stratum_n = (
        spearman_value(
            subset,
            "pair_max_state_age_days",
        )
    )

    quality_rows.append(
        {
            "scope":
                "TEMPORAL_STRATUM",

            "temporal_stratum":
                stratum,

            "quality_variable":
                "pair_max_state_age_days",

            "n":
                stratum_n,

            "spearman_rho":
                stratum_rho,

            "bootstrap_95_lower":
                None,

            "bootstrap_95_upper":
                None,

            "bootstrap_replicates_requested":
                0,

            "bootstrap_replicates_valid":
                0,

            "bootstrap_seed":
                None,
        }
    )


# ------------------------------------------------------------
# Descriptive quality distributions
# ------------------------------------------------------------

pair_state = [
    row[
        "pair_max_state_age_days"
    ]
    for row in event_rows
]

pair_publication = [
    row[
        "pair_max_publication_age_days"
    ]
    for row in event_rows
]

pair_propagation = [
    row[
        "pair_max_propagation_span_days"
    ]
    for row in valid_rows
]


def distribution(values):

    values = [
        finite(value)
        for value in values
        if value is not None
    ]

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

        "minimum":
            min(values),

        "maximum":
            max(values),
    }


age_excluding_nominal = sum(
    (
        row[
            "Ed_wilson_95_lower"
        ]
        is not None
        and row[
            "Ed_wilson_95_upper"
        ]
        is not None
        and not (
            row[
                "Ed_wilson_95_lower"
            ]
            <= 0.90
            <= row[
                "Ed_wilson_95_upper"
            ]
        )
    )
    for row in age_rows
)


# ------------------------------------------------------------
# Write outputs
# ------------------------------------------------------------

event_fields = [
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "final_status",
    "evaluable",
    "valid_associated",
    "replay_time_utc",
    "obj1_NORAD_CAT_ID",
    "obj2_NORAD_CAT_ID",
    "obj1_state_age_days",
    "obj2_state_age_days",
    "pair_max_state_age_days",
    "age_bin",
    "age_bin_label",
    "obj1_publication_age_days",
    "obj2_publication_age_days",
    "pair_max_publication_age_days",
    "obj1_propagation_span_days",
    "obj2_propagation_span_days",
    "pair_max_propagation_span_days",
    "absolute_miss_distance_error_km",
    "miss_distance_covered",
    "absolute_tca_error_s",
]

age_fields = list(
    age_rows[0].keys()
)

quality_fields = [
    "scope",
    "temporal_stratum",
    "quality_variable",
    "n",
    "spearman_rho",
    "bootstrap_95_lower",
    "bootstrap_95_upper",
    "bootstrap_replicates_requested",
    "bootstrap_replicates_valid",
    "bootstrap_seed",
]


write_csv(
    EVENT_OUT,
    event_fields,
    event_rows,
)

write_csv(
    AGE_OUT,
    age_fields,
    age_rows,
)

write_csv(
    QUALITY_OUT,
    quality_fields,
    quality_rows,
)


summary = {
    "status":
        "G7A_ZERO_PROPAGATION_QUALITY_ANALYSIS_PASS",

    "analysis_role":
        "POST_CONFIRMATORY_DESCRIPTIVE_ROBUSTNESS",

    "events":
        EXPECTED_EVENTS,

    "object_selection_rows":
        EXPECTED_OBJECT_ROWS,

    "evaluable_events":
        EXPECTED_EVALUABLE,

    "VALID_ASSOCIATED_events":
        EXPECTED_VALID,

    "age_bins":
        [
            {
                "age_bin":
                    row[
                        "age_bin"
                    ],

                "age_bin_label":
                    row[
                        "age_bin_label"
                    ],

                "total_events":
                    row[
                        "total_events"
                    ],

                "valid_associated":
                    row[
                        "valid_associated_count"
                    ],

                "Ed_successes":
                    row[
                        "Ed_successes"
                    ],

                "Ed_denominator":
                    row[
                        "Ed_denominator"
                    ],

                "Ed_coverage":
                    row[
                        "Ed_coverage"
                    ],

                "Ed_wilson_95_lower":
                    row[
                        "Ed_wilson_95_lower"
                    ],

                "Ed_wilson_95_upper":
                    row[
                        "Ed_wilson_95_upper"
                    ],
            }
            for row in age_rows
        ],

    "age_strata_excluding_nominal_0p90":
        age_excluding_nominal,

    "state_age_association": {
        "n":
            rho_n,

        "spearman_rho":
            rho,

        "bootstrap_95_lower":
            lower,

        "bootstrap_95_upper":
            upper,

        "bootstrap_replicates_requested":
            BOOTSTRAP_REPLICATES,

        "bootstrap_replicates_valid":
            valid_boot,

        "bootstrap_seed":
            BOOTSTRAP_SEED,

        "interpretation":
            "descriptive noncausal post-confirmatory association",
    },

    "pair_max_state_age_days":
        distribution(
            pair_state
        ),

    "pair_max_publication_age_days":
        distribution(
            pair_publication
        ),

    "pair_max_propagation_span_days_VALID_ASSOCIATED":
        distribution(
            pair_propagation
        ),

    "negative_state_age_objects":
        negative_state_age_objects,

    "negative_state_age_events":
        negative_state_age_events,

    "history_dependent_variables_deferred": [
        "publication_count_14d_30d_60d",
        "distinct_state_count_14d_30d_60d",
        "median_publication_cadence_14d_30d_60d",
        "maximum_publication_gap_14d_30d_60d",
        "pair_min_distinct_state_count",
        "pair_max_recent_gap",
    ],

    "raw_GP_HISTORY_rows_scanned":
        0,

    "raw_GP_HISTORY_files_opened":
        0,

    "orbital_propagation_performed":
        False,

    "candidate_generation_performed":
        False,

    "association_recomputed":
        False,

    "CAL0_refit_performed":
        False,

    "method_retuning_performed":
        False,

    "network_requests":
        0,

    "frozen_input_hashes": {
        path.relative_to(ROOT).as_posix():
            expected

        for path, expected
        in EXPECTED_HASHES.items()
    },

    "output_hashes": {
        EVENT_OUT.relative_to(ROOT).as_posix():
            sha256(
                EVENT_OUT
            ),

        AGE_OUT.relative_to(ROOT).as_posix():
            sha256(
                AGE_OUT
            ),

        QUALITY_OUT.relative_to(ROOT).as_posix():
            sha256(
                QUALITY_OUT
            ),
    },

    "next_safe_action":
        "INDEPENDENT_AUDIT_THEN_FREEZE_G7A_BEFORE_RAW_HISTORY_ANALYSIS",
}


with SUMMARY_OUT.open(
    "w",
    encoding="utf-8",
    newline="\n",
) as stream:

    json.dump(
        summary,
        stream,
        indent=2,
        sort_keys=True,
    )

    stream.write("\n")


print(
    json.dumps(
        {
            **summary,

            "summary_sha256":
                sha256(
                    SUMMARY_OUT
                ),
        },
        indent=2,
        sort_keys=True,
    )
)