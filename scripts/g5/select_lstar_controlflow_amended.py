"""Select the frozen G5 L-star using evaluability only.

This amended thin wrapper reuses the validated G4 common-TEME SGP4 numerical
primitives while applying the frozen pre-outcome bracket-level control-flow
amendment for independently redundant LOCAL_NEIGHBORHOOD_INCONSISTENT brackets.
It intentionally emits no protected outcome magnitudes and writes to distinct
paths so the original v2.1 NONE/HOLD-REVISE artifacts remain preserved.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
from sgp4.api import Satrec, WGS72


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "g4"))
import numerics as g4  # noqa: E402


ALLOCATION = ROOT / "docs/research/G5_V2_Calibration_Allocation_Register.csv"
AMENDMENT = ROOT / "docs/research/G5_PreOutcome_Calibration_Lock_v2_1_Amendment.md"
FINAL_AUDIT = ROOT / "results/g5/G5_Final_Causal_History_Audit.csv"
FINAL_SUMMARY = ROOT / "results/g5/G5_Final_Causal_History_Summary.json"
G4_NUMERICS = ROOT / "scripts/g4/numerics.py"
G4_SUMMARY = ROOT / "results/g4/G4_Run_Summary.json"
NUMERICAL_AMENDMENT = ROOT / "docs/research/G5_LStar_Numerical_ControlFlow_Amendment.md"
RAW_DIR = ROOT / "data/raw/g5"

ORIGINAL_OUTPUT_CSV = ROOT / "results/g5/G5_LStar_Evaluability.csv"
ORIGINAL_OUTPUT_JSON = ROOT / "results/g5/G5_LStar_Selection.json"
ORIGINAL_OUTPUT_MD = ROOT / "docs/research/G5_LStar_Selection.md"

OUTPUT_CSV = ROOT / "results/g5/G5_LStar_Evaluability_ControlFlowAmended.csv"
OUTPUT_JSON = ROOT / "results/g5/G5_LStar_Selection_ControlFlowAmended.json"
OUTPUT_MD = ROOT / "docs/research/G5_LStar_Selection_ControlFlowAmended.md"

EXPECTED_ALLOCATION_SHA256 = "78ea7546d0cf679fc00fcb9414c9ba7c1e032b8ec9ab981b9bc9fdb06d975514"
EXPECTED_AMENDMENT_SHA256 = "d80c91400c6f9193c8b62b3cf7868d4465ba118b88de99a8fd7373535c0e7e2c"
EXPECTED_FINAL_AUDIT_SHA256 = "541243677b3cb996dd791745c3c93c418a3ef87c916eb084fbd347aad99037a6"
EXPECTED_FINAL_SUMMARY_SHA256 = "bf3a535548979f20095746c97e4f4194954b45a5898ae8b1da04bcb2d96a127c"
EXPECTED_G4_NUMERICS_SHA256 = "dc8de410b89e05ffd829563b4996b06703c31236e9a10fb56bd7f4e690048406"
EXPECTED_G4_SUMMARY_SHA256 = "d0a0a4f159a9a7eeb9763263f7a66c14b1c76bf07a2149027d17c5916ce22756"
EXPECTED_NUMERICAL_AMENDMENT_SHA256 = "3e1c80ad906816bdc704a76c7c4ebdf2b5003c82f994399412bd71eb7889c50a"
EXPECTED_ORIGINAL_CSV_SHA256 = "28c1c203aa493550b6cd52b6abb5164ba4a6c0758d3a0b1814d35eca59b0b78a"
EXPECTED_ORIGINAL_JSON_SHA256 = "c82906da054ee018327068eab622a81505dddddea21a6f9a4f7ea7965fcabb47"
EXPECTED_ORIGINAL_MD_SHA256 = "704a8b2a15a556517bc5abb7e481a85ea2287219134c0c1b52994f35a6ec256d"
LEADS_HOURS = (72, 48, 24, 12, 6)
EXPECTED_BLOCK_COUNTS = {"1": 36, "2": 36, "3": 35, "4": 35, "5": 35}
OOF_BLOCKS = ("2", "3", "4", "5")
EXPECTED_OOF = 141

COARSE_STEP_S = 10.0
REFINEMENT_TOLERANCE_S = 0.01
DEDUP_TIME_TOLERANCE_S = 0.01
DEDUP_DISTANCE_TOLERANCE_KM = 1.0e-6
ASSOCIATION_TOLERANCE_MIN = 5.0
RECOVERY_GRID_STEP_S = 0.01
RECOVERY_WINDOW_EXPANSION_S = COARSE_STEP_S / 2.0
RECOVERY_TCA_TOLERANCE_S = 0.5
RECOVERY_DISTANCE_TOLERANCE_KM = 1.0e-6

CSV_FIELDS = (
    "conj_id",
    "block",
    "lead_hours",
    "reference_time_utc",
    "object1_NORAD_CAT_ID",
    "object2_NORAD_CAT_ID",
    "object1_current_GP_ID",
    "object2_current_GP_ID",
    "object1_CREATION_DATE",
    "object2_CREATION_DATE",
    "availability_status",
    "parse_status",
    "propagation_status",
    "rejected_redundant_brackets",
    "recovered_redundant_brackets",
    "candidate_exists",
    "association_status",
    "evaluable",
    "non_evaluable_reason",
)


class IntegrityError(RuntimeError):
    pass


class UnrecoveredRefinementFailure(g4.VerificationError):
    """A rejected local-refinement bracket lacks independently verified recovery."""


@dataclass(frozen=True)
class RedundancySafeEnumerationResult:
    raw_candidates: tuple[g4.Candidate, ...]
    candidates: tuple[g4.Candidate, ...]
    unresolved_boundary_count: int
    sampled_count: int
    rejected_local_inconsistency_count: int
    recovered_local_inconsistency_count: int


@dataclass(frozen=True)
class Event:
    conj_id: str
    block: str
    object1: str
    object2: str
    reference_tca_utc: datetime


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_id(value: str) -> str:
    text = value.strip()
    if not text or not text.isdigit():
        raise IntegrityError(f"invalid NORAD_CAT_ID: {value!r}")
    return str(int(text))


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def verify_frozen_inputs() -> tuple[list[Event], dict[str, str], dict[str, dict[str, str]]]:
    input_hashes = {
        "allocation_register_sha256": sha256(ALLOCATION),
        "g5_v2_1_amendment_sha256": sha256(AMENDMENT),
        "final_causal_history_audit_sha256": sha256(FINAL_AUDIT),
        "final_causal_history_summary_sha256": sha256(FINAL_SUMMARY),
        "g4_numerics_sha256": sha256(G4_NUMERICS),
        "g4_run_summary_sha256": sha256(G4_SUMMARY),
        "numerical_control_flow_amendment_sha256": sha256(NUMERICAL_AMENDMENT),
        "original_lstar_evaluability_sha256": sha256(ORIGINAL_OUTPUT_CSV),
        "original_lstar_selection_sha256": sha256(ORIGINAL_OUTPUT_JSON),
        "original_lstar_markdown_sha256": sha256(ORIGINAL_OUTPUT_MD),
    }
    expected_hashes = {
        "allocation_register_sha256": EXPECTED_ALLOCATION_SHA256,
        "g5_v2_1_amendment_sha256": EXPECTED_AMENDMENT_SHA256,
        "final_causal_history_audit_sha256": EXPECTED_FINAL_AUDIT_SHA256,
        "final_causal_history_summary_sha256": EXPECTED_FINAL_SUMMARY_SHA256,
        "g4_numerics_sha256": EXPECTED_G4_NUMERICS_SHA256,
        "g4_run_summary_sha256": EXPECTED_G4_SUMMARY_SHA256,
        "numerical_control_flow_amendment_sha256": EXPECTED_NUMERICAL_AMENDMENT_SHA256,
        "original_lstar_evaluability_sha256": EXPECTED_ORIGINAL_CSV_SHA256,
        "original_lstar_selection_sha256": EXPECTED_ORIGINAL_JSON_SHA256,
        "original_lstar_markdown_sha256": EXPECTED_ORIGINAL_MD_SHA256,
    }
    for key, expected in expected_hashes.items():
        if input_hashes[key] != expected:
            raise IntegrityError(f"frozen input SHA-256 mismatch: {key}")

    with ORIGINAL_OUTPUT_JSON.open("r", encoding="utf-8") as handle:
        original_lstar = json.load(handle)
    if original_lstar.get("selected_L_star_hours") != "NONE":
        raise IntegrityError("preserved original L-star result is not NONE")
    if original_lstar.get("outcome_information_accessed") is not False:
        raise IntegrityError("preserved original L-star result does not certify outcome closure")

    with FINAL_SUMMARY.open("r", encoding="utf-8") as handle:
        causal_summary = json.load(handle)
    required_summary = {
        "total_objects": 354,
        "batch_union_predecessor_count": 245,
        "queried_predecessor_success_count": 71,
        "left_censored_count": 38,
        "pending_count": 0,
        "main_window_nonempty_count": 354,
        "causal_bound_violations": 0,
        "state_raw_sha_mismatches": 0,
        "integrity_status": "PASS",
        "network_requests": 0,
    }
    for key, expected in required_summary.items():
        if causal_summary.get(key) != expected:
            raise IntegrityError(f"final causal-history invariant failed: {key}")

    allocation_rows = read_csv(ALLOCATION)
    if len(allocation_rows) != 177:
        raise IntegrityError("allocation must contain exactly 177 events")
    events: list[Event] = []
    object_ids: list[str] = []
    block_counts: Counter[str] = Counter()
    block_times: dict[str, list[datetime]] = {str(i): [] for i in range(1, 6)}
    for expected_index, row in enumerate(allocation_rows, start=1):
        if int(row["calibration_event_index"]) != expected_index:
            raise IntegrityError("calibration event indices are not deterministic and contiguous")
        block = row["temporal_block"].strip()
        if block not in EXPECTED_BLOCK_COUNTS:
            raise IntegrityError(f"unexpected temporal block: {block}")
        object1 = normalized_id(row["obj1"])
        object2 = normalized_id(row["obj2"])
        tca = g4.parse_utc(row["reference_tca_utc"])
        events.append(Event(row["conj_id"].strip(), block, object1, object2, tca))
        object_ids.extend((object1, object2))
        block_counts[block] += 1
        block_times[block].append(tca)

    if dict(block_counts) != EXPECTED_BLOCK_COUNTS:
        raise IntegrityError(f"block counts mismatch: {dict(block_counts)}")
    if len(object_ids) != 354 or len(set(object_ids)) != 354:
        raise IntegrityError("allocation is not globally object-disjoint")
    if sum(block_counts[b] for b in OOF_BLOCKS) != EXPECTED_OOF:
        raise IntegrityError("OOF denominator is not 141")
    for earlier, later in zip(("1", "2", "3", "4"), ("2", "3", "4", "5")):
        if max(block_times[earlier]) > min(block_times[later]):
            raise IntegrityError("temporal blocks are not chronological")

    audit_rows = read_csv(FINAL_AUDIT)
    if len(audit_rows) != 354:
        raise IntegrityError("final causal-history audit must contain 354 objects")
    audit_by_object: dict[str, dict[str, str]] = {}
    event_tca_by_object = {
        object_id: event.reference_tca_utc
        for event in events
        for object_id in (event.object1, event.object2)
    }
    for row in audit_rows:
        object_id = normalized_id(row["NORAD_CAT_ID"])
        if object_id in audit_by_object:
            raise IntegrityError(f"duplicate causal-history audit object: {object_id}")
        if object_id not in event_tca_by_object:
            raise IntegrityError(f"unexpected causal-history audit object: {object_id}")
        if g4.parse_utc(row["reference_tca_utc"]) != event_tca_by_object[object_id]:
            raise IntegrityError(f"reference TCA mismatch for object {object_id}")
        audit_by_object[object_id] = row
    if set(audit_by_object) != set(object_ids):
        raise IntegrityError("causal-history audit and allocation object sets differ")

    with G4_SUMMARY.open("r", encoding="utf-8") as handle:
        g4_summary = json.load(handle)
    expected_g4 = {
        "coarse_step_s": COARSE_STEP_S,
        "time_tolerance_s": REFINEMENT_TOLERANCE_S,
        "dedup_time_tolerance_s": DEDUP_TIME_TOLERANCE_S,
        "dedup_distance_tolerance_km": DEDUP_DISTANCE_TOLERANCE_KM,
        "delta_assoc_min": ASSOCIATION_TOLERANCE_MIN,
    }
    if g4_summary.get("status") != "PASS" or g4_summary.get("selected") != expected_g4:
        raise IntegrityError("validated G4 numerical settings do not match the frozen setup")
    if g4_summary.get("sgp4_reference", {}).get("gravity_constants") != "WGS-72":
        raise IntegrityError("validated G4 SGP4 gravity constants are not WGS-72")

    return events, input_hashes, audit_by_object


def expected_main_raw_paths() -> list[Path]:
    paths = [
        RAW_DIR / "gp_history_g5_main_bs05_b001_11416_13809.csv",
        RAW_DIR / "gp_history_g5_main_bs10_b001_14051_23893.csv",
    ]
    production = sorted(RAW_DIR.glob("gp_history_g5_prod_b???_*.csv"))
    pattern = re.compile(r"gp_history_g5_prod_b(\d{3})_\d+_\d+\.csv")
    batches: dict[int, Path] = {}
    for path in production:
        match = pattern.fullmatch(path.name)
        if match is None:
            continue
        batch = int(match.group(1))
        if batch in batches:
            raise IntegrityError(f"duplicate production batch raw file: b{batch:03d}")
        batches[batch] = path
    if set(batches) != set(range(1, 35)):
        raise IntegrityError("expected production batch raw files b001-b034 were not found exactly once")
    paths.extend(batches[i] for i in range(1, 35))
    if len(paths) != 36 or not all(path.is_file() for path in paths):
        raise IntegrityError("expected 36 retained main-history raw files")
    return paths


def gp_record(row: dict[str, str], expected_object: str | None = None) -> g4.GPRecord:
    object_id = normalized_id(row["NORAD_CAT_ID"])
    if expected_object is not None and object_id != expected_object:
        raise IntegrityError(f"raw predecessor object mismatch for {expected_object}")
    gp_id = row["GP_ID"].strip()
    if not gp_id:
        raise IntegrityError(f"missing GP_ID for {object_id}")
    return g4.GPRecord(
        object_id=object_id,
        epoch_utc=g4.parse_utc(row["EPOCH"], schema_declares_utc=True),
        creation_date_utc=g4.parse_utc(row["CREATION_DATE"], schema_declares_utc=True),
        stable_tie_key=gp_id,
        payload={
            "GP_ID": gp_id,
            "CREATION_DATE": row["CREATION_DATE"].strip(),
            "TLE_LINE1": row["TLE_LINE1"].strip(),
            "TLE_LINE2": row["TLE_LINE2"].strip(),
        },
    )


def load_causal_gp_records(
    frozen_objects: set[str], audit_by_object: dict[str, dict[str, str]]
) -> dict[str, list[g4.GPRecord]]:
    records: dict[str, list[g4.GPRecord]] = {object_id: [] for object_id in frozen_objects}
    file_by_object: dict[str, Path] = {}
    required = {"NORAD_CAT_ID", "CREATION_DATE", "EPOCH", "GP_ID", "TLE_LINE1", "TLE_LINE2"}
    for path in expected_main_raw_paths():
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not required.issubset(set(reader.fieldnames or ())):
                raise IntegrityError(f"required GP schema missing from {path.relative_to(ROOT)}")
            for row in reader:
                record = gp_record(row)
                if record.object_id not in frozen_objects:
                    raise IntegrityError(f"unexpected object {record.object_id} in {path.relative_to(ROOT)}")
                prior = file_by_object.setdefault(record.object_id, path)
                if prior != path:
                    raise IntegrityError(f"object {record.object_id} occurs in multiple main raw files")
                records[record.object_id].append(record)
    if set(file_by_object) != frozen_objects:
        missing = sorted(frozen_objects - set(file_by_object), key=int)
        raise IntegrityError(f"main-history object coverage incomplete: {missing}")

    for object_id, audit in audit_by_object.items():
        predecessor_class = audit["final_predecessor_class"]
        if predecessor_class == "PREDECESSOR_ACQUIRED_BY_QUERY":
            path = ROOT / Path(audit["predecessor_raw_path"])
            if not path.is_file() or sha256(path) != audit["predecessor_raw_SHA256"]:
                raise IntegrityError(f"queried predecessor raw integrity failure for {object_id}")
            rows = read_csv(path)
            if len(rows) != 1:
                raise IntegrityError(f"queried predecessor raw must contain exactly one record for {object_id}")
            predecessor = gp_record(rows[0], expected_object=object_id)
            if (
                predecessor.stable_tie_key != audit["predecessor_GP_ID"]
                or predecessor.creation_date_utc != g4.parse_utc(audit["predecessor_CREATION_DATE"])
                or predecessor.epoch_utc != g4.parse_utc(audit["predecessor_EPOCH"])
            ):
                raise IntegrityError(f"queried predecessor does not match frozen audit for {object_id}")
            records[object_id].append(predecessor)
        elif predecessor_class == "PREDECESSOR_CAPTURED_IN_BATCH_UNION":
            gp_id = audit["predecessor_GP_ID"]
            if not any(record.stable_tie_key == gp_id for record in records[object_id]):
                raise IntegrityError(f"captured predecessor missing from main union for {object_id}")
        elif predecessor_class == "LEFT_CENSORED_NO_PREDECESSOR_RETURNED":
            if audit["predecessor_GP_ID"].strip():
                raise IntegrityError(f"left-censored object has a frozen predecessor GP_ID: {object_id}")
        else:
            raise IntegrityError(f"unexpected predecessor class for {object_id}: {predecessor_class}")
        records[object_id].sort(
            key=lambda record: (record.creation_date_utc, record.epoch_utc, record.stable_tie_key)
        )
    return records


def select_or_none(records: Iterable[g4.GPRecord], cutoff: datetime) -> g4.GPRecord | None:
    try:
        return g4.select_current_gp(tuple(records), cutoff)
    except g4.VerificationError as exc:
        if str(exc) == "MISSING_CURRENT_GP":
            return None
        raise


def satrec_from_record(record: g4.GPRecord) -> Satrec:
    payload = record.payload
    if not isinstance(payload, dict):
        raise g4.ParseError("missing GP payload")
    line1 = str(payload.get("TLE_LINE1", ""))
    line2 = str(payload.get("TLE_LINE2", ""))
    if not line1.startswith("1 ") or not line2.startswith("2 "):
        raise g4.ParseError("missing or malformed TLE lines")
    try:
        satrec = Satrec.twoline2rv(line1, line2, WGS72)
    except Exception as exc:  # package exceptions vary by parser failure
        raise g4.ParseError("TLE parse failure") from exc
    if str(int(satrec.satnum)) != record.object_id:
        raise g4.ParseError("TLE satellite number does not match NORAD_CAT_ID")
    return satrec


def independent_recovery_reference(
    quantity: g4.ScalarQuantity, lower: float, upper: float
) -> tuple[float, float] | None:
    """Return the unique objective-only local minimum near one rejected bracket.

    This reproduces the already established G4 independent orbital-reference
    construction: a 0.01 s q(t) grid with three-point parabolic interpolation.
    The reference is diagnostic only and is never inserted into the production
    candidate set.
    """

    start = max(0.0, lower - RECOVERY_WINDOW_EXPANSION_S)
    stop = min(g4.PRIMARY_HORIZON_S, upper + RECOVERY_WINDOW_EXPANSION_S)
    times = np.arange(start, stop + RECOVERY_GRID_STEP_S / 2.0, RECOVERY_GRID_STEP_S, dtype=float)
    q, _ = quantity.evaluate_many(times)
    if not np.all(np.isfinite(q)):
        raise g4.PropagationError("NONFINITE_RECOVERY_REFERENCE")
    minima = np.flatnonzero((q[1:-1] < q[:-2]) & (q[1:-1] <= q[2:])) + 1
    if len(minima) != 1:
        return None
    index = int(minima[0])
    if index == 0 or index == len(times) - 1:
        return None
    y0, y1, y2 = float(q[index - 1]), float(q[index]), float(q[index + 1])
    denominator = y0 - 2.0 * y1 + y2
    offset = (
        0.0
        if denominator == 0.0
        else 0.5 * (y0 - y2) / denominator * RECOVERY_GRID_STEP_S
    )
    time_s = float(times[index] + offset)
    if not 0.0 < time_s < g4.PRIMARY_HORIZON_S:
        return None
    q_value = float(quantity.evaluate(time_s)[0])
    if not math.isfinite(q_value):
        raise g4.PropagationError("NONFINITE_RECOVERY_REFERENCE")
    return time_s, math.sqrt(max(q_value, 0.0))


def enumerate_candidates_redundancy_safe(
    quantity: g4.ScalarQuantity,
) -> RedundancySafeEnumerationResult:
    """Enumerate candidates while rejecting only independently redundant bad brackets.

    Frozen G4 numerical primitives and tolerances are unchanged.  Only
    LOCAL_NEIGHBORHOOD_INCONSISTENT is handled at bracket scope.  Every rejected
    bracket must be independently recovered by the final deduplicated successful
    candidate set within the frozen G4 0.5 s / 1e-6 km acceptance limits.
    """

    times = np.arange(0.0, g4.PRIMARY_HORIZON_S, COARSE_STEP_S, dtype=float)
    if times[-1] != g4.PRIMARY_HORIZON_S:
        times = np.append(times, g4.PRIMARY_HORIZON_S)
    q, qdot = quantity.evaluate_many(times)
    if not np.all(np.isfinite(q)) or not np.all(np.isfinite(qdot)):
        raise g4.PropagationError("NONFINITE_MINIMIZATION_QUANTITY")

    brackets = g4._candidate_brackets(times, q, qdot)
    raw: list[g4.Candidate] = []
    rejected: list[tuple[float, float, int]] = []
    unresolved_boundary_count = 0
    boundary_margin = max(
        REFINEMENT_TOLERANCE_S,
        16.0 * np.finfo(float).eps * g4.PRIMARY_HORIZON_S,
    )

    for lower, upper, index in brackets:
        if upper == g4.PRIMARY_HORIZON_S and q[-1] <= q[-2] and qdot[-1] <= 0.0:
            unresolved_boundary_count += 1
            continue
        try:
            candidate = g4.refine_bracket(
                quantity,
                (lower, upper),
                REFINEMENT_TOLERANCE_S,
                index,
            )
        except g4.OptimizationError as exc:
            if str(exc) == "LOCAL_NEIGHBORHOOD_INCONSISTENT":
                rejected.append((lower, upper, index))
                continue
            raise
        if candidate.time_s <= boundary_margin or candidate.time_s >= g4.PRIMARY_HORIZON_S - boundary_margin:
            unresolved_boundary_count += 1
            continue
        raw.append(candidate)

    candidates = g4.deduplicate_candidates(
        raw,
        DEDUP_TIME_TOLERANCE_S,
        DEDUP_DISTANCE_TOLERANCE_KM,
    )

    recovered = 0
    for lower, upper, index in rejected:
        reference = independent_recovery_reference(quantity, lower, upper)
        if reference is None:
            raise UnrecoveredRefinementFailure(
                f"UNRECOVERED_REFINEMENT_FAILURE: bracket_index={index}; "
                "independent objective minimum absent or ambiguous"
            )
        reference_time_s, reference_distance_km = reference
        if not any(
            abs(candidate.time_s - reference_time_s) <= RECOVERY_TCA_TOLERANCE_S
            and abs(candidate.distance_km - reference_distance_km) <= RECOVERY_DISTANCE_TOLERANCE_KM
            for candidate in candidates
        ):
            raise UnrecoveredRefinementFailure(
                f"UNRECOVERED_REFINEMENT_FAILURE: bracket_index={index}; "
                "no final candidate recovers independent reference"
            )
        recovered += 1

    return RedundancySafeEnumerationResult(
        raw_candidates=tuple(raw),
        candidates=tuple(candidates),
        unresolved_boundary_count=unresolved_boundary_count,
        sampled_count=len(times),
        rejected_local_inconsistency_count=len(rejected),
        recovered_local_inconsistency_count=recovered,
    )


def evaluate_event_lead(
    event: Event, lead_hours: int, records: dict[str, list[g4.GPRecord]]
) -> dict[str, str]:
    replay = event.reference_tca_utc - timedelta(hours=lead_hours)
    first = select_or_none(records[event.object1], replay)
    second = select_or_none(records[event.object2], replay)
    first_payload = first.payload if first is not None and isinstance(first.payload, dict) else {}
    second_payload = second.payload if second is not None and isinstance(second.payload, dict) else {}
    row = {
        "conj_id": event.conj_id,
        "block": event.block,
        "lead_hours": str(lead_hours),
        "reference_time_utc": iso_utc(replay),
        "object1_NORAD_CAT_ID": event.object1,
        "object2_NORAD_CAT_ID": event.object2,
        "object1_current_GP_ID": str(first_payload.get("GP_ID", "")),
        "object2_current_GP_ID": str(second_payload.get("GP_ID", "")),
        "object1_CREATION_DATE": iso_utc(first.creation_date_utc) if first is not None else "",
        "object2_CREATION_DATE": iso_utc(second.creation_date_utc) if second is not None else "",
        "availability_status": "AVAILABLE_BOTH",
        "parse_status": "NOT_EVALUATED",
        "propagation_status": "NOT_EVALUATED",
        "rejected_redundant_brackets": "0",
        "recovered_redundant_brackets": "0",
        "candidate_exists": "NOT_EVALUATED",
        "association_status": "NOT_EVALUATED",
        "evaluable": "FALSE",
        "non_evaluable_reason": "",
    }
    if first is None or second is None:
        if first is None and second is None:
            row["availability_status"] = "UNAVAILABLE_BOTH"
        elif first is None:
            row["availability_status"] = "UNAVAILABLE_OBJECT_1"
        else:
            row["availability_status"] = "UNAVAILABLE_OBJECT_2"
        row["non_evaluable_reason"] = (
            "NO_CAUSAL_GP_OBJECT_1" if first is None else "NO_CAUSAL_GP_OBJECT_2"
        )
        return row

    try:
        satrec1 = satrec_from_record(first)
        satrec2 = satrec_from_record(second)
    except (g4.ParseError, ValueError, TypeError):
        row["parse_status"] = "FAILED"
        row["non_evaluable_reason"] = "GP_PARSE_FAILURE"
        return row
    row["parse_status"] = "SUCCESS"

    try:
        quantity = g4.Sgp4PairQuantity(satrec1, satrec2, replay)
        candidates = enumerate_candidates_redundancy_safe(quantity)
    except UnrecoveredRefinementFailure:
        row["propagation_status"] = "FAILED_NUMERICAL_REFINEMENT"
        row["non_evaluable_reason"] = "UNRECOVERED_REFINEMENT_FAILURE"
        return row
    except g4.OptimizationError:
        row["propagation_status"] = "FAILED_NUMERICAL_REFINEMENT"
        row["non_evaluable_reason"] = "NUMERICAL_REFINEMENT_FAILURE"
        return row
    except (g4.VerificationError, FloatingPointError, OverflowError, ValueError):
        row["propagation_status"] = "FAILED"
        row["non_evaluable_reason"] = "PROPAGATION_FAILURE"
        return row
    row["rejected_redundant_brackets"] = str(candidates.rejected_local_inconsistency_count)
    row["recovered_redundant_brackets"] = str(candidates.recovered_local_inconsistency_count)
    row["propagation_status"] = (
        "SUCCESS_WITH_REDUNDANT_BRACKET_RECOVERY"
        if candidates.recovered_local_inconsistency_count
        else "SUCCESS"
    )

    if not candidates.candidates:
        row["candidate_exists"] = "FALSE"
        row["non_evaluable_reason"] = "NO_VALID_CANDIDATE"
        return row
    row["candidate_exists"] = "TRUE"

    # The frozen reference TCA is used only through its relative replay time.
    # The returned offset and candidate geometry are deliberately not read.
    association = g4.associate_reference_event(
        candidates.candidates,
        (event.reference_tca_utc - replay).total_seconds(),
        ASSOCIATION_TOLERANCE_MIN,
    )
    row["association_status"] = association.status
    if association.status != "VALID_ASSOCIATED":
        row["non_evaluable_reason"] = "NO_ASSOCIATED_CANDIDATE"
        return row

    row["evaluable"] = "TRUE"
    return row


def summarize(rows: list[dict[str, str]], input_hashes: dict[str, str]) -> dict[str, object]:
    if len(rows) != 885:
        raise IntegrityError("evaluability output must contain exactly 885 rows")
    keys = [(row["conj_id"], row["lead_hours"]) for row in rows]
    if len(set(keys)) != 885:
        raise IntegrityError("duplicate event/lead output rows")
    if {int(row["lead_hours"]) for row in rows} != set(LEADS_HOURS):
        raise IntegrityError("evaluability output does not contain exactly the five frozen leads")

    per_lead: dict[str, object] = {}
    selected: int | str = "NONE"
    for lead in LEADS_HOURS:
        lead_rows = [row for row in rows if int(row["lead_hours"]) == lead]
        if len(lead_rows) != 177:
            raise IntegrityError(f"lead {lead} does not contain 177 events")
        block_evaluable = {
            f"B{block}_evaluable": sum(
                row["block"] == block and row["evaluable"] == "TRUE" for row in lead_rows
            )
            for block in ("1", "2", "3", "4", "5")
        }
        oof_population = sum(row["block"] in OOF_BLOCKS for row in lead_rows)
        if oof_population != EXPECTED_OOF:
            raise IntegrityError(f"lead {lead} OOF denominator is not 141")
        oof_evaluable = sum(
            row["block"] in OOF_BLOCKS and row["evaluable"] == "TRUE" for row in lead_rows
        )
        failure_counts = Counter(
            row["non_evaluable_reason"] for row in lead_rows if row["non_evaluable_reason"]
        )
        recovery_cases = sum(
            row["propagation_status"] == "SUCCESS_WITH_REDUNDANT_BRACKET_RECOVERY"
            for row in lead_rows
        )
        rejected_redundant_brackets = sum(
            int(row["rejected_redundant_brackets"]) for row in lead_rows
        )
        recovered_redundant_brackets = sum(
            int(row["recovered_redundant_brackets"]) for row in lead_rows
        )
        per_lead[str(lead)] = {
            **block_evaluable,
            "OOF_evaluable": oof_evaluable,
            "recovery_cases": recovery_cases,
            "rejected_redundant_brackets": rejected_redundant_brackets,
            "recovered_redundant_brackets": recovered_redundant_brackets,
            "categorical_failure_counts": dict(sorted(failure_counts.items())),
        }
        if selected == "NONE" and oof_evaluable >= EXPECTED_OOF and block_evaluable["B1_evaluable"] >= 1:
            selected = lead

    return {
        "input_hashes": input_hashes,
        "total_events": 177,
        "block_counts": {f"B{key}": value for key, value in EXPECTED_BLOCK_COUNTS.items()},
        "lead_order_hours": list(LEADS_HOURS),
        "per_lead": per_lead,
        "selected_L_star_hours": selected,
        "selection_rule": "first descending lead with OOF_evaluable >= 141 and B1_evaluable >= 1; otherwise NONE/HOLD_REVISE",
        "original_v2_1_result": {
            "selected_L_star_hours": "NONE",
            "decision": "HOLD_REVISE",
            "preserved": True,
        },
        "numerical_control_flow_amendment": {
            "scope": "LOCAL_NEIGHBORHOOD_INCONSISTENT handled at bracket scope only when independently redundant",
            "recovery_reference": "0.01 s objective grid plus three-point parabolic interpolation in rejected bracket expanded by 5 s each side",
            "recovery_tca_tolerance_s": RECOVERY_TCA_TOLERANCE_S,
            "recovery_distance_tolerance_km": RECOVERY_DISTANCE_TOLERANCE_KM,
        },
        "outcome_information_accessed": False,
        "network_requests": 0,
        "integrity_status": "PASS",
    }


def write_csv(rows: list[dict[str, str]]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_json(summary: dict[str, object]) -> None:
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_JSON.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=False)
        handle.write("\n")


def write_markdown(summary: dict[str, object]) -> None:
    per_lead = summary["per_lead"]
    selected = summary["selected_L_star_hours"]
    selection_text = f"{selected} h" if isinstance(selected, int) else "NONE — HOLD_REVISE"
    lines = [
        "# G5 L-star Evaluability Selection — Numerical Control-Flow Amendment",
        "",
        "Status: amended evaluability-only rerun; original v2.1 NONE/HOLD-REVISE result is preserved; this does not complete G5.",
        "",
        "## Frozen rule",
        "",
        "Leads were tested in descending order `72, 48, 24, 12, 6 h`. A lead is admissible only when all 141 B2–B5 out-of-fold events are evaluable and at least one B1 event is evaluable. The first admissible lead is L-star; if none is admissible, the result is `NONE — HOLD_REVISE`.",
        "",
        "Causal availability was determined by `CREATION_DATE <= t_r`. Frozen G4 numerical primitives and tolerances were retained. Under the pre-outcome control-flow amendment, only an individual `LOCAL_NEIGHBORHOOD_INCONSISTENT` bracket may be discarded, and only when its objective minimum is independently recovered by the final deduplicated candidate set within the frozen G4 0.5 s / 1e-6 km acceptance limits. The independent reference is not inserted into the candidate set. The frozen 5-minute association rule is unchanged.",
        "",
        "## Allocation verification",
        "",
        "PASS: 177 globally object-disjoint events; block counts B1–B5 = 36, 36, 35, 35, 35; OOF = B2+B3+B4+B5 = 141.",
        "",
        "## Evaluability counts",
        "",
        "| Lead (h) | B1 | B2 | B3 | B4 | B5 | OOF |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for lead in LEADS_HOURS:
        item = per_lead[str(lead)]
        lines.append(
            f"| {lead} | {item['B1_evaluable']} | {item['B2_evaluable']} | {item['B3_evaluable']} | {item['B4_evaluable']} | {item['B5_evaluable']} | {item['OOF_evaluable']} |"
        )
    total_recovery_cases = sum(per_lead[str(lead)]["recovery_cases"] for lead in LEADS_HOURS)
    total_rejected = sum(per_lead[str(lead)]["rejected_redundant_brackets"] for lead in LEADS_HOURS)
    total_recovered = sum(per_lead[str(lead)]["recovered_redundant_brackets"] for lead in LEADS_HOURS)
    lines.extend(
        [
            "",
            "## Numerical control-flow amendment",
            "",
            f"Event/lead cases using independently verified redundant-bracket recovery: `{total_recovery_cases}`.",
            f"Rejected `LOCAL_NEIGHBORHOOD_INCONSISTENT` brackets: `{total_rejected}`; independently recovered: `{total_recovered}`.",
            "",
            "## Selection",
            "",
            f"`L_star = {selection_text}`",
            "",
            "The original v2.1 `NONE — HOLD_REVISE` result remains preserved. No protected outcome information was accessed or emitted. No network request was made. Calibration performance has not been evaluated.",
            "",
        ]
    )
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    events, input_hashes, audit_by_object = verify_frozen_inputs()
    frozen_objects = {object_id for event in events for object_id in (event.object1, event.object2)}
    records = load_causal_gp_records(frozen_objects, audit_by_object)
    rows = [
        evaluate_event_lead(event, lead, records)
        for event in events
        for lead in LEADS_HOURS
    ]
    summary = summarize(rows, input_hashes)
    write_csv(rows)
    write_json(summary)
    write_markdown(summary)
    print(json.dumps(summary, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
