#!/usr/bin/env python3

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np


ROOT = Path.cwd()

LOCK = ROOT / "docs/research/G7_Robustness_Sensitivity_Analysis_Lock.md"
SAMPLE = ROOT / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"

CANDIDATES = ROOT / "results/g6/G6_PreOutcome_Candidates.csv"
FROZEN_ASSOC = ROOT / "results/g6/G6_Reference_TCA_Association.csv"

G6_EVENT = ROOT / "results/g6/G6_Confirmatory_Event_Metrics.csv"
G6_SUMMARY = ROOT / "results/g6/G6_Confirmatory_Metric_Summary.json"

TRACSS = (
    ROOT
    / "data/raw/g2/IVV_Releasable_Dataset_SFSH_DiscreteHBR.csv.gz"
)

EVENT_OUT = ROOT / "results/g7/G7C_Association_Tolerance_Event.csv"
SUMMARY_CSV = ROOT / "results/g7/G7C_Association_Tolerance_Summary.csv"
SUMMARY_JSON = ROOT / "results/g7/G7C_Association_Tolerance_Summary.json"


EXPECTED_HASHES = {
    LOCK:
        "9323799c51520da1d9bf39a386b7b9ff3c9dbd171dd8d175dca3408efae004b6",

    SAMPLE:
        "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef",

    CANDIDATES:
        "21b8a9f5c8aab4b3427fad5cd3f3c2823ffc0219bd2a36aeccafc6a0aaa80e36",

    FROZEN_ASSOC:
        "31327a65448908791928aaa41a822f02086f97662c2c89ed4ea337fec2cfa622",

    G6_EVENT:
        "e92553698deba0643e5c11ad48ddccab33c2f9a0fd9ce4298a6ee167860c0a79",

    G6_SUMMARY:
        "0b057d16ee25ce8beb6a729fc51d0aa330f980375bb23dcb7a5be51160dbd976",

    TRACSS:
        "b8e49b9c40f974e5ff37e26ade3a855813436bb4c6dab4d7ddeccc8ff5752d39",
}


EXPECTED_EVENTS = 6927
EXPECTED_CANDIDATE_READY = 6906
EXPECTED_CANDIDATE_ROWS = 929821

EXPECTED_G6_VALID = 6839
EXPECTED_G6_UNASSOCIATED = 67
EXPECTED_G6_REFINEMENT_FAILURE = 20
EXPECTED_G6_PROPAGATION_FAILURE = 1

EXPECTED_REFERENCE_ROWS = 283595

MISS_RADIUS_KM = 78.75554652312711
TCA_RADIUS_S = 7.105892

Z95 = 1.959963984540054

TOLERANCES_MIN = (
    5,
    15,
    30,
    60,
)


class G7CError(RuntimeError):
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


def parse_utc(value: str) -> datetime:

    text = str(value).strip()

    if not text:
        raise G7CError("empty timestamp")

    text = text.replace(" ", "T")

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    try:
        result = datetime.fromisoformat(text)
    except ValueError as exc:
        raise G7CError(
            f"malformed timestamp: {value!r}"
        ) from exc

    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)

    return result.astimezone(UTC)


def iso_utc(value: datetime) -> str:

    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def parse_bool(value: str) -> bool:

    text = str(value).strip().upper()

    if text == "TRUE":
        return True

    if text == "FALSE":
        return False

    raise G7CError(
        f"invalid boolean: {value!r}"
    )


def finite(value) -> float:

    result = float(value)

    if not math.isfinite(result):
        raise G7CError(
            f"nonfinite value: {value!r}"
        )

    return result


def wilson(successes: int, denominator: int):

    if denominator <= 0:
        return None, None

    if not 0 <= successes <= denominator:
        raise G7CError(
            "invalid Wilson count"
        )

    p = successes / denominator

    z2 = Z95 * Z95

    scale = (
        1.0
        + z2 / denominator
    )

    center = (
        p
        + z2
        / (
            2.0
            * denominator
        )
    ) / scale

    half = (
        Z95
        / scale
        * math.sqrt(
            p
            * (
                1.0 - p
            )
            / denominator
            + z2
            / (
                4.0
                * denominator
                * denominator
            )
        )
    )

    return (
        max(
            0.0,
            center - half,
        ),
        min(
            1.0,
            center + half,
        ),
    )


def quantile(values, q):

    if not values:
        return None

    return float(
        np.quantile(
            np.asarray(
                values,
                dtype=float,
            ),
            q,
        )
    )


def distribution(values):

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
        }

    return {
        "n":
            len(values),

        "median":
            quantile(
                values,
                0.50,
            ),

        "p90":
            quantile(
                values,
                0.90,
            ),

        "p95":
            quantile(
                values,
                0.95,
            ),

        "p99":
            quantile(
                values,
                0.99,
            ),

        "maximum":
            max(values),
    }


def close(actual, expected, label, tol=1.0e-12):

    if not math.isclose(
        float(actual),
        float(expected),
        rel_tol=0.0,
        abs_tol=tol,
    ):
        raise G7CError(
            f"{label}: "
            f"{actual!r} != {expected!r}"
        )


def fmt(value):

    if value is None:
        return ""

    if isinstance(value, bool):
        return (
            "TRUE"
            if value
            else "FALSE"
        )

    if isinstance(value, float):

        if not math.isfinite(value):
            raise G7CError(
                "cannot serialize nonfinite float"
            )

        return format(
            value,
            ".17g",
        )

    return str(value)


def write_csv(
    path,
    fields,
    rows,
):

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

        for row in rows:

            writer.writerow(
                {
                    field:
                        fmt(
                            row.get(field)
                        )

                    for field in fields
                }
            )


# ============================================================
# 1. Frozen-input integrity
# ============================================================

for path, expected in EXPECTED_HASHES.items():

    if not path.is_file():
        raise G7CError(
            f"missing frozen input: {path}"
        )

    actual = sha256(path)

    if actual != expected:
        raise G7CError(
            f"input SHA mismatch: {path}"
        )


# ============================================================
# 2. Load frozen sample
# ============================================================

sample = {}
conj_to_index = {}

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
            raise G7CError(
                "sample ordering changed"
            )

        conj_id = (
            row[
                "conj_id"
            ].strip()
        )

        if conj_id in conj_to_index:
            raise G7CError(
                "duplicate sample conjunction ID"
            )

        reference_tca = parse_utc(
            row[
                "reference_tca_utc"
            ]
        )

        sample[index] = {
            "final_g6_sample_index":
                index,

            "g6_temporal_stratum":
                row[
                    "g6_temporal_stratum"
                ].strip(),

            "conj_id":
                conj_id,

            "reference_tca_utc":
                reference_tca,
        }

        conj_to_index[
            conj_id
        ] = index


if len(sample) != EXPECTED_EVENTS:
    raise G7CError(
        "sample event count mismatch"
    )


# ============================================================
# 3. Load frozen G6 association result
# ============================================================

frozen_assoc = {}

with FROZEN_ASSOC.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    for row in reader:

        index = int(
            row[
                "final_g6_sample_index"
            ]
        )

        frozen_assoc[
            index
        ] = row


if len(frozen_assoc) != EXPECTED_EVENTS:
    raise G7CError(
        "frozen association row count mismatch"
    )


status_counts = Counter(
    row[
        "final_status"
    ].strip()
    for row in frozen_assoc.values()
)


expected_status_counts = {
    "VALID_ASSOCIATED":
        EXPECTED_G6_VALID,

    "UNASSOCIATED_REFERENCE_EVENT":
        EXPECTED_G6_UNASSOCIATED,

    "UNRECOVERED_REFINEMENT_FAILURE":
        EXPECTED_G6_REFINEMENT_FAILURE,

    "PROPAGATION_FAILURE":
        EXPECTED_G6_PROPAGATION_FAILURE,
}


if dict(status_counts) != expected_status_counts:
    raise G7CError(
        f"frozen G6 status census changed: "
        f"{dict(status_counts)}"
    )


ready_indices = {
    index
    for index, row
    in frozen_assoc.items()
    if row[
        "final_status"
    ].strip()
    in {
        "VALID_ASSOCIATED",
        "UNASSOCIATED_REFERENCE_EVENT",
    }
}


if len(ready_indices) != EXPECTED_CANDIDATE_READY:
    raise G7CError(
        "candidate-ready frozen census mismatch"
    )


# ============================================================
# 4. Load frozen G6 confirmatory event metrics
# ============================================================

g6_event = {}

with G6_EVENT.open(
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
            raise G7CError(
                "G6 metric ordering changed"
            )

        g6_event[
            index
        ] = row


if len(g6_event) != EXPECTED_EVENTS:
    raise G7CError(
        "G6 metric row count mismatch"
    )


g6_summary = json.loads(
    G6_SUMMARY.read_text(
        encoding="utf-8"
    )
)


# ============================================================
# 5. SINGLE PASS OVER FROZEN CANDIDATES
#    Keep only the nearest candidate under frozen ordering.
# ============================================================

best = {}
candidate_counts = Counter()

candidate_rows_scanned = 0


with CANDIDATES.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    for row in reader:

        candidate_rows_scanned += 1

        index = int(
            row[
                "final_g6_sample_index"
            ]
        )

        if index not in sample:
            raise G7CError(
                "candidate references non-sample event"
            )

        candidate_index = int(
            row[
                "candidate_index"
            ]
        )

        candidate_time = parse_utc(
            row[
                "candidate_time_utc"
            ]
        )

        distance_km = finite(
            row[
                "distance_km"
            ]
        )

        reference_tca = sample[
            index
        ][
            "reference_tca_utc"
        ]

        offset_s = abs(
            (
                candidate_time
                - reference_tca
            ).total_seconds()
        )

        # Frozen G4/G6 association ordering:
        # 1) min absolute TCA offset
        # 2) earlier candidate time
        # 3) smaller candidate distance
        # 4) lower stable candidate index
        key = (
            offset_s,
            candidate_time,
            distance_km,
            candidate_index,
        )

        candidate_counts[
            index
        ] += 1

        current = best.get(
            index
        )

        if (
            current is None
            or key < current[
                "key"
            ]
        ):
            best[index] = {
                "key":
                    key,

                "candidate_index":
                    candidate_index,

                "candidate_time_utc":
                    candidate_time,

                "distance_km":
                    distance_km,

                "absolute_reference_tca_offset_s":
                    offset_s,
            }


if candidate_rows_scanned != EXPECTED_CANDIDATE_ROWS:
    raise G7CError(
        "candidate-row census mismatch"
    )


if len(best) != EXPECTED_CANDIDATE_READY:
    raise G7CError(
        "candidate-bearing event census mismatch"
    )


if set(best) != ready_indices:
    raise G7CError(
        "candidate-bearing event set differs from "
        "frozen candidate-ready event set"
    )


# ============================================================
# 6. Read protected reference source once
# ============================================================

reference = {}

reference_rows_scanned = 0


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

    if not required.issubset(
        set(
            reader.fieldnames
            or ()
        )
    ):
        raise G7CError(
            "TraCSS reference schema changed"
        )


    for row in reader:

        reference_rows_scanned += 1

        conj_id = (
            row[
                "conj_id"
            ].strip()
        )

        index = conj_to_index.get(
            conj_id
        )

        if index is None:
            continue

        if index in reference:
            raise G7CError(
                f"duplicate reference conjunction: {conj_id}"
            )

        epoch = parse_utc(
            row[
                "epoch"
            ]
        )

        expected_epoch = (
            sample[
                index
            ][
                "reference_tca_utc"
            ]
        )

        if epoch != expected_epoch:
            raise G7CError(
                f"reference epoch mismatch: {conj_id}"
            )

        reference[
            index
        ] = {
            "reference_tca_utc":
                epoch,

            "reference_miss_distance_km":
                finite(
                    row[
                        "min_range"
                    ]
                ),
        }


if reference_rows_scanned != EXPECTED_REFERENCE_ROWS:
    raise G7CError(
        "protected reference source row census mismatch"
    )


if len(reference) != EXPECTED_EVENTS:
    raise G7CError(
        "protected reference source missing sample events"
    )


# ============================================================
# 7. Evaluate frozen nearest candidate under each tolerance
# ============================================================

event_rows = []
summary_rows = []

baseline_associated = None


for tolerance_min in TOLERANCES_MIN:

    tolerance_s = (
        tolerance_min
        * 60.0
    )

    associated_count = 0

    ed_values = []
    et_values = []

    ed_successes = 0
    et_successes = 0


    for index in range(
        1,
        EXPECTED_EVENTS + 1,
    ):

        sample_row = (
            sample[
                index
            ]
        )

        inherited = (
            frozen_assoc[
                index
            ]
        )

        nearest = best.get(
            index
        )

        candidate_available = (
            nearest is not None
        )

        if candidate_available:

            associated = (
                nearest[
                    "absolute_reference_tca_offset_s"
                ]
                <= tolerance_s
            )

        else:
            associated = False


        sensitivity_status = (
            "ASSOCIATED"
            if associated
            else (
                "UNASSOCIATED"
                if candidate_available
                else "NO_CANDIDATE_AVAILABLE"
            )
        )


        predicted_tca = None
        predicted_distance = None

        signed_t = None
        abs_t = None
        tca_covered = None

        reference_distance = None
        signed_d = None
        abs_d = None
        md_covered = None


        if associated:

            associated_count += 1

            predicted_tca = (
                nearest[
                    "candidate_time_utc"
                ]
            )

            predicted_distance = (
                nearest[
                    "distance_km"
                ]
            )

            reference_tca = (
                reference[
                    index
                ][
                    "reference_tca_utc"
                ]
            )

            reference_distance = (
                reference[
                    index
                ][
                    "reference_miss_distance_km"
                ]
            )


            signed_t = (
                predicted_tca
                - reference_tca
            ).total_seconds()

            abs_t = abs(
                signed_t
            )

            signed_d = (
                predicted_distance
                - reference_distance
            )

            abs_d = abs(
                signed_d
            )

            tca_covered = (
                abs_t
                <= TCA_RADIUS_S
            )

            md_covered = (
                abs_d
                <= MISS_RADIUS_KM
            )


            et_values.append(
                abs_t
            )

            ed_values.append(
                abs_d
            )

            et_successes += int(
                tca_covered
            )

            ed_successes += int(
                md_covered
            )


        # ----------------------------------------------------
        # Exact 5-minute reproduction check, event by event.
        # ----------------------------------------------------

        if tolerance_min == 5:

            frozen_valid = (
                inherited[
                    "final_status"
                ].strip()
                == "VALID_ASSOCIATED"
            )

            if associated != frozen_valid:
                raise G7CError(
                    f"5-min association mismatch: event {index}"
                )


            if frozen_valid:

                frozen_candidate_index = int(
                    inherited[
                        "selected_candidate_index"
                    ]
                )

                if (
                    nearest[
                        "candidate_index"
                    ]
                    != frozen_candidate_index
                ):
                    raise G7CError(
                        f"5-min selected-candidate mismatch: "
                        f"event {index}"
                    )


                metric = g6_event[
                    index
                ]

                if not parse_bool(
                    metric[
                        "valid_associated"
                    ]
                ):
                    raise G7CError(
                        "frozen G6 metric validity mismatch"
                    )


                if (
                    parse_utc(
                        metric[
                            "predicted_tca_utc"
                        ]
                    )
                    != predicted_tca
                ):
                    raise G7CError(
                        f"5-min predicted TCA mismatch: {index}"
                    )


                close(
                    metric[
                        "predicted_miss_distance_km"
                    ],
                    predicted_distance,
                    "5-min predicted miss distance",
                    tol=1.0e-10,
                )

                close(
                    metric[
                        "reference_miss_distance_km"
                    ],
                    reference_distance,
                    "5-min reference miss distance",
                    tol=1.0e-10,
                )

                close(
                    metric[
                        "absolute_tca_error_s"
                    ],
                    abs_t,
                    "5-min E_t",
                    tol=1.0e-9,
                )

                close(
                    metric[
                        "absolute_miss_distance_error_km"
                    ],
                    abs_d,
                    "5-min E_d",
                    tol=1.0e-10,
                )


        event_rows.append(
            {
                "final_g6_sample_index":
                    index,

                "g6_temporal_stratum":
                    sample_row[
                        "g6_temporal_stratum"
                    ],

                "conj_id":
                    sample_row[
                        "conj_id"
                    ],

                "tolerance_min":
                    tolerance_min,

                "inherited_g6_final_status":
                    inherited[
                        "final_status"
                    ].strip(),

                "candidate_available":
                    candidate_available,

                "candidate_count":
                    candidate_counts.get(
                        index,
                        0,
                    ),

                "sensitivity_status":
                    sensitivity_status,

                "associated":
                    associated,

                "nearest_candidate_index":
                    (
                        nearest[
                            "candidate_index"
                        ]
                        if nearest
                        else None
                    ),

                "nearest_candidate_time_utc":
                    (
                        iso_utc(
                            nearest[
                                "candidate_time_utc"
                            ]
                        )
                        if nearest
                        else None
                    ),

                "nearest_candidate_distance_km":
                    (
                        nearest[
                            "distance_km"
                        ]
                        if nearest
                        else None
                    ),

                "absolute_reference_tca_offset_s":
                    (
                        nearest[
                            "absolute_reference_tca_offset_s"
                        ]
                        if nearest
                        else None
                    ),

                "predicted_tca_utc":
                    (
                        iso_utc(
                            predicted_tca
                        )
                        if predicted_tca
                        else None
                    ),

                "reference_tca_utc":
                    iso_utc(
                        sample_row[
                            "reference_tca_utc"
                        ]
                    ),

                "signed_tca_residual_s":
                    signed_t,

                "absolute_tca_error_s":
                    abs_t,

                "tca_radius_s":
                    (
                        TCA_RADIUS_S
                        if associated
                        else None
                    ),

                "tca_covered":
                    tca_covered,

                "predicted_miss_distance_km":
                    predicted_distance,

                "reference_miss_distance_km":
                    reference_distance,

                "signed_miss_distance_residual_km":
                    signed_d,

                "absolute_miss_distance_error_km":
                    abs_d,

                "miss_distance_radius_km":
                    (
                        MISS_RADIUS_KM
                        if associated
                        else None
                    ),

                "miss_distance_covered":
                    md_covered,
            }
        )


    association_lo, association_hi = (
        wilson(
            associated_count,
            EXPECTED_EVENTS,
        )
    )


    ed_lo, ed_hi = wilson(
        ed_successes,
        associated_count,
    )

    et_lo, et_hi = wilson(
        et_successes,
        associated_count,
    )


    ed_dist = distribution(
        ed_values
    )

    et_dist = distribution(
        et_values
    )


    if baseline_associated is None:
        baseline_associated = (
            associated_count
        )


    summary_rows.append(
        {
            "tolerance_min":
                tolerance_min,

            "total_events":
                EXPECTED_EVENTS,

            "candidate_ready_events":
                EXPECTED_CANDIDATE_READY,

            "no_candidate_events":
                (
                    EXPECTED_EVENTS
                    - EXPECTED_CANDIDATE_READY
                ),

            "associated_events":
                associated_count,

            "unassociated_candidate_ready_events":
                (
                    EXPECTED_CANDIDATE_READY
                    - associated_count
                ),

            "newly_associated_vs_5min":
                (
                    associated_count
                    - baseline_associated
                ),

            "association_rate_all_events":
                (
                    associated_count
                    / EXPECTED_EVENTS
                ),

            "association_wilson_95_lower":
                association_lo,

            "association_wilson_95_upper":
                association_hi,

            "association_rate_given_candidate_ready":
                (
                    associated_count
                    / EXPECTED_CANDIDATE_READY
                ),

            "Ed_successes":
                ed_successes,

            "Ed_denominator":
                associated_count,

            "Ed_coverage":
                (
                    ed_successes
                    / associated_count
                ),

            "Ed_wilson_95_lower":
                ed_lo,

            "Ed_wilson_95_upper":
                ed_hi,

            "Ed_absolute_deviation_from_0p90":
                abs(
                    (
                        ed_successes
                        / associated_count
                    )
                    - 0.90
                ),

            "Ed_median_km":
                ed_dist[
                    "median"
                ],

            "Ed_p90_km":
                ed_dist[
                    "p90"
                ],

            "Ed_p95_km":
                ed_dist[
                    "p95"
                ],

            "Ed_p99_km":
                ed_dist[
                    "p99"
                ],

            "Ed_maximum_km":
                ed_dist[
                    "maximum"
                ],

            "Et_successes":
                et_successes,

            "Et_denominator":
                associated_count,

            "Et_coverage":
                (
                    et_successes
                    / associated_count
                ),

            "Et_wilson_95_lower":
                et_lo,

            "Et_wilson_95_upper":
                et_hi,

            "Et_median_s":
                et_dist[
                    "median"
                ],

            "Et_p90_s":
                et_dist[
                    "p90"
                ],

            "Et_p95_s":
                et_dist[
                    "p95"
                ],

            "Et_p99_s":
                et_dist[
                    "p99"
                ],

            "Et_maximum_s":
                et_dist[
                    "maximum"
                ],
        }
    )


# ============================================================
# 8. Exact frozen 5-minute pooled reproduction
# ============================================================

five = summary_rows[0]

if five["tolerance_min"] != 5:
    raise G7CError(
        "5-min row is not first"
    )

if (
    five[
        "associated_events"
    ]
    != EXPECTED_G6_VALID
):
    raise G7CError(
        "5-min associated census mismatch"
    )


primary = g6_summary[
    "primary_Ed"
]

secondary = g6_summary[
    "secondary_Et"
]


if (
    five[
        "Ed_successes"
    ]
    != 5609
):
    raise G7CError(
        "5-min E_d successes mismatch"
    )


if (
    five[
        "Et_successes"
    ]
    != 5125
):
    raise G7CError(
        "5-min E_t successes mismatch"
    )


for actual, expected, label in (
    (
        five[
            "Ed_coverage"
        ],
        primary[
            "observed_coverage"
        ],
        "5-min pooled E_d coverage",
    ),

    (
        five[
            "Ed_wilson_95_lower"
        ],
        primary[
            "wilson_95_lower"
        ],
        "5-min pooled E_d Wilson lower",
    ),

    (
        five[
            "Ed_wilson_95_upper"
        ],
        primary[
            "wilson_95_upper"
        ],
        "5-min pooled E_d Wilson upper",
    ),

    (
        five[
            "Ed_median_km"
        ],
        primary[
            "median"
        ],
        "5-min E_d median",
    ),

    (
        five[
            "Ed_p90_km"
        ],
        primary[
            "p90"
        ],
        "5-min E_d P90",
    ),

    (
        five[
            "Ed_p95_km"
        ],
        primary[
            "p95"
        ],
        "5-min E_d P95",
    ),

    (
        five[
            "Ed_p99_km"
        ],
        primary[
            "p99"
        ],
        "5-min E_d P99",
    ),

    (
        five[
            "Ed_maximum_km"
        ],
        primary[
            "maximum"
        ],
        "5-min E_d maximum",
    ),

    (
        five[
            "Et_coverage"
        ],
        secondary[
            "observed_coverage"
        ],
        "5-min pooled E_t coverage",
    ),

    (
        five[
            "Et_wilson_95_lower"
        ],
        secondary[
            "wilson_95_lower"
        ],
        "5-min pooled E_t Wilson lower",
    ),

    (
        five[
            "Et_wilson_95_upper"
        ],
        secondary[
            "wilson_95_upper"
        ],
        "5-min pooled E_t Wilson upper",
    ),
):

    close(
        actual,
        expected,
        label,
        tol=1.0e-12,
    )


# Association support must be nested as tolerance increases.
associated_counts = [
    row[
        "associated_events"
    ]
    for row in summary_rows
]

if associated_counts != sorted(
    associated_counts
):
    raise G7CError(
        "association support is not monotonic in tolerance"
    )


# ============================================================
# 9. Write deterministic outputs
# ============================================================

event_fields = [
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "tolerance_min",
    "inherited_g6_final_status",
    "candidate_available",
    "candidate_count",
    "sensitivity_status",
    "associated",
    "nearest_candidate_index",
    "nearest_candidate_time_utc",
    "nearest_candidate_distance_km",
    "absolute_reference_tca_offset_s",
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
]


summary_fields = list(
    summary_rows[0].keys()
)


write_csv(
    EVENT_OUT,
    event_fields,
    event_rows,
)

write_csv(
    SUMMARY_CSV,
    summary_fields,
    summary_rows,
)


summary_payload = {
    "status":
        "G7C_ASSOCIATION_TOLERANCE_SENSITIVITY_PASS",

    "analysis_role":
        "POST_CONFIRMATORY_ASSOCIATION_TOLERANCE_SENSITIVITY",

    "events":
        EXPECTED_EVENTS,

    "candidate_ready_events":
        EXPECTED_CANDIDATE_READY,

    "candidate_rows_scanned":
        candidate_rows_scanned,

    "protected_reference_rows_scanned":
        reference_rows_scanned,

    "tolerances_minutes":
        list(
            TOLERANCES_MIN
        ),

    "frozen_primary_tolerance_min":
        5,

    "sensitivity_tolerances_minutes":
        [
            15,
            30,
            60,
        ],

    "association_rule":
        (
            "minimum absolute reference-TCA offset, "
            "then earlier candidate time, "
            "smaller candidate distance, "
            "lower candidate index; "
            "inclusive tolerance threshold"
        ),

    "selected_candidate_identity_changes_with_tolerance":
        False,

    "five_minute_exact_G6_reproduction":
        True,

    "association_support_monotonic":
        True,

    "summary_rows":
        summary_rows,

    "orbital_propagation_performed":
        False,

    "candidate_generation_performed":
        False,

    "GP_HISTORY_accessed":
        False,

    "association_sensitivity_computed_from_frozen_candidates":
        True,

    "CAL0_refit_performed":
        False,

    "method_retuning_performed":
        False,

    "network_requests":
        0,

    "input_hashes": {
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

        SUMMARY_CSV.relative_to(ROOT).as_posix():
            sha256(
                SUMMARY_CSV
            ),
    },

    "interpretation_boundary":
        (
            "All 15/30/60-minute results are post-confirmatory "
            "sensitivity analyses. They do not replace the frozen "
            "5-minute G6 association rule or confirmatory result."
        ),

    "next_safe_action":
        (
            "INDEPENDENT_G7C_AUDIT_BEFORE_FREEZE"
        ),
}


with SUMMARY_JSON.open(
    "w",
    encoding="utf-8",
    newline="\n",
) as stream:

    json.dump(
        summary_payload,
        stream,
        indent=2,
        sort_keys=True,
    )

    stream.write("\n")


print(
    json.dumps(
        {
            **summary_payload,

            "summary_json_sha256":
                sha256(
                    SUMMARY_JSON
                ),
        },
        indent=2,
        sort_keys=True,
    )
)