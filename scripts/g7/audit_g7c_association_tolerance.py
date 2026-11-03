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

SAMPLE = ROOT / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"
CANDIDATES = ROOT / "results/g6/G6_PreOutcome_Candidates.csv"
TRACSS = ROOT / "data/raw/g2/IVV_Releasable_Dataset_SFSH_DiscreteHBR.csv.gz"
G6_EVENT = ROOT / "results/g6/G6_Confirmatory_Event_Metrics.csv"

EVENT = ROOT / "results/g7/G7C_Association_Tolerance_Event.csv"
SUMMARY_CSV = ROOT / "results/g7/G7C_Association_Tolerance_Summary.csv"
SUMMARY_JSON = ROOT / "results/g7/G7C_Association_Tolerance_Summary.json"

OUT = ROOT / "results/g7/G7C_Association_Tolerance_Independent_Audit.json"

EXPECTED_EVENTS = 6927
EXPECTED_READY = 6906
EXPECTED_CANDIDATES = 929821
EXPECTED_REFERENCE_ROWS = 283595
EXPECTED_VALID_5 = 6839

TOLERANCES = (5, 15, 30, 60)

MISS_RADIUS = 78.75554652312711
TCA_RADIUS = 7.105892
Z95 = 1.959963984540054


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


def utc(value):

    text = str(value).strip().replace(" ", "T")

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    result = datetime.fromisoformat(text)

    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)

    return result.astimezone(UTC)


def as_bool(value):

    text = str(value).strip().upper()

    if text == "TRUE":
        return True

    if text == "FALSE":
        return False

    raise AuditError(f"bad boolean {value!r}")


def maybe_float(value):

    text = str(value).strip()

    return (
        None
        if not text
        else float(text)
    )


def close(actual, expected, label, tol=1e-10):

    if expected is None:

        if str(actual).strip():
            raise AuditError(
                f"{label}: expected blank"
            )

        return

    if not math.isclose(
        float(actual),
        float(expected),
        rel_tol=0.0,
        abs_tol=tol,
    ):
        raise AuditError(
            f"{label}: {actual} != {expected}"
        )


def wilson(k, n):

    if not n:
        return None, None

    p = k / n
    z2 = Z95 * Z95

    d = 1.0 + z2 / n

    center = (
        p
        + z2 / (2.0 * n)
    ) / d

    half = (
        Z95 / d
        * math.sqrt(
            p * (1.0 - p) / n
            + z2 / (4.0 * n * n)
        )
    )

    return (
        max(0.0, center - half),
        min(1.0, center + half),
    )


def q(values, p):

    return float(
        np.quantile(
            np.asarray(values, dtype=float),
            p,
        )
    )


# ------------------------------------------------------------
# Load sample
# ------------------------------------------------------------

sample = {}
conj_to_index = {}

with SAMPLE.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    for row in csv.DictReader(stream):

        idx = int(
            row["final_g6_sample_index"]
        )

        sample[idx] = row

        conj_to_index[
            row["conj_id"].strip()
        ] = idx


if len(sample) != EXPECTED_EVENTS:
    raise AuditError("sample count mismatch")


# ------------------------------------------------------------
# Independently rescan candidate corpus
# ------------------------------------------------------------

best = {}
candidate_counts = Counter()
candidate_rows = 0

with CANDIDATES.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    for row in csv.DictReader(stream):

        candidate_rows += 1

        idx = int(
            row["final_g6_sample_index"]
        )

        candidate_index = int(
            row["candidate_index"]
        )

        candidate_time = utc(
            row["candidate_time_utc"]
        )

        distance = float(
            row["distance_km"]
        )

        reference_tca = utc(
            sample[idx]["reference_tca_utc"]
        )

        offset = abs(
            (
                candidate_time
                - reference_tca
            ).total_seconds()
        )

        key = (
            offset,
            candidate_time,
            distance,
            candidate_index,
        )

        candidate_counts[idx] += 1

        current = best.get(idx)

        if current is None or key < current["key"]:

            best[idx] = {
                "key": key,
                "candidate_index": candidate_index,
                "candidate_time": candidate_time,
                "distance": distance,
                "offset": offset,
            }


if candidate_rows != EXPECTED_CANDIDATES:
    raise AuditError("candidate row count mismatch")

if len(best) != EXPECTED_READY:
    raise AuditError("candidate-ready event count mismatch")


# ------------------------------------------------------------
# Independently rescan reference source
# ------------------------------------------------------------

reference = {}
reference_rows = 0

with gzip.open(
    TRACSS,
    "rt",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    for row in reader:

        reference_rows += 1

        conj = row["conj_id"].strip()

        idx = conj_to_index.get(conj)

        if idx is None:
            continue

        if idx in reference:
            raise AuditError("duplicate reference event")

        epoch = utc(row["epoch"])

        if epoch != utc(
            sample[idx]["reference_tca_utc"]
        ):
            raise AuditError("reference TCA mismatch")

        reference[idx] = {
            "tca": epoch,
            "distance": float(row["min_range"]),
        }


if reference_rows != EXPECTED_REFERENCE_ROWS:
    raise AuditError("reference row count mismatch")

if len(reference) != EXPECTED_EVENTS:
    raise AuditError("reference sample coverage mismatch")


# ------------------------------------------------------------
# Load produced G7-C event rows
# ------------------------------------------------------------

produced = {}

with EVENT.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    for row in csv.DictReader(stream):

        idx = int(row["final_g6_sample_index"])
        tol = int(row["tolerance_min"])

        key = (tol, idx)

        if key in produced:
            raise AuditError("duplicate G7-C event row")

        produced[key] = row


if len(produced) != EXPECTED_EVENTS * len(TOLERANCES):
    raise AuditError("G7-C event output row count mismatch")


# ------------------------------------------------------------
# Frozen G6 metric cross-check
# ------------------------------------------------------------

g6 = {}

with G6_EVENT.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    for row in csv.DictReader(stream):
        g6[
            int(row["final_g6_sample_index"])
        ] = row


if len(g6) != EXPECTED_EVENTS:
    raise AuditError("G6 event count mismatch")


# ------------------------------------------------------------
# Recompute every tolerance/event row
# ------------------------------------------------------------

recomputed_summary = []

association_sets = {}

for tol in TOLERANCES:

    tol_s = tol * 60.0

    associated_indices = set()

    ed = []
    et = []

    ed_success = 0
    et_success = 0


    for idx in range(1, EXPECTED_EVENTS + 1):

        row = produced[(tol, idx)]

        nearest = best.get(idx)

        available = nearest is not None

        associated = (
            available
            and nearest["offset"] <= tol_s
        )


        if as_bool(row["candidate_available"]) != available:
            raise AuditError(
                f"candidate-availability mismatch {tol}/{idx}"
            )


        if int(row["candidate_count"]) != candidate_counts.get(idx, 0):
            raise AuditError(
                f"candidate-count mismatch {tol}/{idx}"
            )


        if as_bool(row["associated"]) != associated:
            raise AuditError(
                f"association mismatch {tol}/{idx}"
            )


        if available:

            if int(row["nearest_candidate_index"]) != nearest["candidate_index"]:
                raise AuditError(
                    f"nearest index mismatch {tol}/{idx}"
                )

            if utc(row["nearest_candidate_time_utc"]) != nearest["candidate_time"]:
                raise AuditError(
                    f"nearest time mismatch {tol}/{idx}"
                )

            close(
                row["nearest_candidate_distance_km"],
                nearest["distance"],
                "nearest distance",
            )

            close(
                row["absolute_reference_tca_offset_s"],
                nearest["offset"],
                "nearest reference offset",
                tol=1e-8,
            )

        else:

            for field in (
                "nearest_candidate_index",
                "nearest_candidate_time_utc",
                "nearest_candidate_distance_km",
                "absolute_reference_tca_offset_s",
            ):
                if row[field].strip():
                    raise AuditError(
                        f"no-candidate field populated {tol}/{idx}/{field}"
                    )


        if associated:

            associated_indices.add(idx)

            predicted_tca = nearest["candidate_time"]
            predicted_d = nearest["distance"]

            ref_tca = reference[idx]["tca"]
            ref_d = reference[idx]["distance"]

            signed_t = (
                predicted_tca
                - ref_tca
            ).total_seconds()

            abs_t = abs(signed_t)

            signed_d = (
                predicted_d
                - ref_d
            )

            abs_d = abs(signed_d)

            close(
                row["absolute_tca_error_s"],
                abs_t,
                "E_t",
                tol=1e-8,
            )

            close(
                row["absolute_miss_distance_error_km"],
                abs_d,
                "E_d",
            )

            if as_bool(row["tca_covered"]) != (abs_t <= TCA_RADIUS):
                raise AuditError("TCA coverage mismatch")

            if as_bool(row["miss_distance_covered"]) != (abs_d <= MISS_RADIUS):
                raise AuditError("miss-distance coverage mismatch")

            ed.append(abs_d)
            et.append(abs_t)

            ed_success += int(abs_d <= MISS_RADIUS)
            et_success += int(abs_t <= TCA_RADIUS)

        else:

            for field in (
                "predicted_tca_utc",
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
            ):
                if row[field].strip():
                    raise AuditError(
                        f"unassociated metric populated {tol}/{idx}/{field}"
                    )


        # Exact frozen 5-minute validity reproduction.
        if tol == 5:

            frozen_valid = as_bool(
                g6[idx]["valid_associated"]
            )

            if associated != frozen_valid:
                raise AuditError(
                    f"5-minute G6 validity mismatch {idx}"
                )

            if associated:

                if int(g6[idx]["selected_candidate_index"]) != nearest["candidate_index"]:
                    raise AuditError(
                        f"5-minute candidate identity mismatch {idx}"
                    )

                close(
                    g6[idx]["absolute_tca_error_s"],
                    abs_t,
                    "5-minute G6 E_t",
                    tol=1e-8,
                )

                close(
                    g6[idx]["absolute_miss_distance_error_km"],
                    abs_d,
                    "5-minute G6 E_d",
                )


    association_sets[tol] = associated_indices

    n = len(associated_indices)

    assoc_lo, assoc_hi = wilson(
        n,
        EXPECTED_EVENTS,
    )

    ed_lo, ed_hi = wilson(
        ed_success,
        n,
    )

    et_lo, et_hi = wilson(
        et_success,
        n,
    )


    recomputed_summary.append({
        "tolerance_min": tol,
        "associated_events": n,
        "newly_associated_vs_5min": None,
        "association_rate_all_events":
            n / EXPECTED_EVENTS,
        "association_wilson_95_lower":
            assoc_lo,
        "association_wilson_95_upper":
            assoc_hi,
        "Ed_successes": ed_success,
        "Ed_denominator": n,
        "Ed_coverage": ed_success / n,
        "Ed_wilson_95_lower": ed_lo,
        "Ed_wilson_95_upper": ed_hi,
        "Ed_median_km": q(ed, 0.50),
        "Ed_p90_km": q(ed, 0.90),
        "Ed_p95_km": q(ed, 0.95),
        "Ed_p99_km": q(ed, 0.99),
        "Ed_maximum_km": max(ed),
        "Et_successes": et_success,
        "Et_denominator": n,
        "Et_coverage": et_success / n,
        "Et_wilson_95_lower": et_lo,
        "Et_wilson_95_upper": et_hi,
        "Et_median_s": q(et, 0.50),
        "Et_p90_s": q(et, 0.90),
        "Et_p95_s": q(et, 0.95),
        "Et_p99_s": q(et, 0.99),
        "Et_maximum_s": max(et),
    })


base_n = recomputed_summary[0]["associated_events"]

for row in recomputed_summary:
    row["newly_associated_vs_5min"] = (
        row["associated_events"]
        - base_n
    )


# Nested association sets are stronger than count monotonicity alone.
for low, high in zip(
    TOLERANCES[:-1],
    TOLERANCES[1:],
):
    if not association_sets[low].issubset(
        association_sets[high]
    ):
        raise AuditError(
            f"association-set nesting failure {low}->{high}"
        )


# ------------------------------------------------------------
# Verify summary CSV independently
# ------------------------------------------------------------

with SUMMARY_CSV.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    emitted_summary = list(
        csv.DictReader(stream)
    )


if len(emitted_summary) != 4:
    raise AuditError("summary CSV row count mismatch")


numeric_fields = (
    "associated_events",
    "newly_associated_vs_5min",
    "association_rate_all_events",
    "association_wilson_95_lower",
    "association_wilson_95_upper",
    "Ed_successes",
    "Ed_denominator",
    "Ed_coverage",
    "Ed_wilson_95_lower",
    "Ed_wilson_95_upper",
    "Ed_median_km",
    "Ed_p90_km",
    "Ed_p95_km",
    "Ed_p99_km",
    "Ed_maximum_km",
    "Et_successes",
    "Et_denominator",
    "Et_coverage",
    "Et_wilson_95_lower",
    "Et_wilson_95_upper",
    "Et_median_s",
    "Et_p90_s",
    "Et_p95_s",
    "Et_p99_s",
    "Et_maximum_s",
)


for expected, emitted in zip(
    recomputed_summary,
    emitted_summary,
):

    if int(emitted["tolerance_min"]) != expected["tolerance_min"]:
        raise AuditError("summary tolerance ordering mismatch")

    for field in numeric_fields:
        close(
            emitted[field],
            expected[field],
            f"summary {field}",
            tol=1e-8,
        )


# ------------------------------------------------------------
# Verify JSON integrity/boundaries
# ------------------------------------------------------------

payload = json.loads(
    SUMMARY_JSON.read_text(
        encoding="utf-8"
    )
)

if payload["status"] != "G7C_ASSOCIATION_TOLERANCE_SENSITIVITY_PASS":
    raise AuditError("G7-C JSON status mismatch")

if payload["five_minute_exact_G6_reproduction"] is not True:
    raise AuditError("5-minute reproduction flag false")

if payload["association_support_monotonic"] is not True:
    raise AuditError("monotonic-support flag false")

if payload["selected_candidate_identity_changes_with_tolerance"] is not False:
    raise AuditError("candidate-identity flag changed")

for key in (
    "orbital_propagation_performed",
    "candidate_generation_performed",
    "GP_HISTORY_accessed",
    "CAL0_refit_performed",
    "method_retuning_performed",
):
    if payload[key] is not False:
        raise AuditError(f"forbidden operation flag true: {key}")

if payload["network_requests"] != 0:
    raise AuditError("network activity reported")

if payload["candidate_rows_scanned"] != EXPECTED_CANDIDATES:
    raise AuditError("candidate scan census mismatch")

if payload["protected_reference_rows_scanned"] != EXPECTED_REFERENCE_ROWS:
    raise AuditError("reference scan census mismatch")


# ------------------------------------------------------------
# Added-event diagnostic
# ------------------------------------------------------------

new_60 = (
    association_sets[60]
    - association_sets[5]
)

if len(new_60) != 48:
    raise AuditError("5->60 added-event count mismatch")

added_ed_success = 0
added_et_success = 0

for idx in new_60:

    row = produced[(60, idx)]

    added_ed_success += int(
        as_bool(
            row["miss_distance_covered"]
        )
    )

    added_et_success += int(
        as_bool(
            row["tca_covered"]
        )
    )


if added_ed_success != 5:
    raise AuditError("added-event E_d success count mismatch")

if added_et_success != 0:
    raise AuditError("added-event E_t success count mismatch")


result = {
    "status":
        "G7C_ASSOCIATION_TOLERANCE_INDEPENDENT_AUDIT_PASS",

    "events_verified":
        EXPECTED_EVENTS,

    "event_tolerance_rows_verified":
        len(produced),

    "candidate_rows_independently_rescanned":
        candidate_rows,

    "candidate_ready_events_independently_verified":
        len(best),

    "protected_reference_rows_independently_rescanned":
        reference_rows,

    "five_minute_exact_G6_reproduction_independently_verified":
        True,

    "association_sets_nested":
        True,

    "summary_rows_independently_recomputed":
        4,

    "associated_event_counts": {
        str(row["tolerance_min"]):
            row["associated_events"]
        for row in recomputed_summary
    },

    "newly_associated_vs_5min": {
        str(row["tolerance_min"]):
            row["newly_associated_vs_5min"]
        for row in recomputed_summary
    },

    "newly_associated_5_to_60_events":
        len(new_60),

    "newly_associated_5_to_60_Ed_successes":
        added_ed_success,

    "newly_associated_5_to_60_Et_successes":
        added_et_success,

    "independent_candidate_selection_reproduction":
        True,

    "independent_reference_metric_reproduction":
        True,

    "orbital_propagation_performed":
        False,

    "candidate_generation_performed":
        False,

    "GP_HISTORY_accessed":
        False,

    "CAL0_refit_performed":
        False,

    "method_retuning_performed":
        False,

    "network_requests":
        0,

    "input_hashes": {
        EVENT.relative_to(ROOT).as_posix():
            sha256(EVENT),

        SUMMARY_CSV.relative_to(ROOT).as_posix():
            sha256(SUMMARY_CSV),

        SUMMARY_JSON.relative_to(ROOT).as_posix():
            sha256(SUMMARY_JSON),

        CANDIDATES.relative_to(ROOT).as_posix():
            sha256(CANDIDATES),

        TRACSS.relative_to(ROOT).as_posix():
            sha256(TRACSS),
    },

    "interpretation":
        (
            "The independent audit reproduces frozen nearest-candidate "
            "selection and all 5/15/30/60-minute sensitivity results. "
            "Relaxing association tolerance adds support but does not "
            "restore nominal 90% miss-distance coverage."
        ),

    "next_safe_action":
        "FREEZE_G7C_WITH_DECISION_RECORD",
}


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
            "audit_sha256": sha256(OUT),
        },
        indent=2,
        sort_keys=True,
    )
)