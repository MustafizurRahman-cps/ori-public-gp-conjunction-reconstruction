#!/usr/bin/env python3
"""G7 R6 A2 sequential reconstruction controller core.

This implementation binds the A2 execution path to the frozen materialized
causal schedule and to the existing G4/G5/G6 numerical primitives.

At this implementation-review stage:
  * --offline-validate is permitted and performs no orbital propagation.
  * --run-pilot is intentionally blocked at the CLI boundary.
  * --run-production is intentionally blocked.

A later, separately frozen authorization launcher may import this module and
call execute_pilot(authorization=True).  Full 107,740-replay production
propagation remains unauthorized.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PARENT_COMMIT = "247845491a35ddff15cfc2515b8d5b68d672aa29"
PARENT_TAG = "g7-r6-a2-materialization-frozen"

DB = ROOT / "data/raw/g7/G7R6_A2_Materialized.sqlite3"
PILOT_MANIFEST = ROOT / "results/g7/G7R6_A2_Predetermined_Pilot_Manifest.json"

MATERIALIZATION_SUMMARY = ROOT / "results/g7/G7R6_A2_Materialization_Summary.json"
MATERIALIZATION_INDEPENDENT_AUDIT = ROOT / "results/g7/G7R6_A2_Materialization_Independent_Audit.json"
DR0058 = ROOT / "docs/research/DR-0058_G7_R6_A2_Materialization_Implementation_and_Evidence_Freeze.md"

G4_NUMERICS = ROOT / "scripts/g4/numerics.py"
G5_CONTROLFLOW = ROOT / "scripts/g5/select_lstar_controlflow_amended.py"
G6_CANDIDATE = ROOT / "scripts/g6/generate_g6_preoutcome_candidates.py"
CAUSAL_AUDIT = ROOT / "results/g6/G6_Final_Causal_History_Audit.csv"

OFFLINE_VALIDATION = ROOT / "results/g7/G7R6_A2_Controller_Offline_Validation.json"
PILOT_OUTPUT = ROOT / "results/g7/G7R6_A2_Pilot_Execution.json"

EXPECTED_DB_SHA256 = "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"
EXPECTED_PILOT_MANIFEST_SHA256 = "c3c803e9957a1241854fa81f3e51bbe9923d7710c866fa34e08d79d907ac8cac"

EXPECTED_HASHES = {
    MATERIALIZATION_SUMMARY:
        "697ad58a01755ab2367e7318eb3219030f952d4ca4dedcb8263d2346e0c42096",
    MATERIALIZATION_INDEPENDENT_AUDIT:
        "9618d235f9ec1a78ed60763f3701d9f51e4b25a6cc76d8e8cad43f34fa2a50b6",
    DR0058:
        "71c86ebd2c30bbd4d78acb20969dc365b3e9a25df142c2ecb249b5f70ef589fc",
    G4_NUMERICS:
        "dc8de410b89e05ffd829563b4996b06703c31236e9a10fb56bd7f4e690048406",
    G5_CONTROLFLOW:
        "553578f4e90b044c6a783209a756ea3accc009ecbb7a09853e8cae46503f33c2",
    G6_CANDIDATE:
        "bce2a4b5e4dab8d0c4e461e2acb4dbac8afe22d3ac412922ba3670a832adcecb",
    CAUSAL_AUDIT:
        "41802759162ef4889b071aa6f15391d48a7a96cce7ededdd99c8e1d8a3ebc924",
}

EXPECTED_PACKAGES = {
    "numpy": "2.3.5",
    "scipy": "1.18.1",
    "sgp4": "2.27",
}

EXPECTED_EVENTS = 12
EXPECTED_PILOT_REPLAYS = 28
EXPECTED_SUPPORT_REPLAYS = 107_740
ASSOCIATION_TOLERANCE_MIN = 5.0
PRIMARY_HORIZON_S = 432_000.0


class A2ControllerError(RuntimeError):
    pass


class A2PilotExecutionNotAuthorized(A2ControllerError):
    pass


class A2ProductionExecutionNotAuthorized(A2ControllerError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise A2ControllerError(message)


def sha256(path: Path, *, progress: bool = False) -> str:
    h = hashlib.sha256()
    total = path.stat().st_size if path.exists() else 0
    done = 0
    next_report = 256 * 1024 * 1024
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
            done += len(block)
            if progress and done >= next_report:
                print(
                    f"A2_CONTROLLER_DB_HASH_PROGRESS = {done}/{total} bytes",
                    flush=True,
                )
                next_report += 256 * 1024 * 1024
    return h.hexdigest()


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def git_value(*args: str) -> str:
    return git(*args).stdout.strip()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def us_to_datetime(value: int) -> datetime:
    return datetime.fromtimestamp(int(value) / 1_000_000.0, tz=UTC)


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def verify_parent_boundary() -> None:
    require(
        git_value("rev-list", "-n", "1", PARENT_TAG) == PARENT_COMMIT,
        "materialization freeze tag target changed",
    )


def verify_environment() -> dict:
    require(
        sys.version_info[:3] == (3, 12, 14),
        "Python runtime changed: " + platform.python_version(),
    )
    packages = {
        name: importlib.metadata.version(name)
        for name in EXPECTED_PACKAGES
    }
    require(
        packages == EXPECTED_PACKAGES,
        f"runtime package versions changed: {packages}",
    )
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
    }


def verify_frozen_hashes() -> dict[str, str]:
    result = {}
    for path, expected in EXPECTED_HASHES.items():
        require(path.is_file(), f"missing frozen dependency: {path}")
        actual = sha256(path)
        require(
            actual == expected,
            "frozen dependency SHA mismatch\n"
            f"path={path}\nactual={actual}\nexpected={expected}",
        )
        result[path.relative_to(ROOT).as_posix()] = actual

    require(PILOT_MANIFEST.is_file(), "pilot manifest is missing")
    manifest_sha = sha256(PILOT_MANIFEST)
    require(
        manifest_sha == EXPECTED_PILOT_MANIFEST_SHA256,
        "pilot manifest SHA mismatch",
    )
    result[PILOT_MANIFEST.relative_to(ROOT).as_posix()] = manifest_sha

    return result


def load_manifest() -> dict:
    with PILOT_MANIFEST.open("r", encoding="utf-8") as stream:
        manifest = json.load(stream)

    require(
        manifest.get("status") == "G7R6_A2_PREDETERMINED_PILOT_MANIFEST",
        "pilot manifest status changed",
    )
    require(
        manifest.get("parent_materialization_commit") == PARENT_COMMIT,
        "pilot manifest parent commit changed",
    )
    require(
        manifest.get("materialized_db_sha256") == EXPECTED_DB_SHA256,
        "pilot manifest DB hash changed",
    )
    require(
        int(manifest.get("selected_event_count", -1)) == EXPECTED_EVENTS,
        "pilot event count changed",
    )
    require(
        int(manifest.get("selected_pilot_replay_count", -1))
        == EXPECTED_PILOT_REPLAYS,
        "pilot replay count changed",
    )
    return manifest


def connect_db() -> sqlite3.Connection:
    require(DB.is_file(), "materialized DB is missing")
    uri = DB.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def verify_manifest_against_db(manifest: dict) -> dict:
    connection = connect_db()
    checked_events = 0
    checked_replays = 0
    required_keys: set[str] = set()
    try:
        total_support = int(
            connection.execute(
                "SELECT COUNT(*) FROM replay_updates WHERE temporal_support=1"
            ).fetchone()[0]
        )
        require(
            total_support == EXPECTED_SUPPORT_REPLAYS,
            "materialized temporal-support count changed",
        )

        for event_item in manifest["selected_events"]:
            event_index = int(event_item["event_index"])
            event = connection.execute(
                "SELECT * FROM events WHERE event_index=?",
                (event_index,),
            ).fetchone()
            require(event is not None, f"pilot event absent from DB: {event_index}")
            require(
                str(event["conj_id"]) == str(event_item["conj_id"]),
                f"pilot conjunction mismatch: {event_index}",
            )
            require(
                int(event["expected_support_replays"])
                == int(event_item["support_count"]),
                f"pilot support-count mismatch: {event_index}",
            )
            checked_events += 1

            for replay_item in event_item["pilot_replays"]:
                row = connection.execute(
                    """
                    SELECT *
                    FROM replay_updates
                    WHERE event_index=? AND replay_sequence=?
                    """,
                    (event_index, int(replay_item["replay_sequence"])),
                ).fetchone()
                require(
                    row is not None,
                    f"pilot replay absent: {event_index}/"
                    f"{replay_item['replay_sequence']}",
                )
                require(int(row["temporal_support"]) == 1, "pilot replay lacks temporal support")
                require(
                    int(row["replay_us"]) == int(replay_item["replay_us"]),
                    "pilot replay timestamp mismatch",
                )
                for field in ("obj1_record_key", "obj2_record_key"):
                    require(
                        str(row[field]) == str(replay_item[field]),
                        f"pilot selected-GP identity mismatch: {field}",
                    )
                    required_keys.add(str(row[field]))
                require(
                    str(row["obj1_gp_id"]) == str(replay_item["obj1_GP_ID"]),
                    "pilot obj1 GP_ID mismatch",
                )
                require(
                    str(row["obj2_gp_id"]) == str(replay_item["obj2_GP_ID"]),
                    "pilot obj2 GP_ID mismatch",
                )
                require(
                    str(row["pair_parse_status"]) == "PARSE_READY_BOTH",
                    "pilot replay is not PARSE_READY_BOTH",
                )
                checked_replays += 1

        require(checked_events == EXPECTED_EVENTS, "pilot event verification incomplete")
        require(checked_replays == EXPECTED_PILOT_REPLAYS, "pilot replay verification incomplete")

        placeholders = ",".join("?" for _ in required_keys)
        payload_rows = connection.execute(
            f"""
            SELECT record_key, parse_status, source_class
            FROM support_payloads
            WHERE record_key IN ({placeholders})
            """,
            tuple(sorted(required_keys)),
        ).fetchall()
        require(
            len(payload_rows) == len(required_keys),
            "pilot support-payload coverage incomplete",
        )
        require(
            all(str(row["parse_status"]) == "SUCCESS" for row in payload_rows),
            "pilot contains non-success TLE parse status",
        )
        require(
            all(str(row["source_class"]) == "MAIN_WINDOW" for row in payload_rows),
            "pilot unexpectedly selects predecessor/non-main payload",
        )

    finally:
        connection.close()

    return {
        "events_verified": checked_events,
        "pilot_replays_verified": checked_replays,
        "unique_selected_payloads": len(required_keys),
        "all_pair_parse_status": "PARSE_READY_BOTH",
        "all_selected_payload_source": "MAIN_WINDOW",
    }


def import_execution_dependencies():
    g6_dir = ROOT / "scripts/g6"
    if str(g6_dir) not in sys.path:
        sys.path.insert(0, str(g6_dir))
    import generate_g6_preoutcome_candidates as g6

    s = g6.import_frozen_controlflow()

    require(
        float(s.g4.PRIMARY_HORIZON_S) == PRIMARY_HORIZON_S,
        "primary candidate horizon changed",
    )

    constants = g6.verify_numerical_constants(s)

    return g6, s, constants


def load_strata() -> dict[int, str]:
    result: dict[int, str] = {}
    with CAUSAL_AUDIT.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            event_index = int(row["final_g6_sample_index"])
            value = str(row["g6_temporal_stratum"]).strip()
            if event_index in result:
                require(
                    result[event_index] == value,
                    f"temporal stratum mismatch within event {event_index}",
                )
            else:
                result[event_index] = value
    return result


def payload_to_cache_item(row: sqlite3.Row, replay_us: int) -> dict:
    return {
        "GP_ID": str(row["gp_id"]),
        "EPOCH": iso_utc(us_to_datetime(int(row["epoch_us"]))),
        "CREATION_DATE": iso_utc(us_to_datetime(int(row["creation_us"]))),
        "stable_tie_key": str(row["stable_tie_key"]),
        "TLE_LINE1": str(row["tle_line1"]),
        "TLE_LINE2": str(row["tle_line2"]),
        "replay_time_utc": iso_utc(us_to_datetime(replay_us)),
    }


def reconstruct_candidates(g4, rows: list[dict]):
    result = []
    for row in rows:
        result.append(
            g4.Candidate(
                time_s=float(row["time_from_replay_s"]),
                distance_km=float(row["distance_km"]),
                bracket_s=(
                    float(row["bracket_lower_s"]),
                    float(row["bracket_upper_s"]),
                ),
                bracket_index=int(row["bracket_index"]),
                converged=bool(row["converged"]),
                iterations=int(row["iterations"]),
                function_evaluations=int(row["function_evaluations"]),
                termination=str(row["termination"]),
            )
        )
    return result


def normalize_direct_candidate(candidate, candidate_index: int) -> dict:
    return {
        "candidate_index": int(candidate_index),
        "time_from_replay_s": float(candidate.time_s),
        "distance_km": float(candidate.distance_km),
        "bracket_lower_s": float(candidate.bracket_s[0]),
        "bracket_upper_s": float(candidate.bracket_s[1]),
        "bracket_index": int(candidate.bracket_index),
        "converged": bool(candidate.converged),
        "iterations": int(candidate.iterations),
        "function_evaluations": int(candidate.function_evaluations),
        "termination": str(candidate.termination),
    }


def normalize_g6_candidate(row: dict) -> dict:
    return {
        "candidate_index": int(row["candidate_index"]),
        "time_from_replay_s": float(row["time_from_replay_s"]),
        "distance_km": float(row["distance_km"]),
        "bracket_lower_s": float(row["bracket_lower_s"]),
        "bracket_upper_s": float(row["bracket_upper_s"]),
        "bracket_index": int(row["bracket_index"]),
        "converged": bool(row["converged"]),
        "iterations": int(row["iterations"]),
        "function_evaluations": int(row["function_evaluations"]),
        "termination": str(row["termination"]),
    }


def independent_direct_path(g6, s, event: dict, cache: dict) -> dict:
    try:
        record1 = g6.make_gp_record(s, event["obj1"], cache[event["obj1"]])
        record2 = g6.make_gp_record(s, event["obj2"], cache[event["obj2"]])
        sat1 = s.satrec_from_record(record1)
        sat2 = s.satrec_from_record(record2)
    except (s.g4.ParseError, ValueError, TypeError) as exc:
        return {
            "preoutcome_status": "GP_PARSE_FAILURE",
            "failure_type": type(exc).__name__,
            "candidates": [],
        }

    try:
        quantity = s.g4.Sgp4PairQuantity(
            sat1,
            sat2,
            event["replay_time"],
        )
        enumeration = s.enumerate_candidates_redundancy_safe(quantity)
    except (s.UnrecoveredRefinementFailure, s.g4.OptimizationError) as exc:
        return {
            "preoutcome_status": "UNRECOVERED_REFINEMENT_FAILURE",
            "failure_type": type(exc).__name__,
            "candidates": [],
        }
    except (
        s.g4.VerificationError,
        FloatingPointError,
        OverflowError,
        ValueError,
    ) as exc:
        return {
            "preoutcome_status": "PROPAGATION_FAILURE",
            "failure_type": type(exc).__name__,
            "candidates": [],
        }

    status = (
        "CANDIDATE_SET_READY"
        if enumeration.candidates
        else "NO_VALID_CANDIDATE"
    )

    return {
        "preoutcome_status": status,
        "rejected_local_inconsistency_count":
            int(enumeration.rejected_local_inconsistency_count),
        "recovered_local_inconsistency_count":
            int(enumeration.recovered_local_inconsistency_count),
        "unresolved_boundary_count":
            int(enumeration.unresolved_boundary_count),
        "sampled_count":
            int(enumeration.sampled_count),
        "raw_candidate_count":
            len(enumeration.raw_candidates),
        "candidate_count":
            len(enumeration.candidates),
        "candidates": [
            normalize_direct_candidate(candidate, i)
            for i, candidate in enumerate(enumeration.candidates, start=1)
        ],
    }


def candidate_sets_equal(first: list[dict], second: list[dict]) -> bool:
    if len(first) != len(second):
        return False
    for a, b in zip(first, second):
        if a["candidate_index"] != b["candidate_index"]:
            return False
        if a["bracket_index"] != b["bracket_index"]:
            return False
        if a["converged"] != b["converged"]:
            return False
        if a["iterations"] != b["iterations"]:
            return False
        if a["function_evaluations"] != b["function_evaluations"]:
            return False
        if a["termination"] != b["termination"]:
            return False
        for field in (
            "time_from_replay_s",
            "distance_km",
            "bracket_lower_s",
            "bracket_upper_s",
        ):
            if float(a[field]) != float(b[field]):
                return False
    return True


def execute_pilot(*, authorization: bool) -> dict:
    if not authorization:
        raise A2PilotExecutionNotAuthorized(
            "A2_PILOT_EXECUTION_NOT_AUTHORIZED"
        )

    verify_parent_boundary()
    frozen_hashes = verify_frozen_hashes()
    environment = verify_environment()
    manifest = load_manifest()
    manifest_check = verify_manifest_against_db(manifest)

    actual_db_sha = sha256(DB, progress=True)
    require(
        actual_db_sha == EXPECTED_DB_SHA256,
        "materialized DB SHA mismatch before pilot execution",
    )

    require(
        not PILOT_OUTPUT.exists(),
        "refusing to overwrite existing A2 pilot output",
    )

    g6, s, constants = import_execution_dependencies()
    strata = load_strata()

    connection = connect_db()
    results = []
    equivalence_mismatches = 0
    started = time.perf_counter()

    try:
        for event_item in manifest["selected_events"]:
            event_index = int(event_item["event_index"])
            event_row = connection.execute(
                "SELECT * FROM events WHERE event_index=?",
                (event_index,),
            ).fetchone()
            require(event_row is not None, f"pilot event missing: {event_index}")

            for pilot_item in event_item["pilot_replays"]:
                replay_sequence = int(pilot_item["replay_sequence"])
                replay_row = connection.execute(
                    """
                    SELECT *
                    FROM replay_updates
                    WHERE event_index=? AND replay_sequence=?
                    """,
                    (event_index, replay_sequence),
                ).fetchone()
                require(replay_row is not None, "pilot replay disappeared")

                payload1 = connection.execute(
                    "SELECT * FROM support_payloads WHERE record_key=?",
                    (str(replay_row["obj1_record_key"]),),
                ).fetchone()
                payload2 = connection.execute(
                    "SELECT * FROM support_payloads WHERE record_key=?",
                    (str(replay_row["obj2_record_key"]),),
                ).fetchone()
                require(payload1 is not None and payload2 is not None, "pilot TLE payload missing")

                replay_us = int(replay_row["replay_us"])
                replay_dt = us_to_datetime(replay_us)

                obj1 = str(event_row["obj1"])
                obj2 = str(event_row["obj2"])

                cache = {
                    obj1: payload_to_cache_item(payload1, replay_us),
                    obj2: payload_to_cache_item(payload2, replay_us),
                }

                event = {
                    "final_g6_sample_index": event_index,
                    "g6_temporal_stratum": strata[event_index],
                    "conj_id": str(event_row["conj_id"]),
                    "obj1": obj1,
                    "obj2": obj2,
                    "replay_time": replay_dt,
                }

                g6_event, g6_candidates = g6.process_event(
                    s,
                    event,
                    cache,
                )

                direct = independent_direct_path(
                    g6,
                    s,
                    event,
                    cache,
                )

                normalized_g6 = [
                    normalize_g6_candidate(row)
                    for row in g6_candidates
                ]

                equivalent = (
                    str(g6_event["preoutcome_status"])
                    == str(direct["preoutcome_status"])
                    and candidate_sets_equal(
                        normalized_g6,
                        direct["candidates"],
                    )
                )

                if equivalent and direct["preoutcome_status"] in {
                    "CANDIDATE_SET_READY",
                    "NO_VALID_CANDIDATE",
                }:
                    for field in (
                        "rejected_local_inconsistency_count",
                        "recovered_local_inconsistency_count",
                        "unresolved_boundary_count",
                        "sampled_count",
                        "raw_candidate_count",
                        "candidate_count",
                    ):
                        if int(g6_event[field]) != int(direct[field]):
                            equivalent = False
                            break

                if not equivalent:
                    equivalence_mismatches += 1

                association_status = "NOT_APPLICABLE_PREOUTCOME_FAILURE"
                selected_candidate_index = None
                predicted_tca_utc = None
                predicted_miss_distance_km = None
                signed_tca_residual_s = None

                if g6_event["preoutcome_status"] == "CANDIDATE_SET_READY":
                    candidate_objects = reconstruct_candidates(
                        s.g4,
                        g6_candidates,
                    )
                    reference_time_s = (
                        int(event_row["reference_us"]) - replay_us
                    ) / 1_000_000.0

                    association = s.g4.associate_reference_event(
                        candidate_objects,
                        reference_time_s,
                        ASSOCIATION_TOLERANCE_MIN,
                    )

                    association_status = association.status

                    if association.candidate is not None:
                        position = candidate_objects.index(association.candidate)
                        selected_candidate_index = int(
                            g6_candidates[position]["candidate_index"]
                        )
                        predicted_tca = (
                            replay_dt
                            + timedelta(seconds=float(association.candidate.time_s))
                        )
                        predicted_tca_utc = iso_utc(predicted_tca)
                        predicted_miss_distance_km = float(
                            association.candidate.distance_km
                        )
                        signed_tca_residual_s = float(
                            association.tca_error_s
                        )

                results.append(
                    {
                        "selection_group": event_item["selection_group"],
                        "selection_rank": int(event_item["selection_rank"]),
                        "pilot_position": pilot_item["pilot_position"],
                        "event_index": event_index,
                        "conj_id": str(event_row["conj_id"]),
                        "support_count": int(event_item["support_count"]),
                        "replay_sequence": replay_sequence,
                        "replay_utc": iso_utc(replay_dt),
                        "obj1_record_key": str(replay_row["obj1_record_key"]),
                        "obj2_record_key": str(replay_row["obj2_record_key"]),
                        "obj1_GP_ID": str(replay_row["obj1_gp_id"]),
                        "obj2_GP_ID": str(replay_row["obj2_gp_id"]),
                        "g6_preoutcome_status": str(g6_event["preoutcome_status"]),
                        "independent_direct_preoutcome_status":
                            str(direct["preoutcome_status"]),
                        "candidate_count": int(g6_event["candidate_count"]),
                        "implementation_equivalent": bool(equivalent),
                        "association_status": association_status,
                        "selected_candidate_index": selected_candidate_index,
                        "predicted_tca_utc": predicted_tca_utc,
                        "predicted_miss_distance_km": predicted_miss_distance_km,
                        "signed_tca_residual_s": signed_tca_residual_s,
                    }
                )

                print(
                    "A2_PILOT_PROGRESS = "
                    f"{len(results)}/{EXPECTED_PILOT_REPLAYS}; "
                    f"event={event_index}; replay={replay_sequence}; "
                    f"status={g6_event['preoutcome_status']}; "
                    f"equivalent={equivalent}",
                    flush=True,
                )

    finally:
        connection.close()

    elapsed = time.perf_counter() - started

    status = (
        "G7R6_A2_PILOT_PASS"
        if equivalence_mismatches == 0
        else "G7R6_A2_PILOT_HOLD_EQUIVALENCE_MISMATCH"
    )

    output = {
        "status": status,
        "analysis_role": "A2_PREDETERMINED_IMPLEMENTATION_EQUIVALENCE_PILOT",
        "parent_materialization_commit": PARENT_COMMIT,
        "parent_materialization_tag": PARENT_TAG,
        "materialized_db_sha256": actual_db_sha,
        "pilot_manifest_sha256": EXPECTED_PILOT_MANIFEST_SHA256,
        "controller_sha256": sha256(Path(__file__)),
        "frozen_dependency_hashes": frozen_hashes,
        "environment": environment,
        "numerical_constants": constants,
        "candidate_horizon_days": 5.0,
        "association_tolerance_min": ASSOCIATION_TOLERANCE_MIN,
        "pilot_manifest_check": manifest_check,
        "pilot_replays_executed": len(results),
        "implementation_equivalence_mismatches": equivalence_mismatches,
        "preoutcome_status_counts": dict(sorted(Counter(
            row["g6_preoutcome_status"] for row in results
        ).items())),
        "association_status_counts": dict(sorted(Counter(
            row["association_status"] for row in results
        ).items())),
        "wall_seconds": elapsed,
        "results": results,
        "operations": {
            "raw_GP_HISTORY_opened": False,
            "materialized_SQLite_read": True,
            "SGP4_orbital_propagation_performed": True,
            "candidate_generation_performed": True,
            "independent_direct_equivalence_path_performed": True,
            "reference_association_performed": True,
            "reference_miss_distance_accessed": False,
            "CAL0_refit_performed": False,
            "network_requests": 0,
        },
        "interpretation": (
            "Pilot validates the A2 materialized-input adapter and frozen "
            "candidate/association control path on predetermined replay identities. "
            "It is not a scientific accuracy estimate and does not authorize "
            "full A2 production propagation."
        ),
        "next_safe_action": (
            "INDEPENDENT_PILOT_AUDIT_BEFORE_PRODUCTION_AUTHORIZATION"
            if status == "G7R6_A2_PILOT_PASS"
            else "HOLD_AND_REVIEW_PILOT_EQUIVALENCE_MISMATCH"
        ),
    }

    atomic_json(PILOT_OUTPUT, output)
    return output


def execute_production(*, authorization: bool) -> dict:
    # Deliberately blocked even when called programmatically at this stage.
    raise A2ProductionExecutionNotAuthorized(
        "A2_FULL_PRODUCTION_PROPAGATION_NOT_AUTHORIZED"
    )


def offline_validate() -> dict:
    verify_parent_boundary()
    frozen_hashes = verify_frozen_hashes()
    environment = verify_environment()

    require(DB.is_file(), "materialized DB is missing")

    ignore = git(
        "check-ignore",
        "-q",
        DB.relative_to(ROOT).as_posix(),
        check=False,
    )
    require(ignore.returncode == 0, "materialized DB is not Git-ignored")

    require(
        not PILOT_OUTPUT.exists(),
        "pilot output already exists before authorization",
    )

    manifest = load_manifest()
    manifest_check = verify_manifest_against_db(manifest)

    with MATERIALIZATION_SUMMARY.open("r", encoding="utf-8") as stream:
        materialization_summary = json.load(stream)
    with MATERIALIZATION_INDEPENDENT_AUDIT.open("r", encoding="utf-8") as stream:
        materialization_audit = json.load(stream)

    require(materialization_summary["status"] == "PASS", "materialization summary lost PASS")
    require(
        materialization_audit["status"]
        == "G7R6_A2_MATERIALIZATION_INDEPENDENT_AUDIT_PASS",
        "materialization independent audit lost PASS",
    )
    require(
        materialization_summary["materialized_db_sha256"] == EXPECTED_DB_SHA256,
        "materialization summary DB binding changed",
    )
    require(
        materialization_audit["materialized_db_sha256"] == EXPECTED_DB_SHA256,
        "independent audit DB binding changed",
    )

    _g6, _s, constants = import_execution_dependencies()

    pilot_blocked = False
    try:
        execute_pilot(authorization=False)
    except A2PilotExecutionNotAuthorized:
        pilot_blocked = True
    require(pilot_blocked, "unauthorized A2 pilot execution was not blocked")

    production_blocked = False
    try:
        execute_production(authorization=False)
    except A2ProductionExecutionNotAuthorized:
        production_blocked = True
    require(production_blocked, "unauthorized A2 production execution was not blocked")

    result = {
        "status": "G7R6_A2_CONTROLLER_OFFLINE_VALIDATION_PASS",
        "parent_materialization_commit": PARENT_COMMIT,
        "parent_materialization_tag": PARENT_TAG,
        "controller_sha256": sha256(Path(__file__)),
        "pilot_manifest_sha256": EXPECTED_PILOT_MANIFEST_SHA256,
        "materialized_db_expected_sha256": EXPECTED_DB_SHA256,
        "frozen_dependency_hashes": frozen_hashes,
        "environment": environment,
        "numerical_constants": constants,
        "manifest_check": manifest_check,
        "pilot_event_count": EXPECTED_EVENTS,
        "pilot_replay_count": EXPECTED_PILOT_REPLAYS,
        "pilot_execution_blocked_without_authorization": pilot_blocked,
        "full_production_blocked": production_blocked,
        "raw_GP_HISTORY_opened": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_performed": False,
        "network_requests": 0,
        "next_safe_action":
            "INDEPENDENT_CONTROLLER_IMPLEMENTATION_REVIEW_BEFORE_FREEZE",
    }

    atomic_json(OFFLINE_VALIDATION, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--offline-validate", action="store_true")
    group.add_argument("--run-pilot", action="store_true")
    group.add_argument("--run-production", action="store_true")
    args = parser.parse_args()

    if args.offline_validate:
        offline_validate()
        return 0

    if args.run_pilot:
        execute_pilot(authorization=False)
        return 0

    if args.run_production:
        execute_production(authorization=False)
        return 0

    raise A2ControllerError("unreachable command state")


if __name__ == "__main__":
    raise SystemExit(main())
