from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]

LOCK = (
    ROOT
    / "docs/research/G7_Robustness_Sensitivity_Analysis_Lock.md"
)

SAMPLE = (
    ROOT
    / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"
)

TRACSS = (
    ROOT
    / "data/raw/g2/IVV_Releasable_Dataset_SFSH_DiscreteHBR.csv.gz"
)

H5_EVENT = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Event_Status.csv"
)

H5_CAND = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidates.csv"
)

G6_ASSOC = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association.csv"
)

G6_METRIC = (
    ROOT
    / "results/g6/G6_Confirmatory_Event_Metrics.csv"
)

G7C_EVENT = (
    ROOT
    / "results/g7/G7C_Association_Tolerance_Event.csv"
)

G7C_SUMMARY = (
    ROOT
    / "results/g7/G7C_Association_Tolerance_Summary.csv"
)

H3_EVENT = (
    ROOT
    / "results/g7/G7D_H3_Event_Status.csv"
)

H3_CAND = (
    ROOT
    / "results/g7/G7D_H3_Candidates.csv"
)

H7_EVENT = (
    ROOT
    / "results/g7/G7D_H7_Event_Status.csv"
)

H7_CAND = (
    ROOT
    / "results/g7/G7D_H7_Candidates.csv"
)

VALIDATION = (
    ROOT
    / "results/g7/"
      "G7D_Horizon_Association_Offline_Validation.json"
)


EXPECTED = {
    LOCK:
        "9323799c51520da1d9bf39a386b7b9ff3c9dbd171dd8d175dca3408efae004b6",

    SAMPLE:
        "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef",

    H5_EVENT:
        "844a4c3f90a1b5bb738f13911b5e9f99f22bfe72e01bdb43dd6242e292b7b308",

    H5_CAND:
        "21b8a9f5c8aab4b3427fad5cd3f3c2823ffc0219bd2a36aeccafc6a0aaa80e36",

    G6_ASSOC:
        "31327a65448908791928aaa41a822f02086f97662c2c89ed4ea337fec2cfa622",

    G6_METRIC:
        "e92553698deba0643e5c11ad48ddccab33c2f9a0fd9ce4298a6ee167860c0a79",

    G7C_EVENT:
        "f848fbc1327456fa6051c1bafb5f9f602b0f00e2d62ebc6b19857a468f0b57e6",

    G7C_SUMMARY:
        "bc5d66904dc2dfc1ebaaa7ff8a0a6161e3ff26f0a4853a59d5bc6e88ffea6e47",
}


HORIZONS = {
    3: {
        "event": H3_EVENT,
        "cand": H3_CAND,

        "event_sha":
            "5689d7a8b279a6d6a7f7ccd7941d977f2ce9ff04928a29201b82c385c3e94b43",

        "cand_sha":
            "a64c500a73a2b66df44642066337d1cdd039404cc72147ada65f0862dad77aae",

        "candidate_rows":
            558469,

        "ready":
            6915,
    },

    7: {
        "event": H7_EVENT,
        "cand": H7_CAND,

        "event_sha":
            "418348e3389b47462d196ccbc53d0a4a0e90b0c7a4a9f17e93de51a3a75c2595",

        "cand_sha":
            "1f32718dc5607ed3d5b8d6a75c6bdd728fe6518b39e49bba607339cf71793791",

        "candidate_rows":
            1299383,

        "ready":
            6889,
    },
}


N_EVENTS = 6927

H5_READY = 6906

H5_CANDIDATE_ROWS = 929821

ASSOC_TOL_MIN = 5

ASSOC_TOL_S = 300.0

MISS_RADIUS_KM = 78.75554652312711

TCA_RADIUS_S = 7.105892

Z95 = 1.959963984540054

TRACSS_SHA256 = (
    "b8e49b9c40f974e5ff37e26ade3a855813436bb4c6dab4d7ddeccc8ff5752d39"
)

EXPECTED_REFERENCE_ROWS = 283595


class AssociationError(RuntimeError):
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


def utc(value: str) -> datetime:

    text = str(value).strip().replace(
        " ",
        "T",
    )

    if not text:
        raise AssociationError(
            "empty timestamp"
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
        raise AssociationError(
            f"malformed timestamp: {value!r}"
        ) from exc

    if result.tzinfo is None:
        raise AssociationError(
            f"naive timestamp: {value!r}"
        )

    return result.astimezone(
        timezone.utc
    )


def tracss_epoch_utc(value: str) -> datetime:

    """
    Parse the protected TraCSS `epoch` field.

    The frozen G7-C reference path established that timezone-naive
    TraCSS epoch strings are UTC. This source-specific parser
    reproduces that convention without weakening the generic utc()
    parser used for other inputs.
    """

    text = str(value).strip().replace(
        " ",
        "T",
    )

    if not text:
        raise AssociationError(
            "empty TraCSS epoch timestamp"
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
        raise AssociationError(
            f"malformed TraCSS epoch timestamp: {value!r}"
        ) from exc

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=timezone.utc
        )

    return result.astimezone(
        timezone.utc
    )


def finite(value) -> float:

    result = float(value)

    if not math.isfinite(result):
        raise AssociationError(
            f"nonfinite value: {value!r}"
        )

    return result


def boolean(value) -> bool:

    text = str(value).strip().upper()

    if text == "TRUE":
        return True

    if text == "FALSE":
        return False

    raise AssociationError(
        f"invalid boolean: {value!r}"
    )


def close(
    actual,
    expected,
    label: str,
    tol: float = 1.0e-10,
):

    if (
        abs(
            finite(actual)
            - finite(expected)
        )
        > tol
    ):
        raise AssociationError(
            f"{label} mismatch"
        )


def wilson(
    successes: int,
    denominator: int,
):

    if denominator <= 0:
        return None, None

    if not (
        0
        <= successes
        <= denominator
    ):
        raise AssociationError(
            "invalid Wilson count"
        )

    proportion = (
        successes
        / denominator
    )

    z2 = Z95 * Z95

    scale = (
        1.0
        + z2 / denominator
    )

    center = (
        proportion
        + z2 / (
            2.0 * denominator
        )
    ) / scale

    half = (
        Z95
        * math.sqrt(
            proportion
            * (
                1.0
                - proportion
            )
            / denominator
            + z2
            / (
                4.0
                * denominator
                * denominator
            )
        )
        / scale
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


def distribution(values):

    array = np.asarray(
        values,
        dtype=float,
    )

    if array.size == 0:
        raise AssociationError(
            "empty error distribution"
        )

    return {
        "median":
            float(
                np.quantile(
                    array,
                    0.50,
                )
            ),

        "p90":
            float(
                np.quantile(
                    array,
                    0.90,
                )
            ),

        "p95":
            float(
                np.quantile(
                    array,
                    0.95,
                )
            ),

        "p99":
            float(
                np.quantile(
                    array,
                    0.99,
                )
            ),

        "maximum":
            float(
                np.max(
                    array
                )
            ),
    }


def indexed(path: Path):

    rows = {}

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        reader = csv.DictReader(
            stream
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

            if (
                index
                != expected_index
            ):
                raise AssociationError(
                    "event ordering changed: "
                    f"{path.name}"
                )

            if index in rows:
                raise AssociationError(
                    "duplicate event index: "
                    f"{path.name}"
                )

            rows[index] = row

    if len(rows) != N_EVENTS:
        raise AssociationError(
            "event census changed: "
            f"{path.name}"
        )

    return rows


def g7c_five():

    rows = {}

    with G7C_EVENT.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        reader = csv.DictReader(
            stream
        )

        for row in reader:

            if (
                int(
                    row[
                        "tolerance_min"
                    ]
                )
                != ASSOC_TOL_MIN
            ):
                continue

            index = int(
                row[
                    "final_g6_sample_index"
                ]
            )

            if index in rows:
                raise AssociationError(
                    "duplicate G7-C 5-min event"
                )

            rows[index] = row

    if len(rows) != N_EVENTS:
        raise AssociationError(
            "G7-C 5-min census mismatch"
        )

    return rows


def scan(
    event_rows,
    reference_rows,
    candidate_path: Path,
    expected_rows: int,
):

    best = {}

    counts = Counter()

    scanned = 0

    with candidate_path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        reader = csv.DictReader(
            stream
        )

        for row in reader:

            scanned += 1

            index = int(
                row[
                    "final_g6_sample_index"
                ]
            )

            event = event_rows.get(
                index
            )

            if event is None:
                raise AssociationError(
                    "candidate references "
                    "non-sample event"
                )

            if (
                event[
                    "preoutcome_status"
                ].strip()
                != "CANDIDATE_SET_READY"
            ):
                raise AssociationError(
                    "candidate belongs to "
                    "non-ready event: "
                    f"{index}"
                )

            candidate_index = int(
                row[
                    "candidate_index"
                ]
            )

            candidate_time = utc(
                row[
                    "candidate_time_utc"
                ]
            )

            distance = finite(
                row[
                    "distance_km"
                ]
            )

            reference_text = (
                reference_rows[
                    index
                ][
                    "reference_tca_utc"
                ].strip()
            )

            if not reference_text:
                raise AssociationError(
                    "candidate-ready event "
                    "lacks frozen reference TCA: "
                    f"{index}"
                )

            reference_time = utc(
                reference_text
            )

            offset = abs(
                (
                    candidate_time
                    - reference_time
                ).total_seconds()
            )

            # Frozen G4/G6/G7-C ordering:
            #
            # 1. minimum absolute TCA offset
            # 2. earlier candidate time
            # 3. smaller candidate distance
            # 4. lower stable candidate index

            key = (
                offset,
                candidate_time,
                distance,
                candidate_index,
            )

            counts[index] += 1

            if (
                index not in best
                or key
                < best[index]["key"]
            ):

                best[index] = {
                    "key":
                        key,

                    "candidate_index":
                        candidate_index,

                    "candidate_time":
                        candidate_time,

                    "distance_km":
                        distance,

                    "offset_s":
                        offset,
                }

    if scanned != expected_rows:
        raise AssociationError(
            "candidate-row census mismatch: "
            f"{scanned}"
        )

    ready = {
        index
        for index, row
        in event_rows.items()
        if (
            row[
                "preoutcome_status"
            ].strip()
            == "CANDIDATE_SET_READY"
        )
    }

    if set(best) != ready:
        raise AssociationError(
            "candidate-bearing event set "
            "differs from ready set"
        )

    for index, row in event_rows.items():

        if (
            counts.get(
                index,
                0,
            )
            != int(
                row[
                    "candidate_count"
                ]
            )
        ):
            raise AssociationError(
                "candidate-count mismatch: "
                f"{index}"
            )

    return (
        best,
        counts,
        scanned,
    )


def evaluate(
    event_rows,
    reference_rows,
    best,
    counts,
    horizon_days: int,
):

    events = {}

    ed_values = []

    et_values = []

    ed_successes = 0

    et_successes = 0

    ready_count = 0

    associated_count = 0

    for index in range(
        1,
        N_EVENTS + 1,
    ):

        pre_status = (
            event_rows[
                index
            ][
                "preoutcome_status"
            ].strip()
        )

        ready = (
            pre_status
            == "CANDIDATE_SET_READY"
        )

        nearest = best.get(
            index
        )

        if ready:

            ready_count += 1

            if nearest is None:
                raise AssociationError(
                    "ready event has no "
                    f"candidate: {index}"
                )

            associated = (
                nearest[
                    "offset_s"
                ]
                <= ASSOC_TOL_S
            )

            association_status = (
                "VALID_ASSOCIATED"
                if associated
                else
                "UNASSOCIATED_REFERENCE_EVENT"
            )

        else:

            if nearest is not None:
                raise AssociationError(
                    "non-ready event has "
                    f"candidate: {index}"
                )

            associated = False

            association_status = (
                pre_status
            )

        result = {
            "horizon_days":
                horizon_days,

            "final_g6_sample_index":
                index,

            "g6_temporal_stratum":
                event_rows[
                    index
                ][
                    "g6_temporal_stratum"
                ].strip(),

            "conj_id":
                event_rows[
                    index
                ][
                    "conj_id"
                ].strip(),

            "preoutcome_status":
                pre_status,

            "candidate_ready":
                ready,

            "candidate_count":
                counts.get(
                    index,
                    0,
                ),

            "association_status":
                association_status,

            "associated":
                associated,

            "selected_candidate_index":
                None,

            "selected_candidate_time_utc":
                None,

            "selected_candidate_distance_km":
                None,

            "absolute_reference_tca_offset_s":
                (
                    nearest[
                        "offset_s"
                    ]
                    if ready
                    else None
                ),

            "signed_tca_residual_s":
                None,

            "absolute_tca_error_s":
                None,

            "tca_covered":
                None,

            "signed_miss_distance_residual_km":
                None,

            "absolute_miss_distance_error_km":
                None,

            "miss_distance_covered":
                None,
        }

        if associated:

            associated_count += 1

            reference_time = utc(
                reference_rows[
                    index
                ][
                    "reference_tca_utc"
                ]
            )

            reference_distance = finite(
                reference_rows[
                    index
                ][
                    "reference_miss_distance_km"
                ]
            )

            signed_t = (
                nearest[
                    "candidate_time"
                ]
                - reference_time
            ).total_seconds()

            abs_t = abs(
                signed_t
            )

            signed_d = (
                nearest[
                    "distance_km"
                ]
                - reference_distance
            )

            abs_d = abs(
                signed_d
            )

            tca_covered = (
                abs_t
                <= TCA_RADIUS_S
            )

            miss_covered = (
                abs_d
                <= MISS_RADIUS_KM
            )

            result.update(
                {
                    "selected_candidate_index":
                        nearest[
                            "candidate_index"
                        ],

                    "selected_candidate_time_utc":
                        nearest[
                            "candidate_time"
                        ],

                    "selected_candidate_distance_km":
                        nearest[
                            "distance_km"
                        ],

                    "signed_tca_residual_s":
                        signed_t,

                    "absolute_tca_error_s":
                        abs_t,

                    "tca_covered":
                        tca_covered,

                    "signed_miss_distance_residual_km":
                        signed_d,

                    "absolute_miss_distance_error_km":
                        abs_d,

                    "miss_distance_covered":
                        miss_covered,
                }
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
                miss_covered
            )

        events[index] = result

    (
        ready_lo,
        ready_hi,
    ) = wilson(
        ready_count,
        N_EVENTS,
    )

    (
        association_lo,
        association_hi,
    ) = wilson(
        associated_count,
        N_EVENTS,
    )

    (
        ed_lo,
        ed_hi,
    ) = wilson(
        ed_successes,
        associated_count,
    )

    (
        et_lo,
        et_hi,
    ) = wilson(
        et_successes,
        associated_count,
    )

    ed_distribution = distribution(
        ed_values
    )

    et_distribution = distribution(
        et_values
    )

    summary = {
        "horizon_days":
            horizon_days,

        "total_events":
            N_EVENTS,

        "candidate_ready_events":
            ready_count,

        "candidate_ready_rate_all_events":
            ready_count
            / N_EVENTS,

        "candidate_ready_wilson_95_lower":
            ready_lo,

        "candidate_ready_wilson_95_upper":
            ready_hi,

        "associated_events":
            associated_count,

        "unassociated_candidate_ready_events":
            (
                ready_count
                - associated_count
            ),

        "association_rate_all_events":
            associated_count
            / N_EVENTS,

        "association_wilson_95_lower":
            association_lo,

        "association_wilson_95_upper":
            association_hi,

        "association_rate_given_candidate_ready":
            (
                associated_count
                / ready_count
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

        "Ed_median_km":
            ed_distribution[
                "median"
            ],

        "Ed_p90_km":
            ed_distribution[
                "p90"
            ],

        "Ed_p95_km":
            ed_distribution[
                "p95"
            ],

        "Ed_p99_km":
            ed_distribution[
                "p99"
            ],

        "Ed_maximum_km":
            ed_distribution[
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
            et_distribution[
                "median"
            ],

        "Et_p90_s":
            et_distribution[
                "p90"
            ],

        "Et_p95_s":
            et_distribution[
                "p95"
            ],

        "Et_p99_s":
            et_distribution[
                "p99"
            ],

        "Et_maximum_s":
            et_distribution[
                "maximum"
            ],
    }

    return (
        events,
        summary,
    )



def load_protected_reference(sample_rows):

    if not TRACSS.is_file():
        raise AssociationError(
            "protected TraCSS reference source missing"
        )

    if sha256(TRACSS) != TRACSS_SHA256:
        raise AssociationError(
            "protected TraCSS reference SHA mismatch"
        )

    conj_to_index = {}

    for index, row in sample_rows.items():

        conj_id = row[
            "conj_id"
        ].strip()

        if not conj_id:
            raise AssociationError(
                f"empty sample conjunction ID: {index}"
            )

        if conj_id in conj_to_index:
            raise AssociationError(
                "duplicate sample conjunction ID"
            )

        if not row[
            "reference_tca_utc"
        ].strip():
            raise AssociationError(
                "sample register lacks reference TCA: "
                f"{index}"
            )

        conj_to_index[
            conj_id
        ] = index

    reference = {}

    rows_scanned = 0

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
            raise AssociationError(
                "protected TraCSS reference schema changed"
            )

        for row in reader:

            rows_scanned += 1

            conj_id = row[
                "conj_id"
            ].strip()

            index = conj_to_index.get(
                conj_id
            )

            if index is None:
                continue

            if index in reference:
                raise AssociationError(
                    "duplicate protected reference event: "
                    f"{index}"
                )

            epoch = tracss_epoch_utc(
                row[
                    "epoch"
                ]
            )

            expected_epoch = utc(
                sample_rows[
                    index
                ][
                    "reference_tca_utc"
                ]
            )

            if epoch != expected_epoch:
                raise AssociationError(
                    "protected reference TCA mismatch: "
                    f"{index}"
                )

            reference[
                index
            ] = {
                "reference_tca_utc":
                    sample_rows[
                        index
                    ][
                        "reference_tca_utc"
                    ].strip(),

                "reference_miss_distance_km":
                    finite(
                        row[
                            "min_range"
                        ]
                    ),
            }

    if rows_scanned != EXPECTED_REFERENCE_ROWS:
        raise AssociationError(
            "protected reference row census mismatch: "
            f"{rows_scanned}"
        )

    if len(reference) != N_EVENTS:
        raise AssociationError(
            "protected reference sample coverage mismatch: "
            f"{len(reference)}"
        )

    return (
        reference,
        rows_scanned,
    )


def synthetic_tests():

    from datetime import timedelta

    reference = utc(
        "2025-01-01T00:00:00Z"
    )

    failed_attempt_epoch = (
        "2025-01-04 12:00:13.371034"
    )

    expected_failed_attempt_epoch = utc(
        "2025-01-04T12:00:13.371034Z"
    )

    tracss_failed_attempt_epoch = (
        tracss_epoch_utc(
            failed_attempt_epoch
        )
    )

    generic_naive_rejected = False

    try:
        utc(
            failed_attempt_epoch
        )

    except AssociationError:
        generic_naive_rejected = True

    def candidate(
        index,
        seconds,
        distance,
    ):
        return {
            "candidate_index":
                index,

            "candidate_time":
                (
                    reference
                    + timedelta(
                        seconds=seconds
                    )
                ),

            "distance_km":
                float(
                    distance
                ),
        }

    def pick(candidates):

        return min(
            candidates,
            key=lambda item: (
                abs(
                    (
                        item[
                            "candidate_time"
                        ]
                        - reference
                    ).total_seconds()
                ),
                item[
                    "candidate_time"
                ],
                item[
                    "distance_km"
                ],
                item[
                    "candidate_index"
                ],
            ),
        )

    tests = {
        "tracss_naive_epoch_interpreted_as_UTC":
            (
                tracss_failed_attempt_epoch
                == expected_failed_attempt_epoch
            ),

        "generic_utc_naive_timestamp_rejected":
            generic_naive_rejected,

        "minimum_absolute_offset":
            (
                pick(
                    [
                        candidate(
                            1,
                            -20,
                            4,
                        ),
                        candidate(
                            2,
                            10,
                            8,
                        ),
                    ]
                )[
                    "candidate_index"
                ]
                == 2
            ),

        "tie_earlier_time":
            (
                pick(
                    [
                        candidate(
                            1,
                            -10,
                            9,
                        ),
                        candidate(
                            2,
                            10,
                            1,
                        ),
                    ]
                )[
                    "candidate_index"
                ]
                == 1
            ),

        "tie_smaller_distance":
            (
                pick(
                    [
                        candidate(
                            2,
                            10,
                            9,
                        ),
                        candidate(
                            1,
                            10,
                            1,
                        ),
                    ]
                )[
                    "candidate_index"
                ]
                == 1
            ),

        "tie_lower_candidate_index":
            (
                pick(
                    [
                        candidate(
                            2,
                            10,
                            1,
                        ),
                        candidate(
                            1,
                            10,
                            1,
                        ),
                    ]
                )[
                    "candidate_index"
                ]
                == 1
            ),

        "inclusive_300s":
            (
                abs(
                    (
                        candidate(
                            1,
                            300,
                            1,
                        )[
                            "candidate_time"
                        ]
                        - reference
                    ).total_seconds()
                )
                <= ASSOC_TOL_S
            ),

        "outside_300s":
            (
                abs(
                    (
                        candidate(
                            1,
                            300.000001,
                            1,
                        )[
                            "candidate_time"
                        ]
                        - reference
                    ).total_seconds()
                )
                > ASSOC_TOL_S
            ),
    }

    if not all(
        tests.values()
    ):
        raise AssociationError(
            "synthetic tests failed: "
            f"{tests}"
        )

    return tests


def offline_validate():

    # ------------------------------------------------------------
    # Only H5/frozen control evidence is accessed here.
    # H3/H7 event and candidate files are deliberately not opened.
    # ------------------------------------------------------------

    for path, expected in EXPECTED.items():

        if not path.is_file():
            raise AssociationError(
                f"missing frozen input: {path}"
            )

        if sha256(path) != expected:
            raise AssociationError(
                "frozen input hash mismatch: "
                f"{path}"
            )

    sample_rows = indexed(
        SAMPLE
    )

    event_rows = indexed(
        H5_EVENT
    )

    association_rows = indexed(
        G6_ASSOC
    )

    metric_rows = indexed(
        G6_METRIC
    )

    g7c5 = g7c_five()

    for index in range(
        1,
        N_EVENTS + 1,
    ):

        if (
            sample_rows[
                index
            ][
                "conj_id"
            ].strip()
            != event_rows[
                index
            ][
                "conj_id"
            ].strip()
        ):
            raise AssociationError(
                "sample/H5 conjunction mismatch: "
                f"{index}"
            )

        if (
            sample_rows[
                index
            ][
                "g6_temporal_stratum"
            ].strip()
            != event_rows[
                index
            ][
                "g6_temporal_stratum"
            ].strip()
        ):
            raise AssociationError(
                "sample/H5 temporal-stratum mismatch: "
                f"{index}"
            )

        if not sample_rows[
            index
        ][
            "reference_tca_utc"
        ].strip():
            raise AssociationError(
                "sample register lacks reference TCA: "
                f"{index}"
            )

    pre_counts = Counter(
        row[
            "preoutcome_status"
        ].strip()
        for row
        in event_rows.values()
    )

    expected_pre_counts = Counter(
        {
            "CANDIDATE_SET_READY":
                6906,

            "UNRECOVERED_REFINEMENT_FAILURE":
                20,

            "PROPAGATION_FAILURE":
                1,
        }
    )

    if (
        pre_counts
        != expected_pre_counts
    ):
        raise AssociationError(
            "H5 preoutcome census changed: "
            f"{dict(pre_counts)}"
        )

    (
        best,
        counts,
        scanned,
    ) = scan(
        event_rows,
        sample_rows,
        H5_CAND,
        H5_CANDIDATE_ROWS,
    )

    (
        derived,
        summary,
    ) = evaluate(
        event_rows,
        metric_rows,
        best,
        counts,
        5,
    )

    final_counts = Counter()

    for index in range(
        1,
        N_EVENTS + 1,
    ):

        ours = derived[
            index
        ]

        frozen_association = (
            association_rows[
                index
            ]
        )

        frozen_metric = (
            metric_rows[
                index
            ]
        )

        g7c = g7c5[
            index
        ]

        if (
            ours[
                "association_status"
            ]
            != frozen_association[
                "final_status"
            ].strip()
        ):
            raise AssociationError(
                "G6 final-status "
                f"mismatch: {index}"
            )

        final_counts[
            ours[
                "association_status"
            ]
        ] += 1

        if (
            boolean(
                frozen_metric[
                    "evaluable"
                ]
            )
            != ours[
                "candidate_ready"
            ]
        ):
            raise AssociationError(
                "G6 evaluability "
                f"mismatch: {index}"
            )

        if (
            boolean(
                frozen_metric[
                    "valid_associated"
                ]
            )
            != ours[
                "associated"
            ]
        ):
            raise AssociationError(
                "G6 valid-association "
                f"mismatch: {index}"
            )

        if ours[
            "associated"
        ]:

            if (
                int(
                    frozen_association[
                        "selected_candidate_index"
                    ]
                )
                != ours[
                    "selected_candidate_index"
                ]
            ):
                raise AssociationError(
                    "G6 selected-candidate "
                    f"mismatch: {index}"
                )

            if (
                int(
                    frozen_metric[
                        "selected_candidate_index"
                    ]
                )
                != ours[
                    "selected_candidate_index"
                ]
            ):
                raise AssociationError(
                    "G6 metric candidate "
                    f"mismatch: {index}"
                )

            if (
                utc(
                    frozen_metric[
                        "predicted_tca_utc"
                    ]
                )
                != ours[
                    "selected_candidate_time_utc"
                ]
            ):
                raise AssociationError(
                    "G6 predicted-TCA "
                    f"mismatch: {index}"
                )

            close(
                frozen_metric[
                    "predicted_miss_distance_km"
                ],
                ours[
                    "selected_candidate_distance_km"
                ],
                f"G6 distance {index}",
            )

            close(
                frozen_metric[
                    "absolute_tca_error_s"
                ],
                ours[
                    "absolute_tca_error_s"
                ],
                f"G6 Et {index}",
            )

            close(
                frozen_metric[
                    "absolute_miss_distance_error_km"
                ],
                ours[
                    "absolute_miss_distance_error_km"
                ],
                f"G6 Ed {index}",
            )

        if (
            boolean(
                g7c[
                    "candidate_available"
                ]
            )
            != ours[
                "candidate_ready"
            ]
        ):
            raise AssociationError(
                "G7-C candidate-available "
                f"mismatch: {index}"
            )

        if (
            int(
                g7c[
                    "candidate_count"
                ]
            )
            != ours[
                "candidate_count"
            ]
        ):
            raise AssociationError(
                "G7-C candidate-count "
                f"mismatch: {index}"
            )

        if (
            boolean(
                g7c[
                    "associated"
                ]
            )
            != ours[
                "associated"
            ]
        ):
            raise AssociationError(
                "G7-C associated "
                f"mismatch: {index}"
            )

        if ours[
            "candidate_ready"
        ]:

            if (
                int(
                    g7c[
                        "nearest_candidate_index"
                    ]
                )
                != best[
                    index
                ][
                    "candidate_index"
                ]
            ):
                raise AssociationError(
                    "G7-C nearest-candidate "
                    f"mismatch: {index}"
                )

            if (
                utc(
                    g7c[
                        "nearest_candidate_time_utc"
                    ]
                )
                != best[
                    index
                ][
                    "candidate_time"
                ]
            ):
                raise AssociationError(
                    "G7-C nearest-time "
                    f"mismatch: {index}"
                )

            close(
                g7c[
                    "nearest_candidate_distance_km"
                ],
                best[
                    index
                ][
                    "distance_km"
                ],
                "G7-C nearest distance "
                f"{index}",
            )

            close(
                g7c[
                    "absolute_reference_tca_offset_s"
                ],
                best[
                    index
                ][
                    "offset_s"
                ],
                "G7-C offset "
                f"{index}",
            )

    expected_final_counts = Counter(
        {
            "VALID_ASSOCIATED":
                6839,

            "UNASSOCIATED_REFERENCE_EVENT":
                67,

            "UNRECOVERED_REFINEMENT_FAILURE":
                20,

            "PROPAGATION_FAILURE":
                1,
        }
    )

    if (
        final_counts
        != expected_final_counts
    ):
        raise AssociationError(
            "H5 final census changed: "
            f"{dict(final_counts)}"
        )

    with G7C_SUMMARY.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        five_rows = [
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

    if len(five_rows) != 1:
        raise AssociationError(
            "G7-C 5-min summary "
            "missing or duplicated"
        )

    five = five_rows[0]

    integer_keys = [
        "total_events",
        "candidate_ready_events",
        "associated_events",
        "unassociated_candidate_ready_events",
        "Ed_successes",
        "Ed_denominator",
        "Et_successes",
        "Et_denominator",
    ]

    numeric_keys = [
        "association_rate_all_events",
        "association_wilson_95_lower",
        "association_wilson_95_upper",
        "association_rate_given_candidate_ready",
        "Ed_coverage",
        "Ed_wilson_95_lower",
        "Ed_wilson_95_upper",
        "Ed_median_km",
        "Ed_p90_km",
        "Ed_p95_km",
        "Ed_p99_km",
        "Ed_maximum_km",
        "Et_coverage",
        "Et_wilson_95_lower",
        "Et_wilson_95_upper",
        "Et_median_s",
        "Et_p90_s",
        "Et_p95_s",
        "Et_p99_s",
        "Et_maximum_s",
    ]

    for key in integer_keys:

        if (
            int(
                five[key]
            )
            != int(
                summary[key]
            )
        ):
            raise AssociationError(
                "G7-C summary integer "
                f"mismatch: {key}"
            )

    for key in numeric_keys:

        close(
            five[key],
            summary[key],
            "G7-C summary "
            f"{key}",
            tol=1.0e-12,
        )

    tests = synthetic_tests()

    payload = {
        "status":
            "G7D_HORIZON_ASSOCIATION_OFFLINE_VALIDATION_PASS",

        "analysis_role":
            "POST_CONFIRMATORY_PROPAGATION_HORIZON_SENSITIVITY",

        "association_tolerance_min":
            5,

        "association_threshold_inclusive":
            True,

        "protected_reference_time_semantics":
            (
                "TraCSS timezone-naive epoch is interpreted as UTC "
                "to reproduce the frozen G7-C source convention"
            ),

        "generic_utc_naive_timestamp_rejected":
            True,

        "tracss_naive_epoch_interpreted_as_UTC":
            True,

        "association_ordering": [
            "minimum absolute reference-TCA offset",
            "earlier candidate time",
            "smaller candidate distance",
            "lower stable candidate index",
        ],

        "H5_control_events":
            N_EVENTS,

        "H5_control_candidate_rows_scanned":
            scanned,

        "H5_control_candidate_ready_events":
            summary[
                "candidate_ready_events"
            ],

        "H5_control_valid_associated_events":
            summary[
                "associated_events"
            ],

        "H5_exact_G6_status_reproduction":
            True,

        "H5_exact_G6_selected_candidate_reproduction":
            True,

        "H5_exact_G6_metric_reproduction":
            True,

        "H5_exact_G7C_5min_reproduction":
            True,

        "synthetic_tests":
            tests,

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

        "next_safe_action":
            "INDEPENDENT_AUDIT_THEN_FREEZE_EVALUATOR_BEFORE_H3_H7_ASSOCIATION",
    }

    VALIDATION.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with VALIDATION.open(
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

        stream.write(
            "\n"
        )

    print(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
    )


def evaluate_horizon(
    horizon_days: int,
    *,
    authorization: bool = False,
):

    # This function is deliberately unreachable from the direct CLI.
    # A separate reviewed/frozen authorization launcher is required.

    if not authorization:
        raise AssociationError(
            "real H3/H7 association is unauthorized; "
            "a separate frozen authorization launcher is required"
        )

    if horizon_days not in HORIZONS:
        raise AssociationError(
            "horizon must be exactly 3 or 7 days"
        )

    specification = HORIZONS[
        horizon_days
    ]

    execution_inputs = (
        (
            LOCK,
            EXPECTED[
                LOCK
            ],
        ),
        (
            SAMPLE,
            EXPECTED[
                SAMPLE
            ],
        ),
        (
            specification[
                "event"
            ],
            specification[
                "event_sha"
            ],
        ),
        (
            specification[
                "cand"
            ],
            specification[
                "cand_sha"
            ],
        ),
    )

    for path, expected in execution_inputs:

        if not path.is_file():
            raise AssociationError(
                "missing frozen horizon "
                f"input: {path}"
            )

        if sha256(path) != expected:
            raise AssociationError(
                "frozen horizon input "
                f"mismatch: {path}"
            )

    sample_rows = indexed(
        SAMPLE
    )

    event_rows = indexed(
        specification[
            "event"
        ]
    )

    (
        reference_rows,
        reference_rows_scanned,
    ) = load_protected_reference(
        sample_rows
    )

    (
        best,
        counts,
        scanned,
    ) = scan(
        event_rows,
        sample_rows,
        specification[
            "cand"
        ],
        specification[
            "candidate_rows"
        ],
    )

    (
        events,
        summary,
    ) = evaluate(
        event_rows,
        reference_rows,
        best,
        counts,
        horizon_days,
    )

    if (
        summary[
            "candidate_ready_events"
        ]
        != specification[
            "ready"
        ]
    ):
        raise AssociationError(
            "candidate-ready census "
            "mismatch"
        )

    return {
        "events":
            events,

        "summary":
            summary,

        "candidate_rows_scanned":
            scanned,

        "protected_reference_rows_scanned":
            reference_rows_scanned,
    }


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--offline-validate",
        action="store_true",
    )

    arguments = parser.parse_args()

    if not arguments.offline_validate:
        raise AssociationError(
            "direct real H3/H7 association "
            "is disabled; only "
            "--offline-validate is permitted"
        )

    offline_validate()


if __name__ == "__main__":
    main()