from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

H7_EVENT = ROOT / "results/g7/G7D_H7_Event_Status.csv"
H7_CAND = ROOT / "results/g7/G7D_H7_Candidates.csv"
H7_SUMMARY = ROOT / "results/g7/G7D_H7_Candidate_Summary.json"
H7_DB = ROOT / "data/raw/g7/G7D_H7_Candidate_Work.sqlite3"

H5_EVENT = ROOT / "results/g6/G6_PreOutcome_Candidate_Event_Status.csv"
H5_CAND = ROOT / "results/g6/G6_PreOutcome_Candidates.csv"

H3_EVENT = ROOT / "results/g7/G7D_H3_Event_Status.csv"
H3_CAND = ROOT / "results/g7/G7D_H3_Candidates.csv"
H3_SUMMARY = ROOT / "results/g7/G7D_H3_Candidate_Summary.json"

AUDIT_OUT = (
    ROOT
    / "results/g7/G7D_H7_Candidate_Output_Independent_Audit.json"
)

EXPECTED_HEAD = (
    "cb4340ea959678dd74acf4ce5b95582107c23657"
)

EXPECTED_TAG = "g7d-horizon-execution-authorized"

EXPECTED_H7_EVENT_SHA = (
    "418348e3389b47462d196ccbc53d0a4a0e90b0c7a4a9f17e93de51a3a75c2595"
)

EXPECTED_H7_CAND_SHA = (
    "1f32718dc5607ed3d5b8d6a75c6bdd728fe6518b39e49bba607339cf71793791"
)

EXPECTED_H3_EVENT_SHA = (
    "5689d7a8b279a6d6a7f7ccd7941d977f2ce9ff04928a29201b82c385c3e94b43"
)

EXPECTED_H3_CAND_SHA = (
    "a64c500a73a2b66df44642066337d1cdd039404cc72147ada65f0862dad77aae"
)

EXPECTED_H3_SUMMARY_SHA = (
    "4712398461d66686208f1891accbcd07b952f8e7d07049c773c03e3daefbf462"
)

EXPECTED_H5_CAND_SHA = (
    "21b8a9f5c8aab4b3427fad5cd3f3c2823ffc0219bd2a36aeccafc6a0aaa80e36"
)

EXPECTED_EXECUTOR_SHA = (
    "488c99aec614cd51c6ae3682fb8ef404fc767d6417681b09c82e2f5b1c4aa48d"
)

EXPECTED_G6_SHA = (
    "bce2a4b5e4dab8d0c4e461e2acb4dbac8afe22d3ac412922ba3670a832adcecb"
)

EXPECTED_KERNEL_SHA = (
    "6bba58d77f1c91f1d19fc25de684be1079d66e0808a428e9fa44a213d2ba48a0"
)

EXPECTED_SELECTION_SHA = (
    "6d03f0b4c7104b7cbac76b649bedc87e9f497f297dccc19f2270e7fec5552095"
)

EXPECTED_CACHE_SHA = (
    "b77cf2893cf92b2e577bf04f5f23d67dc6be31b9d1e8aeafe0e40efd6b08ed69"
)

EVENT_FIELDS = [
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "obj1",
    "obj2",
    "replay_time_utc",
    "obj1_GP_ID",
    "obj2_GP_ID",
    "parse_status",
    "propagation_status",
    "rejected_local_inconsistency_count",
    "recovered_local_inconsistency_count",
    "unresolved_boundary_count",
    "sampled_count",
    "raw_candidate_count",
    "candidate_count",
    "preoutcome_status",
    "failure_detail",
]

CAND_FIELDS = [
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "candidate_index",
    "time_from_replay_s",
    "candidate_time_utc",
    "distance_km",
    "bracket_lower_s",
    "bracket_upper_s",
    "bracket_index",
    "converged",
    "iterations",
    "function_evaluations",
    "termination",
]

IDENTITY_FIELDS = [
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "obj1",
    "obj2",
    "replay_time_utc",
    "obj1_GP_ID",
    "obj2_GP_ID",
    "parse_status",
]

N_EVENTS = 6927
N_OBJECTS = 13854

N_H3_CAND = 558469
N_H5_CAND = 929821
N_H7_CAND = 1299383

H7_END = 7.0 * 86400.0
EXPECTED_H7_SAMPLED = int(H7_END / 10.0) + 1


class AuditError(RuntimeError):
    pass


def sha(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def git(*args: str) -> str:
    p = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if p.returncode:
        raise AuditError(
            p.stderr.strip()
        )

    return p.stdout.strip()


def utc(text: str):
    value = text.strip()

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    return datetime.fromisoformat(value)


def csv_value(value):
    if value is None:
        return ""

    if isinstance(value, bool):
        return (
            "True"
            if value
            else "False"
        )

    return str(value)


def read_events(path: Path):
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        reader = csv.DictReader(f)

        if reader.fieldnames != EVENT_FIELDS:
            raise AuditError(
                f"event schema mismatch: {path.name}"
            )

        rows = list(reader)

    by_index = {}

    for row in rows:

        idx = int(
            row["final_g6_sample_index"]
        )

        if idx in by_index:
            raise AuditError(
                f"duplicate event index: {idx}"
            )

        by_index[idx] = row

    return rows, by_index


# ---------------------------------------------------------
# Immutable execution boundary
# ---------------------------------------------------------

if git("rev-parse", "HEAD") != EXPECTED_HEAD:
    raise AuditError(
        "authorization HEAD changed"
    )

if (
    git(
        "rev-parse",
        f"refs/tags/{EXPECTED_TAG}^{{commit}}",
    )
    != EXPECTED_HEAD
):
    raise AuditError(
        "authorization tag target changed"
    )

for path in (
    H7_EVENT,
    H7_CAND,
    H7_SUMMARY,
    H7_DB,
    H5_EVENT,
    H5_CAND,
    H3_EVENT,
    H3_CAND,
    H3_SUMMARY,
):
    if not path.is_file():
        raise AuditError(
            f"missing required file: {path}"
        )

if sha(H7_EVENT) != EXPECTED_H7_EVENT_SHA:
    raise AuditError(
        "H7 event hash mismatch"
    )

if sha(H7_CAND) != EXPECTED_H7_CAND_SHA:
    raise AuditError(
        "H7 candidate hash mismatch"
    )

if sha(H3_EVENT) != EXPECTED_H3_EVENT_SHA:
    raise AuditError(
        "H3 event hash changed"
    )

if sha(H3_CAND) != EXPECTED_H3_CAND_SHA:
    raise AuditError(
        "H3 candidate hash changed"
    )

if sha(H3_SUMMARY) != EXPECTED_H3_SUMMARY_SHA:
    raise AuditError(
        "H3 summary hash changed"
    )

if sha(H5_CAND) != EXPECTED_H5_CAND_SHA:
    raise AuditError(
        "frozen H5 candidate hash changed"
    )


# ---------------------------------------------------------
# H7 summary
# ---------------------------------------------------------

summary = json.loads(
    H7_SUMMARY.read_text(
        encoding="utf-8"
    )
)

if (
    summary["status"]
    != "G7D_H7_CANDIDATE_GENERATION_PASS"
):
    raise AuditError(
        "H7 summary not PASS"
    )

if int(summary["horizon_days"]) != 7:
    raise AuditError(
        "wrong H7 horizon"
    )

if int(summary["events"]) != N_EVENTS:
    raise AuditError(
        "wrong H7 event count"
    )

if int(summary["objects"]) != N_OBJECTS:
    raise AuditError(
        "wrong H7 object count"
    )

if (
    int(summary["candidate_rows"])
    != N_H7_CAND
):
    raise AuditError(
        "wrong H7 candidate count"
    )

if (
    int(
        summary[
            "completed_checkpoint_events_at_start"
        ]
    )
    != 0
):
    raise AuditError(
        "H7 was not a first-pass execution"
    )

if (
    summary["event_output_sha256"]
    != EXPECTED_H7_EVENT_SHA
):
    raise AuditError(
        "summary H7 event hash mismatch"
    )

if (
    summary["candidate_output_sha256"]
    != EXPECTED_H7_CAND_SHA
):
    raise AuditError(
        "summary H7 candidate hash mismatch"
    )

for key in (
    "reference_association_performed",
    "protected_reference_accessed",
    "GP_HISTORY_accessed",
    "CAL0_refit_performed",
    "method_retuning_performed",
    "automatic_retry_performed",
    "frozen_H5_anchor_rerun",
):
    if summary[key] is not False:
        raise AuditError(
            f"forbidden H7 summary flag: {key}"
        )

if int(summary["network_requests"]) != 0:
    raise AuditError(
        "H7 network requests reported"
    )

if (
    summary["executor_sha256"]
    != EXPECTED_EXECUTOR_SHA
):
    raise AuditError(
        "executor hash mismatch"
    )

expected_dependencies = {
    "data/raw/g6/G6_48H_Replay_Selected_GP_Cache.jsonl":
        EXPECTED_CACHE_SHA,

    "results/g6/G6_48H_Replay_Input_Selection.csv":
        EXPECTED_SELECTION_SHA,

    "scripts/g6/generate_g6_preoutcome_candidates.py":
        EXPECTED_G6_SHA,

    "scripts/g7/g7d_horizon_numerics.py":
        EXPECTED_KERNEL_SHA,
}

if (
    summary["dependency_hashes"]
    != expected_dependencies
):
    raise AuditError(
        "H7 dependency map mismatch"
    )


# ---------------------------------------------------------
# Event universes and identities
# ---------------------------------------------------------

h7_events, h7_by = read_events(
    H7_EVENT
)

h5_events, h5_by = read_events(
    H5_EVENT
)

h3_events, h3_by = read_events(
    H3_EVENT
)

for label, rows in (
    ("H7", h7_events),
    ("H5", h5_events),
    ("H3", h3_events),
):
    if len(rows) != N_EVENTS:
        raise AuditError(
            f"{label} event census mismatch"
        )

expected_indices = set(
    range(1, N_EVENTS + 1)
)

if set(h7_by) != expected_indices:
    raise AuditError(
        "H7 event-index universe mismatch"
    )

if (
    set(h3_by) != expected_indices
    or set(h5_by) != expected_indices
):
    raise AuditError(
        "anchor event-index universe mismatch"
    )

for idx in sorted(expected_indices):

    for field in IDENTITY_FIELDS:

        h7_value = h7_by[idx][field]

        if h7_value != h5_by[idx][field]:
            raise AuditError(
                f"H7/H5 identity mismatch: "
                f"{idx} {field}"
            )

        if h7_value != h3_by[idx][field]:
            raise AuditError(
                f"H7/H3 identity mismatch: "
                f"{idx} {field}"
            )


# ---------------------------------------------------------
# Event census
# ---------------------------------------------------------

h7_status = Counter(
    row["preoutcome_status"]
    for row in h7_events
)

expected_h7_status = Counter({
    "CANDIDATE_SET_READY": 6889,
    "PROPAGATION_FAILURE": 1,
    "UNRECOVERED_REFINEMENT_FAILURE": 37,
})

if h7_status != expected_h7_status:
    raise AuditError(
        f"unexpected H7 status census: "
        f"{h7_status}"
    )

summary_status = Counter({
    str(key): int(value)
    for key, value
    in summary[
        "event_status_counts"
    ].items()
})

if summary_status != h7_status:
    raise AuditError(
        "summary/event CSV status mismatch"
    )

strata = Counter(
    row["g6_temporal_stratum"]
    for row in h7_events
)

expected_strata = Counter({
    "G6-T1": 1386,
    "G6-T2": 1386,
    "G6-T3": 1385,
    "G6-T4": 1385,
    "G6-T5": 1385,
})

if strata != expected_strata:
    raise AuditError(
        "H7 temporal-stratum census mismatch"
    )

objects = []

for row in h7_events:
    objects.extend(
        (
            row["obj1"],
            row["obj2"],
        )
    )

if (
    len(objects) != N_OBJECTS
    or len(set(objects)) != N_OBJECTS
):
    raise AuditError(
        "H7 object-disjoint invariant failed"
    )


# ---------------------------------------------------------
# Checkpoint metadata
# ---------------------------------------------------------

expected_metadata = {
    "CAL0_refit":
        "PROHIBITED",

    "executor_sha256":
        EXPECTED_EXECUTOR_SHA,

    "expected_events":
        "6927",

    "expected_objects":
        "13854",

    "g6_implementation_sha256":
        EXPECTED_G6_SHA,

    "g7d_horizon_kernel_sha256":
        EXPECTED_KERNEL_SHA,

    "horizon_days":
        "7",

    "method_retuning":
        "PROHIBITED",

    "reference_association":
        "PROHIBITED_IN_CANDIDATE_GENERATION",

    "selected_TLE_cache_sha256":
        EXPECTED_CACHE_SHA,

    "selection_sha256":
        EXPECTED_SELECTION_SHA,
}

candidate_counts = defaultdict(int)
previous_time = {}

minimum_time = math.inf
maximum_time = -math.inf

with sqlite3.connect(
    str(H7_DB)
) as con:

    metadata = dict(
        con.execute(
            "SELECT key, value "
            "FROM metadata "
            "ORDER BY key"
        )
    )

    if metadata != expected_metadata:
        raise AuditError(
            "H7 checkpoint metadata mismatch"
        )

    db_event_count = con.execute(
        "SELECT COUNT(*) "
        "FROM event_results"
    ).fetchone()[0]

    db_candidate_count = con.execute(
        "SELECT COUNT(*) "
        "FROM candidate_results"
    ).fetchone()[0]

    if db_event_count != N_EVENTS:
        raise AuditError(
            "H7 DB event count mismatch"
        )

    if db_candidate_count != N_H7_CAND:
        raise AuditError(
            "H7 DB candidate count mismatch"
        )

    # -----------------------------------------------------
    # Exact DB -> event CSV payload equivalence
    # -----------------------------------------------------

    db_events = con.execute(
        "SELECT "
        "final_g6_sample_index, "
        "payload_json "
        "FROM event_results "
        "ORDER BY final_g6_sample_index"
    )

    for csv_row, db_row in itertools.zip_longest(
        h7_events,
        db_events,
    ):

        if (
            csv_row is None
            or db_row is None
        ):
            raise AuditError(
                "H7 event CSV/DB length mismatch"
            )

        idx, payload_text = db_row

        payload = json.loads(
            payload_text
        )

        if (
            int(
                csv_row[
                    "final_g6_sample_index"
                ]
            )
            != int(idx)
        ):
            raise AuditError(
                "H7 event CSV/DB key mismatch"
            )

        for field in EVENT_FIELDS:

            if (
                csv_row[field]
                != csv_value(
                    payload.get(field)
                )
            ):
                raise AuditError(
                    "H7 event CSV/DB mismatch: "
                    f"{idx} {field}"
                )

    # -----------------------------------------------------
    # Exact DB -> candidate CSV payload equivalence
    # -----------------------------------------------------

    db_candidates = con.execute(
        "SELECT "
        "final_g6_sample_index, "
        "candidate_index, "
        "payload_json "
        "FROM candidate_results "
        "ORDER BY "
        "final_g6_sample_index, "
        "candidate_index"
    )

    with H7_CAND.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        reader = csv.DictReader(f)

        if reader.fieldnames != CAND_FIELDS:
            raise AuditError(
                "H7 candidate schema mismatch"
            )

        candidate_rows = 0

        for crow, drow in itertools.zip_longest(
            reader,
            db_candidates,
        ):

            if (
                crow is None
                or drow is None
            ):
                raise AuditError(
                    "H7 candidate CSV/DB "
                    "length mismatch"
                )

            db_event, db_index, payload_text = drow

            payload = json.loads(
                payload_text
            )

            event = int(
                crow[
                    "final_g6_sample_index"
                ]
            )

            candidate_index = int(
                crow["candidate_index"]
            )

            if (
                event != int(db_event)
                or candidate_index
                != int(db_index)
            ):
                raise AuditError(
                    "H7 candidate CSV/DB "
                    "key mismatch"
                )

            for field in CAND_FIELDS:

                if (
                    crow[field]
                    != csv_value(
                        payload.get(field)
                    )
                ):
                    raise AuditError(
                        "H7 candidate CSV/DB "
                        f"mismatch: "
                        f"{event} "
                        f"{candidate_index} "
                        f"{field}"
                    )

            # Frozen G6 serializer is 1-based.
            if (
                candidate_index
                != candidate_counts[event] + 1
            ):
                raise AuditError(
                    "H7 candidate-index "
                    f"discontinuity: {event}"
                )

            event_row = h7_by[event]

            if (
                crow["g6_temporal_stratum"]
                != event_row[
                    "g6_temporal_stratum"
                ]
            ):
                raise AuditError(
                    "H7 candidate stratum mismatch"
                )

            if (
                crow["conj_id"]
                != event_row["conj_id"]
            ):
                raise AuditError(
                    "H7 candidate conj_id mismatch"
                )

            time_s = float(
                crow["time_from_replay_s"]
            )

            distance_km = float(
                crow["distance_km"]
            )

            lower_s = float(
                crow["bracket_lower_s"]
            )

            upper_s = float(
                crow["bracket_upper_s"]
            )

            if not (
                math.isfinite(time_s)
                and 0.0 < time_s < H7_END
            ):
                raise AuditError(
                    "H7 candidate outside "
                    f"open horizon: {event} "
                    f"{time_s}"
                )

            if not (
                math.isfinite(distance_km)
                and distance_km >= 0.0
            ):
                raise AuditError(
                    "invalid H7 candidate distance"
                )

            if not (
                0.0
                <= lower_s
                <= time_s
                <= upper_s
                <= H7_END
            ):
                raise AuditError(
                    "invalid H7 candidate bracket"
                )

            if (
                crow["converged"].lower()
                != "true"
            ):
                raise AuditError(
                    "non-converged H7 candidate "
                    "serialized"
                )

            if event in previous_time:

                if (
                    time_s
                    <= previous_time[event]
                ):
                    raise AuditError(
                        "H7 candidate time-ordering "
                        f"failure: {event}"
                    )

            previous_time[event] = time_s

            replay_time = utc(
                event_row["replay_time_utc"]
            )

            candidate_time = utc(
                crow["candidate_time_utc"]
            )

            serialized_offset = (
                candidate_time
                - replay_time
            ).total_seconds()

            if (
                abs(
                    serialized_offset
                    - time_s
                )
                > 5e-6
            ):
                raise AuditError(
                    "H7 candidate UTC-offset "
                    f"mismatch: {event}"
                )

            candidate_counts[event] += 1
            candidate_rows += 1

            minimum_time = min(
                minimum_time,
                time_s,
            )

            maximum_time = max(
                maximum_time,
                time_s,
            )


if candidate_rows != N_H7_CAND:
    raise AuditError(
        "H7 candidate row census mismatch"
    )


# ---------------------------------------------------------
# Event-level numerical/status semantics
# ---------------------------------------------------------

candidate_sum = 0

for row in h7_events:

    event = int(
        row["final_g6_sample_index"]
    )

    declared = int(
        row["candidate_count"]
    )

    actual = candidate_counts.get(
        event,
        0,
    )

    if declared != actual:
        raise AuditError(
            "H7 event candidate-count "
            f"mismatch: {event}"
        )

    candidate_sum += declared

    status = row["preoutcome_status"]

    if status == "CANDIDATE_SET_READY":

        if row["parse_status"] != "SUCCESS":
            raise AuditError(
                "H7 ready event parse failure"
            )

        recovered = int(
            row[
                "recovered_local_inconsistency_count"
            ]
        )

        expected_propagation = (
            "SUCCESS_WITH_REDUNDANT_BRACKET_RECOVERY"
            if recovered > 0
            else "SUCCESS"
        )

        if (
            row["propagation_status"]
            != expected_propagation
        ):
            raise AuditError(
                "H7 ready-event "
                "propagation-status mismatch"
            )

        if declared <= 0:
            raise AuditError(
                "H7 ready event has no candidates"
            )

        if (
            int(row["sampled_count"])
            != EXPECTED_H7_SAMPLED
        ):
            raise AuditError(
                "H7 sampled-count mismatch"
            )

        if (
            int(row["raw_candidate_count"])
            < declared
        ):
            raise AuditError(
                "H7 raw candidate count "
                "below retained count"
            )

    elif (
        status
        == "UNRECOVERED_REFINEMENT_FAILURE"
    ):

        if row["parse_status"] != "SUCCESS":
            raise AuditError(
                "H7 refinement failure "
                "has parse failure"
            )

        if (
            row["propagation_status"]
            != "FAILED_NUMERICAL_REFINEMENT"
        ):
            raise AuditError(
                "H7 refinement-failure "
                "status mismatch"
            )

        if declared != 0:
            raise AuditError(
                "H7 refinement-failure "
                "event has candidates"
            )

    elif status == "PROPAGATION_FAILURE":

        if row["parse_status"] != "SUCCESS":
            raise AuditError(
                "H7 propagation failure "
                "has parse failure"
            )

        if (
            row["propagation_status"]
            != "FAILED"
        ):
            raise AuditError(
                "H7 propagation-failure "
                "status mismatch"
            )

        if declared != 0:
            raise AuditError(
                "H7 propagation-failure "
                "event has candidates"
            )

    else:
        raise AuditError(
            f"unexpected H7 event status: {status}"
        )


if candidate_sum != N_H7_CAND:
    raise AuditError(
        "H7 candidate-count sum mismatch"
    )


# ---------------------------------------------------------
# Anchor status transitions — descriptive only
# ---------------------------------------------------------

h5_status = Counter(
    row["preoutcome_status"]
    for row in h5_events
)

h3_status = Counter(
    row["preoutcome_status"]
    for row in h3_events
)

expected_h5_status = Counter({
    "CANDIDATE_SET_READY": 6906,
    "PROPAGATION_FAILURE": 1,
    "UNRECOVERED_REFINEMENT_FAILURE": 20,
})

expected_h3_status = Counter({
    "CANDIDATE_SET_READY": 6915,
    "UNRECOVERED_REFINEMENT_FAILURE": 12,
})

if h5_status != expected_h5_status:
    raise AuditError(
        "frozen H5 status census changed"
    )

if h3_status != expected_h3_status:
    raise AuditError(
        "frozen H3 status census changed"
    )

h5_to_h7 = Counter(
    (
        h5_by[idx]["preoutcome_status"],
        h7_by[idx]["preoutcome_status"],
    )
    for idx in sorted(expected_indices)
)

h3_to_h7 = Counter(
    (
        h3_by[idx]["preoutcome_status"],
        h7_by[idx]["preoutcome_status"],
    )
    for idx in sorted(expected_indices)
)


# ---------------------------------------------------------
# Independent audit result
# ---------------------------------------------------------

summary_sha = sha(
    H7_SUMMARY
)

payload = {
    "status":
        "G7D_H7_CANDIDATE_OUTPUT_INDEPENDENT_AUDIT_PASS",

    "analysis_role":
        "POST_CONFIRMATORY_PROPAGATION_HORIZON_SENSITIVITY",

    "horizon_days":
        7,

    "events":
        N_EVENTS,

    "objects":
        N_OBJECTS,

    "candidate_rows":
        N_H7_CAND,

    "event_status_counts":
        dict(
            sorted(
                h7_status.items()
            )
        ),

    "temporal_stratum_counts":
        dict(
            sorted(
                strata.items()
            )
        ),

    "candidate_time_min_s":
        minimum_time,

    "candidate_time_max_s":
        maximum_time,

    "expected_sampled_count_ready_event":
        EXPECTED_H7_SAMPLED,

    "H7_event_sha256":
        sha(H7_EVENT),

    "H7_candidate_sha256":
        sha(H7_CAND),

    "H7_summary_sha256":
        summary_sha,

    "H7_checkpoint_bytes":
        H7_DB.stat().st_size,

    "H7_checkpoint_event_rows":
        N_EVENTS,

    "H7_checkpoint_candidate_rows":
        N_H7_CAND,

    "csv_payloads_exactly_match_checkpoint":
        True,

    "schema_verified":
        True,

    "candidate_index_contiguity_verified":
        True,

    "candidate_time_ordering_verified":
        True,

    "candidate_horizon_bounds_verified":
        True,

    "candidate_bracket_bounds_verified":
        True,

    "candidate_UTC_offset_consistency_verified":
        True,

    "event_identity_fields_exactly_match_H5":
        True,

    "event_identity_fields_exactly_match_H3":
        True,

    "object_disjoint_sample_verified":
        True,

    "H5_anchor_candidate_sha256":
        EXPECTED_H5_CAND_SHA,

    "H5_anchor_candidate_rows":
        N_H5_CAND,

    "H3_candidate_sha256":
        EXPECTED_H3_CAND_SHA,

    "H3_candidate_rows":
        N_H3_CAND,

    "H7_to_H5_candidate_row_ratio":
        N_H7_CAND / N_H5_CAND,

    "H7_to_H3_candidate_row_ratio":
        N_H7_CAND / N_H3_CAND,

    "H5_to_H7_status_transition": {
        f"{a} -> {b}": n
        for (a, b), n
        in sorted(
            h5_to_h7.items()
        )
    },

    "H3_to_H7_status_transition": {
        f"{a} -> {b}": n
        for (a, b), n
        in sorted(
            h3_to_h7.items()
        )
    },

    "completed_checkpoint_events_at_start":
        int(
            summary[
                "completed_checkpoint_events_at_start"
            ]
        ),

    "frozen_H5_anchor_rerun":
        False,

    "orbital_propagation_performed_by_audit":
        False,

    "candidate_generation_performed_by_audit":
        False,

    "reference_association_performed":
        False,

    "protected_reference_accessed":
        False,

    "GP_HISTORY_accessed":
        False,

    "CAL0_refit_performed":
        False,

    "method_retuning_performed":
        False,

    "network_requests":
        0,

    "automatic_retry_performed":
        False,

    "next_safe_action":
        "RESTORE_H3_AUDIT_EVIDENCE_AND_FREEZE_G7D_HORIZON_CANDIDATE_OUTPUTS",
}

AUDIT_OUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

with AUDIT_OUT.open(
    "w",
    encoding="utf-8",
    newline="\n",
) as f:

    json.dump(
        payload,
        f,
        indent=2,
        sort_keys=True,
    )

    f.write("\n")


print(
    json.dumps(
        payload,
        indent=2,
        sort_keys=True,
    )
)