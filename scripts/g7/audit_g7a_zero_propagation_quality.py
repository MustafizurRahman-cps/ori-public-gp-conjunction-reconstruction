#!/usr/bin/env python3
"""Independent audit of G7-A zero-propagation robustness outputs."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr


ROOT = Path.cwd()

SELECTION = ROOT / "results/g6/G6_48H_Replay_Input_Selection.csv"
METRICS = ROOT / "results/g6/G6_Confirmatory_Event_Metrics.csv"

EVENT = ROOT / "results/g7/G7A_Event_Quality.csv"
AGE = ROOT / "results/g7/G7A_Age_Sensitivity.csv"
QUALITY = ROOT / "results/g7/G7A_StateAge_Association.csv"
SUMMARY = ROOT / "results/g7/G7A_Zero_Propagation_Quality_Summary.json"

OUT = ROOT / "results/g7/G7A_Zero_Propagation_Quality_Independent_Audit.json"


EXPECTED_EVENTS = 6927
EXPECTED_OBJECT_ROWS = 13854
EXPECTED_VALID = 6839
EXPECTED_EVALUABLE = 6906

MISS_RADIUS = 78.75554652312711
Z95 = 1.959963984540054
SEED = 20260908
REPLICATES = 2000

AGE_BINS = (
    ("LT1", "<1 d"),
    ("1_LT2", "1–<2 d"),
    ("2_LT3", "2–<3 d"),
    ("3_LT5", "3–<5 d"),
    ("5_LT7", "5–<7 d"),
    ("GE7", ">=7 d"),
)


class AuditError(RuntimeError):
    pass


def sha256(path):

    h = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def utc(text):

    value = text.strip().replace(
        " ",
        "T",
    )

    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"

    result = datetime.fromisoformat(
        value
    )

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=UTC
        )

    return result.astimezone(UTC)


def as_bool(text):

    value = text.strip().upper()

    if value == "TRUE":
        return True

    if value == "FALSE":
        return False

    raise AuditError(
        f"bad boolean {text!r}"
    )


def bin_for(x):

    x = float(x)

    if x < 1:
        return AGE_BINS[0][0]

    if x < 2:
        return AGE_BINS[1][0]

    if x < 3:
        return AGE_BINS[2][0]

    if x < 5:
        return AGE_BINS[3][0]

    if x < 7:
        return AGE_BINS[4][0]

    return AGE_BINS[5][0]


def wilson(k, n):

    if n == 0:
        return None, None

    p = k / n
    z2 = Z95 * Z95

    d = 1 + z2 / n

    c = (
        p
        + z2 / (2 * n)
    ) / d

    h = (
        Z95
        / d
        * math.sqrt(
            p * (1 - p) / n
            + z2 / (4 * n * n)
        )
    )

    return (
        max(0.0, c - h),
        min(1.0, c + h),
    )


def close(a, b, tol=1e-10):

    if a is None and b is None:
        return

    if a is None or b is None:
        raise AuditError(
            f"None mismatch: {a!r} vs {b!r}"
        )

    if not math.isclose(
        float(a),
        float(b),
        rel_tol=1e-12,
        abs_tol=tol,
    ):
        raise AuditError(
            f"numeric mismatch {a} vs {b}"
        )


# ------------------------------------------------------------
# Independently reconstruct state ages from frozen inputs
# ------------------------------------------------------------

sel = defaultdict(dict)
selection_count = 0

with SELECTION.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    for row in csv.DictReader(stream):

        selection_count += 1

        idx = int(
            row[
                "final_g6_sample_index"
            ]
        )

        role = row[
            "object_role"
        ]

        sel[idx][role] = row


if selection_count != EXPECTED_OBJECT_ROWS:
    raise AuditError(
        "selection row count mismatch"
    )


metrics = {}

with METRICS.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    for row in csv.DictReader(stream):

        idx = int(
            row[
                "final_g6_sample_index"
            ]
        )

        metrics[idx] = row


actual_events = {}

with EVENT.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    for row in csv.DictReader(stream):

        idx = int(
            row[
                "final_g6_sample_index"
            ]
        )

        actual_events[idx] = row


if (
    len(metrics) != EXPECTED_EVENTS
    or len(actual_events) != EXPECTED_EVENTS
):
    raise AuditError(
        "event count mismatch"
    )


reconstructed = []

for idx in range(
    1,
    EXPECTED_EVENTS + 1,
):

    pair = sel[idx]

    one = pair["obj1"]
    two = pair["obj2"]

    replay = utc(
        one[
            "replay_time_utc"
        ]
    )

    if replay != utc(
        two[
            "replay_time_utc"
        ]
    ):
        raise AuditError(
            "pair replay mismatch"
        )


    e1 = utc(
        one[
            "replay_EPOCH"
        ]
    )

    e2 = utc(
        two[
            "replay_EPOCH"
        ]
    )

    c1 = utc(
        one[
            "replay_CREATION_DATE"
        ]
    )

    c2 = utc(
        two[
            "replay_CREATION_DATE"
        ]
    )


    s1 = (
        replay - e1
    ).total_seconds() / 86400.0

    s2 = (
        replay - e2
    ).total_seconds() / 86400.0

    p1 = (
        replay - c1
    ).total_seconds() / 86400.0

    p2 = (
        replay - c2
    ).total_seconds() / 86400.0

    if p1 < -1e-12 or p2 < -1e-12:
        raise AuditError(
            "negative publication age"
        )


    pair_state = max(
        s1,
        s2,
    )

    pair_pub = max(
        max(
            p1,
            0.0,
        ),
        max(
            p2,
            0.0,
        ),
    )

    metric = metrics[idx]
    emitted = actual_events[idx]

    if emitted["age_bin"] != bin_for(
        pair_state
    ):
        raise AuditError(
            f"age-bin mismatch {idx}"
        )

    close(
        emitted[
            "pair_max_state_age_days"
        ],
        pair_state,
    )

    close(
        emitted[
            "pair_max_publication_age_days"
        ],
        pair_pub,
    )


    valid = as_bool(
        metric[
            "valid_associated"
        ]
    )

    evaluable = as_bool(
        metric[
            "evaluable"
        ]
    )

    ed = None
    pair_prop = None


    if valid:

        predicted = utc(
            metric[
                "predicted_tca_utc"
            ]
        )

        pair_prop = max(
            abs(
                (
                    predicted - e1
                ).total_seconds()
                / 86400.0
            ),
            abs(
                (
                    predicted - e2
                ).total_seconds()
                / 86400.0
            ),
        )

        ed = float(
            metric[
                "absolute_miss_distance_error_km"
            ]
        )

        close(
            emitted[
                "pair_max_propagation_span_days"
            ],
            pair_prop,
        )

        close(
            emitted[
                "absolute_miss_distance_error_km"
            ],
            ed,
        )


    reconstructed.append(
        {
            "idx":
                idx,

            "bin":
                bin_for(
                    pair_state
                ),

            "state":
                pair_state,

            "publication":
                pair_pub,

            "propagation":
                pair_prop,

            "valid":
                valid,

            "evaluable":
                evaluable,

            "Ed":
                ed,

            "temporal":
                metric[
                    "g6_temporal_stratum"
                ],
        }
    )


if sum(
    row["valid"]
    for row in reconstructed
) != EXPECTED_VALID:
    raise AuditError(
        "valid count mismatch"
    )


if sum(
    row["evaluable"]
    for row in reconstructed
) != EXPECTED_EVALUABLE:
    raise AuditError(
        "evaluable count mismatch"
    )


# ------------------------------------------------------------
# Independently check all six age rows
# ------------------------------------------------------------

with AGE.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    age_rows = list(
        csv.DictReader(stream)
    )


if len(age_rows) != 6:
    raise AuditError(
        "age output row count mismatch"
    )


for emitted, (
    age_id,
    age_label,
) in zip(
    age_rows,
    AGE_BINS,
):

    if (
        emitted["age_bin"] != age_id
        or emitted["age_bin_label"] != age_label
    ):
        raise AuditError(
            "age row ordering mismatch"
        )

    subset = [
        row
        for row in reconstructed
        if row["bin"] == age_id
    ]

    total = len(subset)

    eval_n = sum(
        row["evaluable"]
        for row in subset
    )

    valid_n = sum(
        row["valid"]
        for row in subset
    )

    if int(
        emitted[
            "total_events"
        ]
    ) != total:
        raise AuditError(
            "age total mismatch"
        )

    if int(
        emitted[
            "evaluable_count"
        ]
    ) != eval_n:
        raise AuditError(
            "age evaluable mismatch"
        )

    if int(
        emitted[
            "valid_associated_count"
        ]
    ) != valid_n:
        raise AuditError(
            "age valid mismatch"
        )


    errors = [
        row["Ed"]
        for row in subset
        if row["Ed"] is not None
    ]

    successes = sum(
        value <= MISS_RADIUS
        for value in errors
    )

    if int(
        emitted[
            "Ed_successes"
        ]
    ) != successes:
        raise AuditError(
            "age E_d successes mismatch"
        )

    if int(
        emitted[
            "Ed_denominator"
        ]
    ) != len(errors):
        raise AuditError(
            "age E_d denominator mismatch"
        )

    if errors:

        coverage = (
            successes
            / len(errors)
        )

        lo, hi = wilson(
            successes,
            len(errors),
        )

        close(
            emitted[
                "Ed_coverage"
            ],
            coverage,
        )

        close(
            emitted[
                "Ed_wilson_95_lower"
            ],
            lo,
        )

        close(
            emitted[
                "Ed_wilson_95_upper"
            ],
            hi,
        )

        array = np.asarray(
            errors,
            dtype=float,
        )

        for field, q in (
            ("Ed_median_km", 0.50),
            ("Ed_p90_km", 0.90),
            ("Ed_p95_km", 0.95),
            ("Ed_p99_km", 0.99),
        ):
            close(
                emitted[field],
                float(
                    np.quantile(
                        array,
                        q,
                    )
                ),
            )

        close(
            emitted[
                "Ed_maximum_km"
            ],
            max(errors),
        )


# ------------------------------------------------------------
# Independently reproduce pooled state-age Spearman/bootstrap
# ------------------------------------------------------------

valid = [
    row
    for row in reconstructed
    if row["valid"]
]

x = np.asarray(
    [
        row["state"]
        for row in valid
    ],
    dtype=float,
)

y = np.asarray(
    [
        row["Ed"]
        for row in valid
    ],
    dtype=float,
)

rho = float(
    spearmanr(
        x,
        y,
    ).statistic
)

rng = np.random.default_rng(
    SEED
)

boot = []

for _ in range(
    REPLICATES
):

    idx = rng.integers(
        0,
        len(x),
        size=len(x),
    )

    bx = x[idx]
    by = y[idx]

    if (
        len(np.unique(bx)) < 2
        or len(np.unique(by)) < 2
    ):
        continue

    value = float(
        spearmanr(
            bx,
            by,
        ).statistic
    )

    if math.isfinite(value):
        boot.append(
            value
        )


boot_array = np.asarray(
    boot,
    dtype=float,
)

lo = float(
    np.quantile(
        boot_array,
        0.025,
    )
)

hi = float(
    np.quantile(
        boot_array,
        0.975,
    )
)


with QUALITY.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    quality_rows = list(
        csv.DictReader(stream)
    )


pooled = [
    row
    for row in quality_rows
    if row[
        "scope"
    ] == "ALL_G7_G6_SAMPLE"
]


if len(pooled) != 1:
    raise AuditError(
        "expected exactly one pooled state-age association"
    )

pooled = pooled[0]

if int(
    pooled["n"]
) != EXPECTED_VALID:
    raise AuditError(
        "pooled association support mismatch"
    )

close(
    pooled[
        "spearman_rho"
    ],
    rho,
)

close(
    pooled[
        "bootstrap_95_lower"
    ],
    lo,
)

close(
    pooled[
        "bootstrap_95_upper"
    ],
    hi,
)

if int(
    pooled[
        "bootstrap_replicates_valid"
    ]
) != len(boot):
    raise AuditError(
        "bootstrap valid-count mismatch"
    )


# Temporal point estimates independently checked.
for stratum in (
    "G6-T1",
    "G6-T2",
    "G6-T3",
    "G6-T4",
    "G6-T5",
):

    subset = [
        row
        for row in valid
        if row[
            "temporal"
        ] == stratum
    ]

    sx = np.asarray(
        [
            row["state"]
            for row in subset
        ],
        dtype=float,
    )

    sy = np.asarray(
        [
            row["Ed"]
            for row in subset
        ],
        dtype=float,
    )

    srho = float(
        spearmanr(
            sx,
            sy,
        ).statistic
    )

    matches = [
        row
        for row in quality_rows
        if (
            row[
                "scope"
            ]
            == "TEMPORAL_STRATUM"
            and row[
                "temporal_stratum"
            ]
            == stratum
        )
    ]

    if len(matches) != 1:
        raise AuditError(
            f"temporal association row missing: {stratum}"
        )

    if int(
        matches[0]["n"]
    ) != len(subset):
        raise AuditError(
            f"temporal n mismatch: {stratum}"
        )

    close(
        matches[0][
            "spearman_rho"
        ],
        srho,
    )


summary = json.loads(
    SUMMARY.read_text(
        encoding="utf-8"
    )
)

if (
    summary.get("status")
    != "G7A_ZERO_PROPAGATION_QUALITY_ANALYSIS_PASS"
):
    raise AuditError(
        "G7-A summary is not PASS"
    )


for key in (
    "orbital_propagation_performed",
    "candidate_generation_performed",
    "association_recomputed",
    "CAL0_refit_performed",
    "method_retuning_performed",
):

    if summary.get(key) is not False:
        raise AuditError(
            f"forbidden operation reported: {key}"
        )


if int(
    summary.get(
        "raw_GP_HISTORY_rows_scanned",
        -1,
    )
) != 0:
    raise AuditError(
        "raw history was scanned"
    )


if int(
    summary.get(
        "network_requests",
        -1,
    )
) != 0:
    raise AuditError(
        "network activity reported"
    )


result = {
    "status":
        "G7A_ZERO_PROPAGATION_QUALITY_INDEPENDENT_AUDIT_PASS",

    "events_independently_reconstructed":
        EXPECTED_EVENTS,

    "selection_rows_independently_checked":
        EXPECTED_OBJECT_ROWS,

    "VALID_ASSOCIATED_events":
        EXPECTED_VALID,

    "evaluable_events":
        EXPECTED_EVALUABLE,

    "age_strata_verified":
        6,

    "state_age_spearman_rho":
        rho,

    "state_age_bootstrap_95_lower":
        lo,

    "state_age_bootstrap_95_upper":
        hi,

    "bootstrap_replicates_requested":
        REPLICATES,

    "bootstrap_replicates_valid":
        len(boot),

    "bootstrap_seed":
        SEED,

    "event_output_sha256":
        sha256(EVENT),

    "age_output_sha256":
        sha256(AGE),

    "quality_output_sha256":
        sha256(QUALITY),

    "summary_sha256":
        sha256(SUMMARY),

    "raw_GP_HISTORY_rows_scanned_by_audit":
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

    "next_safe_action":
        "FREEZE_G7A_THEN_DECIDE_CONTROLLED_RAW_HISTORY_PASS",
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

            "audit_artifact_sha256":
                sha256(
                    OUT
                ),
        },
        indent=2,
        sort_keys=True,
    )
)