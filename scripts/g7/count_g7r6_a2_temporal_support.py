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

PARENT_HEAD = "1bbf016e601033315ea3021a3a61f280aad91c26"

HISTORY_AUDIT = Path("results/g6/G6_Final_Causal_History_Audit.csv")
WORKLOAD_EVENT = Path("results/g7/G7R6_A2_Workload_Per_Event.csv")
WORKLOAD_SUMMARY = Path("results/g7/G7R6_A2_Workload_Summary.json")
WORKLOAD_AUDIT = Path("results/g7/G7R6_A2_Workload_Audit.txt")
WORKLOAD_FREEZE = Path(
    "docs/research/DR-0055_G7_R6_A2_Workload_Count_Freeze.md"
)

OUTPUT_EVENT = Path(
    "results/g7/G7R6_A2_Temporal_Support_Per_Event.csv"
)
OUTPUT_SUMMARY = Path(
    "results/g7/G7R6_A2_Temporal_Support_Summary.json"
)
OUTPUT_AUDIT = Path(
    "results/g7/G7R6_A2_Temporal_Support_Audit.txt"
)

EXPECTED_HASHES = {
    str(HISTORY_AUDIT):
        "41802759162ef4889b071aa6f15391d48a7a96cce7ededdd99c8e1d8a3ebc924",
    str(WORKLOAD_EVENT):
        "5947e913c18aabb135c6401808189a1b343cba168d8efb76f7918d36b91bf40f",
    str(WORKLOAD_SUMMARY):
        "11ffbfeb54b8eb51f3495948c8b10d7d173f67cf08503d4fa393c3b6ec99958a",
    str(WORKLOAD_AUDIT):
        "2af941274e47432404c054392aaccfe438ddc7e0585f838fa6136e5899f35d08",
    str(WORKLOAD_FREEZE):
        "426491fd170c091db13dca1591d517fbd28a511367c9d9e826963f3b8ecd0a55",
}

EXPECTED_EVENTS = 6927
EXPECTED_OBJECTS = 13854
EXPECTED_MAIN_RAW_FILES = 1386
EXPECTED_ELIGIBLE_REPLAYS = 2020081

HORIZON = timedelta(days=5)
ASSOCIATION_TOLERANCE = timedelta(minutes=5)
TEMPORAL_SUPPORT_SPAN = HORIZON + ASSOCIATION_TOLERANCE

UTC0 = datetime(1970, 1, 1, tzinfo=timezone.utc)


class SupportCountError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SupportCountError(message)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        require(reader.fieldnames is not None, f"Missing CSV header: {path}")
        rows = [
            {(k or ""): (v if v is not None else "") for k, v in row.items()}
            for row in reader
        ]
        return list(reader.fieldnames), rows


def parse_utc(value: str) -> datetime:
    text = value.strip()
    require(bool(text), "Empty UTC timestamp.")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SupportCountError(
            f"Invalid UTC timestamp: {value!r}"
        ) from exc
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


def from_us(value: int) -> str:
    dt = UTC0 + timedelta(microseconds=value)
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalized_path(text: str) -> Path:
    p = Path(text.strip())
    if not p.is_absolute():
        p = ROOT / p
    return Path(os.path.abspath(os.path.normpath(str(p))))


def path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))


def unique_sorted(values: list[int]) -> list[int]:
    if not values:
        return []
    values.sort()
    out = [values[0]]
    for value in values[1:]:
        if value != out[-1]:
            out.append(value)
    return out


def nearest_rank(ordered: list[int], probability: float) -> int:
    require(bool(ordered), "Cannot calculate quantile from empty sequence.")
    rank = math.ceil(probability * len(ordered))
    return ordered[max(rank, 1) - 1]


def union_count_and_boundary_hits(
    first: list[int],
    second: list[int],
    boundary: int,
) -> tuple[int, int]:
    i = 0
    j = 0
    union_count = 0
    exact_boundary_hits = 0

    while i < len(first) or j < len(second):
        if i >= len(first):
            t = second[j]
        elif j >= len(second):
            t = first[i]
        else:
            t = min(first[i], second[j])

        if i < len(first) and first[i] == t:
            i += 1
        if j < len(second) and second[j] == t:
            j += 1

        union_count += 1

        if t == boundary:
            exact_boundary_hits += 1

    return union_count, exact_boundary_hits


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# 1. Verify frozen input identities
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

print("A2_TEMPORAL_SUPPORT_FROZEN_INPUT_HASHES = PASS")


# ---------------------------------------------------------------------------
# 2. Load frozen workload event table
# ---------------------------------------------------------------------------

workload_header, workload_rows = read_csv(WORKLOAD_EVENT)

required_workload_columns = {
    "final_g6_sample_index",
    "conj_id",
    "reference_tca_utc",
    "object1_NORAD_CAT_ID",
    "object2_NORAD_CAT_ID",
    "A2_exact_replay_epoch_count",
    "first_eligible_replay_time_utc",
    "last_eligible_replay_time_utc",
    "zero_replay",
}

missing = sorted(required_workload_columns - set(workload_header))
require(not missing, f"Workload event table missing columns: {missing}")
require(
    len(workload_rows) == EXPECTED_EVENTS,
    f"Expected {EXPECTED_EVENTS} workload rows, observed {len(workload_rows)}.",
)

workload: dict[str, dict[str, object]] = {}
eligible_total = 0

for row in workload_rows:
    sample = row["final_g6_sample_index"].strip()
    require(sample, "Blank final_g6_sample_index in workload table.")
    require(sample not in workload, f"Duplicate workload event: {sample}")

    replay_count = int(row["A2_exact_replay_epoch_count"])
    require(replay_count > 0, f"Non-positive frozen replay count: {sample}")
    require(
        row["zero_replay"].strip().lower() == "false",
        f"Unexpected zero_replay flag for event {sample}.",
    )

    ref_dt = parse_utc(row["reference_tca_utc"])
    first_dt = parse_utc(row["first_eligible_replay_time_utc"])
    last_dt = parse_utc(row["last_eligible_replay_time_utc"])

    require(
        ref_dt - timedelta(days=90) <= first_dt <= last_dt < ref_dt,
        f"Frozen workload chronology invalid for event {sample}.",
    )

    support_boundary_dt = ref_dt - TEMPORAL_SUPPORT_SPAN

    workload[sample] = {
        "conj_id": row["conj_id"].strip(),
        "reference_tca_utc": row["reference_tca_utc"].strip(),
        "reference_us": to_us(ref_dt),
        "support_boundary_us": to_us(support_boundary_dt),
        "first_eligible_us": to_us(first_dt),
        "last_eligible_us": to_us(last_dt),
        "effective_support_start_us": max(
            to_us(support_boundary_dt),
            to_us(first_dt),
        ),
        "obj1": row["object1_NORAD_CAT_ID"].strip(),
        "obj2": row["object2_NORAD_CAT_ID"].strip(),
        "eligible_count": replay_count,
    }

    eligible_total += replay_count

require(
    eligible_total == EXPECTED_ELIGIBLE_REPLAYS,
    (
        "Frozen eligible replay total mismatch: "
        f"expected={EXPECTED_ELIGIBLE_REPLAYS}, observed={eligible_total}"
    ),
)

print("A2_TEMPORAL_SUPPORT_WORKLOAD_BASELINE = PASS")


# ---------------------------------------------------------------------------
# 3. Load frozen history audit and verify exact event/object mapping
# ---------------------------------------------------------------------------

audit_header, audit_rows = read_csv(HISTORY_AUDIT)

required_audit_columns = {
    "final_g6_sample_index",
    "conj_id",
    "NORAD_CAT_ID",
    "object_role",
    "reference_tca_utc",
    "main_raw_path",
    "assembly_status",
}

missing = sorted(required_audit_columns - set(audit_header))
require(not missing, f"History audit missing columns: {missing}")
require(
    len(audit_rows) == EXPECTED_OBJECTS,
    f"Expected {EXPECTED_OBJECTS} audit rows, observed {len(audit_rows)}.",
)

object_event: dict[str, str] = {}
object_raw_path: dict[str, str] = {}
event_roles: dict[str, dict[str, str]] = {}

for row in audit_rows:
    require(
        row["assembly_status"].strip() == "PASS",
        "Encountered non-PASS history assembly row.",
    )

    sample = row["final_g6_sample_index"].strip()
    norad = row["NORAD_CAT_ID"].strip()
    role = row["object_role"].strip()

    require(sample in workload, f"Audit event absent from workload table: {sample}")
    require(role in {"obj1", "obj2"}, f"Unexpected object role: {role!r}")
    require(
        row["conj_id"].strip() == workload[sample]["conj_id"],
        f"conj_id mismatch for event {sample}.",
    )
    require(
        row["reference_tca_utc"].strip()
        == workload[sample]["reference_tca_utc"],
        f"reference_tca_utc mismatch for event {sample}.",
    )
    require(
        norad not in object_event,
        f"Frozen object-disjointness violation: NORAD {norad}.",
    )

    object_event[norad] = sample
    object_raw_path[norad] = path_key(
        normalized_path(row["main_raw_path"])
    )

    roles = event_roles.setdefault(sample, {})
    require(role not in roles, f"Duplicate {role} for event {sample}.")
    roles[role] = norad

require(
    len(object_event) == EXPECTED_OBJECTS,
    f"Object count mismatch: {len(object_event)}",
)
require(
    len(event_roles) == EXPECTED_EVENTS,
    f"Event count mismatch: {len(event_roles)}",
)

for sample, roles in event_roles.items():
    require(
        set(roles) == {"obj1", "obj2"},
        f"Event does not contain exactly obj1/obj2: {sample}",
    )
    require(
        roles["obj1"] == workload[sample]["obj1"],
        f"obj1 mismatch for event {sample}.",
    )
    require(
        roles["obj2"] == workload[sample]["obj2"],
        f"obj2 mismatch for event {sample}.",
    )

print("A2_TEMPORAL_SUPPORT_EVENT_MAPPING = PASS")


# ---------------------------------------------------------------------------
# 4. Main raw-file inventory
# ---------------------------------------------------------------------------

raw_files: dict[str, Path] = {}

for row in audit_rows:
    p = normalized_path(row["main_raw_path"])
    raw_files[path_key(p)] = p

require(
    len(raw_files) == EXPECTED_MAIN_RAW_FILES,
    (
        f"Expected {EXPECTED_MAIN_RAW_FILES} unique main raw files, "
        f"observed {len(raw_files)}."
    ),
)

missing_raw = [str(p) for p in raw_files.values() if not p.is_file()]
require(
    not missing_raw,
    (
        f"Missing frozen main raw files: {len(missing_raw)}; "
        f"examples={missing_raw[:10]}"
    ),
)

print("A2_TEMPORAL_SUPPORT_RAW_FILE_INVENTORY = PASS")


# ---------------------------------------------------------------------------
# 5. One support-focused raw-history pass
#
# Retain only CREATION_DATE values at or after the event-specific effective
# support start=max(t_ref-(H+tau), first eligible replay) and strictly before
# t_ref. No orbit fields are parsed or propagated.
# ---------------------------------------------------------------------------

support_times: dict[str, list[int]] = {
    norad: []
    for norad in object_event
}

raw_rows_scanned = 0
support_raw_rows = 0
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

        require(
            {"NORAD_CAT_ID", "CREATION_DATE"}.issubset(set(reader.fieldnames)),
            f"Raw file lacks NORAD_CAT_ID/CREATION_DATE: {raw_path}",
        )

        for row in reader:
            raw_rows_scanned += 1

            norad = (row.get("NORAD_CAT_ID", "") or "").strip()

            require(
                norad in object_event,
                f"Unexpected NORAD_CAT_ID in frozen raw history: {norad!r}",
            )
            require(
                object_raw_path[norad] == key,
                f"NORAD object appears in wrong frozen main file: {norad}",
            )

            creation_us = to_us(
                parse_utc((row.get("CREATION_DATE", "") or ""))
            )

            sample = object_event[norad]
            event = workload[sample]

            effective_start = int(event["effective_support_start_us"])
            reference = int(event["reference_us"])

            if effective_start <= creation_us < reference:
                support_times[norad].append(creation_us)
                support_raw_rows += 1

    if file_number % 100 == 0 or file_number == EXPECTED_MAIN_RAW_FILES:
        print(
            f"A2_TEMPORAL_SUPPORT_PROGRESS = "
            f"{file_number}/{EXPECTED_MAIN_RAW_FILES}",
            flush=True,
        )

require(
    files_read == EXPECTED_MAIN_RAW_FILES,
    f"Expected {EXPECTED_MAIN_RAW_FILES} raw files read, observed {files_read}.",
)

print("A2_TEMPORAL_SUPPORT_SINGLE_RAW_PASS = PASS")


# ---------------------------------------------------------------------------
# 6. Deduplicate exact same-object CREATION_DATE timestamps
# ---------------------------------------------------------------------------

unique_support_times: dict[str, list[int]] = {}
object_unique_support_total = 0

for norad, values in support_times.items():
    unique_values = unique_sorted(values)
    unique_support_times[norad] = unique_values
    object_unique_support_total += len(unique_values)

del support_times


# ---------------------------------------------------------------------------
# 7. Exact pair-union temporal-support count
# ---------------------------------------------------------------------------

event_output: list[dict[str, str]] = []
inclusive_counts: list[int] = []
strict_counts: list[int] = []

total_inclusive = 0
total_strict = 0
total_boundary_hits = 0
total_early_impossible = 0

zero_support_events_inclusive = 0
zero_support_events_strict = 0

for sample in sorted(workload, key=lambda x: int(x)):
    event = workload[sample]

    obj1 = str(event["obj1"])
    obj2 = str(event["obj2"])

    first = unique_support_times[obj1]
    second = unique_support_times[obj2]

    support_boundary = int(event["support_boundary_us"])

    inclusive_count, boundary_hits = union_count_and_boundary_hits(
        first,
        second,
        support_boundary,
    )

    strict_count = inclusive_count - boundary_hits
    original_eligible = int(event["eligible_count"])
    early_impossible = original_eligible - inclusive_count

    require(
        0 <= strict_count <= inclusive_count <= original_eligible,
        f"Temporal-support count bounds failed for event {sample}.",
    )
    require(
        early_impossible + inclusive_count == original_eligible,
        f"Temporal-support decomposition failed for event {sample}.",
    )

    if inclusive_count == 0:
        zero_support_events_inclusive += 1

    if strict_count == 0:
        zero_support_events_strict += 1

    total_inclusive += inclusive_count
    total_strict += strict_count
    total_boundary_hits += boundary_hits
    total_early_impossible += early_impossible

    inclusive_counts.append(inclusive_count)
    strict_counts.append(strict_count)

    event_output.append(
        {
            "final_g6_sample_index": sample,
            "conj_id": str(event["conj_id"]),
            "reference_tca_utc": str(event["reference_tca_utc"]),
            "temporal_support_boundary_utc":
                from_us(support_boundary),
            "first_eligible_replay_time_utc":
                from_us(int(event["first_eligible_us"])),
            "effective_support_start_utc":
                from_us(int(event["effective_support_start_us"])),
            "frozen_eligible_A2_replay_epoch_count":
                str(original_eligible),
            "temporal_support_epoch_count_inclusive":
                str(inclusive_count),
            "temporal_support_boundary_exact_hit_count":
                str(boundary_hits),
            "temporal_support_epoch_count_strict":
                str(strict_count),
            "provably_too_early_epoch_count":
                str(early_impossible),
            "inclusive_support_fraction_of_frozen_eligible":
                f"{inclusive_count / original_eligible:.12f}",
            "zero_temporal_support_inclusive":
                str(inclusive_count == 0).lower(),
        }
    )

require(
    len(event_output) == EXPECTED_EVENTS,
    "Temporal-support event output does not contain 6,927 events.",
)
require(
    total_early_impossible + total_inclusive == EXPECTED_ELIGIBLE_REPLAYS,
    "Global temporal-support decomposition failed.",
)
require(
    total_strict + total_boundary_hits == total_inclusive,
    "Inclusive/strict boundary accounting failed.",
)


# ---------------------------------------------------------------------------
# 8. Distribution and reduction diagnostics
# ---------------------------------------------------------------------------

ordered_inclusive = sorted(inclusive_counts)
ordered_strict = sorted(strict_counts)

minimum = min(ordered_inclusive)
median = statistics.median(ordered_inclusive)
mean = statistics.fmean(ordered_inclusive)
p90 = nearest_rank(ordered_inclusive, 0.90)
p95 = nearest_rank(ordered_inclusive, 0.95)
p99 = nearest_rank(ordered_inclusive, 0.99)
maximum = max(ordered_inclusive)

retained_fraction = total_inclusive / EXPECTED_ELIGIBLE_REPLAYS
reduction_fraction = 1.0 - retained_fraction
reduction_factor = (
    EXPECTED_ELIGIBLE_REPLAYS / total_inclusive
    if total_inclusive > 0
    else math.inf
)


# ---------------------------------------------------------------------------
# 9. Write outputs
# ---------------------------------------------------------------------------

OUTPUT_EVENT.parent.mkdir(parents=True, exist_ok=True)

fields = [
    "final_g6_sample_index",
    "conj_id",
    "reference_tca_utc",
    "temporal_support_boundary_utc",
    "first_eligible_replay_time_utc",
    "effective_support_start_utc",
    "frozen_eligible_A2_replay_epoch_count",
    "temporal_support_epoch_count_inclusive",
    "temporal_support_boundary_exact_hit_count",
    "temporal_support_epoch_count_strict",
    "provably_too_early_epoch_count",
    "inclusive_support_fraction_of_frozen_eligible",
    "zero_temporal_support_inclusive",
]

with OUTPUT_EVENT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=fields,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(event_output)

summary = {
    "artifact_id": "G7R6_A2_TEMPORAL_SUPPORT_WORKLOAD",
    "analysis_role":
        "zero-propagation necessary temporal-support workload characterization",
    "parent_head": PARENT_HEAD,
    "frozen_primary_horizon_days": 5,
    "frozen_association_tolerance_minutes": 5,
    "support_rule": (
        "A replay can only contain a candidate within the frozen association "
        "window if t_r + H >= t_ref - tau; equivalently "
        "t_r >= t_ref - (H + tau). This is necessary, not sufficient."
    ),
    "population": {
        "events": EXPECTED_EVENTS,
        "frozen_eligible_A2_replay_epochs": EXPECTED_ELIGIBLE_REPLAYS,
    },
    "scan": {
        "main_raw_files_read": files_read,
        "raw_rows_scanned": raw_rows_scanned,
        "support_window_raw_rows_retained": support_raw_rows,
        "object_unique_support_timestamps": object_unique_support_total,
    },
    "temporal_support": {
        "inclusive_support_epochs": total_inclusive,
        "strict_support_epochs": total_strict,
        "exact_boundary_hits": total_boundary_hits,
        "provably_too_early_epochs": total_early_impossible,
        "inclusive_retained_fraction": retained_fraction,
        "inclusive_reduction_fraction": reduction_fraction,
        "eligible_to_support_reduction_factor": reduction_factor,
        "zero_support_events_inclusive": zero_support_events_inclusive,
        "zero_support_events_strict": zero_support_events_strict,
    },
    "inclusive_event_support_distribution": {
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
        "sgp4_imported_or_called": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "candidate_refinement_performed": False,
        "reference_association_performed": False,
        "s05_computed": False,
        "s06_computed": False,
        "s07_computed": False,
        "cal0_refit_performed": False,
        "network_access_performed": False,
        "g6_membership_changed": False,
    },
    "interpretation": (
        "Temporal-support counts are a deterministic necessary-condition "
        "workload bound only. They are not candidate availability, association "
        "success, reconstruction accuracy, first-reconstruction lead, warning "
        "lead, collision risk, or operational performance."
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
    "G7 R6 A2 ZERO-PROPAGATION TEMPORAL-SUPPORT WORKLOAD AUDIT",
    "==========================================================",
    "",
    f"PARENT_HEAD = {PARENT_HEAD}",
    "",
    "FROZEN_INPUT_HASHES = PASS",
    "FROZEN_EVENT_MAPPING = PASS",
    f"FROZEN_EVENTS = {EXPECTED_EVENTS}",
    f"FROZEN_ELIGIBLE_A2_REPLAY_EPOCHS = {EXPECTED_ELIGIBLE_REPLAYS}",
    "",
    "PRIMARY_HORIZON_DAYS = 5",
    "ASSOCIATION_TOLERANCE_MINUTES = 5",
    "TEMPORAL_SUPPORT_RULE = t_r >= t_ref - (H + association_tolerance)",
    "TEMPORAL_SUPPORT_RULE_ROLE = NECESSARY_NOT_SUFFICIENT",
    "",
    f"MAIN_RAW_FILES_READ = {files_read}",
    f"RAW_ROWS_SCANNED = {raw_rows_scanned}",
    f"SUPPORT_WINDOW_RAW_ROWS_RETAINED = {support_raw_rows}",
    (
        "OBJECT_UNIQUE_SUPPORT_TIMESTAMPS = "
        f"{object_unique_support_total}"
    ),
    "",
    f"TEMPORAL_SUPPORT_EPOCHS_INCLUSIVE = {total_inclusive}",
    f"TEMPORAL_SUPPORT_BOUNDARY_EXACT_HITS = {total_boundary_hits}",
    f"TEMPORAL_SUPPORT_EPOCHS_STRICT = {total_strict}",
    f"PROVABLY_TOO_EARLY_EPOCHS = {total_early_impossible}",
    (
        "INCLUSIVE_SUPPORT_RETAINED_FRACTION = "
        f"{retained_fraction:.12f}"
    ),
    (
        "INCLUSIVE_SUPPORT_REDUCTION_FRACTION = "
        f"{reduction_fraction:.12f}"
    ),
    (
        "ELIGIBLE_TO_SUPPORT_REDUCTION_FACTOR = "
        f"{reduction_factor:.12f}"
    ),
    f"ZERO_SUPPORT_EVENTS_INCLUSIVE = {zero_support_events_inclusive}",
    f"ZERO_SUPPORT_EVENTS_STRICT = {zero_support_events_strict}",
    "",
    f"EVENT_SUPPORT_COUNT_MIN = {minimum}",
    f"EVENT_SUPPORT_COUNT_MEDIAN = {median}",
    f"EVENT_SUPPORT_COUNT_MEAN = {mean:.12f}",
    f"EVENT_SUPPORT_COUNT_P90_NEAREST_RANK = {p90}",
    f"EVENT_SUPPORT_COUNT_P95_NEAREST_RANK = {p95}",
    f"EVENT_SUPPORT_COUNT_P99_NEAREST_RANK = {p99}",
    f"EVENT_SUPPORT_COUNT_MAX = {maximum}",
    "",
    f"PER_EVENT_OUTPUT_SHA256 = {event_hash}",
    f"SUMMARY_OUTPUT_SHA256 = {summary_hash}",
    "",
    "SGP4_IMPORTED_OR_CALLED = false",
    "ORBITAL_PROPAGATION_PERFORMED = false",
    "CANDIDATE_GENERATION_PERFORMED = false",
    "REFERENCE_ASSOCIATION_PERFORMED = false",
    "S05_COMPUTED = false",
    "S06_COMPUTED = false",
    "S07_COMPUTED = false",
    "CAL0_REFIT_PERFORMED = false",
    "NETWORK_ACCESS_PERFORMED = false",
    "G6_MEMBERSHIP_CHANGED = false",
    "",
    "A2_TEMPORAL_SUPPORT_WORKLOAD = PASS",
]

write_text(
    OUTPUT_AUDIT,
    "\n".join(audit_lines) + "\n",
)

print("")
print("A2_TEMPORAL_SUPPORT_WORKLOAD = PASS")
print(f"MAIN_RAW_FILES_READ = {files_read}")
print(f"RAW_ROWS_SCANNED = {raw_rows_scanned}")
print(f"FROZEN_ELIGIBLE_A2_REPLAY_EPOCHS = {EXPECTED_ELIGIBLE_REPLAYS}")
print(f"TEMPORAL_SUPPORT_EPOCHS_INCLUSIVE = {total_inclusive}")
print(f"TEMPORAL_SUPPORT_BOUNDARY_EXACT_HITS = {total_boundary_hits}")
print(f"TEMPORAL_SUPPORT_EPOCHS_STRICT = {total_strict}")
print(f"PROVABLY_TOO_EARLY_EPOCHS = {total_early_impossible}")
print(f"INCLUSIVE_SUPPORT_RETAINED_FRACTION = {retained_fraction:.12f}")
print(f"INCLUSIVE_SUPPORT_REDUCTION_FRACTION = {reduction_fraction:.12f}")
print(f"ELIGIBLE_TO_SUPPORT_REDUCTION_FACTOR = {reduction_factor:.12f}")
print(f"ZERO_SUPPORT_EVENTS_INCLUSIVE = {zero_support_events_inclusive}")
print(f"EVENT_SUPPORT_COUNT_MIN = {minimum}")
print(f"EVENT_SUPPORT_COUNT_MEDIAN = {median}")
print(f"EVENT_SUPPORT_COUNT_MEAN = {mean:.12f}")
print(f"EVENT_SUPPORT_COUNT_P90 = {p90}")
print(f"EVENT_SUPPORT_COUNT_P95 = {p95}")
print(f"EVENT_SUPPORT_COUNT_P99 = {p99}")
print(f"EVENT_SUPPORT_COUNT_MAX = {maximum}")
print(f"PER_EVENT_OUTPUT_SHA256 = {sha256(OUTPUT_EVENT)}")
print(f"SUMMARY_OUTPUT_SHA256 = {sha256(OUTPUT_SUMMARY)}")
print(f"AUDIT_OUTPUT_SHA256 = {sha256(OUTPUT_AUDIT)}")
