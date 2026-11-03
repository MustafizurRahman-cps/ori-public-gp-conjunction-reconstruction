#!/usr/bin/env python3
"""Independent audit of frozen G6 pre-outcome candidate outputs.

This audit performs no orbit propagation, no candidate reconstruction,
no reference association, and no protected-outcome access.

It verifies the generated CSV/JSON artifacts against the frozen replay-input
selection and against the local SQLite execution checkpoint.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
import subprocess
from collections import Counter, defaultdict, deque
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_HEAD = (
    "8577b52f60d4f20b1e3a8be889716f47d5b77ccb"
)

EXPECTED_TAG = (
    "g6-preoutcome-candidate-generation-authorized"
)

EXPECTED_EVENTS = 6927
EXPECTED_OBJECTS = 13854
EXPECTED_CANDIDATES = 929821

EXPECTED_STATUS_COUNTS = {
    "CANDIDATE_SET_READY": 6906,
    "PROPAGATION_FAILURE": 1,
    "UNRECOVERED_REFINEMENT_FAILURE": 20,
}

EXPECTED_STRATUM_COUNTS = {
    "G6-T1": 1386,
    "G6-T2": 1386,
    "G6-T3": 1385,
    "G6-T4": 1385,
    "G6-T5": 1385,
}

HORIZON_S = 432000.0
DEDUP_TIME_S = 0.01
DEDUP_DISTANCE_KM = 1.0e-6

SELECTION = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Selection.csv"
)

EVENT_OUT = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Event_Status.csv"
)

CANDIDATE_OUT = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidates.csv"
)

SUMMARY = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Summary.json"
)

EXECUTION = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Generation_Execution_Audit.json"
)

WORK_DB = (
    ROOT
    / "data/raw/g6/G6_PreOutcome_Candidate_Work.sqlite3"
)

OUT = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Output_Independent_Audit.json"
)


EXPECTED_HASHES = {
    EVENT_OUT:
        "844a4c3f90a1b5bb738f13911b5e9f99f22bfe72e01bdb43dd6242e292b7b308",

    CANDIDATE_OUT:
        "21b8a9f5c8aab4b3427fad5cd3f3c2823ffc0219bd2a36aeccafc6a0aaa80e36",

    SUMMARY:
        "a9a070163d744472e641f9f75e09ebadc42c03151c8c64a3e64f0429f51a8add",

    EXECUTION:
        "b8acbd1dd6f1e562ec77eb94de18d4b5158ab2a9e23b1bc87dbbb82f35568c55",

    WORK_DB:
        "4716f16ea109e15e21794c1acd8b4f25463d3cd4bda005bd1d502bfce646e55c",
}


EVENT_FIELDS = (
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
)


CANDIDATE_FIELDS = (
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
)


class AuditError(RuntimeError):
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


def parse_utc(value: str) -> datetime:

    text = str(value).strip()

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    parsed = datetime.fromisoformat(
        text.replace(" ", "T")
    )

    if parsed.tzinfo is None:
        raise AuditError(
            "naive timestamp encountered"
        )

    return parsed.astimezone(UTC)


def as_int(
    row,
    field,
) -> int:

    try:
        return int(
            row[field]
        )
    except Exception as exc:
        raise AuditError(
            f"invalid integer {field}={row.get(field)!r}"
        ) from exc


def as_float(
    row,
    field,
) -> float:

    try:
        value = float(
            row[field]
        )
    except Exception as exc:
        raise AuditError(
            f"invalid float {field}={row.get(field)!r}"
        ) from exc

    if not math.isfinite(
        value
    ):
        raise AuditError(
            f"nonfinite {field}"
        )

    return value


def csv_value(value) -> str:

    if isinstance(
        value,
        bool,
    ):
        return (
            "True"
            if value
            else "False"
        )

    return str(
        value
    )


# ------------------------------------------------------------
# Repository / immutable output boundary
# ------------------------------------------------------------

if (
    git_value(
        "rev-parse",
        "HEAD",
    )
    != EXPECTED_HEAD
):
    raise AuditError(
        "repository HEAD changed"
    )

if (
    git_value(
        "rev-parse",
        f"refs/tags/{EXPECTED_TAG}^{{commit}}",
    )
    != EXPECTED_HEAD
):
    raise AuditError(
        "authorization tag target changed"
    )

if git_value(
    "status",
    "--porcelain",
    "--untracked-files=no",
):
    raise AuditError(
        "tracked working tree is not clean"
    )

for path, expected in (
    EXPECTED_HASHES.items()
):

    if not path.is_file():
        raise AuditError(
            f"required artifact missing: {path}"
        )

    actual = sha256(
        path
    )

    if actual != expected:
        raise AuditError(
            f"SHA mismatch: {path}"
        )


# ------------------------------------------------------------
# Summary / execution audit
# ------------------------------------------------------------

summary = json.loads(
    SUMMARY.read_text(
        encoding="utf-8"
    )
)

execution = json.loads(
    EXECUTION.read_text(
        encoding="utf-8"
    )
)

if (
    summary.get("status")
    != "G6_PREOUTCOME_CANDIDATE_GENERATION_PASS"
):
    raise AuditError(
        "candidate summary is not PASS"
    )

if (
    execution.get("status")
    != "G6_PREOUTCOME_CANDIDATE_GENERATION_EXECUTION_PASS"
):
    raise AuditError(
        "execution audit is not PASS"
    )

if summary.get(
    "event_status_counts"
) != EXPECTED_STATUS_COUNTS:

    raise AuditError(
        "summary status census changed"
    )

if execution.get(
    "event_status_counts"
) != EXPECTED_STATUS_COUNTS:

    raise AuditError(
        "execution status census changed"
    )

if (
    summary.get("events")
    != EXPECTED_EVENTS
    or execution.get("events")
    != EXPECTED_EVENTS
):
    raise AuditError(
        "event count changed"
    )

if (
    summary.get("candidate_rows")
    != EXPECTED_CANDIDATES
    or execution.get("candidate_rows")
    != EXPECTED_CANDIDATES
):
    raise AuditError(
        "candidate-row count changed"
    )

if (
    summary.get(
        "temporal_stratum_event_counts"
    )
    != EXPECTED_STRATUM_COUNTS
):
    raise AuditError(
        "temporal-stratum census changed"
    )

if (
    summary.get("GP_PARSE_FAILURE")
    != 0
    or summary.get("NO_VALID_CANDIDATE")
    != 0
    or summary.get("PROPAGATION_FAILURE")
    != 1
    or summary.get("UNRECOVERED_REFINEMENT_FAILURE")
    != 20
):
    raise AuditError(
        "explicit failure census changed"
    )

for name, payload in (
    ("summary", summary),
    ("execution", execution),
):

    if payload.get(
        "reference_association_performed"
    ) is not False:
        raise AuditError(
            f"{name}: reference association occurred"
        )

    if payload.get(
        "TraCSS_reference_rows_opened"
    ) != 0:
        raise AuditError(
            f"{name}: TraCSS reference rows opened"
        )

    if payload.get(
        "protected_outcome_access"
    ) is not False:
        raise AuditError(
            f"{name}: protected outcome access"
        )

    if payload.get(
        "G6_error_metrics_computed"
    ) is not False:
        raise AuditError(
            f"{name}: G6 error metrics computed"
        )

    if payload.get(
        "network_requests"
    ) != 0:
        raise AuditError(
            f"{name}: network access"
        )

if execution.get(
    "full_GP_HISTORY_reread"
) is not False:
    raise AuditError(
        "full GP_HISTORY reread reported"
    )


# ------------------------------------------------------------
# Frozen replay-input mapping
# ------------------------------------------------------------

selection_events = defaultdict(
    dict
)

with SELECTION.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(
        stream
    )

    for row in reader:

        event = row[
            "final_g6_sample_index"
        ].strip()

        role = row[
            "object_role"
        ].strip()

        if role not in {
            "obj1",
            "obj2",
        }:
            raise AuditError(
                "unexpected selection object role"
            )

        if role in selection_events[
            event
        ]:
            raise AuditError(
                "duplicate selection role"
            )

        selection_events[
            event
        ][
            role
        ] = row


if len(
    selection_events
) != EXPECTED_EVENTS:

    raise AuditError(
        "selection event count changed"
    )

if any(
    set(rows) != {
        "obj1",
        "obj2",
    }
    for rows in selection_events.values()
):
    raise AuditError(
        "selection pairing changed"
    )


# ------------------------------------------------------------
# Event output
# ------------------------------------------------------------

event_by_index = {}

status_counts = Counter()
stratum_counts = Counter()

candidate_count_expected = {}
failure_records = []

total_rejected = 0
total_recovered = 0
total_unresolved_boundary = 0
events_with_recovery = 0

with EVENT_OUT.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(
        stream
    )

    if tuple(
        reader.fieldnames or ()
    ) != EVENT_FIELDS:

        raise AuditError(
            "event-output schema changed"
        )

    for expected_index, row in enumerate(
        reader,
        start=1,
    ):

        event_index = as_int(
            row,
            "final_g6_sample_index",
        )

        if event_index != expected_index:
            raise AuditError(
                "event output is not canonical contiguous order"
            )

        key = str(
            event_index
        )

        selected = selection_events.get(
            key
        )

        if selected is None:
            raise AuditError(
                "event missing from frozen selection"
            )

        first = selected[
            "obj1"
        ]

        second = selected[
            "obj2"
        ]

        if (
            row["conj_id"].strip()
            != first["conj_id"].strip()
            or row["conj_id"].strip()
            != second["conj_id"].strip()
        ):
            raise AuditError(
                "event conjunction-ID mismatch"
            )

        if (
            row[
                "g6_temporal_stratum"
            ].strip()
            != first[
                "g6_temporal_stratum"
            ].strip()
        ):
            raise AuditError(
                "event temporal-stratum mismatch"
            )

        if (
            row["obj1"].strip()
            != first["NORAD_CAT_ID"].strip()
            or row["obj2"].strip()
            != second["NORAD_CAT_ID"].strip()
        ):
            raise AuditError(
                "event object-ID mismatch"
            )

        if (
            row["obj1_GP_ID"].strip()
            != first["replay_GP_ID"].strip()
            or row["obj2_GP_ID"].strip()
            != second["replay_GP_ID"].strip()
        ):
            raise AuditError(
                "event selected-GP mismatch"
            )

        replay = parse_utc(
            row[
                "replay_time_utc"
            ]
        )

        if (
            replay
            != parse_utc(
                first[
                    "replay_time_utc"
                ]
            )
            or replay
            != parse_utc(
                second[
                    "replay_time_utc"
                ]
            )
        ):
            raise AuditError(
                "event replay-time mismatch"
            )

        status = row[
            "preoutcome_status"
        ].strip()

        if status not in {
            "CANDIDATE_SET_READY",
            "GP_PARSE_FAILURE",
            "PROPAGATION_FAILURE",
            "UNRECOVERED_REFINEMENT_FAILURE",
            "NO_VALID_CANDIDATE",
        }:
            raise AuditError(
                "unexpected pre-outcome status"
            )

        candidate_count = as_int(
            row,
            "candidate_count",
        )

        raw_candidate_count = as_int(
            row,
            "raw_candidate_count",
        )

        rejected = as_int(
            row,
            "rejected_local_inconsistency_count",
        )

        recovered = as_int(
            row,
            "recovered_local_inconsistency_count",
        )

        unresolved = as_int(
            row,
            "unresolved_boundary_count",
        )

        sampled = as_int(
            row,
            "sampled_count",
        )

        if min(
            candidate_count,
            raw_candidate_count,
            rejected,
            recovered,
            unresolved,
            sampled,
        ) < 0:
            raise AuditError(
                "negative event accounting value"
            )

        failure_detail = row[
            "failure_detail"
        ].strip()

        parse_status = row[
            "parse_status"
        ].strip()

        propagation_status = row[
            "propagation_status"
        ].strip()

        if status == "CANDIDATE_SET_READY":

            if parse_status != "SUCCESS":
                raise AuditError(
                    "ready event did not parse successfully"
                )

            if propagation_status not in {
                "SUCCESS",
                "SUCCESS_WITH_REDUNDANT_BRACKET_RECOVERY",
            }:
                raise AuditError(
                    "ready event has invalid propagation status"
                )

            if candidate_count <= 0:
                raise AuditError(
                    "ready event has no candidates"
                )

            if raw_candidate_count < candidate_count:
                raise AuditError(
                    "raw candidate count below final count"
                )

            if sampled != 43201:
                raise AuditError(
                    "ready event frozen coarse-grid sample count changed"
                )

            if rejected != recovered:
                raise AuditError(
                    "successful event has unrecovered rejected bracket"
                )

            if (
                recovered > 0
                and propagation_status
                != "SUCCESS_WITH_REDUNDANT_BRACKET_RECOVERY"
            ):
                raise AuditError(
                    "recovered event status label mismatch"
                )

            if (
                recovered == 0
                and propagation_status
                != "SUCCESS"
            ):
                raise AuditError(
                    "plain-success event status label mismatch"
                )

            if failure_detail:
                raise AuditError(
                    "ready event contains failure detail"
                )

        elif status == "PROPAGATION_FAILURE":

            if (
                parse_status != "SUCCESS"
                or propagation_status != "FAILED"
                or candidate_count != 0
            ):
                raise AuditError(
                    "PROPAGATION_FAILURE semantics mismatch"
                )

            if not failure_detail:
                raise AuditError(
                    "PROPAGATION_FAILURE lacks failure detail"
                )

        elif status == "UNRECOVERED_REFINEMENT_FAILURE":

            if (
                parse_status != "SUCCESS"
                or propagation_status
                != "FAILED_NUMERICAL_REFINEMENT"
                or candidate_count != 0
            ):
                raise AuditError(
                    "UNRECOVERED_REFINEMENT_FAILURE semantics mismatch"
                )

            if not failure_detail:
                raise AuditError(
                    "refinement failure lacks detail"
                )

        elif status == "GP_PARSE_FAILURE":

            if (
                parse_status != "FAILED"
                or candidate_count != 0
            ):
                raise AuditError(
                    "GP_PARSE_FAILURE semantics mismatch"
                )

            if not failure_detail:
                raise AuditError(
                    "GP_PARSE_FAILURE lacks detail"
                )

        elif status == "NO_VALID_CANDIDATE":

            if (
                parse_status != "SUCCESS"
                or propagation_status not in {
                    "SUCCESS",
                    "SUCCESS_WITH_REDUNDANT_BRACKET_RECOVERY",
                }
                or candidate_count != 0
            ):
                raise AuditError(
                    "NO_VALID_CANDIDATE semantics mismatch"
                )

        if status != "CANDIDATE_SET_READY":

            failure_records.append(
                {
                    "final_g6_sample_index":
                        event_index,

                    "g6_temporal_stratum":
                        row[
                            "g6_temporal_stratum"
                        ].strip(),

                    "conj_id":
                        row[
                            "conj_id"
                        ].strip(),

                    "preoutcome_status":
                        status,

                    "parse_status":
                        parse_status,

                    "propagation_status":
                        propagation_status,

                    "failure_detail":
                        failure_detail,
                }
            )

        if recovered > 0:
            events_with_recovery += 1

        total_rejected += rejected
        total_recovered += recovered
        total_unresolved_boundary += unresolved

        status_counts[
            status
        ] += 1

        stratum_counts[
            row[
                "g6_temporal_stratum"
            ].strip()
        ] += 1

        candidate_count_expected[
            event_index
        ] = candidate_count

        event_by_index[
            event_index
        ] = row


if len(
    event_by_index
) != EXPECTED_EVENTS:

    raise AuditError(
        "event-output row count changed"
    )

if dict(
    status_counts
) != EXPECTED_STATUS_COUNTS:

    raise AuditError(
        "event CSV status census mismatch"
    )

if dict(
    stratum_counts
) != EXPECTED_STRATUM_COUNTS:

    raise AuditError(
        "event CSV stratum census mismatch"
    )

if len(
    failure_records
) != 21:

    raise AuditError(
        "expected exactly 21 non-ready events"
    )


# ------------------------------------------------------------
# Candidate output — one streaming pass
# ------------------------------------------------------------

candidate_rows_seen = 0
candidate_rows_by_event = Counter()

previous_key_by_event = {}
recent_by_event = defaultdict(
    deque
)

candidate_count_min = None
candidate_count_max = None

with CANDIDATE_OUT.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(
        stream
    )

    if tuple(
        reader.fieldnames or ()
    ) != CANDIDATE_FIELDS:

        raise AuditError(
            "candidate-output schema changed"
        )

    for row in reader:

        candidate_rows_seen += 1

        event_index = as_int(
            row,
            "final_g6_sample_index",
        )

        event = event_by_index.get(
            event_index
        )

        if event is None:
            raise AuditError(
                "candidate references unknown event"
            )

        if (
            event[
                "preoutcome_status"
            ].strip()
            != "CANDIDATE_SET_READY"
        ):
            raise AuditError(
                "candidate emitted for non-ready event"
            )

        expected_candidate_index = (
            candidate_rows_by_event[
                event_index
            ]
            + 1
        )

        candidate_index = as_int(
            row,
            "candidate_index",
        )

        if (
            candidate_index
            != expected_candidate_index
        ):
            raise AuditError(
                "candidate indices are not contiguous"
            )

        if (
            row["conj_id"].strip()
            != event["conj_id"].strip()
            or row[
                "g6_temporal_stratum"
            ].strip()
            != event[
                "g6_temporal_stratum"
            ].strip()
        ):
            raise AuditError(
                "candidate/event metadata mismatch"
            )

        time_s = as_float(
            row,
            "time_from_replay_s",
        )

        distance = as_float(
            row,
            "distance_km",
        )

        lower = as_float(
            row,
            "bracket_lower_s",
        )

        upper = as_float(
            row,
            "bracket_upper_s",
        )

        bracket_index = as_int(
            row,
            "bracket_index",
        )

        iterations = as_int(
            row,
            "iterations",
        )

        evaluations = as_int(
            row,
            "function_evaluations",
        )

        if not (
            0.0
            < time_s
            < HORIZON_S
        ):
            raise AuditError(
                "candidate outside frozen interior horizon"
            )

        if distance < 0.0:
            raise AuditError(
                "negative candidate distance"
            )

        if not (
            0.0
            <= lower
            < upper
            <= HORIZON_S
        ):
            raise AuditError(
                "invalid candidate bracket"
            )

        if not (
            lower - 1.0e-9
            <= time_s
            <= upper + 1.0e-9
        ):
            raise AuditError(
                "candidate time outside source bracket"
            )

        if (
            bracket_index < 0
            or iterations < 0
            or evaluations <= 0
        ):
            raise AuditError(
                "invalid optimization accounting"
            )

        if row[
            "converged"
        ].strip() != "True":
            raise AuditError(
                "non-converged candidate exported"
            )

        if not row[
            "termination"
        ].strip():
            raise AuditError(
                "candidate lacks optimizer termination record"
            )

        replay = parse_utc(
            event[
                "replay_time_utc"
            ]
        )

        exported_time = parse_utc(
            row[
                "candidate_time_utc"
            ]
        )

        expected_time = (
            replay
            + timedelta(
                seconds=time_s
            )
        )

        if abs(
            (
                exported_time
                - expected_time
            ).total_seconds()
        ) > 2.0e-6:
            raise AuditError(
                "candidate absolute UTC/time-offset mismatch"
            )

        ordering_key = (
            time_s,
            distance,
            bracket_index,
        )

        previous = (
            previous_key_by_event.get(
                event_index
            )
        )

        if (
            previous is not None
            and ordering_key < previous
        ):
            raise AuditError(
                "candidate ordering is not deterministic"
            )

        previous_key_by_event[
            event_index
        ] = ordering_key

        # Verify frozen two-dimensional deduplication over all
        # candidates within the time tolerance.
        recent = recent_by_event[
            event_index
        ]

        while (
            recent
            and time_s
            - recent[0][0]
            > DEDUP_TIME_S
        ):
            recent.popleft()

        for other_time, other_distance in recent:

            if (
                abs(
                    time_s
                    - other_time
                )
                <= DEDUP_TIME_S
                and abs(
                    distance
                    - other_distance
                )
                <= DEDUP_DISTANCE_KM
            ):
                raise AuditError(
                    "duplicate candidate survived frozen deduplication"
                )

        recent.append(
            (
                time_s,
                distance,
            )
        )

        candidate_rows_by_event[
            event_index
        ] += 1


if (
    candidate_rows_seen
    != EXPECTED_CANDIDATES
):
    raise AuditError(
        "candidate CSV row count changed"
    )

for event_index, expected_count in (
    candidate_count_expected.items()
):

    observed = candidate_rows_by_event[
        event_index
    ]

    if observed != expected_count:
        raise AuditError(
            "event candidate-count/export mismatch"
        )

ready_counts = [
    count
    for event_index, count
    in candidate_count_expected.items()
    if event_by_index[
        event_index
    ][
        "preoutcome_status"
    ].strip()
    == "CANDIDATE_SET_READY"
]

candidate_count_min = min(
    ready_counts
)

candidate_count_max = max(
    ready_counts
)


# ------------------------------------------------------------
# SQLite checkpoint cross-check
# ------------------------------------------------------------

db_uri = (
    "file:"
    + WORK_DB.as_posix()
    + "?mode=ro"
)

connection = sqlite3.connect(
    db_uri,
    uri=True,
)

try:

    integrity = connection.execute(
        "PRAGMA integrity_check"
    ).fetchall()

    if integrity != [
        ("ok",)
    ]:
        raise AuditError(
            "SQLite integrity_check failed"
        )

    metadata = dict(
        connection.execute(
            "SELECT key, value "
            "FROM metadata"
        ).fetchall()
    )

    expected_metadata = {
        "implementation_sha256":
            "bce2a4b5e4dab8d0c4e461e2acb4dbac8afe22d3ac412922ba3670a832adcecb",

        "selection_sha256":
            "6d03f0b4c7104b7cbac76b649bedc87e9f497f297dccc19f2270e7fec5552095",

        "selected_TLE_cache_sha256":
            "b77cf2893cf92b2e577bf04f5f23d67dc6be31b9d1e8aeafe0e40efd6b08ed69",

        "g4_numerics_sha256":
            "dc8de410b89e05ffd829563b4996b06703c31236e9a10fb56bd7f4e690048406",

        "g5_controlflow_sha256":
            "553578f4e90b044c6a783209a756ea3accc009ecbb7a09853e8cae46503f33c2",

        "g6_lock_sha256":
            "21fc2d4eb83b8d00d7bbb632986fcdc33ec437dce9ca3e69020b222d382adb2e",

        "expected_events":
            "6927",
    }

    if metadata != expected_metadata:
        raise AuditError(
            "SQLite frozen metadata changed"
        )

    db_event_count = connection.execute(
        "SELECT COUNT(*) "
        "FROM event_results"
    ).fetchone()[0]

    db_candidate_count = connection.execute(
        "SELECT COUNT(*) "
        "FROM candidate_results"
    ).fetchone()[0]

    if (
        db_event_count
        != EXPECTED_EVENTS
        or db_candidate_count
        != EXPECTED_CANDIDATES
    ):
        raise AuditError(
            "SQLite record counts changed"
        )

    # Event DB payload -> exported CSV.
    db_events = connection.execute(
        "SELECT final_g6_sample_index, payload_json "
        "FROM event_results "
        "ORDER BY final_g6_sample_index"
    )

    with EVENT_OUT.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        csv_events = csv.DictReader(
            stream
        )

        for (
            db_index,
            payload_json,
        ), csv_row in zip(
            db_events,
            csv_events,
            strict=True,
        ):

            payload = json.loads(
                payload_json
            )

            if set(
                payload
            ) != set(
                EVENT_FIELDS
            ):
                raise AuditError(
                    "SQLite event payload schema changed"
                )

            if int(
                csv_row[
                    "final_g6_sample_index"
                ]
            ) != int(
                db_index
            ):
                raise AuditError(
                    "SQLite/CSV event ordering mismatch"
                )

            for field in EVENT_FIELDS:

                if (
                    csv_row[
                        field
                    ]
                    != csv_value(
                        payload[
                            field
                        ]
                    )
                ):
                    raise AuditError(
                        "SQLite/CSV event payload mismatch: "
                        + field
                    )

    # Candidate DB payload -> exported CSV.
    db_candidates = connection.execute(
        "SELECT "
        "final_g6_sample_index, "
        "candidate_index, "
        "payload_json "
        "FROM candidate_results "
        "ORDER BY "
        "final_g6_sample_index, "
        "candidate_index"
    )

    with CANDIDATE_OUT.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        csv_candidates = csv.DictReader(
            stream
        )

        compared = 0

        for (
            db_event,
            db_candidate,
            payload_json,
        ), csv_row in zip(
            db_candidates,
            csv_candidates,
            strict=True,
        ):

            compared += 1

            payload = json.loads(
                payload_json
            )

            if set(
                payload
            ) != set(
                CANDIDATE_FIELDS
            ):
                raise AuditError(
                    "SQLite candidate payload schema changed"
                )

            if (
                int(
                    csv_row[
                        "final_g6_sample_index"
                    ]
                )
                != int(
                    db_event
                )
                or int(
                    csv_row[
                        "candidate_index"
                    ]
                )
                != int(
                    db_candidate
                )
            ):
                raise AuditError(
                    "SQLite/CSV candidate ordering mismatch"
                )

            for field in CANDIDATE_FIELDS:

                if (
                    csv_row[
                        field
                    ]
                    != csv_value(
                        payload[
                            field
                        ]
                    )
                ):
                    raise AuditError(
                        "SQLite/CSV candidate payload mismatch: "
                        + field
                    )

        if compared != EXPECTED_CANDIDATES:
            raise AuditError(
                "SQLite/CSV candidate comparison count changed"
            )

finally:

    connection.close()


# ------------------------------------------------------------
# Work-DB governance
# ------------------------------------------------------------

relative_db = WORK_DB.relative_to(
    ROOT
).as_posix()

ignore = git(
    "check-ignore",
    "-v",
    relative_db,
    check=False,
)

if ignore.returncode != 0:
    raise AuditError(
        "work DB is not Git-ignored"
    )

if git_value(
    "ls-files",
    "--",
    relative_db,
):
    raise AuditError(
        "work DB unexpectedly tracked"
    )


# ------------------------------------------------------------
# Protected-output schema guard
# ------------------------------------------------------------

for path, fields in (
    (EVENT_OUT, EVENT_FIELDS),
    (CANDIDATE_OUT, CANDIDATE_FIELDS),
):

    forbidden = {
        "reference_tca_utc",
        "min_range",
        "mdistance",
        "E_d",
        "E_t",
        "association_status",
        "VALID_ASSOCIATED",
        "UNASSOCIATED_REFERENCE_EVENT",
    }

    if forbidden.intersection(
        fields
    ):
        raise AuditError(
            f"protected/final-association field entered {path}"
        )


# ------------------------------------------------------------
# Failure census
# ------------------------------------------------------------

failure_reason_counts = Counter(
    (
        record[
            "preoutcome_status"
        ],
        record[
            "failure_detail"
        ],
    )
    for record in failure_records
)


result = {
    "status":
        "G6_PREOUTCOME_CANDIDATE_OUTPUT_INDEPENDENT_AUDIT_PASS",

    "audit_scope":
        "OUTPUT_AND_SQLITE_CROSSCHECK_NO_REPROPAGATION_NO_REFERENCE_ASSOCIATION",

    "events_verified":
        EXPECTED_EVENTS,

    "objects_in_frozen_input":
        EXPECTED_OBJECTS,

    "candidate_rows_verified":
        candidate_rows_seen,

    "event_status_counts":
        dict(
            sorted(
                status_counts.items()
            )
        ),

    "temporal_stratum_event_counts":
        dict(
            sorted(
                stratum_counts.items()
            )
        ),

    "nonready_event_count":
        len(
            failure_records
        ),

    "failure_records":
        failure_records,

    "failure_reason_counts": [
        {
            "preoutcome_status":
                status,

            "failure_detail":
                detail,

            "count":
                count,
        }

        for (
            status,
            detail,
        ), count in sorted(
            failure_reason_counts.items()
        )
    ],

    "candidate_count_min_ready_event":
        candidate_count_min,

    "candidate_count_max_ready_event":
        candidate_count_max,

    "events_with_redundant_bracket_recovery":
        events_with_recovery,

    "total_rejected_local_inconsistency_brackets":
        total_rejected,

    "total_recovered_local_inconsistency_brackets":
        total_recovered,

    "total_unresolved_boundary_count":
        total_unresolved_boundary,

    "candidate_horizon_seconds_verified":
        HORIZON_S,

    "candidate_ordering_verified":
        True,

    "candidate_deduplication_invariant_verified":
        True,

    "candidate_absolute_time_arithmetic_verified":
        True,

    "candidate_event_count_accounting_verified":
        True,

    "sqlite_integrity_check":
        "ok",

    "sqlite_frozen_metadata_verified":
        True,

    "sqlite_event_rows_verified":
        EXPECTED_EVENTS,

    "sqlite_candidate_rows_verified":
        EXPECTED_CANDIDATES,

    "sqlite_to_csv_event_payload_equivalence":
        True,

    "sqlite_to_csv_candidate_payload_equivalence":
        True,

    "event_output_sha256":
        EXPECTED_HASHES[
            EVENT_OUT
        ],

    "candidate_output_sha256":
        EXPECTED_HASHES[
            CANDIDATE_OUT
        ],

    "summary_sha256":
        EXPECTED_HASHES[
            SUMMARY
        ],

    "execution_audit_sha256":
        EXPECTED_HASHES[
            EXECUTION
        ],

    "local_work_db_sha256":
        EXPECTED_HASHES[
            WORK_DB
        ],

    "local_work_db_gitignored":
        True,

    "local_work_db_tracked":
        False,

    "audit_orbital_propagation_performed":
        False,

    "audit_candidate_generation_performed":
        False,

    "reference_association_performed":
        False,

    "TraCSS_reference_rows_opened":
        0,

    "protected_outcome_access":
        False,

    "G6_error_metrics_computed":
        False,

    "full_GP_HISTORY_reread":
        False,

    "network_requests":
        0,

    "method_retuning_performed":
        False,

    "next_safe_action":
        "FREEZE_PREOUTCOME_CANDIDATE_OUTPUTS_BEFORE_REFERENCE_ASSOCIATION",
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

            "audit_script_sha256":
                sha256(
                    Path(__file__)
                ),

            "audit_artifact_sha256":
                sha256(
                    OUT
                ),
        },
        indent=2,
        sort_keys=True,
    )
)
