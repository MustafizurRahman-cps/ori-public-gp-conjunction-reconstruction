from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr


ROOT = Path.cwd()

sys.path.insert(
    0,
    str(ROOT / "scripts" / "g5"),
)

import select_lstar_controlflow_amended as s


# ---------------------------------------------------------------------
# Frozen inputs
# ---------------------------------------------------------------------

ALLOCATION = (
    ROOT
    / "docs/research/G5_V2_Calibration_Allocation_Register.csv"
)

PRELOCK_V2 = (
    ROOT
    / "docs/research/G5_PreOutcome_Calibration_Lock_v2.md"
)

PRELOCK_V2_1 = (
    ROOT
    / "docs/research/G5_PreOutcome_Calibration_Lock_v2_1_Amendment.md"
)

CONTROLFLOW_SCRIPT = (
    ROOT
    / "scripts/g5/select_lstar_controlflow_amended.py"
)

REPLAY_AMENDMENT = (
    ROOT
    / "docs/research/G5_LStar_ReplayTime_Amendment.md"
)

FINAL_LSTAR_CLOSURE = (
    ROOT
    / "docs/research/G5_LStar_Final_PreCalibration_Closure.md"
)

EQUIVALENCE_AUDIT = (
    ROOT
    / "results/g5/G5_LStar_ControlFlow_Equivalence_Audit.txt"
)

TRACSS = (
    ROOT
    / "data/raw/g2/"
      "IVV_Releasable_Dataset_SFSH_DiscreteHBR.csv.gz"
)

LEGACY_EVENT_TABLE = (
    ROOT
    / "results/g5/G5_Event_Level_Calibration.csv"
)

LEGACY_T08 = (
    ROOT
    / "tables/publication/T08_Uncertainty_Calibration_Results.csv"
)


EXPECTED_HASHES = {
    ALLOCATION:
        "78ea7546d0cf679fc00fcb9414c9ba7c1e032b8ec9ab981b9bc9fdb06d975514",

    PRELOCK_V2:
        "e1fbee0e4f793c4d974227faa7f959f3597c80788eabb93ea0786f214a63499d",

    PRELOCK_V2_1:
        "d80c91400c6f9193c8b62b3cf7868d4465ba118b88de99a8fd7373535c0e7e2c",

    CONTROLFLOW_SCRIPT:
        "553578f4e90b044c6a783209a756ea3accc009ecbb7a09853e8cae46503f33c2",

    REPLAY_AMENDMENT:
        "c23dad07a9458eb40720e9d63bd277b528cb3f7c106272460f0d17a31dc66f34",

    FINAL_LSTAR_CLOSURE:
        "424b1a6fd83d591049ec982e0c744e2fb91d34b48dc63221f68a2f377b9f9a42",

    EQUIVALENCE_AUDIT:
        "046d5c6a114f2022ca1ea2bafe4f095b87cf31451abeddb4f1dc198c17a7e5e4",
}


# ---------------------------------------------------------------------
# New v2 outputs -- historical zero-row outputs remain untouched.
# ---------------------------------------------------------------------

EVENT_OUT = (
    ROOT
    / "results/g5/G5_Event_Level_Calibration_v2.csv"
)

SUMMARY_OUT = (
    ROOT
    / "results/g5/G5_Calibration_OOF_Summary_v2.csv"
)

AGE_SUPPORT_OUT = (
    ROOT
    / "results/g5/G5_Calibration_Age_Support_v2.csv"
)

QUALITY_OUT = (
    ROOT
    / "results/g5/G5_Quality_Spearman_v2.csv"
)

DECISION_OUT = (
    ROOT
    / "results/g5/G5_Calibration_Decision_v2.json"
)


LSTAR_HOURS = 48

LEVELS = (
    0.50,
    0.80,
    0.90,
    0.95,
)

PRIMARY_LEVEL = 0.90

Z95 = 1.959963984540054

BOOTSTRAP_REPLICATES = 2000

BOOTSTRAP_SEED = 20260908

EXPECTED_EVENTS = 177

EXPECTED_OOF = 141

EXPECTED_RAW_TRACSS_ROWS = 283595

SECONDS_PER_DAY = 86400.0

QUALITY_WINDOW = timedelta(days=30)


AGE_BINS = (
    ("LT1", "<1 d"),
    ("1_LT2", "1–<2 d"),
    ("2_LT3", "2–<3 d"),
    ("3_LT5", "3–<5 d"),
    ("5_LT7", "5–<7 d"),
    ("GE7", ">=7 d"),
)


class CalibrationError(RuntimeError):
    pass


# ---------------------------------------------------------------------
# General utilities
# ---------------------------------------------------------------------

def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def verify_hashes() -> dict[str, str]:
    result = {}

    for path, expected in EXPECTED_HASHES.items():
        actual = sha256(path)

        if actual.lower() != expected.lower():
            raise CalibrationError(
                "frozen input SHA-256 mismatch: "
                f"{path.relative_to(ROOT)}: "
                f"{actual} != {expected}"
            )

        result[
            path.relative_to(ROOT).as_posix()
        ] = actual

    return result


def parse_utc(
    value: str,
    *,
    schema_declares_utc: bool = False,
) -> datetime:

    text = value.strip()

    if not text:
        raise CalibrationError(
            "empty timestamp"
        )

    text = text.replace(" ", "T")

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CalibrationError(
            f"malformed timestamp: {value!r}"
        ) from exc

    if parsed.tzinfo is None:
        if not schema_declares_utc:
            raise CalibrationError(
                "naive timestamp without "
                "declared UTC schema"
            )

        parsed = parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def iso_utc(value: datetime) -> str:
    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def finite(value: float) -> float:
    result = float(value)

    if not math.isfinite(result):
        raise CalibrationError(
            f"nonfinite value: {value!r}"
        )

    return result


def fmt(value) -> str:
    if value is None:
        return ""

    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"

    if isinstance(value, float):
        if not math.isfinite(value):
            raise CalibrationError(
                f"refusing to serialize nonfinite float {value}"
            )

        return format(value, ".17g")

    return str(value)


def write_csv(
    path: Path,
    fields: list[str],
    rows: list[dict],
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            lineterminator="\n",
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    field: fmt(row.get(field))
                    for field in fields
                }
            )


def read_csv(path: Path):
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:

        return list(csv.DictReader(handle))


def assert_historical_zero_row_outputs():
    for path in (
        LEGACY_EVENT_TABLE,
        LEGACY_T08,
    ):
        with path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as handle:

            reader = csv.DictReader(handle)
            rows = list(reader)

        if rows:
            raise CalibrationError(
                "historical HOLD artifact unexpectedly "
                f"contains data rows: "
                f"{path.relative_to(ROOT)}"
            )


# ---------------------------------------------------------------------
# TraCSS protected G5 reference subset
# ---------------------------------------------------------------------

def load_reference_rows(
    wanted_ids: set[str],
) -> tuple[dict[str, dict[str, str]], int]:

    found = {}

    raw_count = 0

    with gzip.open(
        TRACSS,
        "rt",
        encoding="utf-8-sig",
        newline="",
    ) as handle:

        reader = csv.DictReader(handle)

        required = {
            "conj_id",
            "epoch",
            "min_range",
        }

        missing = (
            required
            - set(reader.fieldnames or [])
        )

        if missing:
            raise CalibrationError(
                "TraCSS required fields missing: "
                f"{sorted(missing)}"
            )

        for row in reader:
            raw_count += 1

            cid = row["conj_id"].strip()

            if cid not in wanted_ids:
                continue

            if cid in found:
                raise CalibrationError(
                    f"duplicate frozen conj_id in "
                    f"TraCSS source: {cid}"
                )

            found[cid] = {
                "conj_id": cid,
                "epoch": row["epoch"].strip(),
                "min_range":
                    row["min_range"].strip(),
            }

    if raw_count != EXPECTED_RAW_TRACSS_ROWS:
        raise CalibrationError(
            f"TraCSS row count {raw_count} "
            f"!= {EXPECTED_RAW_TRACSS_ROWS}"
        )

    if set(found) != wanted_ids:
        missing = sorted(
            wanted_ids - set(found),
            key=int,
        )

        raise CalibrationError(
            "missing frozen G5 TraCSS rows: "
            f"{missing[:10]}"
        )

    return found, raw_count


# ---------------------------------------------------------------------
# Publication/state quality semantics
# ---------------------------------------------------------------------

def canonical_state_signature(
    record,
) -> tuple[str, ...]:

    payload = record.payload

    if not isinstance(payload, dict):
        raise CalibrationError(
            "GP record payload is not a dict"
        )

    line1 = str(
        payload.get("TLE_LINE1", "")
    ).rstrip("\r\n")

    line2 = str(
        payload.get("TLE_LINE2", "")
    ).rstrip("\r\n")

    if (
        len(line1) < 63
        or len(line2) < 63
        or not line1.startswith("1 ")
        or not line2.startswith("2 ")
    ):
        raise CalibrationError(
            "malformed TLE while constructing "
            "canonical state signature"
        )

    # SGP4-relevant state/model fields only.
    #
    # Publication metadata such as CREATION_DATE,
    # GP_ID, element-set number, checksum, and
    # revolution counter are excluded.
    #
    # EPOCH is retained because state epoch is part
    # of the physical/model state.

    return (
        iso_utc(record.epoch_utc),

        line1[33:43].strip(),  # ndot
        line1[44:52].strip(),  # nddot
        line1[53:61].strip(),  # BSTAR
        line1[62:63].strip(),  # ephemeris type

        line2[8:16].strip(),   # inclination
        line2[17:25].strip(),  # RAAN
        line2[26:33].strip(),  # eccentricity
        line2[34:42].strip(),  # argument of perigee
        line2[43:51].strip(),  # mean anomaly
        line2[52:63].strip(),  # mean motion
    )


def object_quality(
    object_id: str,
    current,
    replay: datetime,
    predicted_tca: datetime,
    object_records,
    audit_row: dict[str, str],
) -> dict:

    if current.creation_date_utc > replay:
        raise CalibrationError(
            "causal current GP published after replay"
        )

    state_age_days = (
        replay - current.epoch_utc
    ).total_seconds() / SECONDS_PER_DAY

    publication_age_days = (
        replay - current.creation_date_utc
    ).total_seconds() / SECONDS_PER_DAY

    if publication_age_days < -1.0e-12:
        raise CalibrationError(
            "negative publication age under causal replay"
        )

    window_start = (
        replay - QUALITY_WINDOW
    )

    window_records = [
        record
        for record in object_records
        if (
            record.creation_date_utc > window_start
            and record.creation_date_utc <= replay
        )
    ]

    publication_count = len(
        window_records
    )

    distinct_state_count = len(
        {
            canonical_state_signature(record)
            for record in window_records
        }
    )

    publication_times = sorted(
        record.creation_date_utc
        for record in window_records
    )

    cadence_hours = [
        (
            publication_times[index]
            - publication_times[index - 1]
        ).total_seconds() / 3600.0
        for index in range(
            1,
            len(publication_times),
        )
    ]

    if any(
        value < 0.0
        for value in cadence_hours
    ):
        raise CalibrationError(
            "negative publication cadence"
        )

    median_cadence_hours = (
        statistics.median(
            cadence_hours
        )
        if cadence_hours
        else None
    )

    max_gap_hours = (
        max(cadence_hours)
        if cadence_hours
        else None
    )

    lower_bound = parse_utc(
        audit_row["lower_bound_utc"]
    )

    # The 30-day window is left-censored only
    # when the guaranteed acquisition interval
    # begins after the window lower boundary.
    #
    # A single predecessor does not synthesize
    # missing publications within an uncovered
    # rolling window.
    window_left_censored = (
        lower_bound > window_start
    )

    acquisition_left_censored = (
        audit_row[
            "left_censored"
        ].strip().lower()
        == "true"
    )

    signed_propagation_interval_days = (
        predicted_tca
        - current.epoch_utc
    ).total_seconds() / SECONDS_PER_DAY

    propagation_span_days = abs(
        signed_propagation_interval_days
    )

    payload = current.payload

    if not isinstance(payload, dict):
        raise CalibrationError(
            "current GP payload missing"
        )

    return {
        "current_GP_ID":
            str(payload.get("GP_ID", "")),

        "state_age_days":
            state_age_days,

        "publication_age_days":
            publication_age_days,

        "publication_count_30d":
            publication_count,

        "distinct_state_count_30d":
            distinct_state_count,

        "median_cadence_hours":
            median_cadence_hours,

        "max_gap_hours":
            max_gap_hours,

        "window_left_censored":
            window_left_censored,

        "acquisition_left_censored":
            acquisition_left_censored,

        "signed_propagation_interval_days":
            signed_propagation_interval_days,

        "propagation_span_days":
            propagation_span_days,
    }


def age_bin(
    state_age_days: float,
) -> tuple[str, str]:

    x = finite(state_age_days)

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


# ---------------------------------------------------------------------
# Empirical envelopes and calibration metrics
# ---------------------------------------------------------------------

def empirical_envelope(
    errors: list[float],
    nominal: float,
) -> tuple[float | None, int | None]:

    values = sorted(
        finite(value)
        for value in errors
    )

    n = len(values)

    if n == 0:
        return None, None

    k = min(
        n,
        math.ceil(
            (n + 1)
            * nominal
        ),
    )

    if k < 1:
        raise CalibrationError(
            "invalid empirical order-statistic index"
        )

    return values[k - 1], k


def wilson_interval(
    successes: int,
    n: int,
) -> tuple[float | None, float | None]:

    if n <= 0:
        return None, None

    p = successes / n

    z2 = Z95 * Z95

    denominator = (
        1.0
        + z2 / n
    )

    center = (
        p
        + z2 / (2.0 * n)
    ) / denominator

    half = (
        Z95
        / denominator
        * math.sqrt(
            p * (1.0 - p) / n
            + z2 / (4.0 * n * n)
        )
    )

    return (
        max(0.0, center - half),
        min(1.0, center + half),
    )


def summarize_predictions(
    *,
    method: str,
    target: str,
    nominal: float,
    scope: str,
    fold: str,
    predictions: list[dict],
    units: str,
) -> dict:

    supported = [
        item
        for item in predictions
        if item["radius"] is not None
    ]

    unsupported = (
        len(predictions)
        - len(supported)
    )

    successes = sum(
        item["error"]
        <= item["radius"]
        for item in supported
    )

    denominator = len(supported)

    coverage = (
        successes / denominator
        if denominator
        else None
    )

    lower, upper = wilson_interval(
        successes,
        denominator,
    )

    median_radius = (
        statistics.median(
            item["radius"]
            for item in supported
        )
        if supported
        else None
    )

    calibration_error = (
        abs(
            coverage - nominal
        )
        if coverage is not None
        else None
    )

    support_fraction = (
        denominator / len(predictions)
        if predictions
        else None
    )

    return {
        "method":
            method,

        "target":
            target,

        "nominal_coverage":
            nominal,

        "scope":
            scope,

        "fold":
            fold,

        "validation_events":
            len(predictions),

        "supported_events":
            denominator,

        "unsupported_events":
            unsupported,

        "support_fraction":
            support_fraction,

        "successes":
            successes,

        "coverage_denominator":
            denominator,

        "observed_coverage":
            coverage,

        "wilson_95_lower":
            lower,

        "wilson_95_upper":
            upper,

        "absolute_calibration_error":
            calibration_error,

        "median_envelope_radius":
            median_radius,

        "units":
            units,
    }


# ---------------------------------------------------------------------
# Spearman / bootstrap
# ---------------------------------------------------------------------

QUALITY_VARIABLES = (
    (
        "pair_max_state_age_days",
        "pair_max_state_age_days",
    ),
    (
        "pair_min_distinct_state_count_30d",
        "pair_min_distinct_state_count_30d",
    ),
    (
        "pair_max_gap_hours",
        "pair_max_gap_hours",
    ),
    (
        "pair_max_propagation_span_days",
        "pair_max_propagation_span_days",
    ),
)


def spearman_value(
    rows: list[dict],
    field: str,
) -> tuple[float | None, int]:

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
        [pair[0] for pair in pairs],
        dtype=float,
    )

    y = np.asarray(
        [pair[1] for pair in pairs],
        dtype=float,
    )

    if (
        len(np.unique(x)) < 2
        or len(np.unique(y)) < 2
    ):
        return None, n

    result = spearmanr(
        x,
        y,
    )

    rho = float(
        result.statistic
    )

    if not math.isfinite(rho):
        return None, n

    return rho, n


def bootstrap_spearman(
    rows: list[dict],
    field: str,
    rng: np.random.Generator,
) -> tuple[
    float | None,
    float | None,
    int,
]:

    pairs = [
        (
            float(row[field]),
            float(
                row[
                    "absolute_miss_distance_error_km"
                ]
            ),
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

        statistic = float(
            spearmanr(
                bx,
                by,
            ).statistic
        )

        if math.isfinite(statistic):
            values.append(
                statistic
            )

    if not values:
        return None, None, 0

    array = np.asarray(
        values,
        dtype=float,
    )

    lower = float(
        np.quantile(
            array,
            0.025,
        )
    )

    upper = float(
        np.quantile(
            array,
            0.975,
        )
    )

    return (
        lower,
        upper,
        len(values),
    )


# ---------------------------------------------------------------------
# Main event reconstruction
# ---------------------------------------------------------------------

def reconstruct_events():
    input_hashes = verify_hashes()

    assert_historical_zero_row_outputs()

    events, lstar_hashes, audit = (
        s.verify_frozen_inputs()
    )

    if len(events) != EXPECTED_EVENTS:
        raise CalibrationError(
            f"event count "
            f"{len(events)} "
            f"!= {EXPECTED_EVENTS}"
        )

    wanted_ids = {
        event.conj_id
        for event in events
    }

    reference_rows, raw_count = (
        load_reference_rows(
            wanted_ids
        )
    )

    frozen_objects = {
        object_id
        for event in events
        for object_id in (
            event.object1,
            event.object2,
        )
    }

    records = (
        s.load_causal_gp_records(
            frozen_objects,
            audit,
        )
    )

    output_rows = []

    for event in sorted(
        events,
        key=lambda item: (
            int(item.block),
            int(item.conj_id),
        ),
    ):

        cutoff = (
            event.reference_tca_utc
            - timedelta(
                hours=LSTAR_HOURS
            )
        )

        first_cutoff = (
            s.select_or_none(
                records[event.object1],
                cutoff,
            )
        )

        second_cutoff = (
            s.select_or_none(
                records[event.object2],
                cutoff,
            )
        )

        if (
            first_cutoff is None
            or second_cutoff is None
        ):
            raise CalibrationError(
                "48 h frozen evaluability "
                "no longer reproduces: "
                f"{event.conj_id}"
            )

        replay = max(
            first_cutoff.creation_date_utc,
            second_cutoff.creation_date_utc,
        )

        if replay > cutoff:
            raise CalibrationError(
                "publication-driven replay "
                "exceeds nominal cutoff"
            )

        first = s.select_or_none(
            records[event.object1],
            replay,
        )

        second = s.select_or_none(
            records[event.object2],
            replay,
        )

        if (
            first is None
            or second is None
        ):
            raise CalibrationError(
                "missing current GP at "
                "publication-driven replay"
            )

        if (
            first.stable_tie_key
            != first_cutoff.stable_tie_key
            or
            second.stable_tie_key
            != second_cutoff.stable_tie_key
        ):
            raise CalibrationError(
                "GP identity changed between "
                "nominal cutoff and corrected replay"
            )

        sat1 = s.satrec_from_record(
            first
        )

        sat2 = s.satrec_from_record(
            second
        )

        quantity = s.g4.Sgp4PairQuantity(
            sat1,
            sat2,
            replay,
        )

        enumeration = (
            s.enumerate_candidates_redundancy_safe(
                quantity
            )
        )

        if not enumeration.candidates:
            raise CalibrationError(
                "48 h event unexpectedly "
                "has no candidate: "
                f"{event.conj_id}"
            )

        reference_time_s = (
            event.reference_tca_utc
            - replay
        ).total_seconds()

        ref = reference_rows[
            event.conj_id
        ]

        raw_epoch = parse_utc(
            ref["epoch"],
            schema_declares_utc=True,
        )

        if (
            raw_epoch
            != event.reference_tca_utc
        ):
            raise CalibrationError(
                "TraCSS epoch differs from "
                "frozen allocation TCA: "
                f"{event.conj_id}"
            )

        reference_miss_km = finite(
            ref["min_range"]
        )

        if reference_miss_km < 0.0:
            raise CalibrationError(
                "negative reference miss distance"
            )

        association = (
            s.g4.associate_reference_event(
                enumeration.candidates,
                reference_time_s,
                s.ASSOCIATION_TOLERANCE_MIN,
                reference_distance_km=
                    reference_miss_km,
            )
        )

        if (
            association.status
            != "VALID_ASSOCIATED"
            or association.candidate is None
        ):
            raise CalibrationError(
                "48 h corrected frozen event "
                "not associated: "
                f"{event.conj_id}"
            )

        candidate = (
            association.candidate
        )

        predicted_tca = (
            replay
            + timedelta(
                seconds=candidate.time_s
            )
        )

        predicted_miss_km = finite(
            candidate.distance_km
        )

        signed_tca_residual_s = (
            predicted_tca
            - event.reference_tca_utc
        ).total_seconds()

        absolute_tca_error_s = abs(
            signed_tca_residual_s
        )

        signed_miss_residual_km = (
            predicted_miss_km
            - reference_miss_km
        )

        absolute_miss_error_km = abs(
            signed_miss_residual_km
        )

        q1 = object_quality(
            event.object1,
            first,
            replay,
            predicted_tca,
            records[event.object1],
            audit[event.object1],
        )

        q2 = object_quality(
            event.object2,
            second,
            replay,
            predicted_tca,
            records[event.object2],
            audit[event.object2],
        )

        pair_max_state_age = max(
            q1["state_age_days"],
            q2["state_age_days"],
        )

        pair_max_publication_age = max(
            q1["publication_age_days"],
            q2["publication_age_days"],
        )

        pair_min_distinct_state_count = min(
            q1["distinct_state_count_30d"],
            q2["distinct_state_count_30d"],
        )

        # Pair maximum recent gap is estimable only
        # when both object-level gap estimates exist.
        if (
            q1["max_gap_hours"] is None
            or q2["max_gap_hours"] is None
        ):
            pair_max_gap = None
        else:
            pair_max_gap = max(
                q1["max_gap_hours"],
                q2["max_gap_hours"],
            )

        pair_max_propagation_span = max(
            q1["propagation_span_days"],
            q2["propagation_span_days"],
        )

        age_id, age_label = age_bin(
            pair_max_state_age
        )

        output_rows.append(
            {
                "event_id":
                    event.conj_id,

                "fold":
                    event.block,

                "analysis_unit":
                    "OBJECT_DISJOINT_EVENT",

                "obj1":
                    event.object1,

                "obj2":
                    event.object2,

                "status":
                    "VALID_ASSOCIATED",

                "lstar_hours":
                    LSTAR_HOURS,

                "nominal_cutoff_utc":
                    iso_utc(cutoff),

                "causal_replay_time_utc":
                    iso_utc(replay),

                "cutoff_minus_replay_hours":
                    (
                        cutoff - replay
                    ).total_seconds()
                    / 3600.0,

                "obj1_current_GP_ID":
                    q1["current_GP_ID"],

                "obj2_current_GP_ID":
                    q2["current_GP_ID"],

                "obj1_state_age_days":
                    q1["state_age_days"],

                "obj2_state_age_days":
                    q2["state_age_days"],

                "pair_max_state_age_days":
                    pair_max_state_age,

                "obj1_publication_age_days":
                    q1[
                        "publication_age_days"
                    ],

                "obj2_publication_age_days":
                    q2[
                        "publication_age_days"
                    ],

                "pair_max_publication_age_days":
                    pair_max_publication_age,

                "obj1_publication_count_30d":
                    q1[
                        "publication_count_30d"
                    ],

                "obj2_publication_count_30d":
                    q2[
                        "publication_count_30d"
                    ],

                "obj1_distinct_state_count_30d":
                    q1[
                        "distinct_state_count_30d"
                    ],

                "obj2_distinct_state_count_30d":
                    q2[
                        "distinct_state_count_30d"
                    ],

                "pair_min_distinct_state_count_30d":
                    pair_min_distinct_state_count,

                "obj1_median_cadence_hours":
                    q1[
                        "median_cadence_hours"
                    ],

                "obj2_median_cadence_hours":
                    q2[
                        "median_cadence_hours"
                    ],

                "obj1_max_gap_hours":
                    q1[
                        "max_gap_hours"
                    ],

                "obj2_max_gap_hours":
                    q2[
                        "max_gap_hours"
                    ],

                "pair_max_gap_hours":
                    pair_max_gap,

                "obj1_window_left_censored":
                    q1[
                        "window_left_censored"
                    ],

                "obj2_window_left_censored":
                    q2[
                        "window_left_censored"
                    ],

                "pair_window_left_censored":
                    (
                        q1[
                            "window_left_censored"
                        ]
                        or
                        q2[
                            "window_left_censored"
                        ]
                    ),

                "obj1_acquisition_left_censored":
                    q1[
                        "acquisition_left_censored"
                    ],

                "obj2_acquisition_left_censored":
                    q2[
                        "acquisition_left_censored"
                    ],

                "obj1_signed_propagation_interval_days":
                    q1[
                        "signed_propagation_interval_days"
                    ],

                "obj2_signed_propagation_interval_days":
                    q2[
                        "signed_propagation_interval_days"
                    ],

                "obj1_propagation_span_days":
                    q1[
                        "propagation_span_days"
                    ],

                "obj2_propagation_span_days":
                    q2[
                        "propagation_span_days"
                    ],

                "pair_max_propagation_span_days":
                    pair_max_propagation_span,

                "age_bin":
                    age_id,

                "age_bin_label":
                    age_label,

                "predicted_tca_utc":
                    iso_utc(predicted_tca),

                "predicted_miss_distance_km":
                    predicted_miss_km,

                "reference_tca_utc":
                    iso_utc(
                        event.reference_tca_utc
                    ),

                "reference_miss_distance_km":
                    reference_miss_km,

                "signed_tca_residual_s":
                    signed_tca_residual_s,

                "absolute_tca_error_s":
                    absolute_tca_error_s,

                "signed_miss_distance_residual_km":
                    signed_miss_residual_km,

                "absolute_miss_distance_error_km":
                    absolute_miss_error_km,

                "rejected_redundant_brackets":
                    enumeration
                    .rejected_local_inconsistency_count,

                "recovered_redundant_brackets":
                    enumeration
                    .recovered_local_inconsistency_count,

                # Populated below for primary 90% OOF.
                "cal0_train_n_90":
                    None,

                "cal0_order_statistic_k_90":
                    None,

                "cal0_envelope_km_90":
                    None,

                "cal0_covered_90":
                    None,

                "calage_train_n_90":
                    None,

                "calage_order_statistic_k_90":
                    None,

                "calage_envelope_km_90":
                    None,

                "calage_covered_90":
                    None,

                "calage_support_status_90":
                    (
                        "TRAINING_ONLY"
                        if event.block == "1"
                        else None
                    ),
            }
        )

    if len(output_rows) != EXPECTED_EVENTS:
        raise CalibrationError(
            "reconstructed event count mismatch"
        )

    block_counts = Counter(
        row["fold"]
        for row in output_rows
    )

    expected_blocks = {
        "1": 36,
        "2": 36,
        "3": 35,
        "4": 35,
        "5": 35,
    }

    if dict(block_counts) != expected_blocks:
        raise CalibrationError(
            f"block counts changed: "
            f"{dict(block_counts)}"
        )

    return (
        output_rows,
        input_hashes,
        lstar_hashes,
        raw_count,
    )


# ---------------------------------------------------------------------
# Forward OOF calibration
# ---------------------------------------------------------------------

def perform_calibration(
    rows: list[dict],
):
    summary_rows = []

    age_support_rows = []

    pooled_predictions = defaultdict(
        list
    )

    for fold_number in (
        2,
        3,
        4,
        5,
    ):
        fold = str(
            fold_number
        )

        training = [
            row
            for row in rows
            if int(row["fold"])
            < fold_number
        ]

        validation = [
            row
            for row in rows
            if row["fold"] == fold
        ]

        if not training:
            raise CalibrationError(
                "empty forward training set"
            )

        # Age support is independent of nominal level.
        for age_id, age_label in AGE_BINS:
            train_n = sum(
                row["age_bin"] == age_id
                for row in training
            )

            validation_n = sum(
                row["age_bin"] == age_id
                for row in validation
            )

            supported_n = (
                validation_n
                if train_n > 0
                else 0
            )

            age_support_rows.append(
                {
                    "validation_fold":
                        fold,

                    "age_bin":
                        age_id,

                    "age_bin_label":
                        age_label,

                    "training_events":
                        train_n,

                    "validation_events":
                        validation_n,

                    "supported_validation_events":
                        supported_n,

                    "unsupported_validation_events":
                        validation_n
                        - supported_n,
                }
            )

        for nominal in LEVELS:

            cal0_d_radius, cal0_d_k = (
                empirical_envelope(
                    [
                        row[
                            "absolute_miss_distance_error_km"
                        ]
                        for row in training
                    ],
                    nominal,
                )
            )

            cal0_t_radius, cal0_t_k = (
                empirical_envelope(
                    [
                        row[
                            "absolute_tca_error_s"
                        ]
                        for row in training
                    ],
                    nominal,
                )
            )

            if (
                cal0_d_radius is None
                or cal0_t_radius is None
            ):
                raise CalibrationError(
                    "CAL-0 unexpectedly unsupported"
                )

            fold_predictions = {
                (
                    "CAL-0",
                    "E_d",
                ): [],

                (
                    "CAL-0",
                    "E_t",
                ): [],

                (
                    "CAL-AGE",
                    "E_d",
                ): [],

                (
                    "CAL-AGE",
                    "E_t",
                ): [],
            }

            for row in validation:
                age_id = row[
                    "age_bin"
                ]

                age_training = [
                    item
                    for item in training
                    if item[
                        "age_bin"
                    ] == age_id
                ]

                calage_d_radius, calage_d_k = (
                    empirical_envelope(
                        [
                            item[
                                "absolute_miss_distance_error_km"
                            ]
                            for item in age_training
                        ],
                        nominal,
                    )
                )

                calage_t_radius, calage_t_k = (
                    empirical_envelope(
                        [
                            item[
                                "absolute_tca_error_s"
                            ]
                            for item in age_training
                        ],
                        nominal,
                    )
                )

                cal0_d_item = {
                    "event_id":
                        row["event_id"],

                    "error":
                        row[
                            "absolute_miss_distance_error_km"
                        ],

                    "radius":
                        cal0_d_radius,
                }

                cal0_t_item = {
                    "event_id":
                        row["event_id"],

                    "error":
                        row[
                            "absolute_tca_error_s"
                        ],

                    "radius":
                        cal0_t_radius,
                }

                calage_d_item = {
                    "event_id":
                        row["event_id"],

                    "error":
                        row[
                            "absolute_miss_distance_error_km"
                        ],

                    "radius":
                        calage_d_radius,
                }

                calage_t_item = {
                    "event_id":
                        row["event_id"],

                    "error":
                        row[
                            "absolute_tca_error_s"
                        ],

                    "radius":
                        calage_t_radius,
                }

                fold_predictions[
                    ("CAL-0", "E_d")
                ].append(
                    cal0_d_item
                )

                fold_predictions[
                    ("CAL-0", "E_t")
                ].append(
                    cal0_t_item
                )

                fold_predictions[
                    ("CAL-AGE", "E_d")
                ].append(
                    calage_d_item
                )

                fold_predictions[
                    ("CAL-AGE", "E_t")
                ].append(
                    calage_t_item
                )

                if nominal == PRIMARY_LEVEL:

                    row[
                        "cal0_train_n_90"
                    ] = len(training)

                    row[
                        "cal0_order_statistic_k_90"
                    ] = cal0_d_k

                    row[
                        "cal0_envelope_km_90"
                    ] = cal0_d_radius

                    row[
                        "cal0_covered_90"
                    ] = (
                        row[
                            "absolute_miss_distance_error_km"
                        ]
                        <= cal0_d_radius
                    )

                    row[
                        "calage_train_n_90"
                    ] = len(
                        age_training
                    )

                    row[
                        "calage_order_statistic_k_90"
                    ] = calage_d_k

                    row[
                        "calage_envelope_km_90"
                    ] = calage_d_radius

                    row[
                        "calage_covered_90"
                    ] = (
                        (
                            row[
                                "absolute_miss_distance_error_km"
                            ]
                            <= calage_d_radius
                        )
                        if calage_d_radius
                        is not None
                        else None
                    )

                    row[
                        "calage_support_status_90"
                    ] = (
                        "SUPPORTED"
                        if calage_d_radius
                        is not None
                        else
                        "INSUFFICIENT_CALIBRATION_SUPPORT"
                    )

            for (
                method,
                target,
            ), predictions in (
                fold_predictions.items()
            ):

                units = (
                    "km"
                    if target == "E_d"
                    else "s"
                )

                summary = (
                    summarize_predictions(
                        method=method,
                        target=target,
                        nominal=nominal,
                        scope="FOLD",
                        fold=fold,
                        predictions=predictions,
                        units=units,
                    )
                )

                summary_rows.append(
                    summary
                )

                pooled_predictions[
                    (
                        method,
                        target,
                        nominal,
                    )
                ].extend(
                    predictions
                )

    # Pooled OOF summaries.
    for (
        method,
        target,
        nominal,
    ), predictions in sorted(
        pooled_predictions.items()
    ):

        units = (
            "km"
            if target == "E_d"
            else "s"
        )

        summary_rows.append(
            summarize_predictions(
                method=method,
                target=target,
                nominal=nominal,
                scope="OOF_ALL",
                fold="B2-B5",
                predictions=predictions,
                units=units,
            )
        )

    oof_rows = [
        row
        for row in rows
        if row["fold"]
        in {
            "2",
            "3",
            "4",
            "5",
        }
    ]

    if len(oof_rows) != EXPECTED_OOF:
        raise CalibrationError(
            f"OOF event count "
            f"{len(oof_rows)} "
            f"!= {EXPECTED_OOF}"
        )

    return (
        summary_rows,
        age_support_rows,
    )


# ---------------------------------------------------------------------
# Quality-usefulness analysis
# ---------------------------------------------------------------------

def perform_quality_analysis(
    rows: list[dict],
) -> list[dict]:

    output = []

    rng = np.random.default_rng(
        BOOTSTRAP_SEED
    )

    for field, label in QUALITY_VARIABLES:

        rho, n = spearman_value(
            rows,
            field,
        )

        lower, upper, valid_boot = (
            bootstrap_spearman(
                rows,
                field,
                rng,
            )
        )

        output.append(
            {
                "scope":
                    "ALL_G5",

                "fold":
                    "B1-B5",

                "quality_variable":
                    label,

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

        for fold in (
            "1",
            "2",
            "3",
            "4",
            "5",
        ):
            subset = [
                row
                for row in rows
                if row["fold"] == fold
            ]

            fold_rho, fold_n = (
                spearman_value(
                    subset,
                    field,
                )
            )

            output.append(
                {
                    "scope":
                        "TEMPORAL_BLOCK",

                    "fold":
                        f"B{fold}",

                    "quality_variable":
                        label,

                    "n":
                        fold_n,

                    "spearman_rho":
                        fold_rho,

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

    return output


# ---------------------------------------------------------------------
# Retention decision
# ---------------------------------------------------------------------

def pooled_primary(
    summary_rows,
    method,
):

    matches = [
        row
        for row in summary_rows
        if (
            row["method"] == method
            and row["target"] == "E_d"
            and row["nominal_coverage"]
                == PRIMARY_LEVEL
            and row["scope"]
                == "OOF_ALL"
        )
    ]

    if len(matches) != 1:
        raise CalibrationError(
            f"expected one pooled primary "
            f"{method} row"
        )

    return matches[0]


def interval_contains_target(
    row,
    target=PRIMARY_LEVEL,
) -> bool:

    lower = row[
        "wilson_95_lower"
    ]

    upper = row[
        "wilson_95_upper"
    ]

    return (
        lower is not None
        and upper is not None
        and lower <= target <= upper
    )


def make_decision(
    rows,
    summary_rows,
    age_support_rows,
    input_hashes,
    lstar_hashes,
    raw_count,
):

    cal0 = pooled_primary(
        summary_rows,
        "CAL-0",
    )

    calage = pooled_primary(
        summary_rows,
        "CAL-AGE",
    )

    cal0_calibrated = (
        cal0[
            "coverage_denominator"
        ] == EXPECTED_OOF
        and interval_contains_target(
            cal0
        )
    )

    calage_full_support = (
        calage[
            "coverage_denominator"
        ] == EXPECTED_OOF
        and calage[
            "unsupported_events"
        ] == 0
    )

    calage_calibrated = (
        calage_full_support
        and interval_contains_target(
            calage
        )
    )

    calage_no_worse = (
        calage[
            "absolute_calibration_error"
        ] is not None
        and cal0[
            "absolute_calibration_error"
        ] is not None
        and (
            calage[
                "absolute_calibration_error"
            ]
            <=
            cal0[
                "absolute_calibration_error"
            ]
            + 1.0e-15
        )
    )

    if (
        calage_calibrated
        and calage_no_worse
    ):
        retained = "CAL-AGE"

    elif cal0_calibrated:
        retained = "CAL-0"

    else:
        retained = (
            "NO_CALIBRATED_EMPIRICAL_"
            "ERROR_ENVELOPE_RETAINED"
        )

    errors_d = [
        row[
            "absolute_miss_distance_error_km"
        ]
        for row in rows
    ]

    errors_t = [
        row[
            "absolute_tca_error_s"
        ]
        for row in rows
    ]

    oof_rows = [
        row
        for row in rows
        if row["fold"] != "1"
    ]

    age_support_aggregate = []

    for age_id, age_label in AGE_BINS:

        relevant = [
            item
            for item in age_support_rows
            if item[
                "age_bin"
            ] == age_id
        ]

        age_support_aggregate.append(
            {
                "age_bin":
                    age_id,

                "age_bin_label":
                    age_label,

                "OOF_validation_events":
                    sum(
                        item[
                            "validation_events"
                        ]
                        for item in relevant
                    ),

                "OOF_supported_events":
                    sum(
                        item[
                            "supported_validation_events"
                        ]
                        for item in relevant
                    ),

                "OOF_unsupported_events":
                    sum(
                        item[
                            "unsupported_validation_events"
                        ]
                        for item in relevant
                    ),
            }
        )

    payload = {
        "status":
            "G5_CALIBRATION_EXECUTED_"
            "DEVELOPMENT_ONLY",

        "selected_L_star_hours":
            LSTAR_HOURS,

        "event_count":
            len(rows),

        "OOF_event_count":
            len(oof_rows),

        "valid_associated_event_count":
            len(rows),

        "reference_miss_distance_field":
            "min_range",

        "reference_miss_distance_units":
            "km",

        "replay_semantics":
            (
                "latest pair-union CREATION_DATE "
                "<= t_ref - 48 h"
            ),

        "primary_error":
            "absolute_miss_distance_error_km",

        "secondary_error":
            "absolute_tca_error_s",

        "development_accuracy_summary": {
            "conditional_median_absolute_"
            "miss_distance_error_km":
                statistics.median(
                    errors_d
                ),

            "conditional_median_absolute_"
            "tca_error_s":
                statistics.median(
                    errors_t
                ),

            "conditional_p90_absolute_"
            "miss_distance_error_km":
                float(
                    np.quantile(
                        np.asarray(
                            errors_d,
                            dtype=float,
                        ),
                        0.90,
                    )
                ),

            "conditional_p95_absolute_"
            "miss_distance_error_km":
                float(
                    np.quantile(
                        np.asarray(
                            errors_d,
                            dtype=float,
                        ),
                        0.95,
                    )
                ),
        },

        "CAL0_primary_90pct_Ed":
            cal0,

        "CALAGE_primary_90pct_Ed":
            calage,

        "CALAGE_full_OOF_support":
            calage_full_support,

        "CALAGE_calibration_error_no_worse_"
        "than_CAL0":
            calage_no_worse,

        "age_support_OOF":
            age_support_aggregate,

        "retained_development_calibration":
            retained,

        "interpretation":
            (
                "Empirical reconstruction-error "
                "envelope only; not covariance, "
                "collision probability, operational "
                "confidence, or external confirmation."
            ),

        "bootstrap": {
            "replicates":
                BOOTSTRAP_REPLICATES,

            "seed":
                BOOTSTRAP_SEED,

            "unit":
                "object-disjoint selected event",
        },

        "network_requests":
            0,

        "G6_protected_outcomes_accessed":
            False,

        "historical_zero_row_outputs_overwritten":
            False,

        "TraCSS_raw_row_count":
            raw_count,

        "TraCSS_raw_sha256":
            sha256(TRACSS),

        "frozen_input_hashes":
            input_hashes,

        "lstar_verifier_input_hashes":
            lstar_hashes,
    }

    return payload


# ---------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------

def main():

    (
        event_rows,
        input_hashes,
        lstar_hashes,
        raw_count,
    ) = reconstruct_events()

    (
        summary_rows,
        age_support_rows,
    ) = perform_calibration(
        event_rows
    )

    quality_rows = (
        perform_quality_analysis(
            event_rows
        )
    )

    decision = make_decision(
        event_rows,
        summary_rows,
        age_support_rows,
        input_hashes,
        lstar_hashes,
        raw_count,
    )

    event_fields = [
        "event_id",
        "fold",
        "analysis_unit",
        "obj1",
        "obj2",
        "status",
        "lstar_hours",
        "nominal_cutoff_utc",
        "causal_replay_time_utc",
        "cutoff_minus_replay_hours",
        "obj1_current_GP_ID",
        "obj2_current_GP_ID",
        "obj1_state_age_days",
        "obj2_state_age_days",
        "pair_max_state_age_days",
        "obj1_publication_age_days",
        "obj2_publication_age_days",
        "pair_max_publication_age_days",
        "obj1_publication_count_30d",
        "obj2_publication_count_30d",
        "obj1_distinct_state_count_30d",
        "obj2_distinct_state_count_30d",
        "pair_min_distinct_state_count_30d",
        "obj1_median_cadence_hours",
        "obj2_median_cadence_hours",
        "obj1_max_gap_hours",
        "obj2_max_gap_hours",
        "pair_max_gap_hours",
        "obj1_window_left_censored",
        "obj2_window_left_censored",
        "pair_window_left_censored",
        "obj1_acquisition_left_censored",
        "obj2_acquisition_left_censored",
        "obj1_signed_propagation_interval_days",
        "obj2_signed_propagation_interval_days",
        "obj1_propagation_span_days",
        "obj2_propagation_span_days",
        "pair_max_propagation_span_days",
        "age_bin",
        "age_bin_label",
        "predicted_tca_utc",
        "predicted_miss_distance_km",
        "reference_tca_utc",
        "reference_miss_distance_km",
        "signed_tca_residual_s",
        "absolute_tca_error_s",
        "signed_miss_distance_residual_km",
        "absolute_miss_distance_error_km",
        "rejected_redundant_brackets",
        "recovered_redundant_brackets",
        "cal0_train_n_90",
        "cal0_order_statistic_k_90",
        "cal0_envelope_km_90",
        "cal0_covered_90",
        "calage_train_n_90",
        "calage_order_statistic_k_90",
        "calage_envelope_km_90",
        "calage_covered_90",
        "calage_support_status_90",
    ]

    summary_fields = [
        "method",
        "target",
        "nominal_coverage",
        "scope",
        "fold",
        "validation_events",
        "supported_events",
        "unsupported_events",
        "support_fraction",
        "successes",
        "coverage_denominator",
        "observed_coverage",
        "wilson_95_lower",
        "wilson_95_upper",
        "absolute_calibration_error",
        "median_envelope_radius",
        "units",
    ]

    age_support_fields = [
        "validation_fold",
        "age_bin",
        "age_bin_label",
        "training_events",
        "validation_events",
        "supported_validation_events",
        "unsupported_validation_events",
    ]

    quality_fields = [
        "scope",
        "fold",
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
        SUMMARY_OUT,
        summary_fields,
        summary_rows,
    )

    write_csv(
        AGE_SUPPORT_OUT,
        age_support_fields,
        age_support_rows,
    )

    write_csv(
        QUALITY_OUT,
        quality_fields,
        quality_rows,
    )

    output_hashes = {
        EVENT_OUT.relative_to(ROOT).as_posix():
            sha256(EVENT_OUT),

        SUMMARY_OUT.relative_to(ROOT).as_posix():
            sha256(SUMMARY_OUT),

        AGE_SUPPORT_OUT.relative_to(ROOT).as_posix():
            sha256(AGE_SUPPORT_OUT),

        QUALITY_OUT.relative_to(ROOT).as_posix():
            sha256(QUALITY_OUT),
    }

    decision[
        "output_hashes"
    ] = output_hashes

    DECISION_OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with DECISION_OUT.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as handle:

        json.dump(
            decision,
            handle,
            indent=2,
            sort_keys=True,
        )

        handle.write("\n")

    console = {
        "status":
            decision["status"],

        "selected_L_star_hours":
            decision[
                "selected_L_star_hours"
            ],

        "event_count":
            decision["event_count"],

        "OOF_event_count":
            decision[
                "OOF_event_count"
            ],

        "conditional_median_absolute_"
        "miss_distance_error_km":
            decision[
                "development_accuracy_summary"
            ][
                "conditional_median_absolute_"
                "miss_distance_error_km"
            ],

        "CAL0_primary_90pct_Ed":
            decision[
                "CAL0_primary_90pct_Ed"
            ],

        "CALAGE_primary_90pct_Ed":
            decision[
                "CALAGE_primary_90pct_Ed"
            ],

        "CALAGE_full_OOF_support":
            decision[
                "CALAGE_full_OOF_support"
            ],

        "retained_development_calibration":
            decision[
                "retained_development_calibration"
            ],

        "network_requests":
            0,

        "G6_protected_outcomes_accessed":
            False,

        "output_hashes":
            output_hashes,

        "decision_json_sha256":
            sha256(DECISION_OUT),
    }

    print(
        json.dumps(
            console,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
