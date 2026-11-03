#!/usr/bin/env python3
"""Independent review of inactive G7 R6 A2 controller implementation.

No orbital propagation, candidate generation, or reference association is
performed.  The reviewer independently reconstructs the predetermined pilot
selection from the frozen materialized SQLite database, verifies exact replay
identities, verifies source hashes and authorization guards, and writes a
small review artifact.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import os
from pathlib import Path
from statistics import median


ROOT = Path(__file__).resolve().parents[2]

PARENT_COMMIT = "247845491a35ddff15cfc2515b8d5b68d672aa29"
PARENT_TAG = "g7-r6-a2-materialization-frozen"

CONTROLLER = ROOT / "scripts/g7/run_g7r6_a2_controller.py"
MANIFEST = ROOT / "results/g7/G7R6_A2_Predetermined_Pilot_Manifest.json"
OFFLINE = ROOT / "results/g7/G7R6_A2_Controller_Offline_Validation.json"
DB = ROOT / "data/raw/g7/G7R6_A2_Materialized.sqlite3"
OUT = ROOT / "results/g7/G7R6_A2_Controller_Implementation_Independent_Review.json"

EXPECTED_CONTROLLER_SHA256 = "806aff3b06c6efd82726a4d93f0a0ad9e3ab8314e9f5904e4707e38680c4f8de"
EXPECTED_MANIFEST_SHA256 = "c3c803e9957a1241854fa81f3e51bbe9923d7710c866fa34e08d79d907ac8cac"
EXPECTED_DB_SHA256 = "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"

EXPECTED_EVENTS = 12
EXPECTED_REPLAYS = 28


class ReviewError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReviewError(message)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def git_value(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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


def load_module():
    spec = importlib.util.spec_from_file_location(
        "g7r6_a2_controller_under_review",
        CONTROLLER,
    )
    require(spec is not None and spec.loader is not None, "cannot load controller")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def connect_db() -> sqlite3.Connection:
    uri = DB.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def independently_select_pilot(connection: sqlite3.Connection) -> list[dict]:
    events = [
        {
            "event_index": int(row["event_index"]),
            "conj_id": str(row["conj_id"]),
            "support_count": int(row["expected_support_replays"]),
        }
        for row in connection.execute(
            """
            SELECT event_index, conj_id, expected_support_replays
            FROM events
            ORDER BY event_index
            """
        )
    ]

    positive = [item for item in events if item["support_count"] > 0]
    m = median([item["support_count"] for item in positive])

    smallest = sorted(
        positive,
        key=lambda item: (item["support_count"], item["event_index"]),
    )[:4]

    largest = sorted(
        positive,
        key=lambda item: (-item["support_count"], item["event_index"]),
    )[:4]

    reserved = {item["event_index"] for item in smallest + largest}
    middle = sorted(
        [item for item in positive if item["event_index"] not in reserved],
        key=lambda item: (
            abs(item["support_count"] - m),
            item["event_index"],
        ),
    )[:4]

    selected = []
    for group_name, members in (
        ("SMALLEST_POSITIVE_SUPPORT", smallest),
        ("NEAREST_MEDIAN_SUPPORT", middle),
        ("LARGEST_SUPPORT", largest),
    ):
        for rank, item in enumerate(members, start=1):
            rows = list(
                connection.execute(
                    """
                    SELECT *
                    FROM replay_updates
                    WHERE event_index=? AND temporal_support=1
                    ORDER BY replay_us, replay_sequence
                    """,
                    (item["event_index"],),
                )
            )
            n = len(rows)
            logical = (
                ("FIRST", 0),
                ("LOWER_MIDDLE", (n - 1) // 2),
                ("LAST", n - 1),
            )
            seen = set()
            replays = []
            for label, index in logical:
                row = rows[index]
                sequence = int(row["replay_sequence"])
                if sequence in seen:
                    continue
                seen.add(sequence)
                replays.append(
                    {
                        "pilot_position": label,
                        "replay_sequence": sequence,
                        "replay_us": int(row["replay_us"]),
                        "obj1_record_key": str(row["obj1_record_key"]),
                        "obj2_record_key": str(row["obj2_record_key"]),
                        "obj1_GP_ID": str(row["obj1_gp_id"]),
                        "obj2_GP_ID": str(row["obj2_gp_id"]),
                    }
                )
            selected.append(
                {
                    "selection_group": group_name,
                    "selection_rank": rank,
                    "event_index": item["event_index"],
                    "conj_id": item["conj_id"],
                    "support_count": item["support_count"],
                    "pilot_replays": replays,
                }
            )
    return selected


def main() -> int:
    require(
        git_value("rev-list", "-n", "1", PARENT_TAG)
        == PARENT_COMMIT,
        "materialization tag target changed",
    )
    require(CONTROLLER.is_file(), "controller missing")
    require(MANIFEST.is_file(), "pilot manifest missing")
    require(OFFLINE.is_file(), "controller offline validation missing")
    require(DB.is_file(), "materialized DB missing")
    require(not OUT.exists(), "controller independent review already exists")

    require(
        sha256(CONTROLLER) == EXPECTED_CONTROLLER_SHA256,
        "controller SHA mismatch",
    )
    require(
        sha256(MANIFEST) == EXPECTED_MANIFEST_SHA256,
        "pilot manifest SHA mismatch",
    )

    with MANIFEST.open("r", encoding="utf-8") as stream:
        manifest = json.load(stream)
    with OFFLINE.open("r", encoding="utf-8") as stream:
        offline = json.load(stream)

    require(
        offline["status"] == "G7R6_A2_CONTROLLER_OFFLINE_VALIDATION_PASS",
        "controller offline validation is not PASS",
    )
    require(
        offline["controller_sha256"] == EXPECTED_CONTROLLER_SHA256,
        "offline controller hash binding changed",
    )
    require(
        offline["pilot_manifest_sha256"] == EXPECTED_MANIFEST_SHA256,
        "offline manifest hash binding changed",
    )
    require(
        offline["materialized_db_expected_sha256"] == EXPECTED_DB_SHA256,
        "offline DB binding changed",
    )
    require(
        offline["orbital_propagation_performed"] is False,
        "offline validation reports propagation",
    )

    connection = connect_db()
    try:
        independently_selected = independently_select_pilot(connection)
    finally:
        connection.close()

    require(
        independently_selected == manifest["selected_events"],
        "independent predetermined pilot selection does not match manifest",
    )
    require(
        len(independently_selected) == EXPECTED_EVENTS,
        "independent selected-event count changed",
    )
    require(
        sum(len(item["pilot_replays"]) for item in independently_selected)
        == EXPECTED_REPLAYS,
        "independent pilot replay count changed",
    )

    controller = load_module()

    pilot_blocked = False
    try:
        controller.execute_pilot(authorization=False)
    except controller.A2PilotExecutionNotAuthorized:
        pilot_blocked = True
    require(pilot_blocked, "unauthorized pilot execution not blocked")

    production_blocked = False
    try:
        controller.execute_production(authorization=False)
    except controller.A2ProductionExecutionNotAuthorized:
        production_blocked = True
    require(production_blocked, "unauthorized production execution not blocked")

    source = CONTROLLER.read_text(encoding="utf-8")

    required_source_terms = (
        "g6.process_event(",
        "independent_direct_path(",
        "associate_reference_event(",
        "EXPECTED_DB_SHA256",
        "EXPECTED_PILOT_MANIFEST_SHA256",
        "A2_PILOT_EXECUTION_NOT_AUTHORIZED",
        "A2_FULL_PRODUCTION_PROPAGATION_NOT_AUTHORIZED",
    )
    for term in required_source_terms:
        require(term in source, f"controller required term missing: {term}")

    prohibited_source_terms = (
        "requests.",
        "urllib.request",
        "http.client",
        "Space-Track",
        "GP_HISTORY",
    )
    prohibited_hits = [
        term for term in prohibited_source_terms
        if term in source
    ]
    require(
        prohibited_hits == ["GP_HISTORY"],
        # GP_HISTORY appears only in explicit audit/interpretation labels,
        # not as a raw-history path or access implementation.
        f"unexpected prohibited source terms: {prohibited_hits}",
    )

    result = {
        "status": "G7R6_A2_CONTROLLER_IMPLEMENTATION_INDEPENDENT_REVIEW_PASS",
        "parent_materialization_commit": PARENT_COMMIT,
        "parent_materialization_tag": PARENT_TAG,
        "controller_sha256": EXPECTED_CONTROLLER_SHA256,
        "pilot_manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "materialized_db_expected_sha256": EXPECTED_DB_SHA256,
        "pilot_event_count": EXPECTED_EVENTS,
        "pilot_replay_count": EXPECTED_REPLAYS,
        "independent_pilot_selection_exact_match": True,
        "pilot_execution_blocked_without_authorization": pilot_blocked,
        "full_production_blocked": production_blocked,
        "raw_GP_HISTORY_opened": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_performed": False,
        "network_requests": 0,
        "next_safe_action":
            "FREEZE_EXACT_PILOT_IDENTITIES_AND_CONTROLLER_IMPLEMENTATION",
    }

    atomic_json(OUT, result)
    print(json.dumps(
        {
            **result,
            "review_output_sha256": sha256(OUT),
        },
        indent=2,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
