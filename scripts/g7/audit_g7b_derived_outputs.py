from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr


ROOT = Path.cwd()

OBJECT_FILE = ROOT / "results/g7/G7B_Object_History_Quality.csv"
EVENT_FILE = ROOT / "results/g7/G7B_Event_History_Quality.csv"
ASSOC_FILE = ROOT / "results/g7/G7B_History_Quality_Associations.csv"
SUMMARY_FILE = ROOT / "results/g7/G7B_History_Quality_Summary.json"
G7A_FILE = ROOT / "results/g7/G7A_Event_Quality.csv"
OUT = ROOT / "results/g7/G7B_Derived_Output_Independent_Audit.json"

EXPECTED_HASHES = {
    OBJECT_FILE:
        "a05e1a33274a2a62ce17e07ca208a601bb57a9720c5f17698b134ceb93b81828",
    EVENT_FILE:
        "d6bb1743fe84ff1b9c38ab71ea8d2246e4727ebcc519c940c93eb0b712830bbc",
    ASSOC_FILE:
        "9cf40c2790301699af5d16119869bd3d259ab13081285c1fd148bb0e38609d22",
    SUMMARY_FILE:
        "20df696683354b51c69562ec129578fe3caea49a7abd09f85dedc42401ec04ea",
    G7A_FILE:
        "8b03c9972c0ac4e869d52e4b7e23eebe5a25cb0c3588d0b2537ed61ca9c92d89",
}

EXPECTED_OBJECTS = 13854
EXPECTED_EVENTS = 6927
EXPECTED_VALID = 6839
EXPECTED_EVALUABLE = 6906
EXPECTED_MAIN_FILES = 1386
EXPECTED_LOCAL_ROWS = 5153040

WINDOWS = (14, 30, 60)

VARIABLES = (
    ("pair_max_state_age_days", "PRIMARY_AGE_CONTEXT"),
    ("w30_pair_min_distinct_state_count", "PRIMARY_30D_HISTORY"),
    ("w30_pair_max_gap_hours", "PRIMARY_30D_HISTORY"),
    ("pair_max_propagation_span_days", "PROPAGATION_CONTEXT"),
    ("w14_pair_min_distinct_state_count", "DESCRIPTIVE_14D_SENSITIVITY"),
    ("w14_pair_max_gap_hours", "DESCRIPTIVE_14D_SENSITIVITY"),
    ("w60_pair_min_distinct_state_count", "DESCRIPTIVE_60D_SENSITIVITY"),
    ("w60_pair_max_gap_hours", "DESCRIPTIVE_60D_SENSITIVITY"),
)

BOOTSTRAP_SEED = 20260908
BOOTSTRAP_REPLICATES = 2000
TOL = 1.0e-14


class AuditError(RuntimeError):
    pass


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def as_bool(value):
    text = str(value).strip().upper()
    if text == "TRUE":
        return True
    if text == "FALSE":
        return False
    raise AuditError(f"invalid bool: {value!r}")


def maybe_float(value):
    text = str(value).strip()
    return None if text == "" else float(text)


def assert_float(actual, expected, label):
    if expected is None:
        if str(actual).strip() != "":
            raise AuditError(f"{label}: expected blank, found {actual!r}")
        return

    value = float(actual)

    if not math.isclose(
        value,
        float(expected),
        rel_tol=0.0,
        abs_tol=TOL,
    ):
        raise AuditError(
            f"{label}: {value!r} != {expected!r}"
        )


def spearman(rows, field):
    pairs = [
        (
            float(row[field]),
            float(row["absolute_miss_distance_error_km"]),
        )
        for row in rows
        if (
            row.get(field) not in (None, "")
            and row.get("absolute_miss_distance_error_km") not in (None, "")
        )
    ]

    n = len(pairs)

    if n < 3:
        return None, n

    x = np.asarray([p[0] for p in pairs], dtype=float)
    y = np.asarray([p[1] for p in pairs], dtype=float)

    if len(np.unique(x)) < 2 or len(np.unique(y)) < 2:
        return None, n

    rho = float(spearmanr(x, y).statistic)

    if not math.isfinite(rho):
        return None, n

    return rho, n


def bootstrap(rows, field, rng):
    pairs = [
        (
            float(row[field]),
            float(row["absolute_miss_distance_error_km"]),
        )
        for row in rows
        if (
            row.get(field) not in (None, "")
            and row.get("absolute_miss_distance_error_km") not in (None, "")
        )
    ]

    n = len(pairs)

    x = np.asarray([p[0] for p in pairs], dtype=float)
    y = np.asarray([p[1] for p in pairs], dtype=float)

    values = []

    for _ in range(BOOTSTRAP_REPLICATES):
        idx = rng.integers(0, n, size=n)
        bx = x[idx]
        by = y[idx]

        if len(np.unique(bx)) < 2 or len(np.unique(by)) < 2:
            continue

        rho = float(spearmanr(bx, by).statistic)

        if math.isfinite(rho):
            values.append(rho)

    if not values:
        return None, None, 0

    values = np.asarray(values, dtype=float)

    return (
        float(np.quantile(values, 0.025)),
        float(np.quantile(values, 0.975)),
        len(values),
    )


for path, expected in EXPECTED_HASHES.items():
    if sha256(path) != expected:
        raise AuditError(f"input SHA mismatch: {path}")


objects = read_csv(OBJECT_FILE)
events = read_csv(EVENT_FILE)
associations = read_csv(ASSOC_FILE)
g7a = read_csv(G7A_FILE)

with SUMMARY_FILE.open("r", encoding="utf-8") as stream:
    summary = json.load(stream)


if len(objects) != EXPECTED_OBJECTS:
    raise AuditError("object row count mismatch")

if len(events) != EXPECTED_EVENTS:
    raise AuditError("event row count mismatch")

if len(g7a) != EXPECTED_EVENTS:
    raise AuditError("G7-A row count mismatch")

if len(associations) != 48:
    raise AuditError("association row count must be 8 variables x 6 scopes")


norad_ids = [row["NORAD_CAT_ID"] for row in objects]

if len(set(norad_ids)) != EXPECTED_OBJECTS:
    raise AuditError("NORAD object uniqueness failure")


if len(set(row["main_raw_path"] for row in objects)) != EXPECTED_MAIN_FILES:
    raise AuditError("main raw path count mismatch")


if sum(int(row["main_window_rows_verified"]) for row in objects) != EXPECTED_LOCAL_ROWS:
    raise AuditError("verified object-local row census mismatch")


roles = {}

for row in objects:
    idx = int(row["final_g6_sample_index"])
    role = row["object_role"]

    if role not in {"obj1", "obj2"}:
        raise AuditError("unexpected object role")

    key = (idx, role)

    if key in roles:
        raise AuditError("duplicate event/object role")

    roles[key] = row


if len(roles) != 2 * EXPECTED_EVENTS:
    raise AuditError("event/object role census mismatch")


for row in objects:

    for w in WINDOWS:

        prefix = f"w{w}"

        # All realized rolling windows are complete.
        if as_bool(row[f"{prefix}_left_censored"]):
            raise AuditError(f"unexpected {w}d left censor")

        pubs = int(row[f"{prefix}_publication_count"])
        states = int(row[f"{prefix}_distinct_state_count"])

        if states < 0 or pubs < 0 or states > pubs:
            raise AuditError("publication/distinct-state invariant failure")

        median_gap = maybe_float(row[f"{prefix}_median_cadence_hours"])
        max_gap = maybe_float(row[f"{prefix}_max_gap_hours"])

        if pubs < 2:
            if median_gap is not None or max_gap is not None:
                raise AuditError("gap should be undefined with <2 publications")
        else:
            if median_gap is None or max_gap is None:
                raise AuditError("gap unexpectedly missing with >=2 publications")


    p14 = int(row["w14_publication_count"])
    p30 = int(row["w30_publication_count"])
    p60 = int(row["w60_publication_count"])

    s14 = int(row["w14_distinct_state_count"])
    s30 = int(row["w30_distinct_state_count"])
    s60 = int(row["w60_distinct_state_count"])

    if not (p14 <= p30 <= p60):
        raise AuditError("publication-count window nesting failure")

    if not (s14 <= s30 <= s60):
        raise AuditError("distinct-state-count window nesting failure")


g7a_by_index = {
    int(row["final_g6_sample_index"]): row
    for row in g7a
}

event_by_index = {
    int(row["final_g6_sample_index"]): row
    for row in events
}

if set(event_by_index) != set(range(1, EXPECTED_EVENTS + 1)):
    raise AuditError("event index set mismatch")


valid_count = 0

for idx, row in event_by_index.items():

    source = g7a_by_index[idx]
    obj1 = roles[(idx, "obj1")]
    obj2 = roles[(idx, "obj2")]

    if row["conj_id"] != source["conj_id"]:
        raise AuditError("conj_id mismatch")

    if row["g6_temporal_stratum"] != source["g6_temporal_stratum"]:
        raise AuditError("temporal-stratum mismatch")

    valid = as_bool(row["valid_associated"])

    if valid != as_bool(source["valid_associated"]):
        raise AuditError("VALID_ASSOCIATED mismatch")

    if valid:
        valid_count += 1

        assert_float(
            row["absolute_miss_distance_error_km"],
            float(source["absolute_miss_distance_error_km"]),
            "E_d",
        )
    else:
        if row["absolute_miss_distance_error_km"].strip():
            raise AuditError("nonvalid event has E_d")


    assert_float(
        row["pair_max_state_age_days"],
        float(source["pair_max_state_age_days"]),
        "pair state age",
    )


    expected_prop = (
        float(source["pair_max_propagation_span_days"])
        if valid
        else None
    )

    assert_float(
        row["pair_max_propagation_span_days"],
        expected_prop,
        "pair propagation span",
    )


    for w in WINDOWS:

        prefix = f"w{w}"

        pair_censored = (
            as_bool(obj1[f"{prefix}_left_censored"])
            or as_bool(obj2[f"{prefix}_left_censored"])
        )

        if as_bool(row[f"{prefix}_pair_left_censored"]) != pair_censored:
            raise AuditError("pair left-censor derivation mismatch")

        if pair_censored:
            expected_states = None
            expected_gap = None

        else:
            expected_states = min(
                int(obj1[f"{prefix}_distinct_state_count"]),
                int(obj2[f"{prefix}_distinct_state_count"]),
            )

            g1 = maybe_float(obj1[f"{prefix}_max_gap_hours"])
            g2 = maybe_float(obj2[f"{prefix}_max_gap_hours"])

            expected_gap = (
                max(g1, g2)
                if g1 is not None and g2 is not None
                else None
            )

        assert_float(
            row[f"{prefix}_pair_min_distinct_state_count"],
            expected_states,
            "pair minimum distinct-state count",
        )

        assert_float(
            row[f"{prefix}_pair_max_gap_hours"],
            expected_gap,
            "pair maximum gap",
        )


if valid_count != EXPECTED_VALID:
    raise AuditError("VALID_ASSOCIATED census mismatch")


assoc_map = {}

for row in associations:

    key = (
        row["scope"],
        row["temporal_stratum"],
        row["quality_variable"],
    )

    if key in assoc_map:
        raise AuditError("duplicate association row")

    assoc_map[key] = row


valid_rows = [
    row for row in events
    if as_bool(row["valid_associated"])
]

rng = np.random.default_rng(BOOTSTRAP_SEED)

verified_associations = 0


for field, role in VARIABLES:

    rho, n = spearman(valid_rows, field)
    lower, upper, valid_boot = bootstrap(valid_rows, field, rng)

    key = (
        "ALL_G7_G6_SAMPLE",
        "ALL",
        field,
    )

    observed = assoc_map.get(key)

    if observed is None:
        raise AuditError(f"missing pooled association: {field}")

    if observed["analysis_role"] != role:
        raise AuditError("analysis-role mismatch")

    if int(observed["n"]) != n:
        raise AuditError("pooled association n mismatch")

    assert_float(observed["spearman_rho"], rho, "pooled rho")
    assert_float(observed["bootstrap_95_lower"], lower, "bootstrap lower")
    assert_float(observed["bootstrap_95_upper"], upper, "bootstrap upper")

    if int(observed["bootstrap_replicates_valid"]) != valid_boot:
        raise AuditError("valid bootstrap count mismatch")

    if int(observed["bootstrap_replicates_requested"]) != BOOTSTRAP_REPLICATES:
        raise AuditError("bootstrap request count mismatch")

    if int(observed["bootstrap_seed"]) != BOOTSTRAP_SEED:
        raise AuditError("bootstrap seed mismatch")

    verified_associations += 1


    for stratum in ("G6-T1", "G6-T2", "G6-T3", "G6-T4", "G6-T5"):

        subset = [
            row for row in valid_rows
            if row["g6_temporal_stratum"] == stratum
        ]

        srho, sn = spearman(subset, field)

        key = (
            "TEMPORAL_STRATUM",
            stratum,
            field,
        )

        observed = assoc_map.get(key)

        if observed is None:
            raise AuditError("missing temporal association")

        if int(observed["n"]) != sn:
            raise AuditError("temporal association n mismatch")

        assert_float(observed["spearman_rho"], srho, "temporal rho")

        if (
            observed["bootstrap_95_lower"].strip()
            or observed["bootstrap_95_upper"].strip()
            or int(observed["bootstrap_replicates_requested"]) != 0
            or int(observed["bootstrap_replicates_valid"]) != 0
        ):
            raise AuditError("unexpected temporal bootstrap")

        verified_associations += 1


if verified_associations != 48:
    raise AuditError("association verification census mismatch")


window_support = {}

for w in WINDOWS:

    prefix = f"w{w}"

    object_left = sum(
        as_bool(row[f"{prefix}_left_censored"])
        for row in objects
    )

    pair_left = sum(
        as_bool(row[f"{prefix}_pair_left_censored"])
        for row in events
    )

    valid_complete = sum(
        as_bool(row["valid_associated"])
        and not as_bool(row[f"{prefix}_pair_left_censored"])
        for row in events
    )

    valid_gap = sum(
        as_bool(row["valid_associated"])
        and row[f"{prefix}_pair_max_gap_hours"].strip() != ""
        for row in events
    )

    window_support[str(w)] = {
        "object_left_censored": object_left,
        "pair_left_censored": pair_left,
        "VALID_ASSOCIATED_complete_pair_windows": valid_complete,
        "VALID_ASSOCIATED_pair_gap_supported": valid_gap,
    }


if summary["window_support"] != window_support:
    raise AuditError("summary window-support mismatch")


pred_counts = dict(
    sorted(
        Counter(
            row["full_history_predecessor_class"]
            for row in objects
        ).items()
    )
)

if summary["full_history_predecessor_class_counts"] != pred_counts:
    raise AuditError("predecessor-class summary mismatch")


for required_false in (
    "CAL0_refit_performed",
    "association_recomputed",
    "candidate_generation_performed",
    "method_retuning_performed",
    "orbital_propagation_performed",
):

    if summary[required_false] is not False:
        raise AuditError(f"{required_false} unexpectedly true")


if summary["network_requests"] != 0:
    raise AuditError("network request count changed")

if summary["events"] != EXPECTED_EVENTS:
    raise AuditError("summary event census mismatch")

if summary["objects"] != EXPECTED_OBJECTS:
    raise AuditError("summary object census mismatch")

if summary["VALID_ASSOCIATED_events"] != EXPECTED_VALID:
    raise AuditError("summary valid-associated census mismatch")

if summary["evaluable_events"] != EXPECTED_EVALUABLE:
    raise AuditError("summary evaluable census mismatch")

if summary["main_raw_files_opened"] != EXPECTED_MAIN_FILES:
    raise AuditError("summary main-file census mismatch")

if summary["object_local_main_rows_verified"] != EXPECTED_LOCAL_ROWS:
    raise AuditError("summary local-row census mismatch")

if summary["predecessor_raw_files_opened"] != 0:
    raise AuditError("unexpected predecessor raw access")

if summary["predecessor_evidence_files_opened"] != 0:
    raise AuditError("unexpected predecessor evidence access")


for path in (OBJECT_FILE, EVENT_FILE, ASSOC_FILE):

    rel = path.relative_to(ROOT).as_posix()

    if summary["output_hashes"][rel] != sha256(path):
        raise AuditError(f"summary output hash mismatch: {rel}")


payload = {
    "status":
        "G7B_DERIVED_OUTPUT_INDEPENDENT_AUDIT_PASS",

    "objects_verified":
        len(objects),

    "events_verified":
        len(events),

    "VALID_ASSOCIATED_verified":
        valid_count,

    "association_rows_independently_recomputed":
        verified_associations,

    "window_support_independently_recomputed":
        window_support,

    "event_pair_derivations_verified":
        EXPECTED_EVENTS,

    "object_window_internal_invariants_verified":
        EXPECTED_OBJECTS,

    "input_hashes": {
        path.relative_to(ROOT).as_posix(): sha256(path)
        for path in EXPECTED_HASHES
    },

    "raw_GP_HISTORY_files_opened":
        0,

    "raw_GP_HISTORY_rows_scanned":
        0,

    "raw_content_rehashed":
        False,

    "second_raw_history_pass_performed":
        False,

    "independent_raw_extraction_reproduction":
        False,

    "independent_derived_output_reproduction":
        True,

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

    "interpretation":
        (
            "Independent audit verifies G7-B derived object/event "
            "consistency, pair aggregation, window support, pooled and "
            "temporal Spearman statistics, and pooled bootstrap intervals "
            "without reopening GP_HISTORY. The original single-pass raw "
            "extraction is therefore hash-frozen rather than falsely "
            "claimed to have been independently re-extracted."
        ),

    "next_safe_action":
        "HUMAN_REVIEW_THEN_FREEZE_G7B_WITH_DR0039",
}


with OUT.open(
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
        {
            **payload,
            "audit_sha256": sha256(OUT),
        },
        indent=2,
        sort_keys=True,
    )
)