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

ROOT = Path(".").resolve()

H3_EVENT = ROOT / "results/g7/G7D_H3_Event_Status.csv"
H3_CAND = ROOT / "results/g7/G7D_H3_Candidates.csv"
H3_SUMMARY = ROOT / "results/g7/G7D_H3_Candidate_Summary.json"
H3_DB = ROOT / "data/raw/g7/G7D_H3_Candidate_Work.sqlite3"

H5_EVENT = ROOT / "results/g6/G6_PreOutcome_Candidate_Event_Status.csv"
H5_CAND = ROOT / "results/g6/G6_PreOutcome_Candidates.csv"

AUDIT_OUT = (
    ROOT
    / "results/g7/G7D_H3_Candidate_Output_Independent_Audit.json"
)

EXPECTED_HEAD = "cb4340ea959678dd74acf4ce5b95582107c23657"
EXPECTED_TAG = "g7d-horizon-execution-authorized"

EXPECTED_EVENT_SHA = (
    "5689d7a8b279a6d6a7f7ccd7941d977f2ce9ff04928a29201b82c385c3e94b43"
)
EXPECTED_CAND_SHA = (
    "a64c500a73a2b66df44642066337d1cdd039404cc72147ada65f0862dad77aae"
)
EXPECTED_SUMMARY_SHA = (
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
H3_END = 259200.0


class AuditError(RuntimeError):
    pass


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def git(*args):
    p = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if p.returncode:
        raise AuditError(p.stderr.strip())
    return p.stdout.strip()


def utc(text):
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def csv_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


if git("rev-parse", "HEAD") != EXPECTED_HEAD:
    raise AuditError("unexpected HEAD")

if (
    git("rev-parse", f"refs/tags/{EXPECTED_TAG}^{{commit}}")
    != EXPECTED_HEAD
):
    raise AuditError("authorization-tag target mismatch")

for path in (
    H3_EVENT,
    H3_CAND,
    H3_SUMMARY,
    H3_DB,
    H5_EVENT,
    H5_CAND,
):
    if not path.is_file():
        raise AuditError(f"missing required file: {path}")

if sha(H3_EVENT) != EXPECTED_EVENT_SHA:
    raise AuditError("H3 event hash mismatch")

if sha(H3_CAND) != EXPECTED_CAND_SHA:
    raise AuditError("H3 candidate hash mismatch")

if sha(H3_SUMMARY) != EXPECTED_SUMMARY_SHA:
    raise AuditError("H3 summary hash mismatch")

if sha(H5_CAND) != EXPECTED_H5_CAND_SHA:
    raise AuditError("frozen H5 candidate hash mismatch")

summary = json.loads(H3_SUMMARY.read_text(encoding="utf-8"))

if summary["status"] != "G7D_H3_CANDIDATE_GENERATION_PASS":
    raise AuditError("H3 summary not PASS")

if int(summary["horizon_days"]) != 3:
    raise AuditError("wrong horizon")

if int(summary["events"]) != N_EVENTS:
    raise AuditError("wrong event count")

if int(summary["objects"]) != N_OBJECTS:
    raise AuditError("wrong object count")

if int(summary["candidate_rows"]) != N_H3_CAND:
    raise AuditError("wrong candidate count")

if int(summary["completed_checkpoint_events_at_start"]) != 0:
    raise AuditError("H3 was not a first-pass execution")

for key in (
    "reference_association_performed",
    "protected_reference_accessed",
    "GP_HISTORY_accessed",
    "CAL0_refit_performed",
    "method_retuning_performed",
    "automatic_retry_performed",
):
    if summary[key] is not False:
        raise AuditError(f"forbidden summary flag: {key}")

if int(summary["network_requests"]) != 0:
    raise AuditError("network requests reported")

if summary["executor_sha256"] != EXPECTED_EXECUTOR_SHA:
    raise AuditError("executor hash mismatch")

expected_deps = {
    "data/raw/g6/G6_48H_Replay_Selected_GP_Cache.jsonl":
        EXPECTED_CACHE_SHA,
    "results/g6/G6_48H_Replay_Input_Selection.csv":
        EXPECTED_SELECTION_SHA,
    "scripts/g6/generate_g6_preoutcome_candidates.py":
        EXPECTED_G6_SHA,
    "scripts/g7/g7d_horizon_numerics.py":
        EXPECTED_KERNEL_SHA,
}

if summary["dependency_hashes"] != expected_deps:
    raise AuditError("dependency hash map mismatch")


def read_events(path):
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        r = csv.DictReader(f)

        if r.fieldnames != EVENT_FIELDS:
            raise AuditError(f"event schema mismatch: {path.name}")

        rows = list(r)

    by_index = {}

    for row in rows:
        idx = int(row["final_g6_sample_index"])

        if idx in by_index:
            raise AuditError(f"duplicate event index: {idx}")

        by_index[idx] = row

    return rows, by_index


h3_events, h3_by = read_events(H3_EVENT)
h5_events, h5_by = read_events(H5_EVENT)

if len(h3_events) != N_EVENTS:
    raise AuditError("H3 event CSV census mismatch")

if len(h5_events) != N_EVENTS:
    raise AuditError("H5 event CSV census mismatch")

if set(h3_by) != set(h5_by):
    raise AuditError("H3/H5 event universe mismatch")

for idx in sorted(h3_by):
    for field in IDENTITY_FIELDS:
        if h3_by[idx][field] != h5_by[idx][field]:
            raise AuditError(
                f"H3/H5 event identity mismatch: {idx} {field}"
            )

h3_status = Counter(
    row["preoutcome_status"]
    for row in h3_events
)

if h3_status != Counter({
    "CANDIDATE_SET_READY": 6915,
    "UNRECOVERED_REFINEMENT_FAILURE": 12,
}):
    raise AuditError(f"unexpected H3 status census: {h3_status}")

h5_status = Counter(
    row["preoutcome_status"]
    for row in h5_events
)

if h5_status != Counter({
    "CANDIDATE_SET_READY": 6906,
    "UNRECOVERED_REFINEMENT_FAILURE": 20,
    "PROPAGATION_FAILURE": 1,
}):
    raise AuditError(f"unexpected H5 status census: {h5_status}")

strata = Counter(
    row["g6_temporal_stratum"]
    for row in h3_events
)

expected_strata = Counter({
    "G6-T1": 1386,
    "G6-T2": 1386,
    "G6-T3": 1385,
    "G6-T4": 1385,
    "G6-T5": 1385,
})

if strata != expected_strata:
    raise AuditError("H3 stratum census mismatch")

objects = []

for row in h3_events:
    objects.extend(
        (row["obj1"], row["obj2"])
    )

if (
    len(objects) != N_OBJECTS
    or len(set(objects)) != N_OBJECTS
):
    raise AuditError("object-disjoint sample invariant failed")

expected_metadata = {
    "CAL0_refit": "PROHIBITED",
    "executor_sha256": EXPECTED_EXECUTOR_SHA,
    "expected_events": "6927",
    "expected_objects": "13854",
    "g6_implementation_sha256": EXPECTED_G6_SHA,
    "g7d_horizon_kernel_sha256": EXPECTED_KERNEL_SHA,
    "horizon_days": "3",
    "method_retuning": "PROHIBITED",
    "reference_association":
        "PROHIBITED_IN_CANDIDATE_GENERATION",
    "selected_TLE_cache_sha256": EXPECTED_CACHE_SHA,
    "selection_sha256": EXPECTED_SELECTION_SHA,
}

candidate_counts = defaultdict(int)
previous_time = {}
min_time = math.inf
max_time = -math.inf

with sqlite3.connect(str(H3_DB)) as con:

    metadata = dict(
        con.execute(
            "SELECT key, value FROM metadata ORDER BY key"
        )
    )

    if metadata != expected_metadata:
        raise AuditError("checkpoint metadata mismatch")

    db_event_count = con.execute(
        "SELECT COUNT(*) FROM event_results"
    ).fetchone()[0]

    db_candidate_count = con.execute(
        "SELECT COUNT(*) FROM candidate_results"
    ).fetchone()[0]

    if db_event_count != N_EVENTS:
        raise AuditError("DB event count mismatch")

    if db_candidate_count != N_H3_CAND:
        raise AuditError("DB candidate count mismatch")

    db_events = con.execute(
        "SELECT final_g6_sample_index, payload_json "
        "FROM event_results "
        "ORDER BY final_g6_sample_index"
    )

    for csv_row, db_row in itertools.zip_longest(
        h3_events,
        db_events,
    ):
        if csv_row is None or db_row is None:
            raise AuditError("event CSV/DB length mismatch")

        idx, payload_text = db_row
        payload = json.loads(payload_text)

        if int(csv_row["final_g6_sample_index"]) != int(idx):
            raise AuditError("event CSV/DB index mismatch")

        for field in EVENT_FIELDS:
            if csv_row[field] != csv_value(payload.get(field)):
                raise AuditError(
                    f"event CSV/DB mismatch: {idx} {field}"
                )

    db_candidates = con.execute(
        "SELECT final_g6_sample_index, "
        "candidate_index, payload_json "
        "FROM candidate_results "
        "ORDER BY final_g6_sample_index, candidate_index"
    )

    with H3_CAND.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        reader = csv.DictReader(f)

        if reader.fieldnames != CAND_FIELDS:
            raise AuditError("H3 candidate schema mismatch")

        candidate_rows = 0

        for crow, drow in itertools.zip_longest(
            reader,
            db_candidates,
        ):
            if crow is None or drow is None:
                raise AuditError("candidate CSV/DB length mismatch")

            db_event, db_idx, payload_text = drow
            payload = json.loads(payload_text)

            event = int(crow["final_g6_sample_index"])
            cidx = int(crow["candidate_index"])

            if event != int(db_event) or cidx != int(db_idx):
                raise AuditError("candidate CSV/DB key mismatch")

            for field in CAND_FIELDS:
                if crow[field] != csv_value(payload.get(field)):
                    raise AuditError(
                        f"candidate CSV/DB mismatch: "
                        f"{event} {cidx} {field}"
                    )

            if cidx != candidate_counts[event] + 1:
                raise AuditError(
                    f"candidate index discontinuity: {event}"
                )

            erow = h3_by[event]

            if (
                crow["g6_temporal_stratum"]
                != erow["g6_temporal_stratum"]
            ):
                raise AuditError("candidate stratum mismatch")

            if crow["conj_id"] != erow["conj_id"]:
                raise AuditError("candidate conj_id mismatch")

            t = float(crow["time_from_replay_s"])
            d = float(crow["distance_km"])
            lo = float(crow["bracket_lower_s"])
            hi = float(crow["bracket_upper_s"])

            if not (
                math.isfinite(t)
                and 0.0 < t < H3_END
            ):
                raise AuditError(
                    f"candidate outside H3 horizon: {event} {t}"
                )

            if not (
                math.isfinite(d)
                and d >= 0.0
            ):
                raise AuditError("invalid candidate distance")

            if not (
                0.0 <= lo <= t <= hi <= H3_END
            ):
                raise AuditError("invalid candidate bracket")

            if crow["converged"].lower() != "true":
                raise AuditError("non-converged candidate serialized")

            if event in previous_time:
                if t <= previous_time[event]:
                    raise AuditError(
                        f"candidate ordering failure: {event}"
                    )

            previous_time[event] = t

            replay_dt = utc(erow["replay_time_utc"])
            candidate_dt = utc(crow["candidate_time_utc"])

            serialized_offset = (
                candidate_dt - replay_dt
            ).total_seconds()

            if abs(serialized_offset - t) > 5e-6:
                raise AuditError(
                    f"candidate UTC offset mismatch: {event}"
                )

            candidate_counts[event] += 1
            candidate_rows += 1
            min_time = min(min_time, t)
            max_time = max(max_time, t)

if candidate_rows != N_H3_CAND:
    raise AuditError("H3 candidate row census mismatch")

candidate_sum = 0

for row in h3_events:

    idx = int(row["final_g6_sample_index"])
    declared = int(row["candidate_count"])
    actual = candidate_counts.get(idx, 0)

    if declared != actual:
        raise AuditError(
            f"event candidate_count mismatch: {idx}"
        )

    candidate_sum += declared

    status = row["preoutcome_status"]

    if status == "CANDIDATE_SET_READY":

        if row["parse_status"] != "SUCCESS":
            raise AuditError("ready event parse failure")

        recovered = int(
            row["recovered_local_inconsistency_count"]
        )

        expected_propagation_status = (
            "SUCCESS_WITH_REDUNDANT_BRACKET_RECOVERY"
            if recovered > 0
            else "SUCCESS"
        )

        if (
            row["propagation_status"]
            != expected_propagation_status
        ):
            raise AuditError(
                "ready event propagation-status mismatch"
            )

        if declared <= 0:
            raise AuditError("ready event has no candidates")

    elif status == "UNRECOVERED_REFINEMENT_FAILURE":

        if (
            row["propagation_status"]
            != "FAILED_NUMERICAL_REFINEMENT"
        ):
            raise AuditError(
                "refinement failure status mismatch"
            )

        if declared != 0:
            raise AuditError(
                "refinement-failure event has candidates"
            )

if candidate_sum != N_H3_CAND:
    raise AuditError("candidate-count sum mismatch")

h5_candidate_counts = defaultdict(int)
h5_candidate_rows = 0

with H5_CAND.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as f:

    reader = csv.DictReader(f)

    if reader.fieldnames != CAND_FIELDS:
        raise AuditError("H5 candidate schema mismatch")

    for row in reader:

        idx = int(row["final_g6_sample_index"])
        cidx = int(row["candidate_index"])

        if cidx != h5_candidate_counts[idx] + 1:
            raise AuditError(
                f"H5 candidate index discontinuity: {idx}"
            )

        h5_candidate_counts[idx] += 1
        h5_candidate_rows += 1

if h5_candidate_rows != N_H5_CAND:
    raise AuditError("H5 candidate census mismatch")

for idx, row in h5_by.items():

    if (
        int(row["candidate_count"])
        != h5_candidate_counts.get(idx, 0)
    ):
        raise AuditError(
            f"H5 event/candidate count mismatch: {idx}"
        )

transition = Counter(
    (
        h3_by[idx]["preoutcome_status"],
        h5_by[idx]["preoutcome_status"],
    )
    for idx in sorted(h3_by)
)

for path in (
    ROOT / "results/g7/G7D_H7_Event_Status.csv",
    ROOT / "results/g7/G7D_H7_Candidates.csv",
    ROOT / "results/g7/G7D_H7_Candidate_Summary.json",
    ROOT / "data/raw/g7/G7D_H7_Candidate_Work.sqlite3",
):
    if path.exists():
        raise AuditError(
            f"H7 artifact exists unexpectedly: {path}"
        )

payload = {
    "status":
        "G7D_H3_CANDIDATE_OUTPUT_INDEPENDENT_AUDIT_PASS",

    "horizon_days": 3,
    "events": N_EVENTS,
    "objects": N_OBJECTS,
    "candidate_rows": N_H3_CAND,

    "event_status_counts":
        dict(sorted(h3_status.items())),

    "temporal_stratum_counts":
        dict(sorted(strata.items())),

    "candidate_time_min_s": min_time,
    "candidate_time_max_s": max_time,

    "H3_event_sha256": sha(H3_EVENT),
    "H3_candidate_sha256": sha(H3_CAND),
    "H3_summary_sha256": sha(H3_SUMMARY),

    "H3_checkpoint_bytes":
        H3_DB.stat().st_size,

    "H3_checkpoint_event_rows":
        N_EVENTS,

    "H3_checkpoint_candidate_rows":
        N_H3_CAND,

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

    "object_disjoint_sample_verified":
        True,

    "H5_anchor_candidate_sha256":
        EXPECTED_H5_CAND_SHA,

    "H5_anchor_candidate_rows":
        N_H5_CAND,

    "H5_event_status_counts":
        dict(sorted(h5_status.items())),

    "H3_to_H5_status_transition": {
        f"{a} -> {b}": n
        for (a, b), n in sorted(transition.items())
    },

    "H3_to_H5_candidate_row_ratio":
        N_H3_CAND / N_H5_CAND,

    "event_identity_fields_exactly_match_H5":
        True,

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

    "H7_execution_performed":
        False,

    "next_safe_action":
        "FREEZE_H3_CANDIDATE_OUTPUTS_BEFORE_H7",
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