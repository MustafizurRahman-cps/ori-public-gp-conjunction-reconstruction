from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path.cwd()

PARENT_HEAD = "2d5834ff387e54c3ee6a30deb6afca6f40ec6371"

HISTORY_AUDIT = Path("results/g6/G6_Final_Causal_History_Audit.csv")
PLAN_PATH = Path("docs/research/G7_R6_A2_Workload_Count_Plan.md")
DECISION_PATH = Path(
    "docs/research/DR-0054_G7_R6_A2_Workload_Count_Plan_Freeze.md"
)

OUTPUT_EVENT = Path("results/g7/G7R6_A2_Workload_Per_Event.csv")
OUTPUT_SUMMARY = Path("results/g7/G7R6_A2_Workload_Summary.json")
OUTPUT_AUDIT = Path("results/g7/G7R6_A2_Workload_Audit.txt")

EXPECTED_HASHES = {
    str(HISTORY_AUDIT):
        "41802759162ef4889b071aa6f15391d48a7a96cce7ededdd99c8e1d8a3ebc924",
    str(PLAN_PATH):
        "4b7928b375d1aaab1e45b1f48208de882df1a01ca9cf53a1cf3c576398622343",
    str(DECISION_PATH):
        "6ebe3a2b52680d66438e2daceb9e0609c5c1ad04a6de5d5f2159d597935009e8",
}

EXPECTED_EVENTS = 6927
EXPECTED_OBJECTS = 13854
EXPECTED_MAIN_RAW_FILES = 1386
EXPECTED_MAIN_WINDOW_ROWS = 5153040
EXPECTED_CAUSAL_HISTORY_RECORDS = 5165579

EXPECTED_PREDECESSOR_COUNTS = {
    "PREDECESSOR_CAPTURED_IN_BATCH_UNION": 7507,
    "PREDECESSOR_ACQUIRED_BY_QUERY": 5032,
    "LEFT_CENSORED_NO_PREDECESSOR_RETURNED": 1315,
}

AVAILABLE_PREDECESSOR_CLASSES = {
    "PREDECESSOR_CAPTURED_IN_BATCH_UNION",
    "PREDECESSOR_ACQUIRED_BY_QUERY",
}

UTC0 = datetime(1970, 1, 1, tzinfo=timezone.utc)


class CountError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CountError(message)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_utc(value: str) -> datetime:
    text = value.strip()
    require(bool(text), "Empty UTC timestamp.")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CountError(f"Invalid UTC timestamp: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def to_us(dt: datetime) -> int:
    delta = dt - UTC0
    return (
        delta.days * 86400 * 1_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def from_us(value: int | None) -> str:
    if value is None:
        return ""
    dt = UTC0 + timedelta(microseconds=value)
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalized_path(text: str) -> Path:
    p = Path(text.strip())
    if not p.is_absolute():
        p = ROOT / p
    return Path(os.path.abspath(os.path.normpath(str(p))))


def path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        require(reader.fieldnames is not None, f"Missing CSV header: {path}")
        rows = [
            {
                (k or ""): (v if v is not None else "")
                for k, v in row.items()
            }
            for row in reader
        ]
        return list(reader.fieldnames), rows


def unique_sorted(values: list[int]) -> list[int]:
    if not values:
        return []
    values.sort()
    out = [values[0]]
    for value in values[1:]:
        if value != out[-1]:
            out.append(value)
    return out


def nearest_rank(ordered: list[int], p: float) -> int:
    require(bool(ordered), "Cannot calculate quantile from empty sequence.")
    rank = math.ceil(p * len(ordered))
    return ordered[max(rank, 1) - 1]


def merge_clock(
    first: list[int],
    second: list[int],
    first_initialized: bool,
    second_initialized: bool,
) -> dict[str, int | None]:
    i = 0
    j = 0
    init1 = first_initialized
    init2 = second_initialized
    simultaneous = 0
    union_count = 0
    excluded = 0
    eligible = 0
    first_eligible: int | None = None
    last_eligible: int | None = None

    while i < len(first) or j < len(second):
        if i >= len(first):
            t = second[j]
        elif j >= len(second):
            t = first[i]
        else:
            t = min(first[i], second[j])

        pub1 = i < len(first) and first[i] == t
        pub2 = j < len(second) and second[j] == t

        if pub1:
            i += 1
        if pub2:
            j += 1
        if pub1 and pub2:
            simultaneous += 1

        # Frozen A2 workload-count rule:
        # apply all same-time publications first, then evaluate initialization.
        if pub1:
            init1 = True
        if pub2:
            init2 = True

        union_count += 1

        if init1 and init2:
            eligible += 1
            if first_eligible is None:
                first_eligible = t
            last_eligible = t
        else:
            excluded += 1

    require(
        eligible + excluded == union_count,
        "Pair-union accounting failure.",
    )

    return {
        "simultaneous": simultaneous,
        "union_count": union_count,
        "excluded": excluded,
        "eligible": eligible,
        "first_eligible": first_eligible,
        "last_eligible": last_eligible,
    }


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# 1. Frozen small-input identities
# ---------------------------------------------------------------------------

observed_hashes: dict[str, str] = {}

for rel, expected in EXPECTED_HASHES.items():
    p = ROOT / rel
    require(p.is_file(), f"Frozen input missing: {rel}")
    observed = sha256(p)
    require(
        observed == expected,
        f"Hash mismatch for {rel}: expected={expected}, observed={observed}",
    )
    observed_hashes[rel] = observed

print("A2_COUNT_SMALL_INPUT_HASHES = PASS")


# ---------------------------------------------------------------------------
# 2. Complete frozen event/object chronology from causal-history audit
# ---------------------------------------------------------------------------

header, rows = read_csv(HISTORY_AUDIT)

required_columns = {
    "final_g6_sample_index",
    "conj_id",
    "NORAD_CAT_ID",
    "object_role",
    "reference_tca_utc",
    "lower_bound_utc",
    "main_raw_path",
    "main_window_rows",
    "final_predecessor_class",
    "causal_history_record_count",
    "assembly_status",
}

missing = sorted(required_columns - set(header))
require(not missing, f"History audit missing columns: {missing}")
require(
    len(rows) == EXPECTED_OBJECTS,
    f"History audit row count mismatch: {len(rows)}",
)

events: dict[str, dict[str, object]] = {}
object_event: dict[str, str] = {}
object_raw_path: dict[str, str] = {}
object_predecessor: dict[str, str] = {}

predecessor_counts: Counter[str] = Counter()
sum_main_window_rows = 0
sum_causal_history_records = 0

for row in rows:
    require(
        row["assembly_status"].strip() == "PASS",
        "Encountered non-PASS history assembly row.",
    )

    sample = row["final_g6_sample_index"].strip()
    conj_id = row["conj_id"].strip()
    norad = row["NORAD_CAT_ID"].strip()
    role = row["object_role"].strip()
    ref_text = row["reference_tca_utc"].strip()
    lower_text = row["lower_bound_utc"].strip()

    require(sample, "Blank final_g6_sample_index.")
    require(conj_id, f"Blank conj_id for event {sample}.")
    require(norad, f"Blank NORAD_CAT_ID for event {sample}.")
    require(
        role in {"obj1", "obj2"},
        f"Unexpected object_role={role!r} for event {sample}.",
    )

    ref_dt = parse_utc(ref_text)
    lower_dt = parse_utc(lower_text)

    require(
        lower_dt == ref_dt - timedelta(days=180),
        f"Frozen 180-day lower bound mismatch for event {sample}.",
    )

    if sample not in events:
        events[sample] = {
            "conj_id": conj_id,
            "reference_tca_utc": ref_text,
            "reference_us": to_us(ref_dt),
            "report_start_us": to_us(ref_dt - timedelta(days=90)),
            "history_start_us": to_us(lower_dt),
            "roles": {},
        }
    else:
        event = events[sample]
        require(
            event["conj_id"] == conj_id,
            f"Pair conj_id disagreement for event {sample}.",
        )
        require(
            event["reference_tca_utc"] == ref_text,
            f"Pair reference_tca_utc disagreement for event {sample}.",
        )
        require(
            event["history_start_us"] == to_us(lower_dt),
            f"Pair lower-bound disagreement for event {sample}.",
        )

    roles = events[sample]["roles"]
    assert isinstance(roles, dict)
    require(role not in roles, f"Duplicate {role} for event {sample}.")
    roles[role] = norad

    require(
        norad not in object_event,
        f"Object-disjointness violation for NORAD {norad}.",
    )

    raw_path = normalized_path(row["main_raw_path"])
    predecessor = row["final_predecessor_class"].strip()

    require(
        predecessor in EXPECTED_PREDECESSOR_COUNTS,
        f"Unexpected predecessor class for NORAD {norad}: {predecessor}",
    )

    object_event[norad] = sample
    object_raw_path[norad] = path_key(raw_path)
    object_predecessor[norad] = predecessor
    predecessor_counts[predecessor] += 1

    sum_main_window_rows += int(row["main_window_rows"])
    sum_causal_history_records += int(row["causal_history_record_count"])

require(len(events) == EXPECTED_EVENTS, f"Event count mismatch: {len(events)}")
require(
    len(object_event) == EXPECTED_OBJECTS,
    f"Object count mismatch: {len(object_event)}",
)

for sample, event in events.items():
    roles = event["roles"]
    assert isinstance(roles, dict)
    require(
        set(roles) == {"obj1", "obj2"},
        f"Event does not contain exactly obj1 and obj2: {sample}",
    )

require(
    predecessor_counts == Counter(EXPECTED_PREDECESSOR_COUNTS),
    f"Predecessor-class counts changed: {dict(predecessor_counts)}",
)
require(
    sum_main_window_rows == EXPECTED_MAIN_WINDOW_ROWS,
    f"Audit main-window row sum mismatch: {sum_main_window_rows}",
)
require(
    sum_causal_history_records == EXPECTED_CAUSAL_HISTORY_RECORDS,
    f"Audit causal-history row sum mismatch: {sum_causal_history_records}",
)

valid_predecessor_records = sum(
    predecessor_counts[x]
    for x in AVAILABLE_PREDECESSOR_CLASSES
)

require(
    valid_predecessor_records == 12539,
    f"Valid predecessor count mismatch: {valid_predecessor_records}",
)
require(
    EXPECTED_MAIN_WINDOW_ROWS + valid_predecessor_records
    == EXPECTED_CAUSAL_HISTORY_RECORDS,
    "Frozen causal-history arithmetic does not reconcile.",
)

print("A2_COUNT_COMPLETE_EVENT_CHRONOLOGY = PASS")
print("A2_COUNT_REFERENCE_TCA_SOURCE = G6_FINAL_CAUSAL_HISTORY_AUDIT")


# ---------------------------------------------------------------------------
# 3. Unique main raw-file inventory; existence only
# ---------------------------------------------------------------------------

raw_files: dict[str, Path] = {}

for row in rows:
    p = normalized_path(row["main_raw_path"])
    raw_files[path_key(p)] = p

require(
    len(raw_files) == EXPECTED_MAIN_RAW_FILES,
    f"Main raw file count mismatch: {len(raw_files)}",
)

missing_raw = [
    str(p)
    for p in raw_files.values()
    if not p.is_file()
]

require(
    not missing_raw,
    f"Missing raw history files: {len(missing_raw)}; examples={missing_raw[:10]}",
)

print("A2_COUNT_RAW_FILE_INVENTORY = PASS")


# ---------------------------------------------------------------------------
# 4. Single primary raw-history pass
# ---------------------------------------------------------------------------

in_window_times: dict[str, list[int]] = {
    norad: []
    for norad in object_event
}

raw_in_window_rows: Counter[str] = Counter()

main_pre_window_state: dict[str, bool] = {
    norad: False
    for norad in object_event
}

raw_main_rows_scanned = 0
retained_180d_rows = 0
files_read = 0

for file_number, (key, raw_path) in enumerate(
    sorted(raw_files.items()),
    start=1,
):
    files_read += 1

    with raw_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        require(
            reader.fieldnames is not None,
            f"Missing raw CSV header: {raw_path}",
        )

        columns = set(reader.fieldnames)
        require(
            {"NORAD_CAT_ID", "CREATION_DATE"}.issubset(columns),
            f"Raw file lacks NORAD_CAT_ID/CREATION_DATE: {raw_path}",
        )

        for row in reader:
            raw_main_rows_scanned += 1

            norad = (row.get("NORAD_CAT_ID", "") or "").strip()
            require(
                norad in object_event,
                f"Unexpected NORAD_CAT_ID in frozen main history: {norad!r}",
            )
            require(
                object_raw_path[norad] == key,
                f"NORAD object appears in wrong frozen main file: {norad}",
            )

            creation_us = to_us(
                parse_utc((row.get("CREATION_DATE", "") or ""))
            )

            sample = object_event[norad]
            event = events[sample]

            history_start = int(event["history_start_us"])
            report_start = int(event["report_start_us"])
            reference = int(event["reference_us"])

            # Transport envelopes may contain rows outside the exact
            # per-object 180-day scientific interval.
            if not (history_start <= creation_us < reference):
                continue

            retained_180d_rows += 1

            if creation_us < report_start:
                main_pre_window_state[norad] = True
            else:
                in_window_times[norad].append(creation_us)
                raw_in_window_rows[norad] += 1

    if file_number % 100 == 0 or file_number == EXPECTED_MAIN_RAW_FILES:
        print(
            f"A2_COUNT_PROGRESS = {file_number}/{EXPECTED_MAIN_RAW_FILES}",
            flush=True,
        )

require(
    files_read == EXPECTED_MAIN_RAW_FILES,
    f"Expected 1386 files read, observed {files_read}.",
)
require(
    retained_180d_rows == EXPECTED_MAIN_WINDOW_ROWS,
    (
        "Exact scientific 180-day row reconciliation failed: "
        f"expected={EXPECTED_MAIN_WINDOW_ROWS}; observed={retained_180d_rows}"
    ),
)
require(
    retained_180d_rows + valid_predecessor_records
    == EXPECTED_CAUSAL_HISTORY_RECORDS,
    "Raw-pass causal-history reconciliation failed.",
)

print("A2_COUNT_SINGLE_RAW_PASS = PASS")


# ---------------------------------------------------------------------------
# 5. Object-level unique in-window publication clocks
# ---------------------------------------------------------------------------

unique_times: dict[str, list[int]] = {}
total_object_unique = 0

for norad, values in in_window_times.items():
    unique_values = unique_sorted(values)
    unique_times[norad] = unique_values
    total_object_unique += len(unique_values)

total_raw_in_window_rows = sum(raw_in_window_rows.values())
del in_window_times


# ---------------------------------------------------------------------------
# 6. Exact event-level pair-union workload
# ---------------------------------------------------------------------------

event_rows: list[dict[str, str]] = []
replay_counts: list[int] = []

total_pair_union = 0
total_simultaneous = 0
total_excluded = 0
total_eligible = 0

zero_replay_events = 0
one_unavailable_at_start = 0
both_unavailable_at_start = 0

for sample in sorted(events, key=lambda x: int(x)):
    event = events[sample]
    roles = event["roles"]
    assert isinstance(roles, dict)

    obj1 = str(roles["obj1"])
    obj2 = str(roles["obj2"])

    init1 = (
        object_predecessor[obj1] in AVAILABLE_PREDECESSOR_CLASSES
        or main_pre_window_state[obj1]
    )
    init2 = (
        object_predecessor[obj2] in AVAILABLE_PREDECESSOR_CLASSES
        or main_pre_window_state[obj2]
    )

    unavailable_count = int(not init1) + int(not init2)

    if unavailable_count == 1:
        one_unavailable_at_start += 1
    elif unavailable_count == 2:
        both_unavailable_at_start += 1

    first = unique_times[obj1]
    second = unique_times[obj2]

    merged = merge_clock(first, second, init1, init2)

    simultaneous = int(merged["simultaneous"])
    union_count = int(merged["union_count"])
    excluded = int(merged["excluded"])
    eligible = int(merged["eligible"])
    first_eligible = merged["first_eligible"]
    last_eligible = merged["last_eligible"]

    require(
        0 <= eligible <= union_count,
        f"Invalid replay count for event {sample}.",
    )
    require(
        eligible + excluded == union_count,
        f"Pair-union accounting mismatch for event {sample}.",
    )

    if eligible == 0:
        zero_replay_events += 1
        require(
            first_eligible is None and last_eligible is None,
            f"Zero-replay event unexpectedly has replay time: {sample}",
        )
    else:
        report_start = int(event["report_start_us"])
        reference = int(event["reference_us"])

        require(
            first_eligible is not None and last_eligible is not None,
            f"Positive replay event lacks replay times: {sample}",
        )
        require(
            report_start
            <= int(first_eligible)
            <= int(last_eligible)
            < reference,
            f"Eligible replay chronology outside frozen interval: {sample}",
        )

    total_pair_union += union_count
    total_simultaneous += simultaneous
    total_excluded += excluded
    total_eligible += eligible
    replay_counts.append(eligible)

    event_rows.append(
        {
            "final_g6_sample_index": sample,
            "conj_id": str(event["conj_id"]),
            "reference_tca_utc": str(event["reference_tca_utc"]),
            "object1_NORAD_CAT_ID": obj1,
            "object2_NORAD_CAT_ID": obj2,
            "object1_initialized_before_reporting_window": str(init1).lower(),
            "object2_initialized_before_reporting_window": str(init2).lower(),
            "object1_raw_in_window_publication_rows":
                str(raw_in_window_rows[obj1]),
            "object2_raw_in_window_publication_rows":
                str(raw_in_window_rows[obj2]),
            "object1_unique_in_window_CREATION_DATE_count": str(len(first)),
            "object2_unique_in_window_CREATION_DATE_count": str(len(second)),
            "simultaneous_pair_publication_timestamp_count":
                str(simultaneous),
            "raw_pair_union_unique_timestamp_count": str(union_count),
            "pair_union_timestamps_excluded_before_pair_initialization":
                str(excluded),
            "A2_exact_replay_epoch_count": str(eligible),
            "first_eligible_replay_time_utc":
                from_us(int(first_eligible) if first_eligible is not None else None),
            "last_eligible_replay_time_utc":
                from_us(int(last_eligible) if last_eligible is not None else None),
            "zero_replay": str(eligible == 0).lower(),
        }
    )

require(
    len(event_rows) == EXPECTED_EVENTS,
    "Per-event workload output does not contain 6,927 events.",
)
require(
    total_eligible + total_excluded == total_pair_union,
    "Global pair-union accounting mismatch.",
)


# ---------------------------------------------------------------------------
# 7. Workload distribution
# ---------------------------------------------------------------------------

ordered = sorted(replay_counts)

minimum = min(ordered)
median = statistics.median(ordered)
mean = statistics.fmean(ordered)
p90 = nearest_rank(ordered, 0.90)
p95 = nearest_rank(ordered, 0.95)
p99 = nearest_rank(ordered, 0.99)
maximum = max(ordered)


# ---------------------------------------------------------------------------
# 8. Write outputs
# ---------------------------------------------------------------------------

OUTPUT_EVENT.parent.mkdir(parents=True, exist_ok=True)

fields = [
    "final_g6_sample_index",
    "conj_id",
    "reference_tca_utc",
    "object1_NORAD_CAT_ID",
    "object2_NORAD_CAT_ID",
    "object1_initialized_before_reporting_window",
    "object2_initialized_before_reporting_window",
    "object1_raw_in_window_publication_rows",
    "object2_raw_in_window_publication_rows",
    "object1_unique_in_window_CREATION_DATE_count",
    "object2_unique_in_window_CREATION_DATE_count",
    "simultaneous_pair_publication_timestamp_count",
    "raw_pair_union_unique_timestamp_count",
    "pair_union_timestamps_excluded_before_pair_initialization",
    "A2_exact_replay_epoch_count",
    "first_eligible_replay_time_utc",
    "last_eligible_replay_time_utc",
    "zero_replay",
]

with OUTPUT_EVENT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=fields,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(event_rows)

summary = {
    "artifact_id": "G7R6_A2_WORKLOAD_COUNT",
    "analysis_role":
        "count-only pre-propagation A2 workload characterization",
    "parent_head": PARENT_HEAD,
    "chronology_source": str(HISTORY_AUDIT),
    "population": {
        "events": EXPECTED_EVENTS,
        "objects": EXPECTED_OBJECTS,
        "object_disjoint": True,
    },
    "raw_history_scan": {
        "main_raw_files_read": files_read,
        "raw_main_rows_scanned": raw_main_rows_scanned,
        "exact_180d_main_rows": retained_180d_rows,
        "valid_frozen_predecessor_records_not_reopened":
            valid_predecessor_records,
        "total_causal_history_records_reconciled":
            retained_180d_rows + valid_predecessor_records,
        "predecessor_evidence_files_opened": 0,
    },
    "reporting_window_clock": {
        "raw_in_window_publication_rows": total_raw_in_window_rows,
        "object_level_unique_in_window_publication_timestamps":
            total_object_unique,
        "pair_union_unique_in_window_timestamps": total_pair_union,
        "simultaneous_pair_publication_timestamps": total_simultaneous,
        "pair_union_timestamps_excluded_before_pair_initialization":
            total_excluded,
        "exact_eligible_A2_replay_epochs": total_eligible,
    },
    "event_initialization": {
        "events_with_exactly_one_object_unavailable_at_window_start":
            one_unavailable_at_start,
        "events_with_both_objects_unavailable_at_window_start":
            both_unavailable_at_start,
        "zero_replay_events": zero_replay_events,
    },
    "event_replay_count_distribution": {
        "minimum": minimum,
        "median": median,
        "mean": mean,
        "p90_nearest_rank": p90,
        "p95_nearest_rank": p95,
        "p99_nearest_rank": p99,
        "maximum": maximum,
        "quantile_method": "empirical nearest-rank",
    },
    "frozen_input_sha256": observed_hashes,
    "constraints": {
        "confirmatory_metrics_used_for_chronology": False,
        "predecessor_evidence_content_opened": False,
        "sgp4_imported_or_called": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "candidate_refinement_performed": False,
        "reference_association_performed": False,
        "tca_computed": False,
        "miss_distance_computed": False,
        "s05_computed": False,
        "s06_computed": False,
        "s07_computed": False,
        "cal0_refit_performed": False,
        "network_access_performed": False,
        "g6_membership_changed": False,
    },
    "interpretation": (
        "Execution-planning workload only. Replay counts are not accuracy, "
        "reconstruction success, warning lead time, operational latency, "
        "collision risk, or conjunction-discovery performance."
    ),
}

write_text(
    OUTPUT_SUMMARY,
    json.dumps(
        summary,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ) + "\n",
)

event_hash = sha256(OUTPUT_EVENT)
summary_hash = sha256(OUTPUT_SUMMARY)

audit_lines = [
    "G7 R6 A2 COUNT-ONLY WORKLOAD AUDIT",
    "===================================",
    "",
    f"PARENT_HEAD = {PARENT_HEAD}",
    "",
    "CORRECTED_CHRONOLOGY_SOURCE = G6_Final_Causal_History_Audit.csv",
    "CONFIRMATORY_METRICS_USED_FOR_CHRONOLOGY = false",
    "",
    "FROZEN_INPUT_HASHES = PASS",
    "FROZEN_EVENT_MEMBERSHIP = PASS",
    "FROZEN_OBJECT_DISJOINTNESS = PASS",
    "EXACTLY_TWO_OBJECT_ROLES_PER_EVENT = PASS",
    "REFERENCE_TCA_COMPLETE_FOR_ALL_EVENTS = PASS",
    "PREDECESSOR_ACCOUNTING = PASS",
    "",
    f"FROZEN_EVENTS = {EXPECTED_EVENTS}",
    f"FROZEN_OBJECTS = {EXPECTED_OBJECTS}",
    f"MAIN_RAW_FILES_READ = {files_read}",
    f"RAW_MAIN_ROWS_SCANNED = {raw_main_rows_scanned}",
    f"EXACT_180D_MAIN_ROWS = {retained_180d_rows}",
    (
        "VALID_FROZEN_PREDECESSOR_RECORDS_NOT_REOPENED = "
        f"{valid_predecessor_records}"
    ),
    (
        "TOTAL_CAUSAL_HISTORY_RECORDS_RECONCILED = "
        f"{retained_180d_rows + valid_predecessor_records}"
    ),
    "",
    f"RAW_IN_WINDOW_PUBLICATION_ROWS = {total_raw_in_window_rows}",
    (
        "OBJECT_UNIQUE_IN_WINDOW_PUBLICATION_TIMESTAMPS = "
        f"{total_object_unique}"
    ),
    f"PAIR_UNION_UNIQUE_IN_WINDOW_TIMESTAMPS = {total_pair_union}",
    (
        "SIMULTANEOUS_PAIR_PUBLICATION_TIMESTAMPS = "
        f"{total_simultaneous}"
    ),
    (
        "PAIR_UNION_TIMESTAMPS_EXCLUDED_BEFORE_PAIR_INITIALIZATION = "
        f"{total_excluded}"
    ),
    f"EXACT_ELIGIBLE_A2_REPLAY_EPOCHS = {total_eligible}",
    "",
    (
        "EVENTS_WITH_ONE_OBJECT_UNAVAILABLE_AT_WINDOW_START = "
        f"{one_unavailable_at_start}"
    ),
    (
        "EVENTS_WITH_BOTH_OBJECTS_UNAVAILABLE_AT_WINDOW_START = "
        f"{both_unavailable_at_start}"
    ),
    f"ZERO_REPLAY_EVENTS = {zero_replay_events}",
    "",
    f"EVENT_REPLAY_COUNT_MIN = {minimum}",
    f"EVENT_REPLAY_COUNT_MEDIAN = {median}",
    f"EVENT_REPLAY_COUNT_MEAN = {mean:.12f}",
    f"EVENT_REPLAY_COUNT_P90_NEAREST_RANK = {p90}",
    f"EVENT_REPLAY_COUNT_P95_NEAREST_RANK = {p95}",
    f"EVENT_REPLAY_COUNT_P99_NEAREST_RANK = {p99}",
    f"EVENT_REPLAY_COUNT_MAX = {maximum}",
    "",
    f"PER_EVENT_OUTPUT_SHA256 = {event_hash}",
    f"SUMMARY_OUTPUT_SHA256 = {summary_hash}",
    "",
    "RAW_MAIN_FILES_PRIMARY_PASSES = 1",
    "PREDECESSOR_EVIDENCE_CONTENT_OPENED = false",
    "SGP4_IMPORTED_OR_CALLED = false",
    "ORBITAL_PROPAGATION_PERFORMED = false",
    "CANDIDATE_GENERATION_PERFORMED = false",
    "REFERENCE_ASSOCIATION_PERFORMED = false",
    "TCA_COMPUTED = false",
    "MISS_DISTANCE_COMPUTED = false",
    "S05_COMPUTED = false",
    "S06_COMPUTED = false",
    "S07_COMPUTED = false",
    "CAL0_REFIT_PERFORMED = false",
    "NETWORK_ACCESS_PERFORMED = false",
    "G6_MEMBERSHIP_CHANGED = false",
    "",
    "A2_WORKLOAD_COUNT = PASS",
]

write_text(
    OUTPUT_AUDIT,
    "\n".join(audit_lines) + "\n",
)

print("")
print("A2_WORKLOAD_COUNT = PASS")
print(f"MAIN_RAW_FILES_READ = {files_read}")
print(f"RAW_MAIN_ROWS_SCANNED = {raw_main_rows_scanned}")
print(f"EXACT_180D_MAIN_ROWS = {retained_180d_rows}")
print(
    "TOTAL_CAUSAL_HISTORY_RECORDS_RECONCILED = "
    f"{retained_180d_rows + valid_predecessor_records}"
)
print(f"RAW_IN_WINDOW_PUBLICATION_ROWS = {total_raw_in_window_rows}")
print(
    "OBJECT_UNIQUE_IN_WINDOW_TIMESTAMPS = "
    f"{total_object_unique}"
)
print(
    "PAIR_UNION_UNIQUE_IN_WINDOW_TIMESTAMPS = "
    f"{total_pair_union}"
)
print(
    "PAIR_UNION_TIMESTAMPS_EXCLUDED_BEFORE_PAIR_INITIALIZATION = "
    f"{total_excluded}"
)
print(f"EXACT_ELIGIBLE_A2_REPLAY_EPOCHS = {total_eligible}")
print(f"ZERO_REPLAY_EVENTS = {zero_replay_events}")
print(f"EVENT_REPLAY_COUNT_MIN = {minimum}")
print(f"EVENT_REPLAY_COUNT_MEDIAN = {median}")
print(f"EVENT_REPLAY_COUNT_MEAN = {mean:.12f}")
print(f"EVENT_REPLAY_COUNT_P90 = {p90}")
print(f"EVENT_REPLAY_COUNT_P95 = {p95}")
print(f"EVENT_REPLAY_COUNT_P99 = {p99}")
print(f"EVENT_REPLAY_COUNT_MAX = {maximum}")
print(f"PER_EVENT_OUTPUT_SHA256 = {sha256(OUTPUT_EVENT)}")
print(f"SUMMARY_OUTPUT_SHA256 = {sha256(OUTPUT_SUMMARY)}")
print(f"AUDIT_OUTPUT_SHA256 = {sha256(OUTPUT_AUDIT)}")
