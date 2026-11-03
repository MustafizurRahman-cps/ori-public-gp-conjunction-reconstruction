#!/usr/bin/env python3
"""Independent post-execution audit of the frozen G7 R6 A2 28-replay pilot.

The audit:
  * verifies the authorization commit/tag and frozen artifact hashes;
  * verifies the pilot-output and execution-audit cryptographic linkage;
  * re-hashes the finalized materialized SQLite database;
  * reconstructs the exact 28 frozen replay identities from the manifest/DB;
  * independently reruns the frozen G4/G5 candidate-enumeration path for all
    28 replay instances WITHOUT importing the A2 controller;
  * independently applies the frozen 5-minute reference-TCA association rule;
  * compares recomputed status/count/association quantities to pilot output.

This is an implementation/reproducibility audit. It does not estimate A2
scientific accuracy, does not access reference miss distance, does not refit
CAL0, does not open raw GP_HISTORY, and does not authorize production.
"""

from __future__ import annotations

import csv
import hashlib
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

AUTHORIZATION_COMMIT = "eb5d9e14a06b0cb9a51fb2b6b2190979f61ed676"
AUTHORIZATION_PARENT = "e9882c325306d0f7b779237a49b7bd0ed0bf05fd"
AUTHORIZATION_TAG = "g7-r6-a2-pilot-authorized"

DB = ROOT / "data/raw/g7/G7R6_A2_Materialized.sqlite3"
MANIFEST = ROOT / "results/g7/G7R6_A2_Predetermined_Pilot_Manifest.json"
PILOT_OUTPUT = ROOT / "results/g7/G7R6_A2_Pilot_Execution.json"
EXECUTION_AUDIT = ROOT / "results/g7/G7R6_A2_Pilot_Authorization_Execution_Audit.json"

DR0060 = ROOT / "docs/research/DR-0060_G7_R6_A2_28_Replay_Pilot_Execution_Authorization.md"
LAUNCHER = ROOT / "scripts/g7/run_g7r6_a2_pilot_authorization.py"
CONTROLLER = ROOT / "scripts/g7/run_g7r6_a2_controller.py"
G4_NUMERICS = ROOT / "scripts/g4/numerics.py"
G5_CONTROLFLOW = ROOT / "scripts/g5/select_lstar_controlflow_amended.py"
G6_CANDIDATE = ROOT / "scripts/g6/generate_g6_preoutcome_candidates.py"

OUT = ROOT / "results/g7/G7R6_A2_Pilot_Independent_Audit.json"

EXPECTED_HASHES = {
    DR0060:
        "41fcd5380dcd2004f4a88cadc28679b16cb6d854e1563acd183a2f7a46ada6bc",
    LAUNCHER:
        "91d8c7358bed926340d72fa5d912cb9c5cc64bc19157d0e88e4a7ae570cc30a5",
    CONTROLLER:
        "806aff3b06c6efd82726a4d93f0a0ad9e3ab8314e9f5904e4707e38680c4f8de",
    MANIFEST:
        "c3c803e9957a1241854fa81f3e51bbe9923d7710c866fa34e08d79d907ac8cac",
    G4_NUMERICS:
        "dc8de410b89e05ffd829563b4996b06703c31236e9a10fb56bd7f4e690048406",
    G5_CONTROLFLOW:
        "553578f4e90b044c6a783209a756ea3accc009ecbb7a09853e8cae46503f33c2",
    G6_CANDIDATE:
        "bce2a4b5e4dab8d0c4e461e2acb4dbac8afe22d3ac412922ba3670a832adcecb",
    PILOT_OUTPUT:
        "b471ca2d8bafdeeee9e6129139653ad9cc6cfdc53093abdb87ae923b75ef2d12",
    EXECUTION_AUDIT:
        "36ad49b2377c88b8484f16f4a39e511add329d2a91aeef59142504b7ce9b4f01",
}

EXPECTED_DB_SHA256 = (
    "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"
)

EXPECTED_PACKAGES = {
    "numpy": "2.3.5",
    "scipy": "1.18.1",
    "sgp4": "2.27",
}

EXPECTED_EVENTS = 12
EXPECTED_REPLAYS = 28
EXPECTED_UNIQUE_PAYLOADS = 56
EXPECTED_PREOUTCOME = {"CANDIDATE_SET_READY": 28}
EXPECTED_ASSOCIATION = {
    "UNASSOCIATED_REFERENCE_EVENT": 2,
    "VALID_ASSOCIATED": 26,
}
ASSOCIATION_TOLERANCE_MIN = 5.0
PRIMARY_HORIZON_S = 432000.0


class PilotAuditError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PilotAuditError(message)


def sha256(path: Path, *, progress: bool = False) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    next_report = 256 * 1024 * 1024

    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
            done += len(block)
            if progress and done >= next_report:
                print(
                    "A2_POSTPILOT_DB_HASH_PROGRESS = "
                    f"{done}/{total} bytes",
                    flush=True,
                )
                next_report += 256 * 1024 * 1024

    return digest.hexdigest()


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
    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def verify_repository_and_hashes() -> dict[str, str]:
    require(
        git_value("rev-parse", "HEAD") == AUTHORIZATION_COMMIT,
        "post-pilot audit must run at the frozen authorization commit",
    )
    require(
        git_value("rev-list", "-n", "1", AUTHORIZATION_TAG)
        == AUTHORIZATION_COMMIT,
        "pilot authorization tag target changed",
    )
    require(
        git_value("rev-parse", "HEAD^") == AUTHORIZATION_PARENT,
        "pilot authorization commit parent changed",
    )
    require(not OUT.exists(), "independent post-pilot audit already exists")

    observed = {}
    for path, expected in EXPECTED_HASHES.items():
        require(path.is_file(), f"required audit artifact missing: {path}")
        actual = sha256(path)
        require(
            actual == expected,
            "artifact SHA mismatch\n"
            f"path={path.relative_to(ROOT).as_posix()}\n"
            f"actual={actual}\nexpected={expected}",
        )
        observed[path.relative_to(ROOT).as_posix()] = actual

    return observed


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


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def verify_execution_evidence(
    manifest: dict,
    pilot: dict,
    execution: dict,
) -> dict:
    require(
        manifest["status"] == "G7R6_A2_PREDETERMINED_PILOT_MANIFEST",
        "pilot manifest status changed",
    )
    require(
        int(manifest["selected_event_count"]) == EXPECTED_EVENTS,
        "pilot manifest event count changed",
    )
    require(
        int(manifest["selected_pilot_replay_count"]) == EXPECTED_REPLAYS,
        "pilot manifest replay count changed",
    )
    require(
        manifest["materialized_db_sha256"] == EXPECTED_DB_SHA256,
        "pilot manifest DB identity changed",
    )

    require(
        pilot["status"] == "G7R6_A2_PILOT_PASS",
        "pilot controller status is not PASS",
    )
    require(
        pilot["analysis_role"]
        == "A2_PREDETERMINED_IMPLEMENTATION_EQUIVALENCE_PILOT",
        "pilot analysis role changed",
    )
    require(
        int(pilot["pilot_replays_executed"]) == EXPECTED_REPLAYS,
        "pilot replay count changed",
    )
    require(
        int(pilot["implementation_equivalence_mismatches"]) == 0,
        "pilot reports implementation-equivalence mismatch",
    )
    require(
        pilot["materialized_db_sha256"] == EXPECTED_DB_SHA256,
        "pilot DB identity changed",
    )
    require(
        pilot["pilot_manifest_sha256"] == EXPECTED_HASHES[MANIFEST],
        "pilot manifest binding changed",
    )
    require(
        pilot["controller_sha256"] == EXPECTED_HASHES[CONTROLLER],
        "pilot controller binding changed",
    )

    require(
        pilot["preoutcome_status_counts"] == EXPECTED_PREOUTCOME,
        "pilot pre-outcome status distribution changed",
    )
    require(
        pilot["association_status_counts"] == EXPECTED_ASSOCIATION,
        "pilot association status distribution changed",
    )

    operations = pilot["operations"]
    require(
        operations["raw_GP_HISTORY_opened"] is False,
        "pilot reports raw GP_HISTORY access",
    )
    require(
        operations["SGP4_orbital_propagation_performed"] is True,
        "pilot did not report authorized SGP4 propagation",
    )
    require(
        operations["candidate_generation_performed"] is True,
        "pilot did not report candidate generation",
    )
    require(
        operations["independent_direct_equivalence_path_performed"] is True,
        "pilot did not report second control path",
    )
    require(
        operations["reference_association_performed"] is True,
        "pilot did not report reference association",
    )
    require(
        operations["reference_miss_distance_accessed"] is False,
        "pilot reports reference miss-distance access",
    )
    require(
        operations["CAL0_refit_performed"] is False,
        "pilot reports CAL0 refit",
    )
    require(
        int(operations["network_requests"]) == 0,
        "pilot reports network access",
    )

    require(
        execution["status"]
        == "G7R6_A2_PILOT_AUTHORIZATION_EXECUTION_COMPLETED",
        "authorization execution audit status changed",
    )
    require(
        execution["authorization_commit"] == AUTHORIZATION_COMMIT,
        "execution audit authorization commit changed",
    )
    require(
        execution["authorization_parent_commit"] == AUTHORIZATION_PARENT,
        "execution audit authorization parent changed",
    )
    require(
        execution["authorization_tag"] == AUTHORIZATION_TAG,
        "execution audit authorization tag changed",
    )
    require(
        execution["authorization_tag_target"] == AUTHORIZATION_COMMIT,
        "execution audit tag target changed",
    )
    require(
        execution["pilot_output_sha256"] == EXPECTED_HASHES[PILOT_OUTPUT],
        "execution audit pilot-output hash binding changed",
    )
    require(
        execution["controller_sha256"] == EXPECTED_HASHES[CONTROLLER],
        "execution audit controller binding changed",
    )
    require(
        execution["pilot_manifest_sha256"] == EXPECTED_HASHES[MANIFEST],
        "execution audit manifest binding changed",
    )
    require(
        int(execution["pilot_replays_executed"]) == EXPECTED_REPLAYS,
        "execution audit replay count changed",
    )
    require(
        int(execution["implementation_equivalence_mismatches"]) == 0,
        "execution audit reports equivalence mismatch",
    )
    require(
        execution["full_production_authorized"] is False,
        "execution audit unexpectedly authorizes production",
    )

    return {
        "pilot_status": pilot["status"],
        "pilot_replays_executed": int(pilot["pilot_replays_executed"]),
        "implementation_equivalence_mismatches":
            int(pilot["implementation_equivalence_mismatches"]),
        "preoutcome_status_counts": pilot["preoutcome_status_counts"],
        "association_status_counts": pilot["association_status_counts"],
        "pilot_output_sha256": EXPECTED_HASHES[PILOT_OUTPUT],
        "execution_audit_sha256": EXPECTED_HASHES[EXECUTION_AUDIT],
        "pilot_output_execution_audit_hash_link": "PASS",
    }


def connect_db() -> sqlite3.Connection:
    require(DB.is_file(), "materialized DB is missing")
    uri = DB.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def import_frozen_execution_dependencies():
    g6_dir = str(ROOT / "scripts/g6")
    if g6_dir not in sys.path:
        sys.path.insert(0, g6_dir)

    import generate_g6_preoutcome_candidates as g6

    require(
        Path(g6.__file__).resolve() == G6_CANDIDATE.resolve(),
        "unexpected G6 candidate module imported",
    )
    require(
        sha256(Path(g6.__file__)) == EXPECTED_HASHES[G6_CANDIDATE],
        "imported G6 candidate module hash changed",
    )

    s = g6.import_frozen_controlflow()

    require(
        float(s.g4.PRIMARY_HORIZON_S) == PRIMARY_HORIZON_S,
        "primary candidate-search horizon changed",
    )

    constants = g6.verify_numerical_constants(s)

    return g6, s, constants


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


def independently_reconstruct_one(
    g6,
    s,
    connection: sqlite3.Connection,
    event_item: dict,
    replay_item: dict,
) -> dict:
    event_index = int(event_item["event_index"])
    replay_sequence = int(replay_item["replay_sequence"])

    event = connection.execute(
        "SELECT * FROM events WHERE event_index=?",
        (event_index,),
    ).fetchone()
    require(event is not None, f"event missing from materialized DB: {event_index}")

    replay = connection.execute(
        """
        SELECT *
        FROM replay_updates
        WHERE event_index=? AND replay_sequence=?
        """,
        (event_index, replay_sequence),
    ).fetchone()
    require(
        replay is not None,
        f"replay missing from materialized DB: {event_index}/{replay_sequence}",
    )
    require(
        int(replay["temporal_support"]) == 1,
        f"pilot replay lacks temporal support: {event_index}/{replay_sequence}",
    )
    require(
        str(replay["pair_parse_status"]) == "PARSE_READY_BOTH",
        f"pilot replay lost parse-ready status: {event_index}/{replay_sequence}",
    )

    payload1 = connection.execute(
        "SELECT * FROM support_payloads WHERE record_key=?",
        (str(replay["obj1_record_key"]),),
    ).fetchone()
    payload2 = connection.execute(
        "SELECT * FROM support_payloads WHERE record_key=?",
        (str(replay["obj2_record_key"]),),
    ).fetchone()
    require(
        payload1 is not None and payload2 is not None,
        "selected pilot support payload missing",
    )
    require(
        str(payload1["parse_status"]) == "SUCCESS"
        and str(payload2["parse_status"]) == "SUCCESS",
        "selected pilot payload no longer parses successfully",
    )
    require(
        str(payload1["source_class"]) == "MAIN_WINDOW"
        and str(payload2["source_class"]) == "MAIN_WINDOW",
        "selected pilot payload unexpectedly comes from predecessor/non-main source",
    )

    replay_us = int(replay["replay_us"])
    replay_dt = us_to_datetime(replay_us)
    obj1 = str(event["obj1"])
    obj2 = str(event["obj2"])

    # Bind exact manifest identities to the materialized database.
    require(
        str(event["conj_id"]) == str(event_item["conj_id"]),
        "manifest/DB conjunction identity mismatch",
    )
    require(
        int(event["expected_support_replays"]) == int(event_item["support_count"]),
        "manifest/DB support-count mismatch",
    )
    require(
        int(replay["replay_us"]) == int(replay_item["replay_us"]),
        "manifest/DB replay timestamp mismatch",
    )
    require(
        str(replay["obj1_record_key"]) == str(replay_item["obj1_record_key"]),
        "manifest/DB obj1 record-key mismatch",
    )
    require(
        str(replay["obj2_record_key"]) == str(replay_item["obj2_record_key"]),
        "manifest/DB obj2 record-key mismatch",
    )
    require(
        str(replay["obj1_gp_id"]) == str(replay_item["obj1_GP_ID"]),
        "manifest/DB obj1 GP_ID mismatch",
    )
    require(
        str(replay["obj2_gp_id"]) == str(replay_item["obj2_GP_ID"]),
        "manifest/DB obj2 GP_ID mismatch",
    )

    cache = {
        obj1: payload_to_cache_item(payload1, replay_us),
        obj2: payload_to_cache_item(payload2, replay_us),
    }

    try:
        record1 = g6.make_gp_record(s, obj1, cache[obj1])
        record2 = g6.make_gp_record(s, obj2, cache[obj2])
        sat1 = s.satrec_from_record(record1)
        sat2 = s.satrec_from_record(record2)
    except (s.g4.ParseError, ValueError, TypeError) as exc:
        return {
            "preoutcome_status": "GP_PARSE_FAILURE",
            "candidate_count": 0,
            "association_status": "NOT_APPLICABLE_PREOUTCOME_FAILURE",
            "selected_candidate_index": None,
            "predicted_tca_utc": None,
            "predicted_miss_distance_km": None,
            "signed_tca_residual_s": None,
            "failure_type": type(exc).__name__,
        }

    try:
        quantity = s.g4.Sgp4PairQuantity(
            sat1,
            sat2,
            replay_dt,
        )
        enumeration = s.enumerate_candidates_redundancy_safe(quantity)
    except (s.UnrecoveredRefinementFailure, s.g4.OptimizationError) as exc:
        return {
            "preoutcome_status": "UNRECOVERED_REFINEMENT_FAILURE",
            "candidate_count": 0,
            "association_status": "NOT_APPLICABLE_PREOUTCOME_FAILURE",
            "selected_candidate_index": None,
            "predicted_tca_utc": None,
            "predicted_miss_distance_km": None,
            "signed_tca_residual_s": None,
            "failure_type": type(exc).__name__,
        }
    except (
        s.g4.VerificationError,
        FloatingPointError,
        OverflowError,
        ValueError,
    ) as exc:
        return {
            "preoutcome_status": "PROPAGATION_FAILURE",
            "candidate_count": 0,
            "association_status": "NOT_APPLICABLE_PREOUTCOME_FAILURE",
            "selected_candidate_index": None,
            "predicted_tca_utc": None,
            "predicted_miss_distance_km": None,
            "signed_tca_residual_s": None,
            "failure_type": type(exc).__name__,
        }

    status = (
        "CANDIDATE_SET_READY"
        if enumeration.candidates
        else "NO_VALID_CANDIDATE"
    )

    association_status = "NOT_APPLICABLE_PREOUTCOME_FAILURE"
    selected_candidate_index = None
    predicted_tca_utc = None
    predicted_miss_distance_km = None
    signed_tca_residual_s = None

    if status == "CANDIDATE_SET_READY":
        reference_time_s = (
            int(event["reference_us"]) - replay_us
        ) / 1_000_000.0

        association = s.g4.associate_reference_event(
            enumeration.candidates,
            reference_time_s,
            ASSOCIATION_TOLERANCE_MIN,
        )

        association_status = str(association.status)

        if association.candidate is not None:
            candidates = list(enumeration.candidates)
            position = candidates.index(association.candidate)
            selected_candidate_index = position + 1
            predicted_tca_utc = iso_utc(
                replay_dt
                + timedelta(seconds=float(association.candidate.time_s))
            )
            predicted_miss_distance_km = float(
                association.candidate.distance_km
            )
            signed_tca_residual_s = float(
                association.tca_error_s
            )

    return {
        "preoutcome_status": status,
        "candidate_count": len(enumeration.candidates),
        "association_status": association_status,
        "selected_candidate_index": selected_candidate_index,
        "predicted_tca_utc": predicted_tca_utc,
        "predicted_miss_distance_km": predicted_miss_distance_km,
        "signed_tca_residual_s": signed_tca_residual_s,
        "sampled_count": int(enumeration.sampled_count),
        "raw_candidate_count": len(enumeration.raw_candidates),
        "unresolved_boundary_count": int(enumeration.unresolved_boundary_count),
        "rejected_local_inconsistency_count":
            int(enumeration.rejected_local_inconsistency_count),
        "recovered_local_inconsistency_count":
            int(enumeration.recovered_local_inconsistency_count),
    }


def floats_identical(first, second) -> bool:
    if first is None or second is None:
        return first is None and second is None
    return float(first) == float(second)


def audit_replay_results(
    manifest: dict,
    pilot: dict,
    environment: dict,
) -> dict:
    require(
        pilot["environment"]["python"] == environment["python"],
        "pilot/audit Python version differs",
    )
    require(
        pilot["environment"]["packages"] == environment["packages"],
        "pilot/audit package versions differ",
    )

    g6, s, constants = import_frozen_execution_dependencies()

    require(
        pilot["numerical_constants"] == constants,
        "pilot numerical constants differ from independently loaded frozen constants",
    )
    require(
        float(pilot["candidate_horizon_days"]) == 5.0,
        "pilot candidate horizon changed",
    )
    require(
        float(pilot["association_tolerance_min"]) == ASSOCIATION_TOLERANCE_MIN,
        "pilot association tolerance changed",
    )

    expected_rows = []
    for event_item in manifest["selected_events"]:
        for replay_item in event_item["pilot_replays"]:
            expected_rows.append((event_item, replay_item))

    require(
        len(expected_rows) == EXPECTED_REPLAYS,
        "flattened manifest replay count changed",
    )
    require(
        len(pilot["results"]) == EXPECTED_REPLAYS,
        "pilot result-row count changed",
    )

    connection = connect_db()
    mismatches = []
    independently_recomputed = []
    unique_payloads = set()
    started = time.perf_counter()

    try:
        for index, ((event_item, replay_item), observed) in enumerate(
            zip(expected_rows, pilot["results"], strict=True),
            start=1,
        ):
            identity_fields = {
                "selection_group": event_item["selection_group"],
                "selection_rank": int(event_item["selection_rank"]),
                "pilot_position": replay_item["pilot_position"],
                "event_index": int(event_item["event_index"]),
                "conj_id": str(event_item["conj_id"]),
                "support_count": int(event_item["support_count"]),
                "replay_sequence": int(replay_item["replay_sequence"]),
                "obj1_record_key": str(replay_item["obj1_record_key"]),
                "obj2_record_key": str(replay_item["obj2_record_key"]),
                "obj1_GP_ID": str(replay_item["obj1_GP_ID"]),
                "obj2_GP_ID": str(replay_item["obj2_GP_ID"]),
            }

            row_mismatches = []

            for field, expected in identity_fields.items():
                if observed[field] != expected:
                    row_mismatches.append(
                        f"{field}: observed={observed[field]!r}, expected={expected!r}"
                    )

            unique_payloads.add(identity_fields["obj1_record_key"])
            unique_payloads.add(identity_fields["obj2_record_key"])

            recomputed = independently_reconstruct_one(
                g6,
                s,
                connection,
                event_item,
                replay_item,
            )

            comparison_fields = (
                ("g6_preoutcome_status", "preoutcome_status"),
                ("candidate_count", "candidate_count"),
                ("association_status", "association_status"),
                ("selected_candidate_index", "selected_candidate_index"),
                ("predicted_tca_utc", "predicted_tca_utc"),
            )

            for observed_field, recomputed_field in comparison_fields:
                if observed[observed_field] != recomputed[recomputed_field]:
                    row_mismatches.append(
                        f"{observed_field}: observed={observed[observed_field]!r}, "
                        f"recomputed={recomputed[recomputed_field]!r}"
                    )

            for observed_field, recomputed_field in (
                ("predicted_miss_distance_km", "predicted_miss_distance_km"),
                ("signed_tca_residual_s", "signed_tca_residual_s"),
            ):
                if not floats_identical(
                    observed[observed_field],
                    recomputed[recomputed_field],
                ):
                    row_mismatches.append(
                        f"{observed_field}: observed={observed[observed_field]!r}, "
                        f"recomputed={recomputed[recomputed_field]!r}"
                    )

            if observed["independent_direct_preoutcome_status"] != recomputed["preoutcome_status"]:
                row_mismatches.append(
                    "independent_direct_preoutcome_status differs from "
                    "independent post-pilot recomputation"
                )

            if observed["implementation_equivalent"] is not True:
                row_mismatches.append("pilot implementation_equivalent is not true")

            independently_recomputed.append(
                {
                    "event_index": identity_fields["event_index"],
                    "replay_sequence": identity_fields["replay_sequence"],
                    "preoutcome_status": recomputed["preoutcome_status"],
                    "candidate_count": recomputed["candidate_count"],
                    "association_status": recomputed["association_status"],
                    "selected_candidate_index":
                        recomputed["selected_candidate_index"],
                    "predicted_tca_utc": recomputed["predicted_tca_utc"],
                    "predicted_miss_distance_km":
                        recomputed["predicted_miss_distance_km"],
                    "signed_tca_residual_s":
                        recomputed["signed_tca_residual_s"],
                }
            )

            if row_mismatches:
                mismatches.append(
                    {
                        "pilot_row": index,
                        "event_index": identity_fields["event_index"],
                        "replay_sequence": identity_fields["replay_sequence"],
                        "mismatches": row_mismatches,
                    }
                )

            print(
                "A2_POSTPILOT_REPLAY_AUDIT_PROGRESS = "
                f"{index}/{EXPECTED_REPLAYS}; "
                f"event={identity_fields['event_index']}; "
                f"replay={identity_fields['replay_sequence']}; "
                f"mismatches={len(row_mismatches)}",
                flush=True,
            )

    finally:
        connection.close()

    elapsed = time.perf_counter() - started

    require(
        len(unique_payloads) == EXPECTED_UNIQUE_PAYLOADS,
        f"pilot unique selected-payload count changed: {len(unique_payloads)}",
    )

    recomputed_preoutcome = dict(sorted(Counter(
        row["preoutcome_status"] for row in independently_recomputed
    ).items()))
    recomputed_association = dict(sorted(Counter(
        row["association_status"] for row in independently_recomputed
    ).items()))

    # Canonical digest of the independent result set.
    canonical_bytes = json.dumps(
        independently_recomputed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    independent_result_sha = hashlib.sha256(canonical_bytes).hexdigest()

    return {
        "replay_rows_audited": EXPECTED_REPLAYS,
        "unique_selected_payloads": len(unique_payloads),
        "exact_result_mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "independently_recomputed_preoutcome_status_counts":
            recomputed_preoutcome,
        "independently_recomputed_association_status_counts":
            recomputed_association,
        "independent_recomputed_result_sha256": independent_result_sha,
        "independent_recomputation_wall_seconds": elapsed,
    }


def main() -> int:
    print("A2_INDEPENDENT_POST_PILOT_AUDIT_START = true")
    print("RAW_GP_HISTORY_OPENED = false")
    print("REFERENCE_MISS_DISTANCE_ACCESSED = false")
    print("FULL_PRODUCTION_AUTHORIZED = false")

    frozen_hashes = verify_repository_and_hashes()
    environment = verify_environment()

    manifest = load_json(MANIFEST)
    pilot = load_json(PILOT_OUTPUT)
    execution = load_json(EXECUTION_AUDIT)

    evidence = verify_execution_evidence(
        manifest,
        pilot,
        execution,
    )

    actual_db_sha = sha256(DB, progress=True)
    require(
        actual_db_sha == EXPECTED_DB_SHA256,
        "materialized DB SHA mismatch during independent post-pilot audit",
    )

    print("A2_POSTPILOT_DB_SHA256 = PASS")

    replay_audit = audit_replay_results(
        manifest,
        pilot,
        environment,
    )

    status = (
        "G7R6_A2_PILOT_INDEPENDENT_AUDIT_PASS"
        if replay_audit["exact_result_mismatch_count"] == 0
        else "G7R6_A2_PILOT_INDEPENDENT_AUDIT_HOLD"
    )

    result = {
        "status": status,
        "authorization_commit": AUTHORIZATION_COMMIT,
        "authorization_parent_commit": AUTHORIZATION_PARENT,
        "authorization_tag": AUTHORIZATION_TAG,
        "materialized_db_sha256": actual_db_sha,
        "frozen_artifact_hashes": frozen_hashes,
        "environment": environment,
        "execution_evidence": evidence,
        "independent_replay_audit": replay_audit,
        "operations_performed_by_independent_audit": {
            "raw_GP_HISTORY_opened": False,
            "materialized_SQLite_read": True,
            "materialized_SQLite_full_SHA256": True,
            "SGP4_orbital_propagation_performed": True,
            "candidate_generation_performed": True,
            "reference_association_performed": True,
            "reference_miss_distance_accessed": False,
            "CAL0_refit_performed": False,
            "network_requests": 0,
        },
        "interpretation": (
            "This audit independently reproduces the 28 predetermined pilot "
            "replay results from the frozen materialized inputs and frozen "
            "G4/G5/G6 numerical primitives. PASS is implementation and "
            "reproducibility evidence only; it is not an A2 scientific "
            "accuracy estimate and does not itself authorize full production."
        ),
        "full_production_authorized": False,
        "A0_execution_authorized": False,
        "next_safe_action": (
            "FREEZE_PILOT_EXECUTION_AND_INDEPENDENT_AUDIT_EVIDENCE"
            if status == "G7R6_A2_PILOT_INDEPENDENT_AUDIT_PASS"
            else "HOLD_AND_REVIEW_POST_PILOT_MISMATCH"
        ),
    }

    atomic_json(OUT, result)

    print(json.dumps(
        {
            "status": status,
            "pilot_replays_audited":
                replay_audit["replay_rows_audited"],
            "unique_selected_payloads":
                replay_audit["unique_selected_payloads"],
            "exact_result_mismatch_count":
                replay_audit["exact_result_mismatch_count"],
            "independently_recomputed_preoutcome_status_counts":
                replay_audit[
                    "independently_recomputed_preoutcome_status_counts"
                ],
            "independently_recomputed_association_status_counts":
                replay_audit[
                    "independently_recomputed_association_status_counts"
                ],
            "independent_recomputed_result_sha256":
                replay_audit["independent_recomputed_result_sha256"],
            "pilot_output_sha256": EXPECTED_HASHES[PILOT_OUTPUT],
            "execution_audit_sha256": EXPECTED_HASHES[EXECUTION_AUDIT],
            "materialized_db_sha256": actual_db_sha,
            "raw_GP_HISTORY_opened": False,
            "reference_miss_distance_accessed": False,
            "full_production_authorized": False,
            "A0_execution_authorized": False,
            "output": OUT.relative_to(ROOT).as_posix(),
            "output_sha256": sha256(OUT),
            "next_safe_action": result["next_safe_action"],
        },
        indent=2,
        sort_keys=True,
    ))

    return 0 if status == "G7R6_A2_PILOT_INDEPENDENT_AUDIT_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
