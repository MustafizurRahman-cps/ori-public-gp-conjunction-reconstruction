#!/usr/bin/env python3

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr


ROOT = Path.cwd()

AUDIT = ROOT / "results/g6/G6_Final_Causal_History_Audit.csv"
SELECTION = ROOT / "results/g6/G6_48H_Replay_Input_Selection.csv"
G7A = ROOT / "results/g7/G7A_Event_Quality.csv"

OBJECT_OUT = ROOT / "results/g7/G7B_Object_History_Quality.csv"
EVENT_OUT = ROOT / "results/g7/G7B_Event_History_Quality.csv"
ASSOC_OUT = ROOT / "results/g7/G7B_History_Quality_Associations.csv"
SUMMARY_OUT = ROOT / "results/g7/G7B_History_Quality_Summary.json"


EXPECTED_OBJECTS = 13854
EXPECTED_EVENTS = 6927
EXPECTED_MAIN_FILES = 1386

# Transported raw rows in the frozen G6 main acquisition.
EXPECTED_RAW_ROWS = 5_245_230

# Exact object-local [t_ref-180d,t_ref) rows.
EXPECTED_LOCAL_MAIN_ROWS = 5_153_040

EXPECTED_VALID = 6839
EXPECTED_EVALUABLE = 6906

WINDOWS = (14, 30, 60)

BOOTSTRAP_SEED = 20260908
BOOTSTRAP_REPLICATES = 2000

SECONDS_PER_DAY = 86400.0


class G7BError(RuntimeError):
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
        raise G7BError("empty timestamp")

    text = text.replace(" ", "T")

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    result = datetime.fromisoformat(text)

    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)

    return result.astimezone(UTC)


def iso_utc(value: datetime) -> str:

    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def as_bool(value: str) -> bool:

    text = str(value).strip().upper()

    if text == "TRUE":
        return True

    if text == "FALSE":
        return False

    raise G7BError(
        f"invalid boolean {value!r}"
    )


def fmt(value):

    if value is None:
        return ""

    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"

    if isinstance(value, float):

        if not math.isfinite(value):
            raise G7BError(
                "attempted to serialize nonfinite float"
            )

        return format(value, ".17g")

    return str(value)


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


# Exact inherited G5 canonical state signature.
def canonical_state_signature(
    row,
    epoch_utc,
):

    line1 = str(
        row.get(
            "TLE_LINE1",
            "",
        )
    ).rstrip("\r\n")

    line2 = str(
        row.get(
            "TLE_LINE2",
            "",
        )
    ).rstrip("\r\n")

    if (
        len(line1) < 63
        or len(line2) < 63
        or not line1.startswith("1 ")
        or not line2.startswith("2 ")
    ):
        raise G7BError(
            "malformed TLE in canonical state signature"
        )

    return (
        iso_utc(epoch_utc),

        line1[33:43].strip(),
        line1[44:52].strip(),
        line1[53:61].strip(),
        line1[62:63].strip(),

        line2[8:16].strip(),
        line2[17:25].strip(),
        line2[26:33].strip(),
        line2[34:42].strip(),
        line2[43:51].strip(),
        line2[52:63].strip(),
    )


def window_metrics(
    records,
    replay,
    lower_bound,
    days,
):

    start = (
        replay
        - timedelta(
            days=days
        )
    )

    left_censored = (
        lower_bound > start
    )

    selected = [
        item
        for item in records
        if (
            item[0] > start
            and item[0] <= replay
        )
    ]

    publication_count = len(
        selected
    )

    distinct_state_count = len(
        {
            item[1]
            for item in selected
        }
    )

    times = sorted(
        item[0]
        for item in selected
    )

    gaps = [
        (
            times[i]
            - times[i - 1]
        ).total_seconds()
        / 3600.0

        for i in range(
            1,
            len(times),
        )
    ]

    if any(
        gap < 0.0
        for gap in gaps
    ):
        raise G7BError(
            "negative cadence interval"
        )

    return {
        "left_censored":
            left_censored,

        "publication_count":
            publication_count,

        "distinct_state_count":
            distinct_state_count,

        "median_cadence_hours":
            (
                statistics.median(
                    gaps
                )
                if gaps
                else None
            ),

        "max_gap_hours":
            (
                max(gaps)
                if gaps
                else None
            ),
    }


def spearman_value(
    rows,
    field,
):

    pairs = [
        (
            float(
                row[field]
            ),
            float(
                row[
                    "absolute_miss_distance_error_km"
                ]
            ),
        )

        for row in rows

        if (
            row.get(field)
            is not None

            and row.get(
                "absolute_miss_distance_error_km"
            )
            is not None
        )
    ]

    n = len(pairs)

    if n < 3:
        return None, n

    x = np.asarray(
        [
            pair[0]
            for pair in pairs
        ],
        dtype=float,
    )

    y = np.asarray(
        [
            pair[1]
            for pair in pairs
        ],
        dtype=float,
    )

    if (
        len(np.unique(x)) < 2
        or len(np.unique(y)) < 2
    ):
        return None, n

    value = float(
        spearmanr(
            x,
            y,
        ).statistic
    )

    if not math.isfinite(value):
        return None, n

    return value, n


def bootstrap_spearman(
    rows,
    field,
    rng,
):

    pairs = [
        (
            float(
                row[field]
            ),
            float(
                row[
                    "absolute_miss_distance_error_km"
                ]
            ),
        )

        for row in rows

        if (
            row.get(field)
            is not None

            and row.get(
                "absolute_miss_distance_error_km"
            )
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

    values = np.asarray(
        values,
        dtype=float,
    )

    return (
        float(
            np.quantile(
                values,
                0.025,
            )
        ),
        float(
            np.quantile(
                values,
                0.975,
            )
        ),
        len(values),
    )


# ============================================================
# Metadata joins
# ============================================================

audit = {}

with AUDIT.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    for row in csv.DictReader(stream):

        object_id = (
            row[
                "NORAD_CAT_ID"
            ].strip()
        )

        if object_id in audit:
            raise G7BError(
                "G6 sample is not object-disjoint"
            )

        audit[object_id] = row


if len(audit) != EXPECTED_OBJECTS:
    raise G7BError(
        "causal-history audit object count mismatch"
    )


selection = {}

with SELECTION.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    for row in csv.DictReader(stream):

        object_id = (
            row[
                "NORAD_CAT_ID"
            ].strip()
        )

        if object_id in selection:
            raise G7BError(
                "duplicate replay selection object"
            )

        selection[
            object_id
        ] = row


if set(selection) != set(audit):
    raise G7BError(
        "history-audit / replay-selection object set mismatch"
    )


events = {}

with G7A.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    for row in csv.DictReader(stream):

        index = int(
            row[
                "final_g6_sample_index"
            ]
        )

        events[index] = row


if len(events) != EXPECTED_EVENTS:
    raise G7BError(
        "G7-A event count mismatch"
    )


if sum(
    as_bool(
        row[
            "valid_associated"
        ]
    )
    for row in events.values()
) != EXPECTED_VALID:
    raise G7BError(
        "VALID_ASSOCIATED count mismatch"
    )


if sum(
    as_bool(
        row[
            "evaluable"
        ]
    )
    for row in events.values()
) != EXPECTED_EVALUABLE:
    raise G7BError(
        "evaluable count mismatch"
    )


# ============================================================
# Group objects by already-frozen main raw file.
# ============================================================

objects_by_raw = defaultdict(
    list
)

for object_id, row in audit.items():

    path_text = (
        row[
            "main_raw_path"
        ].strip()
    )

    if not path_text:
        raise G7BError(
            "empty main_raw_path"
        )

    objects_by_raw[
        path_text
    ].append(
        object_id
    )


if len(objects_by_raw) != EXPECTED_MAIN_FILES:
    raise G7BError(
        "main raw file count mismatch"
    )


# ============================================================
# SINGLE CONTROLLED RAW-HISTORY PASS
# ============================================================

object_rows = []

raw_rows_scanned = 0
local_main_rows_verified = 0
main_files_opened = 0


for path_text in sorted(
    objects_by_raw
):

    path = ROOT / path_text

    if not path.is_file():
        raise G7BError(
            f"missing raw file: {path_text}"
        )

    main_files_opened += 1

    target_ids = set(
        objects_by_raw[
            path_text
        ]
    )

    local_counts = {
        object_id: 0
        for object_id in target_ids
    }

    latest = {
        object_id: None
        for object_id in target_ids
    }

    records60 = {
        object_id: []
        for object_id in target_ids
    }


    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        reader = csv.DictReader(
            stream
        )

        required = {
            "NORAD_CAT_ID",
            "CREATION_DATE",
            "EPOCH",
            "GP_ID",
            "TLE_LINE1",
            "TLE_LINE2",
        }

        if not required.issubset(
            set(
                reader.fieldnames
                or ()
            )
        ):
            raise G7BError(
                f"raw GP schema missing required fields: {path_text}"
            )


        for raw in reader:

            raw_rows_scanned += 1

            object_id = (
                raw[
                    "NORAD_CAT_ID"
                ].strip()
            )

            if object_id not in target_ids:
                continue


            a = audit[
                object_id
            ]

            s = selection[
                object_id
            ]

            lower = parse_utc(
                a[
                    "lower_bound_utc"
                ]
            )

            upper = parse_utc(
                a[
                    "reference_tca_utc"
                ]
            )

            replay = parse_utc(
                s[
                    "replay_time_utc"
                ]
            )

            creation = parse_utc(
                raw[
                    "CREATION_DATE"
                ]
            )

            epoch = parse_utc(
                raw[
                    "EPOCH"
                ]
            )

            gp_id = (
                raw[
                    "GP_ID"
                ].strip()
            )

            if not gp_id:
                raise G7BError(
                    "realized main raw row missing GP_ID"
                )


            # Exact object-local G6 main interval.
            if not (
                lower
                <= creation
                < upper
            ):
                continue


            local_counts[
                object_id
            ] += 1

            local_main_rows_verified += 1


            # Exact causal replay subset.
            if creation <= replay:

                key = (
                    creation,
                    epoch,
                    gp_id,
                )

                prior = latest[
                    object_id
                ]

                if (
                    prior is None
                    or key > prior[0]
                ):
                    latest[
                        object_id
                    ] = (
                        key,
                        gp_id,
                        creation,
                        epoch,
                    )


                # We only need the longest frozen quality window.
                if (
                    creation
                    > replay
                    - timedelta(
                        days=60
                    )
                ):

                    signature = (
                        canonical_state_signature(
                            raw,
                            epoch,
                        )
                    )

                    records60[
                        object_id
                    ].append(
                        (
                            creation,
                            signature,
                        )
                    )


    # Finalize objects in this raw file, then discard its records.
    for object_id in sorted(
        target_ids,
        key=int,
    ):

        a = audit[
            object_id
        ]

        s = selection[
            object_id
        ]

        expected_main = int(
            a[
                "main_window_rows"
            ]
        )

        if (
            local_counts[
                object_id
            ]
            != expected_main
        ):
            raise G7BError(
                "object-local main-window row-count mismatch "
                f"for NORAD {object_id}: "
                f"{local_counts[object_id]} != {expected_main}"
            )


        current = latest[
            object_id
        ]

        if current is None:
            raise G7BError(
                f"no causal replay record recovered for NORAD {object_id}"
            )


        _, gp_id, creation, epoch = (
            current
        )

        if (
            gp_id
            != s[
                "replay_GP_ID"
            ].strip()
        ):
            raise G7BError(
                f"replay GP_ID mismatch for NORAD {object_id}"
            )

        if (
            creation
            != parse_utc(
                s[
                    "replay_CREATION_DATE"
                ]
            )
        ):
            raise G7BError(
                f"replay CREATION_DATE mismatch for NORAD {object_id}"
            )

        if (
            epoch
            != parse_utc(
                s[
                    "replay_EPOCH"
                ]
            )
        ):
            raise G7BError(
                f"replay EPOCH mismatch for NORAD {object_id}"
            )


        replay = parse_utc(
            s[
                "replay_time_utc"
            ]
        )

        lower = parse_utc(
            a[
                "lower_bound_utc"
            ]
        )


        payload = {
            "final_g6_sample_index":
                int(
                    a[
                        "final_g6_sample_index"
                    ]
                ),

            "g6_temporal_stratum":
                a[
                    "g6_temporal_stratum"
                ].strip(),

            "conj_id":
                a[
                    "conj_id"
                ].strip(),

            "NORAD_CAT_ID":
                object_id,

            "object_role":
                a[
                    "object_role"
                ].strip(),

            "replay_time_utc":
                s[
                    "replay_time_utc"
                ].strip(),

            "lower_bound_utc":
                a[
                    "lower_bound_utc"
                ].strip(),

            "main_raw_path":
                path_text,

            "main_window_rows_verified":
                local_counts[
                    object_id
                ],

            "replay_GP_ID_verified":
                gp_id,

            "full_history_predecessor_class":
                a[
                    "final_predecessor_class"
                ].strip(),

            "full_history_lower_boundary_status":
                a[
                    "lower_boundary_status"
                ].strip(),
        }


        for days in WINDOWS:

            wm = window_metrics(
                records60[
                    object_id
                ],
                replay,
                lower,
                days,
            )

            prefix = f"w{days}"

            payload[
                f"{prefix}_left_censored"
            ] = wm[
                "left_censored"
            ]

            payload[
                f"{prefix}_publication_count"
            ] = wm[
                "publication_count"
            ]

            payload[
                f"{prefix}_distinct_state_count"
            ] = wm[
                "distinct_state_count"
            ]

            payload[
                f"{prefix}_median_cadence_hours"
            ] = wm[
                "median_cadence_hours"
            ]

            payload[
                f"{prefix}_max_gap_hours"
            ] = wm[
                "max_gap_hours"
            ]


        object_rows.append(
            payload
        )


if raw_rows_scanned != EXPECTED_RAW_ROWS:
    raise G7BError(
        f"transported raw-row census changed: "
        f"{raw_rows_scanned} != {EXPECTED_RAW_ROWS}"
    )


if (
    local_main_rows_verified
    != EXPECTED_LOCAL_MAIN_ROWS
):
    raise G7BError(
        f"object-local main-row census changed: "
        f"{local_main_rows_verified} "
        f"!= {EXPECTED_LOCAL_MAIN_ROWS}"
    )


if main_files_opened != EXPECTED_MAIN_FILES:
    raise G7BError(
        "main-file open count mismatch"
    )


if len(object_rows) != EXPECTED_OBJECTS:
    raise G7BError(
        "object history output count mismatch"
    )


# ============================================================
# Event-level pair summaries
# ============================================================

by_event_role = {}

for row in object_rows:

    key = (
        row[
            "final_g6_sample_index"
        ],
        row[
            "object_role"
        ],
    )

    if key in by_event_role:
        raise G7BError(
            "duplicate event/object role"
        )

    by_event_role[
        key
    ] = row


event_rows = []

for index in range(
    1,
    EXPECTED_EVENTS + 1,
):

    base = events[
        index
    ]

    obj1 = by_event_role[
        (
            index,
            "obj1",
        )
    ]

    obj2 = by_event_role[
        (
            index,
            "obj2",
        )
    ]

    valid = as_bool(
        base[
            "valid_associated"
        ]
    )

    row = {
        "final_g6_sample_index":
            index,

        "g6_temporal_stratum":
            base[
                "g6_temporal_stratum"
            ].strip(),

        "conj_id":
            base[
                "conj_id"
            ].strip(),

        "valid_associated":
            valid,

        "absolute_miss_distance_error_km":
            (
                float(
                    base[
                        "absolute_miss_distance_error_km"
                    ]
                )
                if valid
                else None
            ),

        "pair_max_state_age_days":
            float(
                base[
                    "pair_max_state_age_days"
                ]
            ),

        "pair_max_propagation_span_days":
            (
                float(
                    base[
                        "pair_max_propagation_span_days"
                    ]
                )
                if valid
                else None
            ),
    }


    for days in WINDOWS:

        prefix = f"w{days}"

        censored = (
            bool(
                obj1[
                    f"{prefix}_left_censored"
                ]
            )
            or bool(
                obj2[
                    f"{prefix}_left_censored"
                ]
            )
        )

        row[
            f"{prefix}_pair_left_censored"
        ] = censored


        # Pair summaries are scientifically supported only
        # when both rolling windows are complete.
        if censored:

            row[
                f"{prefix}_pair_min_distinct_state_count"
            ] = None

            row[
                f"{prefix}_pair_max_gap_hours"
            ] = None

        else:

            row[
                f"{prefix}_pair_min_distinct_state_count"
            ] = min(
                int(
                    obj1[
                        f"{prefix}_distinct_state_count"
                    ]
                ),
                int(
                    obj2[
                        f"{prefix}_distinct_state_count"
                    ]
                ),
            )


            gap1 = obj1[
                f"{prefix}_max_gap_hours"
            ]

            gap2 = obj2[
                f"{prefix}_max_gap_hours"
            ]

            row[
                f"{prefix}_pair_max_gap_hours"
            ] = (
                max(
                    float(gap1),
                    float(gap2),
                )
                if (
                    gap1 is not None
                    and gap2 is not None
                )
                else None
            )


    event_rows.append(
        row
    )


# ============================================================
# Frozen quality associations
# ============================================================

valid_rows = [
    row
    for row in event_rows
    if row[
        "valid_associated"
    ]
]


QUALITY_VARIABLES = (
    (
        "pair_max_state_age_days",
        "PRIMARY_AGE_CONTEXT",
    ),
    (
        "w30_pair_min_distinct_state_count",
        "PRIMARY_30D_HISTORY",
    ),
    (
        "w30_pair_max_gap_hours",
        "PRIMARY_30D_HISTORY",
    ),
    (
        "pair_max_propagation_span_days",
        "PROPAGATION_CONTEXT",
    ),
    (
        "w14_pair_min_distinct_state_count",
        "DESCRIPTIVE_14D_SENSITIVITY",
    ),
    (
        "w14_pair_max_gap_hours",
        "DESCRIPTIVE_14D_SENSITIVITY",
    ),
    (
        "w60_pair_min_distinct_state_count",
        "DESCRIPTIVE_60D_SENSITIVITY",
    ),
    (
        "w60_pair_max_gap_hours",
        "DESCRIPTIVE_60D_SENSITIVITY",
    ),
)


rng = np.random.default_rng(
    BOOTSTRAP_SEED
)

association_rows = []


for field, role in QUALITY_VARIABLES:

    rho, n = spearman_value(
        valid_rows,
        field,
    )

    lower, upper, valid_boot = (
        bootstrap_spearman(
            valid_rows,
            field,
            rng,
        )
    )


    association_rows.append(
        {
            "scope":
                "ALL_G7_G6_SAMPLE",

            "temporal_stratum":
                "ALL",

            "analysis_role":
                role,

            "quality_variable":
                field,

            "n":
                n,

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
    )


    for stratum in (
        "G6-T1",
        "G6-T2",
        "G6-T3",
        "G6-T4",
        "G6-T5",
    ):

        subset = [
            row
            for row in valid_rows
            if row[
                "g6_temporal_stratum"
            ] == stratum
        ]

        srho, sn = spearman_value(
            subset,
            field,
        )

        association_rows.append(
            {
                "scope":
                    "TEMPORAL_STRATUM",

                "temporal_stratum":
                    stratum,

                "analysis_role":
                    role,

                "quality_variable":
                    field,

                "n":
                    sn,

                "spearman_rho":
                    srho,

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


# G7-A same-seed first-variable cross-check.
state_age = [
    row
    for row in association_rows
    if (
        row[
            "scope"
        ] == "ALL_G7_G6_SAMPLE"

        and row[
            "quality_variable"
        ] == "pair_max_state_age_days"
    )
][0]


for actual, expected in (
    (
        state_age[
            "spearman_rho"
        ],
        -0.2135250534067239,
    ),
    (
        state_age[
            "bootstrap_95_lower"
        ],
        -0.23638133046535614,
    ),
    (
        state_age[
            "bootstrap_95_upper"
        ],
        -0.19111321464168726,
    ),
):

    if not math.isclose(
        float(actual),
        expected,
        rel_tol=0.0,
        abs_tol=1.0e-15,
    ):
        raise G7BError(
            "G7-A state-age bootstrap cross-check failed"
        )


# ============================================================
# Write deterministic outputs
# ============================================================

object_fields = [
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "NORAD_CAT_ID",
    "object_role",
    "replay_time_utc",
    "lower_bound_utc",
    "main_raw_path",
    "main_window_rows_verified",
    "replay_GP_ID_verified",
    "full_history_predecessor_class",
    "full_history_lower_boundary_status",
]

for days in WINDOWS:

    prefix = f"w{days}"

    object_fields += [
        f"{prefix}_left_censored",
        f"{prefix}_publication_count",
        f"{prefix}_distinct_state_count",
        f"{prefix}_median_cadence_hours",
        f"{prefix}_max_gap_hours",
    ]


event_fields = [
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "valid_associated",
    "absolute_miss_distance_error_km",
    "pair_max_state_age_days",
    "pair_max_propagation_span_days",
]

for days in WINDOWS:

    prefix = f"w{days}"

    event_fields += [
        f"{prefix}_pair_left_censored",
        f"{prefix}_pair_min_distinct_state_count",
        f"{prefix}_pair_max_gap_hours",
    ]


assoc_fields = [
    "scope",
    "temporal_stratum",
    "analysis_role",
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
    OBJECT_OUT,
    object_fields,
    sorted(
        object_rows,
        key=lambda row: (
            row[
                "final_g6_sample_index"
            ],
            row[
                "object_role"
            ],
        ),
    ),
)

write_csv(
    EVENT_OUT,
    event_fields,
    event_rows,
)

write_csv(
    ASSOC_OUT,
    assoc_fields,
    association_rows,
)


window_summary = {}

for days in WINDOWS:

    prefix = f"w{days}"

    object_censored = sum(
        bool(
            row[
                f"{prefix}_left_censored"
            ]
        )
        for row in object_rows
    )

    pair_censored = sum(
        bool(
            row[
                f"{prefix}_pair_left_censored"
            ]
        )
        for row in event_rows
    )

    valid_complete = sum(
        row[
            "valid_associated"
        ]
        and not row[
            f"{prefix}_pair_left_censored"
        ]
        for row in event_rows
    )

    valid_gap_supported = sum(
        row[
            "valid_associated"
        ]
        and row[
            f"{prefix}_pair_max_gap_hours"
        ]
        is not None
        for row in event_rows
    )

    window_summary[
        str(days)
    ] = {
        "object_left_censored":
            object_censored,

        "pair_left_censored":
            pair_censored,

        "VALID_ASSOCIATED_complete_pair_windows":
            valid_complete,

        "VALID_ASSOCIATED_pair_gap_supported":
            valid_gap_supported,
    }


summary = {
    "status":
        "G7B_SINGLE_PASS_HISTORY_ANALYSIS_PASS",

    "analysis_role":
        "POST_CONFIRMATORY_HISTORY_QUALITY_ROBUSTNESS",

    "events":
        EXPECTED_EVENTS,

    "objects":
        EXPECTED_OBJECTS,

    "VALID_ASSOCIATED_events":
        EXPECTED_VALID,

    "evaluable_events":
        EXPECTED_EVALUABLE,

    "main_raw_files_expected":
        EXPECTED_MAIN_FILES,

    "main_raw_files_opened":
        main_files_opened,

    "transported_main_raw_rows_scanned":
        raw_rows_scanned,

    "object_local_main_rows_verified":
        local_main_rows_verified,

    "predecessor_raw_files_opened":
        0,

    "predecessor_evidence_files_opened":
        0,

    "raw_content_rehash_performed":
        False,

    "window_semantics":
        "(t_r-W,t_r]",

    "windows_days":
        list(WINDOWS),

    "primary_history_window_days":
        30,

    "descriptive_history_windows_days":
        [
            14,
            60,
        ],

    "left_censor_rule":
        "lower_bound_utc > t_r-W",

    "left_censored_pair_metrics_used_in_association":
        False,

    "canonical_state_signature":
        (
            "exact inherited G5 TLE/epoch SGP4-relevant "
            "state signature"
        ),

    "window_support":
        window_summary,

    "bootstrap": {
        "replicates":
            BOOTSTRAP_REPLICATES,

        "seed":
            BOOTSTRAP_SEED,

        "unit":
            "object-disjoint event",

        "rng_stream":
            "single fixed ordered quality-variable stream",
    },

    "state_age_G7A_crosscheck":
        {
            "spearman_rho":
                state_age[
                    "spearman_rho"
                ],

            "bootstrap_95_lower":
                state_age[
                    "bootstrap_95_lower"
                ],

            "bootstrap_95_upper":
                state_age[
                    "bootstrap_95_upper"
                ],
        },

    "full_history_predecessor_class_counts":
        dict(
            sorted(
                Counter(
                    row[
                        "full_history_predecessor_class"
                    ]
                    for row in object_rows
                ).items()
            )
        ),

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

    "output_hashes": {
        OBJECT_OUT.relative_to(ROOT).as_posix():
            sha256(
                OBJECT_OUT
            ),

        EVENT_OUT.relative_to(ROOT).as_posix():
            sha256(
                EVENT_OUT
            ),

        ASSOC_OUT.relative_to(ROOT).as_posix():
            sha256(
                ASSOC_OUT
            ),
    },

    "next_safe_action":
        "INDEPENDENT_DERIVED_OUTPUT_AUDIT_WITHOUT_SECOND_RAW_HISTORY_PASS",
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