#!/usr/bin/env python3
"""Frozen G6 post-association confirmatory metric implementation.

Offline validation:
    - verifies frozen non-protected inputs;
    - verifies only already-recorded TraCSS provenance metadata;
    - does NOT hash, decompress, or parse the TraCSS raw source;
    - exercises statistical logic with synthetic data only.

Real metric execution:
    - is deliberately disabled from the CLI;
    - must later be invoked only by a separately reviewed authorization layer.

Scientific scope:
    process estimands on all 6,927 frozen events;
    conditional E_d and E_t only for VALID_ASSOCIATED events;
    fixed CAL-0 radii;
    prespecified Wilson intervals, temporal strata, and interpretation rules.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------
# Frozen repository boundary
# ---------------------------------------------------------------------

EXPECTED_OFFLINE_HEAD = (
    "5141ce45b2aec0f64c2fc367794a607de86b0f30"
)

EXPECTED_OFFLINE_TAG = (
    "g6-reference-tca-association-outputs-frozen"
)


# ---------------------------------------------------------------------
# Frozen scientific inputs
# ---------------------------------------------------------------------

LOCK = (
    ROOT
    / "docs/research/G6_PreOutcome_Confirmatory_Lock.md"
)

DR0032 = (
    ROOT
    / "docs/research/DR-0032_G6_Reference_TCA_Association_Output_Freeze.md"
)

SAMPLE = (
    ROOT
    / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"
)

EVENT_STATUS = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Event_Status.csv"
)

CANDIDATES = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidates.csv"
)

ASSOCIATION = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association.csv"
)

ASSOCIATION_SUMMARY = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Summary.json"
)

ASSOCIATION_EXECUTION_AUDIT = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Execution_Audit.json"
)

ASSOCIATION_INDEPENDENT_AUDIT = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Output_Independent_Audit.json"
)

G5_DECISION = (
    ROOT
    / "results/g5/G5_Calibration_Decision_v2.json"
)

TRACSS = (
    ROOT
    / "data/raw/g2/IVV_Releasable_Dataset_SFSH_DiscreteHBR.csv.gz"
)


EXPECTED_HASHES = {
    LOCK:
        "21fc2d4eb83b8d00d7bbb632986fcdc33ec437dce9ca3e69020b222d382adb2e",

    DR0032:
        "9cd92637ed955fc9ccddaacf8409b58916836828186f5e5e138930c73620b4aa",

    SAMPLE:
        "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef",

    EVENT_STATUS:
        "844a4c3f90a1b5bb738f13911b5e9f99f22bfe72e01bdb43dd6242e292b7b308",

    CANDIDATES:
        "21b8a9f5c8aab4b3427fad5cd3f3c2823ffc0219bd2a36aeccafc6a0aaa80e36",

    ASSOCIATION:
        "31327a65448908791928aaa41a822f02086f97662c2c89ed4ea337fec2cfa622",

    ASSOCIATION_SUMMARY:
        "909772847e2de5d6c304e5f26697cb08c7bca952bea5a4c81ef78b80645c4281",

    ASSOCIATION_EXECUTION_AUDIT:
        "69d5664725906db8cb2bf443647774a421f96c985f8989c721c5c9376cbfa211",

    ASSOCIATION_INDEPENDENT_AUDIT:
        "852ebfe8e339c8ccddf116b4005563c6776f31ec07d25794e608edf3a52b7130",

    G5_DECISION:
        "336ab27d9949b59b82dfebdbe6573f27bd6a59f1efb3dc4244fc6bc00725f1c9",
}


EXPECTED_TRACSS_SHA = (
    "b8e49b9c40f974e5ff37e26ade3a855813436bb4c6dab4d7ddeccc8ff5752d39"
)

EXPECTED_TRACSS_BYTES = 65406532
EXPECTED_TRACSS_ROWS = 283595

EXPECTED_EVENTS = 6927
EXPECTED_EVALUABLE = 6906
EXPECTED_VALID_ASSOCIATED = 6839
EXPECTED_UNASSOCIATED = 67
EXPECTED_REFINEMENT_FAILURE = 20
EXPECTED_PROPAGATION_FAILURE = 1
EXPECTED_CANDIDATE_ROWS = 929821

MISS_RADIUS_KM = 78.75554652312711
TCA_RADIUS_S = 7.105892

NOMINAL_COVERAGE = 0.90
MIN_CONFIRMATORY_SUPPORT = 141
Z95 = 1.959963984540054


EXPECTED_STRATUM_TOTALS = {
    "G6-T1": 1386,
    "G6-T2": 1386,
    "G6-T3": 1385,
    "G6-T4": 1385,
    "G6-T5": 1385,
}


# ---------------------------------------------------------------------
# Future protected outputs
# ---------------------------------------------------------------------

EVENT_METRICS_OUT = (
    ROOT
    / "results/g6/G6_Confirmatory_Event_Metrics.csv"
)

TEMPORAL_OUT = (
    ROOT
    / "results/g6/G6_Confirmatory_Temporal_Summary.csv"
)

SUMMARY_OUT = (
    ROOT
    / "results/g6/G6_Confirmatory_Metric_Summary.json"
)

OFFLINE_VALIDATION = (
    ROOT
    / "results/g6/G6_Confirmatory_Metric_Implementation_Offline_Validation.json"
)


EVENT_OUTPUT_FIELDS = (
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


TEMPORAL_FIELDS = (
    "g6_temporal_stratum",
    "total_events",
    "evaluable_count",
    "evaluable_rate",
    "evaluable_wilson_95_lower",
    "evaluable_wilson_95_upper",
    "valid_associated_count",
    "valid_association_rate",
    "valid_association_wilson_95_lower",
    "valid_association_wilson_95_upper",
    "Ed_successes",
    "Ed_denominator",
    "Ed_coverage",
    "Ed_wilson_95_lower",
    "Ed_wilson_95_upper",
    "Ed_absolute_deviation_from_0p90",
    "Ed_median_km",
    "Ed_p90_km",
    "Ed_p95_km",
    "Ed_p99_km",
    "Ed_maximum_km",
)


class MetricError(RuntimeError):
    pass


# ---------------------------------------------------------------------
# General utilities
# ---------------------------------------------------------------------

def sha256(path: Path) -> str:

    h = hashlib.sha256()

    with path.open("rb") as stream:

        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def git(
    *args: str,
    check: bool = True,
):

    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def git_value(*args: str) -> str:

    return git(
        *args
    ).stdout.strip()


def finite(value) -> float:

    result = float(value)

    if not math.isfinite(result):
        raise MetricError(
            f"nonfinite value: {value!r}"
        )

    return result


def parse_utc(
    value: str,
    *,
    schema_declares_utc: bool = False,
) -> datetime:

    text = str(value).strip()

    if not text:
        raise MetricError(
            "empty timestamp"
        )

    text = text.replace(
        " ",
        "T",
    )

    if text.endswith(
        ("Z", "z")
    ):
        text = (
            text[:-1]
            + "+00:00"
        )

    try:
        result = datetime.fromisoformat(
            text
        )

    except ValueError as exc:
        raise MetricError(
            f"malformed timestamp: {value!r}"
        ) from exc

    if result.tzinfo is None:

        if not schema_declares_utc:
            raise MetricError(
                "naive timestamp without declared UTC schema"
            )

        result = result.replace(
            tzinfo=UTC
        )

    return result.astimezone(
        UTC
    )


def iso_utc(
    value: datetime,
) -> str:

    return (
        value.astimezone(UTC)
        .isoformat(
            timespec="microseconds"
        )
        .replace(
            "+00:00",
            "Z",
        )
    )


def fmt(value) -> str:

    if value is None:
        return ""

    if isinstance(
        value,
        bool,
    ):
        return (
            "TRUE"
            if value
            else "FALSE"
        )

    if isinstance(
        value,
        float,
    ):

        if not math.isfinite(
            value
        ):
            raise MetricError(
                "refusing to serialize nonfinite float"
            )

        return format(
            value,
            ".17g",
        )

    return str(value)


def atomic_json(
    path: Path,
    payload: dict,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    tmp = Path(
        tmp_name
    )

    try:

        with os.fdopen(
            fd,
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

            stream.flush()
            os.fsync(
                stream.fileno()
            )

        os.replace(
            tmp,
            path,
        )

    finally:

        tmp.unlink(
            missing_ok=True
        )


def atomic_csv(
    path: Path,
    fields,
    rows,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    tmp = Path(
        tmp_name
    )

    try:

        with os.fdopen(
            fd,
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
                                row.get(
                                    field
                                )
                            )

                        for field in fields
                    }
                )

            stream.flush()
            os.fsync(
                stream.fileno()
            )

        os.replace(
            tmp,
            path,
        )

    finally:

        tmp.unlink(
            missing_ok=True
        )


# ---------------------------------------------------------------------
# Frozen statistical definitions
# ---------------------------------------------------------------------

def linear_quantile(
    values,
    q: float,
) -> float:

    ordered = sorted(
        finite(value)
        for value in values
    )

    if not ordered:
        raise MetricError(
            "quantile requested for empty sample"
        )

    q = finite(q)

    if not (
        0.0 <= q <= 1.0
    ):
        raise MetricError(
            "quantile probability outside [0,1]"
        )

    if len(
        ordered
    ) == 1:
        return ordered[0]

    h = (
        (len(ordered) - 1)
        * q
    )

    lo = math.floor(h)
    hi = math.ceil(h)

    if lo == hi:
        return ordered[lo]

    weight = (
        h
        - lo
    )

    return (
        ordered[lo]
        + weight
        * (
            ordered[hi]
            - ordered[lo]
        )
    )


def wilson_interval(
    successes: int,
    denominator: int,
):

    if denominator <= 0:
        raise MetricError(
            "Wilson denominator must be positive"
        )

    if not (
        0
        <= successes
        <= denominator
    ):
        raise MetricError(
            "invalid Wilson successes/denominator"
        )

    n = float(
        denominator
    )

    p = (
        successes
        / n
    )

    z2 = (
        Z95
        * Z95
    )

    denom = (
        1.0
        + z2 / n
    )

    center = (
        p
        + z2
        / (
            2.0
            * n
        )
    ) / denom

    half = (
        Z95
        / denom
        * math.sqrt(
            p
            * (
                1.0
                - p
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
    successes: int,
    denominator: int,
) -> dict:

    lower, upper = wilson_interval(
        successes,
        denominator,
    )

    proportion = (
        successes
        / denominator
    )

    return {
        "successes":
            successes,

        "denominator":
            denominator,

        "proportion":
            proportion,

        "wilson_95_lower":
            lower,

        "wilson_95_upper":
            upper,
    }


def error_summary(
    errors,
    radius: float,
) -> dict:

    values = [
        finite(value)
        for value in errors
    ]

    if not values:
        raise MetricError(
            "empty conditional error distribution"
        )

    if any(
        value < 0.0
        for value in values
    ):
        raise MetricError(
            "absolute-error distribution contains negative value"
        )

    radius = finite(
        radius
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
            binary[
                "proportion"
            ],

        "wilson_95_lower":
            binary[
                "wilson_95_lower"
            ],

        "wilson_95_upper":
            binary[
                "wilson_95_upper"
            ],

        "absolute_deviation_from_0p90":
            abs(
                binary[
                    "proportion"
                ]
                - NOMINAL_COVERAGE
            ),
    }


def primary_interpretation(
    denominator: int,
    observed: float,
    lower: float,
    upper: float,
) -> dict:

    if denominator < MIN_CONFIRMATORY_SUPPORT:

        return {
            "classification":
                "INSUFFICIENT CAUSAL/EVALUABLE SUPPORT",

            "direction":
                None,
        }

    if (
        lower
        <= NOMINAL_COVERAGE
        <= upper
    ):

        return {
            "classification":
                "CONSISTENT WITH NOMINAL 90% COVERAGE",

            "direction":
                None,
        }

    direction = (
        "UNDERCOVERAGE"
        if observed
        < NOMINAL_COVERAGE
        else
        "CONSERVATIVE OVERCOVERAGE"
    )

    return {
        "classification":
            "NOMINAL 90% COVERAGE NOT SUPPORTED",

        "direction":
            direction,
    }


# ---------------------------------------------------------------------
# Immutable-boundary validation
# ---------------------------------------------------------------------

def verify_offline_repository_boundary() -> None:

    if (
        git_value(
            "rev-parse",
            "HEAD",
        )
        != EXPECTED_OFFLINE_HEAD
    ):
        raise MetricError(
            "offline-validation HEAD is not DR-0032"
        )

    if (
        git_value(
            "rev-parse",
            f"refs/tags/{EXPECTED_OFFLINE_TAG}^{{commit}}",
        )
        != EXPECTED_OFFLINE_HEAD
    ):
        raise MetricError(
            "DR-0032 tag target changed"
        )

    if git_value(
        "status",
        "--porcelain",
        "--untracked-files=no",
    ):
        raise MetricError(
            "tracked working tree is not clean"
        )


def verify_frozen_nonprotected_inputs() -> dict:

    result = {}

    for path, expected in EXPECTED_HASHES.items():

        if not path.is_file():
            raise MetricError(
                f"missing frozen dependency: {path}"
            )

        actual = sha256(
            path
        )

        if actual != expected:
            raise MetricError(
                "frozen dependency SHA mismatch\n"
                f"path={path}\n"
                f"actual={actual}\n"
                f"expected={expected}"
            )

        result[
            path.relative_to(
                ROOT
            ).as_posix()
        ] = actual

    return result


def verify_reference_source_metadata_only() -> dict:

    decision = json.loads(
        G5_DECISION.read_text(
            encoding="utf-8"
        )
    )

    recorded_rows = int(
        decision.get(
            "TraCSS_raw_row_count",
            -1,
        )
    )

    recorded_sha = str(
        decision.get(
            "TraCSS_raw_sha256",
            "",
        )
    ).lower()

    if (
        recorded_rows
        != EXPECTED_TRACSS_ROWS
    ):
        raise MetricError(
            "recorded TraCSS row count changed"
        )

    if (
        recorded_sha
        != EXPECTED_TRACSS_SHA
    ):
        raise MetricError(
            "recorded TraCSS SHA changed"
        )

    if not TRACSS.is_file():
        raise MetricError(
            "protected TraCSS source is absent"
        )

    byte_count = (
        TRACSS.stat().st_size
    )

    if (
        byte_count
        != EXPECTED_TRACSS_BYTES
    ):
        raise MetricError(
            "protected TraCSS source byte count changed"
        )

    # Deliberately do NOT hash, open, decompress, or parse TRACSS here.

    return {
        "path":
            TRACSS.relative_to(
                ROOT
            ).as_posix(),

        "exists":
            True,

        "byte_count":
            byte_count,

        "recorded_row_count":
            recorded_rows,

        "recorded_sha256":
            recorded_sha,

        "raw_bytes_hashed_during_offline_validation":
            False,

        "raw_file_opened_during_offline_validation":
            False,

        "raw_rows_parsed_during_offline_validation":
            0,

        "min_range_accessed":
            False,

        "mdistance_accessed":
            False,
    }


def read_frozen_context():

    sample = {}

    with SAMPLE.open(
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

            if index != expected_index:
                raise MetricError(
                    "sample register ordering changed"
                )

            parse_utc(
                row[
                    "reference_tca_utc"
                ]
            )

            sample[index] = row

    if len(
        sample
    ) != EXPECTED_EVENTS:
        raise MetricError(
            "sample event count changed"
        )

    event_status = {}

    with EVENT_STATUS.open(
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

            if index != expected_index:
                raise MetricError(
                    "event-status ordering changed"
                )

            parse_utc(
                row[
                    "replay_time_utc"
                ]
            )

            event_status[index] = row

    association = {}
    census = Counter()

    with ASSOCIATION.open(
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

            if index != expected_index:
                raise MetricError(
                    "association ordering changed"
                )

            if (
                row[
                    "conj_id"
                ].strip()
                != sample[
                    index
                ][
                    "conj_id"
                ].strip()
            ):
                raise MetricError(
                    f"sample/association conjunction mismatch: {index}"
                )

            if (
                row[
                    "g6_temporal_stratum"
                ].strip()
                != sample[
                    index
                ][
                    "g6_temporal_stratum"
                ].strip()
            ):
                raise MetricError(
                    f"sample/association stratum mismatch: {index}"
                )

            association[
                index
            ] = row

            census[
                row[
                    "final_status"
                ].strip()
            ] += 1

    expected_census = {
        "VALID_ASSOCIATED":
            EXPECTED_VALID_ASSOCIATED,

        "UNASSOCIATED_REFERENCE_EVENT":
            EXPECTED_UNASSOCIATED,

        "UNRECOVERED_REFINEMENT_FAILURE":
            EXPECTED_REFINEMENT_FAILURE,

        "PROPAGATION_FAILURE":
            EXPECTED_PROPAGATION_FAILURE,
    }

    if dict(
        census
    ) != expected_census:
        raise MetricError(
            f"association census changed: {dict(census)}"
        )

    for index in range(
        1,
        EXPECTED_EVENTS + 1,
    ):

        if (
            event_status[
                index
            ][
                "conj_id"
            ].strip()
            != sample[
                index
            ][
                "conj_id"
            ].strip()
        ):
            raise MetricError(
                f"event/sample mismatch: {index}"
            )

        if (
            association[
                index
            ][
                "input_preoutcome_status"
            ].strip()
            != event_status[
                index
            ][
                "preoutcome_status"
            ].strip()
        ):
            raise MetricError(
                f"preoutcome status mismatch: {index}"
            )

    return (
        sample,
        event_status,
        association,
    )


# ---------------------------------------------------------------------
# Protected execution helpers
# ---------------------------------------------------------------------

def verify_protected_source_integrity_for_execution() -> str:

    # This is deliberately NOT called by offline validation.
    actual = sha256(
        TRACSS
    )

    if (
        actual
        != EXPECTED_TRACSS_SHA
    ):
        raise MetricError(
            "protected TraCSS raw SHA mismatch"
        )

    return actual


def load_selected_candidate_rows(
    association,
):

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

    if len(
        wanted
    ) != EXPECTED_VALID_ASSOCIATED:
        raise MetricError(
            "VALID_ASSOCIATED membership changed"
        )

    selected = {}
    rows_seen = 0

    with CANDIDATES.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        reader = csv.DictReader(
            stream
        )

        for row in reader:

            rows_seen += 1

            event_index = int(
                row[
                    "final_g6_sample_index"
                ]
            )

            if (
                event_index
                not in wanted
            ):
                continue

            candidate_index = int(
                row[
                    "candidate_index"
                ]
            )

            if (
                candidate_index
                != wanted[
                    event_index
                ]
            ):
                continue

            if (
                event_index
                in selected
            ):
                raise MetricError(
                    f"duplicate selected candidate: {event_index}"
                )

            selected[
                event_index
            ] = {
                "candidate_index":
                    candidate_index,

                "time_from_replay_s":
                    finite(
                        row[
                            "time_from_replay_s"
                        ]
                    ),

                "distance_km":
                    finite(
                        row[
                            "distance_km"
                        ]
                    ),
            }

    if (
        rows_seen
        != EXPECTED_CANDIDATE_ROWS
    ):
        raise MetricError(
            "candidate row count changed"
        )

    if set(
        selected
    ) != set(
        wanted
    ):
        missing = sorted(
            set(wanted)
            - set(selected)
        )

        raise MetricError(
            "selected candidate identities not fully recovered: "
            + repr(
                missing[:10]
            )
        )

    return selected


def load_reference_rows(
    wanted_conj_ids,
):

    found = {}
    raw_count = 0

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

        missing = (
            required
            - set(
                reader.fieldnames
                or ()
            )
        )

        if missing:
            raise MetricError(
                "TraCSS required fields missing: "
                + repr(
                    sorted(
                        missing
                    )
                )
            )

        for row in reader:

            raw_count += 1

            conj_id = (
                row[
                    "conj_id"
                ].strip()
            )

            if (
                conj_id
                not in wanted_conj_ids
            ):
                continue

            if conj_id in found:
                raise MetricError(
                    f"duplicate protected conjunction ID: {conj_id}"
                )

            reference_miss_km = finite(
                row[
                    "min_range"
                ]
            )

            if (
                reference_miss_km
                < 0.0
            ):
                raise MetricError(
                    f"negative physical reference miss distance: {conj_id}"
                )

            found[
                conj_id
            ] = {
                "epoch":
                    row[
                        "epoch"
                    ].strip(),

                "min_range_km":
                    reference_miss_km,
            }

    if (
        raw_count
        != EXPECTED_TRACSS_ROWS
    ):
        raise MetricError(
            f"TraCSS raw row count changed: {raw_count}"
        )

    if set(
        found
    ) != set(
        wanted_conj_ids
    ):
        missing = sorted(
            set(
                wanted_conj_ids
            )
            - set(
                found
            )
        )

        raise MetricError(
            "missing protected reference rows: "
            + repr(
                missing[:10]
            )
        )

    return (
        found,
        raw_count,
    )


# ---------------------------------------------------------------------
# Authorized future metric execution
# ---------------------------------------------------------------------

def run_metrics():

    verify_frozen_nonprotected_inputs()

    source_sha = (
        verify_protected_source_integrity_for_execution()
    )

    (
        sample,
        event_status,
        association,
    ) = read_frozen_context()

    for path in (
        EVENT_METRICS_OUT,
        TEMPORAL_OUT,
        SUMMARY_OUT,
    ):

        if path.exists():
            raise MetricError(
                f"refusing to overwrite metric output: {path}"
            )

    selected = (
        load_selected_candidate_rows(
            association
        )
    )

    wanted_conj_ids = {
        sample[
            index
        ][
            "conj_id"
        ].strip()

        for index in selected
    }

    (
        reference_rows,
        raw_count,
    ) = load_reference_rows(
        wanted_conj_ids
    )

    output_rows = []

    errors_d = []
    errors_t = []

    stratum_all = defaultdict(
        list
    )

    for index in range(
        1,
        EXPECTED_EVENTS + 1,
    ):

        sample_row = sample[
            index
        ]

        event_row = event_status[
            index
        ]

        assoc_row = association[
            index
        ]

        final_status = (
            assoc_row[
                "final_status"
            ].strip()
        )

        evaluable = (
            final_status
            in {
                "VALID_ASSOCIATED",
                "UNASSOCIATED_REFERENCE_EVENT",
            }
        )

        valid = (
            final_status
            == "VALID_ASSOCIATED"
        )

        result = {
            "final_g6_sample_index":
                index,

            "g6_temporal_stratum":
                sample_row[
                    "g6_temporal_stratum"
                ].strip(),

            "conj_id":
                sample_row[
                    "conj_id"
                ].strip(),

            "final_status":
                final_status,

            "evaluable":
                evaluable,

            "valid_associated":
                valid,

            "selected_candidate_index":
                (
                    assoc_row[
                        "selected_candidate_index"
                    ].strip()
                    if valid
                    else None
                ),

            "predicted_tca_utc":
                None,

            "reference_tca_utc":
                (
                    sample_row[
                        "reference_tca_utc"
                    ].strip()
                    if valid
                    else None
                ),

            "signed_tca_residual_s":
                None,

            "absolute_tca_error_s":
                None,

            "tca_radius_s":
                (
                    TCA_RADIUS_S
                    if valid
                    else None
                ),

            "tca_covered":
                None,

            "predicted_miss_distance_km":
                None,

            "reference_miss_distance_km":
                None,

            "signed_miss_distance_residual_km":
                None,

            "absolute_miss_distance_error_km":
                None,

            "miss_distance_radius_km":
                (
                    MISS_RADIUS_KM
                    if valid
                    else None
                ),

            "miss_distance_covered":
                None,
        }

        if valid:

            candidate = selected[
                index
            ]

            replay = parse_utc(
                event_row[
                    "replay_time_utc"
                ]
            )

            reference_tca = parse_utc(
                sample_row[
                    "reference_tca_utc"
                ]
            )

            predicted_tca = (
                replay
                + timedelta(
                    seconds=
                        candidate[
                            "time_from_replay_s"
                        ]
                )
            )

            conj_id = (
                sample_row[
                    "conj_id"
                ].strip()
            )

            reference = (
                reference_rows[
                    conj_id
                ]
            )

            raw_epoch = parse_utc(
                reference[
                    "epoch"
                ],
                schema_declares_utc=True,
            )

            if (
                raw_epoch
                != reference_tca
            ):
                raise MetricError(
                    "TraCSS epoch differs from frozen reference TCA: "
                    + conj_id
                )

            predicted_miss = (
                candidate[
                    "distance_km"
                ]
            )

            reference_miss = (
                reference[
                    "min_range_km"
                ]
            )

            signed_t = (
                predicted_tca
                - reference_tca
            ).total_seconds()

            absolute_t = abs(
                signed_t
            )

            signed_d = (
                predicted_miss
                - reference_miss
            )

            absolute_d = abs(
                signed_d
            )

            errors_t.append(
                absolute_t
            )

            errors_d.append(
                absolute_d
            )

            result.update(
                {
                    "predicted_tca_utc":
                        iso_utc(
                            predicted_tca
                        ),

                    "signed_tca_residual_s":
                        signed_t,

                    "absolute_tca_error_s":
                        absolute_t,

                    "tca_covered":
                        (
                            absolute_t
                            <= TCA_RADIUS_S
                        ),

                    "predicted_miss_distance_km":
                        predicted_miss,

                    "reference_miss_distance_km":
                        reference_miss,

                    "signed_miss_distance_residual_km":
                        signed_d,

                    "absolute_miss_distance_error_km":
                        absolute_d,

                    "miss_distance_covered":
                        (
                            absolute_d
                            <= MISS_RADIUS_KM
                        ),
                }
            )

        output_rows.append(
            result
        )

        stratum_all[
            result[
                "g6_temporal_stratum"
            ]
        ].append(
            result
        )

    if len(
        output_rows
    ) != EXPECTED_EVENTS:
        raise MetricError(
            "event metric output count changed"
        )

    if len(
        errors_d
    ) != EXPECTED_VALID_ASSOCIATED:
        raise MetricError(
            "E_d denominator changed"
        )

    if len(
        errors_t
    ) != EXPECTED_VALID_ASSOCIATED:
        raise MetricError(
            "E_t denominator changed"
        )

    evaluable_count = sum(
        row[
            "evaluable"
        ]
        for row in output_rows
    )

    valid_count = sum(
        row[
            "valid_associated"
        ]
        for row in output_rows
    )

    if (
        evaluable_count
        != EXPECTED_EVALUABLE
    ):
        raise MetricError(
            "evaluable count changed"
        )

    if (
        valid_count
        != EXPECTED_VALID_ASSOCIATED
    ):
        raise MetricError(
            "valid-associated count changed"
        )

    evaluability = binary_summary(
        evaluable_count,
        EXPECTED_EVENTS,
    )

    valid_association = binary_summary(
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

    interpretation = primary_interpretation(
        primary[
            "coverage_denominator"
        ],
        primary[
            "observed_coverage"
        ],
        primary[
            "wilson_95_lower"
        ],
        primary[
            "wilson_95_upper"
        ],
    )

    temporal_rows = []

    for stratum in (
        "G6-T1",
        "G6-T2",
        "G6-T3",
        "G6-T4",
        "G6-T5",
    ):

        rows = stratum_all[
            stratum
        ]

        total = len(
            rows
        )

        if (
            total
            != EXPECTED_STRATUM_TOTALS[
                stratum
            ]
        ):
            raise MetricError(
                f"stratum total changed: {stratum}"
            )

        e_count = sum(
            row[
                "evaluable"
            ]
            for row in rows
        )

        v_rows = [
            row
            for row in rows
            if row[
                "valid_associated"
            ]
        ]

        v_count = len(
            v_rows
        )

        e_binary = binary_summary(
            e_count,
            total,
        )

        v_binary = binary_summary(
            v_count,
            total,
        )

        ed_values = [
            finite(
                row[
                    "absolute_miss_distance_error_km"
                ]
            )
            for row in v_rows
        ]

        ed = error_summary(
            ed_values,
            MISS_RADIUS_KM,
        )

        temporal_rows.append(
            {
                "g6_temporal_stratum":
                    stratum,

                "total_events":
                    total,

                "evaluable_count":
                    e_count,

                "evaluable_rate":
                    e_binary[
                        "proportion"
                    ],

                "evaluable_wilson_95_lower":
                    e_binary[
                        "wilson_95_lower"
                    ],

                "evaluable_wilson_95_upper":
                    e_binary[
                        "wilson_95_upper"
                    ],

                "valid_associated_count":
                    v_count,

                "valid_association_rate":
                    v_binary[
                        "proportion"
                    ],

                "valid_association_wilson_95_lower":
                    v_binary[
                        "wilson_95_lower"
                    ],

                "valid_association_wilson_95_upper":
                    v_binary[
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

    temporal_coverages = [
        row[
            "Ed_coverage"
        ]
        for row in temporal_rows
    ]

    temporal_instability = any(
        not (
            row[
                "Ed_wilson_95_lower"
            ]
            <= NOMINAL_COVERAGE
            <= row[
                "Ed_wilson_95_upper"
            ]
        )
        for row in temporal_rows
    )

    atomic_csv(
        EVENT_METRICS_OUT,
        EVENT_OUTPUT_FIELDS,
        output_rows,
    )

    atomic_csv(
        TEMPORAL_OUT,
        TEMPORAL_FIELDS,
        temporal_rows,
    )

    summary = {
        "status":
            "G6_CONFIRMATORY_METRICS_PASS",

        "events":
            EXPECTED_EVENTS,

        "analysis_unit":
            "ONE_OBJECT_DISJOINT_CONJUNCTION_EVENT",

        "process_estimands": {
            "evaluable":
                evaluability,

            "valid_association":
                valid_association,
        },

        "primary_Ed": {
            **primary,

            "units":
                "km",

            "reference_field":
                "min_range",

            "mdistance_used":
                False,

            "nominal_coverage":
                NOMINAL_COVERAGE,

            "minimum_confirmatory_support":
                MIN_CONFIRMATORY_SUPPORT,

            "interpretation":
                interpretation,
        },

        "secondary_Et": {
            **secondary,

            "units":
                "s",

            "nominal_coverage":
                NOMINAL_COVERAGE,
        },

        "temporal_reporting": {
            "strata":
                [
                    "G6-T1",
                    "G6-T2",
                    "G6-T3",
                    "G6-T4",
                    "G6-T5",
                ],

            "Ed_coverage_min":
                min(
                    temporal_coverages
                ),

            "Ed_coverage_max":
                max(
                    temporal_coverages
                ),

            "TEMPORAL_INSTABILITY_FLAG":
                temporal_instability,

            "interpretation":
                (
                    "DESCRIPTIVE_TEMPORAL_TRANSPORT_STABILITY_EVIDENCE;"
                    "_NOT_FIVE_CO_PRIMARY_HYPOTHESIS_TESTS"
                ),
        },

        "fixed_parameters": {
            "miss_distance_radius_km":
                MISS_RADIUS_KM,

            "TCA_radius_s":
                TCA_RADIUS_S,

            "nominal_coverage":
                NOMINAL_COVERAGE,

            "Wilson_z":
                Z95,

            "quantile_rule":
                "LINEAR_EMPIRICAL_QUANTILE_H=(N-1)Q",
        },

        "reference_source": {
            "path":
                TRACSS.relative_to(
                    ROOT
                ).as_posix(),

            "sha256":
                source_sha,

            "raw_rows_scanned":
                raw_count,

            "valid_associated_reference_rows_used":
                EXPECTED_VALID_ASSOCIATED,

            "physical_reference_miss_distance_field":
                "min_range",

            "mdistance_used":
                False,
        },

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

        "bootstrap_performed":
            False,

        "covariance_access":
            False,

        "Pc_access":
            False,

        "HBR_access":
            False,

        "network_requests":
            0,

        "event_metrics_output":
            EVENT_METRICS_OUT.relative_to(
                ROOT
            ).as_posix(),

        "temporal_summary_output":
            TEMPORAL_OUT.relative_to(
                ROOT
            ).as_posix(),
    }

    atomic_json(
        SUMMARY_OUT,
        summary,
    )

    return summary


# ---------------------------------------------------------------------
# Synthetic-only offline validation
# ---------------------------------------------------------------------

def synthetic_statistical_validation() -> dict:

    values = [
        0.0,
        10.0,
        20.0,
        30.0,
        40.0,
    ]

    expected_quantiles = {
        0.50:
            20.0,

        0.90:
            36.0,

        0.95:
            38.0,

        0.99:
            39.6,
    }

    for q, expected in expected_quantiles.items():

        actual = linear_quantile(
            values,
            q,
        )

        if not math.isclose(
            actual,
            expected,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ):
            raise MetricError(
                f"linear quantile synthetic test failed at q={q}"
            )

    synthetic_errors = [
        1.0,
        2.0,
        3.0,
        4.0,
        5.0,
    ]

    summary = error_summary(
        synthetic_errors,
        3.0,
    )

    if (
        summary[
            "coverage_successes"
        ]
        != 3
        or summary[
            "coverage_denominator"
        ]
        != 5
        or not math.isclose(
            summary[
                "observed_coverage"
            ],
            0.6,
            abs_tol=1.0e-15,
        )
        or not math.isclose(
            summary[
                "median"
            ],
            3.0,
            abs_tol=1.0e-15,
        )
        or not math.isclose(
            summary[
                "p90"
            ],
            4.6,
            abs_tol=1.0e-15,
        )
    ):
        raise MetricError(
            "synthetic error-summary test failed"
        )

    low_90, high_90 = (
        wilson_interval(
            90,
            100,
        )
    )

    if not (
        low_90
        <= 0.90
        <= high_90
    ):
        raise MetricError(
            "Wilson nominal-containment test failed"
        )

    low_under, high_under = (
        wilson_interval(
            70,
            100,
        )
    )

    under = primary_interpretation(
        100,
        0.70,
        low_under,
        high_under,
    )

    if (
        under[
            "classification"
        ]
        != "INSUFFICIENT CAUSAL/EVALUABLE SUPPORT"
    ):
        # n=100 must be support-limited regardless of coverage.
        raise MetricError(
            "minimum-support interpretation test failed"
        )

    low_under2, high_under2 = (
        wilson_interval(
            100,
            150,
        )
    )

    under2 = primary_interpretation(
        150,
        100 / 150,
        low_under2,
        high_under2,
    )

    if (
        under2[
            "classification"
        ]
        != "NOMINAL 90% COVERAGE NOT SUPPORTED"
        or under2[
            "direction"
        ]
        != "UNDERCOVERAGE"
    ):
        raise MetricError(
            "undercoverage interpretation test failed"
        )

    low_over, high_over = (
        wilson_interval(
            149,
            150,
        )
    )

    over = primary_interpretation(
        150,
        149 / 150,
        low_over,
        high_over,
    )

    if (
        over[
            "classification"
        ]
        != "NOMINAL 90% COVERAGE NOT SUPPORTED"
        or over[
            "direction"
        ]
        != "CONSERVATIVE OVERCOVERAGE"
    ):
        raise MetricError(
            "overcoverage interpretation test failed"
        )

    low_consistent, high_consistent = (
        wilson_interval(
            135,
            150,
        )
    )

    consistent = primary_interpretation(
        150,
        0.90,
        low_consistent,
        high_consistent,
    )

    if (
        consistent[
            "classification"
        ]
        != "CONSISTENT WITH NOMINAL 90% COVERAGE"
    ):
        raise MetricError(
            "nominal consistency interpretation test failed"
        )

    return {
        "linear_quantile_median":
            True,

        "linear_quantile_p90":
            True,

        "linear_quantile_p95":
            True,

        "linear_quantile_p99":
            True,

        "fixed_radius_inclusive_boundary":
            True,

        "Wilson_two_sided_95pct":
            True,

        "minimum_support_precedence":
            True,

        "undercoverage_classification":
            True,

        "conservative_overcoverage_classification":
            True,

        "nominal_consistency_classification":
            True,
    }


def offline_validate():

    if OFFLINE_VALIDATION.exists():
        raise MetricError(
            "offline-validation artifact already exists"
        )

    verify_offline_repository_boundary()

    hashes = (
        verify_frozen_nonprotected_inputs()
    )

    source_metadata = (
        verify_reference_source_metadata_only()
    )

    (
        sample,
        _event_status,
        association,
    ) = read_frozen_context()

    if (
        EVENT_METRICS_OUT.exists()
        or TEMPORAL_OUT.exists()
        or SUMMARY_OUT.exists()
    ):
        raise MetricError(
            "real confirmatory metric output unexpectedly exists"
        )

    stratum_counts = Counter(
        row[
            "g6_temporal_stratum"
        ].strip()
        for row in sample.values()
    )

    if dict(
        stratum_counts
    ) != EXPECTED_STRATUM_TOTALS:
        raise MetricError(
            "frozen temporal-stratum totals changed"
        )

    final_counts = Counter(
        row[
            "final_status"
        ].strip()
        for row in association.values()
    )

    synthetic = (
        synthetic_statistical_validation()
    )

    result = {
        "status":
            "G6_CONFIRMATORY_METRIC_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",

        "implementation_sha256":
            sha256(
                Path(__file__)
            ),

        "events_verified":
            len(
                sample
            ),

        "evaluable_events_frozen":
            EXPECTED_EVALUABLE,

        "VALID_ASSOCIATED_frozen":
            EXPECTED_VALID_ASSOCIATED,

        "UNASSOCIATED_REFERENCE_EVENT_frozen":
            EXPECTED_UNASSOCIATED,

        "UNRECOVERED_REFINEMENT_FAILURE_frozen":
            EXPECTED_REFINEMENT_FAILURE,

        "PROPAGATION_FAILURE_frozen":
            EXPECTED_PROPAGATION_FAILURE,

        "frozen_final_status_counts":
            dict(
                sorted(
                    final_counts.items()
                )
            ),

        "temporal_stratum_totals":
            dict(
                sorted(
                    stratum_counts.items()
                )
            ),

        "miss_distance_radius_km":
            MISS_RADIUS_KM,

        "TCA_radius_s":
            TCA_RADIUS_S,

        "nominal_coverage":
            NOMINAL_COVERAGE,

        "minimum_confirmatory_support":
            MIN_CONFIRMATORY_SUPPORT,

        "Wilson_z":
            Z95,

        "quantile_rule":
            "LINEAR_EMPIRICAL_QUANTILE_H=(N-1)Q",

        "primary_reference_field":
            "min_range",

        "mdistance_authorized":
            False,

        "reference_source_metadata":
            source_metadata,

        "frozen_nonprotected_input_hashes":
            hashes,

        "synthetic_statistical_validation":
            synthetic,

        "real_G6_metric_execution_performed":
            False,

        "TraCSS_raw_bytes_hashed":
            False,

        "TraCSS_raw_file_opened":
            False,

        "TraCSS_rows_parsed":
            0,

        "physical_reference_min_range_access":
            False,

        "mdistance_access":
            False,

        "E_d_computed_on_real_G6":
            False,

        "E_t_computed_on_real_G6":
            False,

        "coverage_computed_on_real_G6":
            False,

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

        "covariance_access":
            False,

        "Pc_access":
            False,

        "HBR_access":
            False,

        "network_requests":
            0,

        "direct_execution_authorized":
            False,

        "next_safe_action":
            "INDEPENDENT_REVIEW_AND_FREEZE_INACTIVE_CONFIRMATORY_METRIC_IMPLEMENTATION_BEFORE_PROTECTED_OUTCOME_ACCESS",
    }

    atomic_json(
        OFFLINE_VALIDATION,
        result,
    )

    print(
        json.dumps(
            {
                **result,

                "offline_validation_sha256":
                    sha256(
                        OFFLINE_VALIDATION
                    ),
            },
            indent=2,
            sort_keys=True,
        )
    )


def execute():

    raise MetricError(
        "G6_CONFIRMATORY_METRICS_INACTIVE_UNTIL_INDEPENDENT_REVIEW_AND_EXPLICIT_AUTHORIZATION"
    )


def main() -> int:

    parser = argparse.ArgumentParser()

    group = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    group.add_argument(
        "--offline-validate",
        action="store_true",
    )

    group.add_argument(
        "--execute",
        action="store_true",
    )

    args = parser.parse_args()

    if args.offline_validate:
        offline_validate()
        return 0

    if args.execute:
        execute()
        return 0

    raise MetricError(
        "unreachable command state"
    )


if __name__ == "__main__":

    raise SystemExit(
        main()
    )
