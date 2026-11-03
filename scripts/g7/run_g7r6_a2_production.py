#!/usr/bin/env python3
"""G7 R6 A2 full-production execution implementation.

This module implements the deterministic, resumable execution machinery for
the already-frozen A2 temporal-support workload.

At this implementation-review stage:
  * --offline-validate is permitted and performs no orbital propagation.
  * --run-production is deliberately blocked at the direct CLI boundary.
  * execute_production(authorization=True) exists only for a later, separately
    reviewed authorization launcher.

Scientific invariants are inherited from the frozen materialized A2 inputs and
the frozen G4/G5/G6 numerical path already exercised by the predetermined
28-replay pilot.

The production workload is exactly the 107,740 temporal-support replay rows in
the frozen materialized SQLite database. Non-support publication updates are
not deleted from the A2 analysis universe; they remain represented by the
frozen materialized schedule and are simply not propagated because they cannot
reach the reference-association interval under frozen H=5 d and tau=5 min.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PARENT_COMMIT = "b257cc1ba6942d6ed0fc9b32502e8ed493650856"
PARENT_TAG = "g7-r6-a2-pilot-execution-frozen"

DB = ROOT / "data/raw/g7/G7R6_A2_Materialized.sqlite3"
WORK_DB = ROOT / "data/raw/g7/G7R6_A2_Production_Work.sqlite3"

CONTROLLER = ROOT / "scripts/g7/run_g7r6_a2_controller.py"
DR0061 = ROOT / "docs/research/DR-0061_G7_R6_A2_Pilot_Execution_and_Independent_Audit_Freeze.md"
PILOT_OUTPUT = ROOT / "results/g7/G7R6_A2_Pilot_Execution.json"
PILOT_INDEPENDENT_AUDIT = ROOT / "results/g7/G7R6_A2_Pilot_Independent_Audit.json"

OFFLINE_VALIDATION = ROOT / "results/g7/G7R6_A2_Production_Implementation_Offline_Validation.json"

FINAL_RESULTS = ROOT / "results/g7/G7R6_A2_Production_Replay_Results.csv"
FINAL_SUMMARY = ROOT / "results/g7/G7R6_A2_Production_Summary.json"
FINAL_EXECUTION_AUDIT = ROOT / "results/g7/G7R6_A2_Production_Execution_Audit.json"

EXPECTED_DB_SHA256 = (
    "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"
)
EXPECTED_CONTROLLER_SHA256 = (
    "806aff3b06c6efd82726a4d93f0a0ad9e3ab8314e9f5904e4707e38680c4f8de"
)
EXPECTED_DR0061_SHA256 = (
    "90bfdeec9d5269cc3e7509db62fd4561f843f47a14f510f6c2037c3ed4431969"
)
EXPECTED_PILOT_OUTPUT_SHA256 = (
    "b471ca2d8bafdeeee9e6129139653ad9cc6cfdc53093abdb87ae923b75ef2d12"
)
EXPECTED_PILOT_INDEPENDENT_AUDIT_SHA256 = (
    "3d2a404c94b207e642c7a406e7051869bfe8896282ff7509f711e28bb37a6bb5"
)

EXPECTED_EVENTS = 6_927
EXPECTED_POSITIVE_SUPPORT_EVENTS = 6_926
EXPECTED_ZERO_SUPPORT_EVENTS = 1
EXPECTED_SUPPORT_REPLAYS = 107_740
EXPECTED_PUBLICATION_REPLAY_OPPORTUNITIES = 2_020_081
EXPECTED_TEMPORAL_NON_SUPPORT = 1_912_341

PRIMARY_HORIZON_S = 432_000.0
ASSOCIATION_TOLERANCE_MIN = 5.0

RESULT_FIELDS = (
    "event_index",
    "conj_id",
    "g6_temporal_stratum",
    "reference_tca_utc",
    "replay_sequence",
    "replay_us",
    "replay_utc",
    "replay_pre_tca",
    "reference_lead_h",
    "obj1",
    "obj2",
    "obj1_record_key",
    "obj2_record_key",
    "obj1_GP_ID",
    "obj2_GP_ID",
    "parse_status",
    "propagation_status",
    "preoutcome_status",
    "rejected_local_inconsistency_count",
    "recovered_local_inconsistency_count",
    "unresolved_boundary_count",
    "sampled_count",
    "raw_candidate_count",
    "candidate_count",
    "candidate_set_sha256",
    "failure_detail",
    "association_status",
    "final_status",
    "selected_candidate_index",
    "selected_candidate_time_from_replay_s",
    "predicted_tca_utc",
    "predicted_miss_distance_km",
    "signed_tca_residual_s",
)

CHECKPOINT_SCHEMA_VERSION = "1"
OUTPUT_SCHEMA_VERSION = "1"


class A2ProductionError(RuntimeError):
    pass


class A2ProductionExecutionNotAuthorized(A2ProductionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise A2ProductionError(message)


def sha256(path: Path, *, progress: bool = False) -> str:
    h = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    next_report = 256 * 1024 * 1024

    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
            done += len(block)
            if progress and done >= next_report:
                print(
                    "A2_PRODUCTION_DB_HASH_PROGRESS = "
                    f"{done}/{total} bytes",
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


def atomic_csv(path: Path, fieldnames: tuple[str, ...], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=fieldnames,
                lineterminator="\n",
                extrasaction="raise",
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def load_controller():
    require(CONTROLLER.is_file(), "frozen A2 controller is missing")
    require(
        sha256(CONTROLLER) == EXPECTED_CONTROLLER_SHA256,
        "frozen A2 controller SHA mismatch",
    )

    spec = importlib.util.spec_from_file_location(
        "g7r6_a2_frozen_controller_dependency",
        CONTROLLER,
    )
    require(
        spec is not None and spec.loader is not None,
        "cannot load frozen A2 controller",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    require(
        Path(module.__file__).resolve() == CONTROLLER.resolve(),
        "unexpected A2 controller module imported",
    )

    return module


def verify_parent_freeze() -> dict[str, str]:
    require(
        git_value("rev-list", "-n", "1", PARENT_TAG) == PARENT_COMMIT,
        "pilot-execution freeze tag target changed",
    )

    expected = {
        DR0061: EXPECTED_DR0061_SHA256,
        PILOT_OUTPUT: EXPECTED_PILOT_OUTPUT_SHA256,
        PILOT_INDEPENDENT_AUDIT: EXPECTED_PILOT_INDEPENDENT_AUDIT_SHA256,
        CONTROLLER: EXPECTED_CONTROLLER_SHA256,
    }

    observed = {}
    for path, expected_sha in expected.items():
        require(path.is_file(), f"frozen production dependency missing: {path}")
        actual = sha256(path)
        require(
            actual == expected_sha,
            "frozen production dependency SHA mismatch\n"
            f"path={path.relative_to(ROOT).as_posix()}\n"
            f"actual={actual}\nexpected={expected_sha}",
        )
        observed[path.relative_to(ROOT).as_posix()] = actual

    with PILOT_INDEPENDENT_AUDIT.open("r", encoding="utf-8") as stream:
        audit = json.load(stream)

    require(
        audit["status"] == "G7R6_A2_PILOT_INDEPENDENT_AUDIT_PASS",
        "frozen pilot independent audit lost PASS",
    )
    require(
        int(audit["independent_replay_audit"]["exact_result_mismatch_count"]) == 0,
        "frozen pilot independent audit no longer has zero mismatches",
    )
    require(
        audit["full_production_authorized"] is False,
        "historical pilot audit unexpectedly says production was authorized",
    )

    return observed


def connect_materialized() -> sqlite3.Connection:
    require(DB.is_file(), "materialized A2 database is missing")
    uri = DB.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def inspect_materialized_schedule() -> dict:
    connection = connect_materialized()
    try:
        event_count = int(
            connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        )
        publication_replays = int(
            connection.execute("SELECT COUNT(*) FROM replay_updates").fetchone()[0]
        )
        support_replays = int(
            connection.execute(
                "SELECT COUNT(*) FROM replay_updates WHERE temporal_support=1"
            ).fetchone()[0]
        )
        non_support = int(
            connection.execute(
                "SELECT COUNT(*) FROM replay_updates WHERE temporal_support=0"
            ).fetchone()[0]
        )
        positive_support_events = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM events
                WHERE expected_support_replays > 0
                """
            ).fetchone()[0]
        )
        zero_support_events = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM events
                WHERE expected_support_replays = 0
                """
            ).fetchone()[0]
        )
        support_sum = int(
            connection.execute(
                "SELECT COALESCE(SUM(expected_support_replays),0) FROM events"
            ).fetchone()[0]
        )
        non_parse_ready_support = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM replay_updates
                WHERE temporal_support=1
                  AND pair_parse_status <> 'PARSE_READY_BOTH'
                """
            ).fetchone()[0]
        )
    finally:
        connection.close()

    require(event_count == EXPECTED_EVENTS, "materialized event census changed")
    require(
        publication_replays == EXPECTED_PUBLICATION_REPLAY_OPPORTUNITIES,
        "publication replay schedule count changed",
    )
    require(
        support_replays == EXPECTED_SUPPORT_REPLAYS,
        "temporal-support replay count changed",
    )
    require(
        non_support == EXPECTED_TEMPORAL_NON_SUPPORT,
        "temporal non-support replay count changed",
    )
    require(
        positive_support_events == EXPECTED_POSITIVE_SUPPORT_EVENTS,
        "positive-support event count changed",
    )
    require(
        zero_support_events == EXPECTED_ZERO_SUPPORT_EVENTS,
        "zero-support event count changed",
    )
    require(
        support_sum == EXPECTED_SUPPORT_REPLAYS,
        "event support-count sum changed",
    )
    require(
        non_parse_ready_support == 0,
        "a frozen temporal-support replay is no longer PARSE_READY_BOTH",
    )

    return {
        "events": event_count,
        "publication_replay_opportunities": publication_replays,
        "temporal_support_replays": support_replays,
        "temporal_non_support_replays": non_support,
        "positive_support_events": positive_support_events,
        "zero_support_events": zero_support_events,
        "non_parse_ready_support_replays": non_parse_ready_support,
    }


def expected_checkpoint_metadata(executor_sha: str) -> dict[str, str]:
    return {
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "executor_sha256": executor_sha,
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "materialized_db_sha256": EXPECTED_DB_SHA256,
        "controller_sha256": EXPECTED_CONTROLLER_SHA256,
        "expected_events": str(EXPECTED_EVENTS),
        "expected_positive_support_events": str(EXPECTED_POSITIVE_SUPPORT_EVENTS),
        "expected_support_replays": str(EXPECTED_SUPPORT_REPLAYS),
        "ordering": "event_index,replay_us,replay_sequence",
        "commit_unit": "complete_event",
        "primary_horizon_s": repr(PRIMARY_HORIZON_S),
        "association_tolerance_min": repr(ASSOCIATION_TOLERANCE_MIN),
        "reference_association": "DOWNSTREAM_ONLY_AFTER_IMMUTABLE_CANDIDATE_SET",
        "reference_miss_distance": "PROHIBITED",
        "raw_GP_HISTORY": "PROHIBITED",
        "CAL0_refit": "PROHIBITED",
        "network_access": "PROHIBITED",
        "method_retuning": "PROHIBITED",
    }


def open_checkpoint_at(path: Path, executor_sha: str) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS replay_results (
            event_index INTEGER NOT NULL,
            replay_sequence INTEGER NOT NULL,
            replay_us INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            PRIMARY KEY (event_index, replay_sequence)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS event_completion (
            event_index INTEGER PRIMARY KEY,
            expected_support_replays INTEGER NOT NULL,
            completed_support_replays INTEGER NOT NULL,
            event_result_sha256 TEXT NOT NULL
        )
        """
    )

    expected = expected_checkpoint_metadata(executor_sha)
    existing = dict(
        connection.execute("SELECT key, value FROM metadata").fetchall()
    )

    if existing:
        if existing != expected:
            connection.close()
            raise A2ProductionError(
                "production checkpoint metadata does not match frozen execution boundary"
            )
    else:
        connection.executemany(
            "INSERT INTO metadata(key,value) VALUES (?,?)",
            sorted(expected.items()),
        )
        connection.commit()

    return connection


def open_checkpoint(executor_sha: str) -> sqlite3.Connection:
    return open_checkpoint_at(WORK_DB, executor_sha)


def checkpoint_state(connection: sqlite3.Connection) -> dict:
    replay_rows = int(
        connection.execute("SELECT COUNT(*) FROM replay_results").fetchone()[0]
    )
    completed_events = int(
        connection.execute("SELECT COUNT(*) FROM event_completion").fetchone()[0]
    )

    orphan_rows = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM replay_results r
            LEFT JOIN event_completion e
              ON e.event_index = r.event_index
            WHERE e.event_index IS NULL
            """
        ).fetchone()[0]
    )
    require(orphan_rows == 0, "checkpoint contains replay rows for incomplete events")

    count_mismatches = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM event_completion e
            WHERE e.completed_support_replays <> (
                SELECT COUNT(*)
                FROM replay_results r
                WHERE r.event_index = e.event_index
            )
            OR e.expected_support_replays <> e.completed_support_replays
            """
        ).fetchone()[0]
    )
    require(
        count_mismatches == 0,
        "checkpoint event-completion/replay-row counts are inconsistent",
    )

    duplicate_replay_us = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT event_index, replay_sequence, COUNT(*) AS n
                FROM replay_results
                GROUP BY event_index, replay_sequence
                HAVING n <> 1
            )
            """
        ).fetchone()[0]
    )
    require(duplicate_replay_us == 0, "checkpoint primary-key uniqueness changed")

    return {
        "completed_events": completed_events,
        "completed_replays": replay_rows,
        "orphan_replay_rows": orphan_rows,
        "event_count_mismatches": count_mismatches,
    }


def completed_event_indices(connection: sqlite3.Connection) -> set[int]:
    return {
        int(row[0])
        for row in connection.execute(
            "SELECT event_index FROM event_completion"
        )
    }


def canonical_json_bytes(payload) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def canonical_json_sha(payload) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def commit_event_results(
    connection: sqlite3.Connection,
    event_index: int,
    expected_support_replays: int,
    results: list[dict],
) -> str:
    require(
        len(results) == expected_support_replays,
        f"event {event_index} result count does not match frozen support count",
    )
    require(
        all(int(row["event_index"]) == event_index for row in results),
        "event transaction contains a foreign event index",
    )

    ordered = sorted(
        results,
        key=lambda row: (
            int(row["replay_us"]),
            int(row["replay_sequence"]),
        ),
    )
    event_digest = canonical_json_sha(ordered)

    with connection:
        for row in ordered:
            payload_json = canonical_json_bytes(row).decode("ascii")
            payload_sha = hashlib.sha256(payload_json.encode("ascii")).hexdigest()

            connection.execute(
                """
                INSERT INTO replay_results(
                    event_index,
                    replay_sequence,
                    replay_us,
                    payload_json,
                    payload_sha256
                )
                VALUES (?,?,?,?,?)
                """,
                (
                    event_index,
                    int(row["replay_sequence"]),
                    int(row["replay_us"]),
                    payload_json,
                    payload_sha,
                ),
            )

        connection.execute(
            """
            INSERT INTO event_completion(
                event_index,
                expected_support_replays,
                completed_support_replays,
                event_result_sha256
            )
            VALUES (?,?,?,?)
            """,
            (
                event_index,
                expected_support_replays,
                len(ordered),
                event_digest,
            ),
        )

    return event_digest


def payload_row_to_result(
    controller,
    g6,
    s,
    strata: dict[int, str],
    materialized: sqlite3.Connection,
    event_row: sqlite3.Row,
    replay_row: sqlite3.Row,
) -> dict:
    event_index = int(event_row["event_index"])
    replay_sequence = int(replay_row["replay_sequence"])
    replay_us = int(replay_row["replay_us"])
    replay_dt = controller.us_to_datetime(replay_us)

    obj1 = str(event_row["obj1"])
    obj2 = str(event_row["obj2"])

    payload1 = materialized.execute(
        "SELECT * FROM support_payloads WHERE record_key=?",
        (str(replay_row["obj1_record_key"]),),
    ).fetchone()
    payload2 = materialized.execute(
        "SELECT * FROM support_payloads WHERE record_key=?",
        (str(replay_row["obj2_record_key"]),),
    ).fetchone()

    require(
        payload1 is not None and payload2 is not None,
        f"selected support payload missing at {event_index}/{replay_sequence}",
    )
    require(
        str(payload1["parse_status"]) == "SUCCESS"
        and str(payload2["parse_status"]) == "SUCCESS",
        f"materialized parse readiness changed at {event_index}/{replay_sequence}",
    )

    cache = {
        obj1: controller.payload_to_cache_item(payload1, replay_us),
        obj2: controller.payload_to_cache_item(payload2, replay_us),
    }

    event = {
        "final_g6_sample_index": event_index,
        "g6_temporal_stratum": strata[event_index],
        "conj_id": str(event_row["conj_id"]),
        "obj1": obj1,
        "obj2": obj2,
        "replay_time": replay_dt,
    }

    event_result, candidate_rows = g6.process_event(
        s,
        event,
        cache,
    )

    # Candidate set is immutable before reference information is used below.
    candidate_set_sha = canonical_json_sha(candidate_rows)

    association_status = "NOT_APPLICABLE_PREOUTCOME_FAILURE"
    final_status = str(event_result["preoutcome_status"])
    selected_candidate_index = ""
    selected_candidate_time_from_replay_s = ""
    predicted_tca_utc = ""
    predicted_miss_distance_km = ""
    signed_tca_residual_s = ""

    if event_result["preoutcome_status"] == "CANDIDATE_SET_READY":
        candidates = controller.reconstruct_candidates(
            s.g4,
            candidate_rows,
        )
        reference_time_s = (
            int(event_row["reference_us"]) - replay_us
        ) / 1_000_000.0

        association = s.g4.associate_reference_event(
            candidates,
            reference_time_s,
            ASSOCIATION_TOLERANCE_MIN,
        )
        association_status = str(association.status)
        final_status = association_status

        if association.candidate is not None:
            position = candidates.index(association.candidate)
            selected_candidate_index = int(
                candidate_rows[position]["candidate_index"]
            )
            selected_candidate_time_from_replay_s = repr(
                float(association.candidate.time_s)
            )
            predicted_tca_utc = controller.iso_utc(
                replay_dt
                + timedelta(seconds=float(association.candidate.time_s))
            )
            predicted_miss_distance_km = repr(
                float(association.candidate.distance_km)
            )
            signed_tca_residual_s = repr(
                float(association.tca_error_s)
            )

    reference_dt = controller.us_to_datetime(int(event_row["reference_us"]))
    reference_lead_h = (
        int(event_row["reference_us"]) - replay_us
    ) / 3_600_000_000.0

    return {
        "event_index": event_index,
        "conj_id": str(event_row["conj_id"]),
        "g6_temporal_stratum": strata[event_index],
        "reference_tca_utc": controller.iso_utc(reference_dt),
        "replay_sequence": replay_sequence,
        "replay_us": replay_us,
        "replay_utc": controller.iso_utc(replay_dt),
        "replay_pre_tca": bool(replay_us < int(event_row["reference_us"])),
        "reference_lead_h": repr(float(reference_lead_h)),
        "obj1": obj1,
        "obj2": obj2,
        "obj1_record_key": str(replay_row["obj1_record_key"]),
        "obj2_record_key": str(replay_row["obj2_record_key"]),
        "obj1_GP_ID": str(replay_row["obj1_gp_id"]),
        "obj2_GP_ID": str(replay_row["obj2_gp_id"]),
        "parse_status": str(event_result["parse_status"]),
        "propagation_status": str(event_result["propagation_status"]),
        "preoutcome_status": str(event_result["preoutcome_status"]),
        "rejected_local_inconsistency_count":
            int(event_result["rejected_local_inconsistency_count"]),
        "recovered_local_inconsistency_count":
            int(event_result["recovered_local_inconsistency_count"]),
        "unresolved_boundary_count":
            int(event_result["unresolved_boundary_count"]),
        "sampled_count": int(event_result["sampled_count"]),
        "raw_candidate_count": int(event_result["raw_candidate_count"]),
        "candidate_count": int(event_result["candidate_count"]),
        "candidate_set_sha256": candidate_set_sha,
        "failure_detail": str(event_result["failure_detail"]),
        "association_status": association_status,
        "final_status": final_status,
        "selected_candidate_index": selected_candidate_index,
        "selected_candidate_time_from_replay_s":
            selected_candidate_time_from_replay_s,
        "predicted_tca_utc": predicted_tca_utc,
        "predicted_miss_distance_km": predicted_miss_distance_km,
        "signed_tca_residual_s": signed_tca_residual_s,
    }


def iter_checkpoint_results(
    connection: sqlite3.Connection,
):
    for row in connection.execute(
        """
        SELECT payload_json
        FROM replay_results
        ORDER BY event_index, replay_us, replay_sequence
        """
    ):
        yield json.loads(row[0])


def summarize_checkpoint(
    connection: sqlite3.Connection,
) -> dict:
    preoutcome = Counter()
    association = Counter()
    final_status = Counter()
    candidate_rows = 0
    raw_candidate_rows = 0
    sampled_count = 0
    recovered_local = 0
    rejected_local = 0
    unresolved_boundary = 0

    for row in iter_checkpoint_results(connection):
        preoutcome[row["preoutcome_status"]] += 1
        association[row["association_status"]] += 1
        final_status[row["final_status"]] += 1
        candidate_rows += int(row["candidate_count"])
        raw_candidate_rows += int(row["raw_candidate_count"])
        sampled_count += int(row["sampled_count"])
        recovered_local += int(row["recovered_local_inconsistency_count"])
        rejected_local += int(row["rejected_local_inconsistency_count"])
        unresolved_boundary += int(row["unresolved_boundary_count"])

    return {
        "preoutcome_status_counts": dict(sorted(preoutcome.items())),
        "association_status_counts": dict(sorted(association.items())),
        "final_status_counts": dict(sorted(final_status.items())),
        "candidate_rows_total": candidate_rows,
        "raw_candidate_rows_total": raw_candidate_rows,
        "sampled_grid_points_total": sampled_count,
        "recovered_local_inconsistency_count_total": recovered_local,
        "rejected_local_inconsistency_count_total": rejected_local,
        "unresolved_boundary_count_total": unresolved_boundary,
    }


def write_final_outputs(
    checkpoint: sqlite3.Connection,
    *,
    executor_sha: str,
    db_sha: str,
    started_wall: float,
    resumed_from_events: int,
    resumed_from_replays: int,
    environment: dict,
    frozen_hashes: dict,
    numerical_constants: dict,
) -> dict:
    for path in (
        FINAL_RESULTS,
        FINAL_SUMMARY,
        FINAL_EXECUTION_AUDIT,
    ):
        require(
            not path.exists(),
            f"refusing to overwrite finalized A2 production output: {path}",
        )

    state = checkpoint_state(checkpoint)
    require(
        state["completed_events"] == EXPECTED_POSITIVE_SUPPORT_EVENTS,
        "cannot finalize before every positive-support event is checkpointed",
    )
    require(
        state["completed_replays"] == EXPECTED_SUPPORT_REPLAYS,
        "cannot finalize before all temporal-support replays are checkpointed",
    )

    atomic_csv(
        FINAL_RESULTS,
        RESULT_FIELDS,
        iter_checkpoint_results(checkpoint),
    )

    result_sha = sha256(FINAL_RESULTS)
    checkpoint_summary = summarize_checkpoint(checkpoint)
    wall_seconds = time.perf_counter() - started_wall

    summary = {
        "status": "G7R6_A2_PRODUCTION_EXECUTION_COMPLETED",
        "analysis_role":
            "A2_CAUSAL_SEQUENTIAL_GEOMETRY_TEMPORAL_SUPPORT_EXECUTION",
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "executor_sha256": executor_sha,
        "controller_sha256": EXPECTED_CONTROLLER_SHA256,
        "materialized_db_sha256": db_sha,
        "result_csv_sha256": result_sha,
        "events_total": EXPECTED_EVENTS,
        "positive_support_events": EXPECTED_POSITIVE_SUPPORT_EVENTS,
        "zero_support_events": EXPECTED_ZERO_SUPPORT_EVENTS,
        "publication_replay_opportunities":
            EXPECTED_PUBLICATION_REPLAY_OPPORTUNITIES,
        "temporal_non_support_replays": EXPECTED_TEMPORAL_NON_SUPPORT,
        "temporal_support_replays": EXPECTED_SUPPORT_REPLAYS,
        "replay_results_written": state["completed_replays"],
        "resumed_from_completed_events": resumed_from_events,
        "resumed_from_completed_replays": resumed_from_replays,
        "numerical_constants": numerical_constants,
        "environment": environment,
        "frozen_dependency_hashes": frozen_hashes,
        **checkpoint_summary,
        "wall_seconds_this_invocation": wall_seconds,
        "operations": {
            "raw_GP_HISTORY_opened": False,
            "materialized_SQLite_read": True,
            "SGP4_orbital_propagation_performed": True,
            "candidate_generation_performed": True,
            "reference_association_performed": True,
            "reference_miss_distance_accessed": False,
            "CAL0_refit_performed": False,
            "network_requests": 0,
        },
        "interpretation": (
            "Execution covers exactly the frozen 107,740 temporal-support "
            "replays. The remaining 1,912,341 publication replay opportunities "
            "remain in the frozen schedule but are not propagated because they "
            "cannot reach the frozen reference-association interval. This "
            "summary is execution evidence; scientific S05/S06/S07 and other "
            "A2 metrics require a separately reviewed analysis stage."
        ),
        "next_safe_action":
            "INDEPENDENT_FULL_PRODUCTION_OUTPUT_AUDIT_BEFORE_A2_METRICS",
    }
    atomic_json(FINAL_SUMMARY, summary)
    summary_sha = sha256(FINAL_SUMMARY)

    execution_audit = {
        "status": "G7R6_A2_PRODUCTION_EXECUTION_AUDIT",
        "executor_sha256": executor_sha,
        "controller_sha256": EXPECTED_CONTROLLER_SHA256,
        "materialized_db_sha256": db_sha,
        "work_checkpoint_path": WORK_DB.relative_to(ROOT).as_posix(),
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "result_csv_sha256": result_sha,
        "summary_json_sha256": summary_sha,
        "completed_events": state["completed_events"],
        "completed_replays": state["completed_replays"],
        "full_expected_replays": EXPECTED_SUPPORT_REPLAYS,
        "raw_GP_HISTORY_opened": False,
        "reference_miss_distance_accessed": False,
        "CAL0_refit_performed": False,
        "network_requests": 0,
        "next_safe_action":
            "INDEPENDENT_FULL_PRODUCTION_OUTPUT_AUDIT_BEFORE_A2_METRICS",
    }
    atomic_json(FINAL_EXECUTION_AUDIT, execution_audit)

    return {
        "summary": summary,
        "execution_audit": execution_audit,
        "summary_sha256": summary_sha,
        "execution_audit_sha256": sha256(FINAL_EXECUTION_AUDIT),
    }


def execute_production(*, authorization: bool) -> dict:
    if authorization is not True:
        raise A2ProductionExecutionNotAuthorized(
            "A2_FULL_PRODUCTION_PROPAGATION_NOT_AUTHORIZED"
        )

    started_wall = time.perf_counter()

    frozen_hashes = verify_parent_freeze()

    controller = load_controller()
    environment = controller.verify_environment()
    controller_frozen_hashes = controller.verify_frozen_hashes()
    frozen_hashes.update(
        {
            f"controller_dependency::{key}": value
            for key, value in controller_frozen_hashes.items()
        }
    )

    g6, s, numerical_constants = controller.import_execution_dependencies()
    require(
        float(s.g4.PRIMARY_HORIZON_S) == PRIMARY_HORIZON_S,
        "production candidate horizon changed",
    )

    schedule = inspect_materialized_schedule()

    print("A2_PRODUCTION_MATERIALIZED_SCHEDULE = PASS", flush=True)
    print(
        "A2_PRODUCTION_REPLAYS_EXPECTED = "
        f"{schedule['temporal_support_replays']}",
        flush=True,
    )

    actual_db_sha = sha256(DB, progress=True)
    require(
        actual_db_sha == EXPECTED_DB_SHA256,
        "materialized DB SHA mismatch before production execution",
    )

    print("A2_PRODUCTION_DB_SHA256 = PASS", flush=True)

    for path in (FINAL_RESULTS, FINAL_SUMMARY, FINAL_EXECUTION_AUDIT):
        require(
            not path.exists(),
            f"final production output already exists: {path}",
        )

    executor_sha = sha256(Path(__file__))
    materialized = connect_materialized()
    checkpoint = open_checkpoint(executor_sha)

    try:
        initial_state = checkpoint_state(checkpoint)
        resumed_from_events = initial_state["completed_events"]
        resumed_from_replays = initial_state["completed_replays"]
        completed = completed_event_indices(checkpoint)

        strata = controller.load_strata()

        event_rows = materialized.execute(
            "SELECT * FROM events ORDER BY event_index"
        ).fetchall()

        require(
            len(event_rows) == EXPECTED_EVENTS,
            "materialized event census changed during execution",
        )

        new_events = 0
        new_replays = 0

        for event_row in event_rows:
            event_index = int(event_row["event_index"])
            expected_support = int(event_row["expected_support_replays"])

            if expected_support == 0:
                continue

            if event_index in completed:
                continue

            support_rows = materialized.execute(
                """
                SELECT *
                FROM replay_updates
                WHERE event_index=? AND temporal_support=1
                ORDER BY replay_us, replay_sequence
                """,
                (event_index,),
            ).fetchall()

            require(
                len(support_rows) == expected_support,
                f"support replay count changed for event {event_index}",
            )

            # Compute a whole event first. The checkpoint transaction is then
            # committed atomically. A crash before commit can recompute only
            # that incomplete event; already committed events are never rerun.
            event_results = [
                payload_row_to_result(
                    controller,
                    g6,
                    s,
                    strata,
                    materialized,
                    event_row,
                    replay_row,
                )
                for replay_row in support_rows
            ]

            commit_event_results(
                checkpoint,
                event_index,
                expected_support,
                event_results,
            )

            completed.add(event_index)
            new_events += 1
            new_replays += expected_support

            total_done = resumed_from_replays + new_replays

            if (
                new_events == 1
                or new_events % 100 == 0
                or total_done == EXPECTED_SUPPORT_REPLAYS
            ):
                print(
                    "A2_PRODUCTION_PROGRESS = "
                    f"events={len(completed)}/{EXPECTED_POSITIVE_SUPPORT_EVENTS}; "
                    f"replays={total_done}/{EXPECTED_SUPPORT_REPLAYS}; "
                    f"event={event_index}",
                    flush=True,
                )

        final_state = checkpoint_state(checkpoint)

        require(
            final_state["completed_events"] == EXPECTED_POSITIVE_SUPPORT_EVENTS,
            "production checkpoint did not complete every positive-support event",
        )
        require(
            final_state["completed_replays"] == EXPECTED_SUPPORT_REPLAYS,
            "production checkpoint did not complete every support replay",
        )

        finalized = write_final_outputs(
            checkpoint,
            executor_sha=executor_sha,
            db_sha=actual_db_sha,
            started_wall=started_wall,
            resumed_from_events=resumed_from_events,
            resumed_from_replays=resumed_from_replays,
            environment=environment,
            frozen_hashes=frozen_hashes,
            numerical_constants=numerical_constants,
        )

    finally:
        checkpoint.close()
        materialized.close()

    print(json.dumps(
        {
            "status":
                finalized["summary"]["status"],
            "replay_results_written":
                finalized["summary"]["replay_results_written"],
            "result_csv_sha256":
                finalized["summary"]["result_csv_sha256"],
            "summary_sha256":
                finalized["summary_sha256"],
            "execution_audit_sha256":
                finalized["execution_audit_sha256"],
            "preoutcome_status_counts":
                finalized["summary"]["preoutcome_status_counts"],
            "association_status_counts":
                finalized["summary"]["association_status_counts"],
            "final_status_counts":
                finalized["summary"]["final_status_counts"],
            "raw_GP_HISTORY_opened": False,
            "reference_miss_distance_accessed": False,
            "CAL0_refit_performed": False,
            "network_requests": 0,
            "next_safe_action":
                finalized["summary"]["next_safe_action"],
        },
        indent=2,
        sort_keys=True,
    ))

    return finalized["summary"]


def offline_validate() -> dict:
    require(
        git_value("rev-parse", "HEAD") == PARENT_COMMIT,
        "production implementation offline validation must run at DR-0061 freeze",
    )
    verify_parent_freeze()

    require(
        not WORK_DB.exists(),
        "production work checkpoint exists before production authorization",
    )
    require(
        not Path(str(WORK_DB) + "-wal").exists(),
        "production work checkpoint WAL exists before authorization",
    )
    require(
        not Path(str(WORK_DB) + "-shm").exists(),
        "production work checkpoint SHM exists before authorization",
    )

    for path in (FINAL_RESULTS, FINAL_SUMMARY, FINAL_EXECUTION_AUDIT):
        require(
            not path.exists(),
            f"production final output exists before authorization: {path}",
        )

    controller = load_controller()
    environment = controller.verify_environment()
    controller_frozen_hashes = controller.verify_frozen_hashes()
    _g6, s, numerical_constants = controller.import_execution_dependencies()

    require(
        float(s.g4.PRIMARY_HORIZON_S) == PRIMARY_HORIZON_S,
        "production horizon changed",
    )
    require(
        float(numerical_constants["ASSOCIATION_TOLERANCE_MIN"])
        == ASSOCIATION_TOLERANCE_MIN,
        "production association tolerance changed",
    )

    schedule = inspect_materialized_schedule()

    production_blocked = False
    try:
        execute_production(authorization=False)
    except A2ProductionExecutionNotAuthorized:
        production_blocked = True
    require(
        production_blocked,
        "production execution was not blocked without explicit authorization",
    )

    result = {
        "status": "G7R6_A2_PRODUCTION_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "executor_sha256": sha256(Path(__file__)),
        "controller_sha256": EXPECTED_CONTROLLER_SHA256,
        "materialized_db_expected_sha256": EXPECTED_DB_SHA256,
        "environment": environment,
        "numerical_constants": numerical_constants,
        "materialized_schedule": schedule,
        "controller_frozen_dependency_hashes": controller_frozen_hashes,
        "checkpoint_path": WORK_DB.relative_to(ROOT).as_posix(),
        "checkpoint_commit_unit": "complete_event",
        "checkpoint_resume_rule":
            "skip every atomically committed event; an interrupted uncommitted event may be recomputed",
        "direct_CLI_real_execution_authorized": False,
        "production_execution_blocked_without_authorization": production_blocked,
        "raw_GP_HISTORY_opened": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_performed": False,
        "reference_miss_distance_accessed": False,
        "CAL0_refit_performed": False,
        "network_requests": 0,
        "next_safe_action":
            "INDEPENDENT_PRODUCTION_IMPLEMENTATION_REVIEW_BEFORE_FREEZE",
    }

    require(
        not OFFLINE_VALIDATION.exists(),
        "production implementation offline-validation output already exists",
    )
    atomic_json(OFFLINE_VALIDATION, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--offline-validate", action="store_true")
    group.add_argument("--run-production", action="store_true")
    args = parser.parse_args()

    if args.offline_validate:
        offline_validate()
        return 0

    if args.run_production:
        execute_production(authorization=False)
        return 0

    raise A2ProductionError("unreachable command state")


if __name__ == "__main__":
    raise SystemExit(main())
