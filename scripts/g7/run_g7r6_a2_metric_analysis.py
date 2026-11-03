#!/usr/bin/env python3
"""Frozen-design implementation for G7 R6 A2 metric derivation.

IMPORTANT AUTHORIZATION BOUNDARY
--------------------------------
This module is designed to derive S05, S06, S07 and Q01-Q06 from already
frozen A2 inputs.  Direct CLI execution of real metrics is blocked.  Before
authorization, only --offline-validate is permitted.

No orbital propagation, candidate generation/refinement, reference
re-association, CAL0 fitting, raw GP_HISTORY access, or network access occurs.

The real execution path:
  * reads the frozen 107,740-row production CSV;
  * reads the frozen materialized A2 SQLite database in read-only mode;
  * derives S05/S06 on the filtered chronological VALID_ASSOCIATED sequence;
  * derives S07 from the earliest pre-TCA VALID_ASSOCIATED replay;
  * derives Q01-Q05 on the complete 2,020,081 publication-opportunity schedule;
  * executes the frozen primary 30 d Q03-Q05 window only; 14/60 d secondary
    sensitivity remains frozen but deferred from this primary pass;
  * derives Q06 only for VALID_ASSOCIATED reconstructions;
  * writes a resumable local derived SQLite database event-by-event;
  * finalizes small tracked CSV/JSON artifacts only after all 6,927 events
    complete.

The local derived SQLite database is an execution/checkpoint artifact and is
expected to remain Git-ignored.
"""

from __future__ import annotations

import argparse
import ast
import bisect
import csv
import hashlib
import importlib.util
import json
import math
import os
import sqlite3
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]

PARENT_COMMIT = "68e1a188db9ecc18d80c65655e75e05c4cb123b1"
PARENT_TAG = "g7-r6-a2-metric-semantics-frozen"

DR0065 = ROOT / "docs/research/DR-0065_G7_R6_A2_Metric_Execution_Semantics_Lock.md"
MATERIALIZED_DB = ROOT / "data/raw/g7/G7R6_A2_Materialized.sqlite3"
PRODUCTION_CSV = ROOT / "results/g7/G7R6_A2_Production_Replay_Results.csv"
PRODUCTION_SUMMARY = ROOT / "results/g7/G7R6_A2_Production_Summary.json"

OFFLINE_VALIDATION = (
    ROOT / "results/g7/G7R6_A2_Metric_Implementation_Offline_Validation.json"
)

WORK_DB = ROOT / "data/raw/g7/G7R6_A2_Metric_Derived.sqlite3"

S05_S06_CHANGES_CSV = ROOT / "results/g7/G7R6_A2_Metric_S05_S06_Changes.csv"
S05_S06_EVENT_STATUS_CSV = (
    ROOT / "results/g7/G7R6_A2_Metric_S05_S06_Event_Status.csv"
)
S07_EVENT_CSV = ROOT / "results/g7/G7R6_A2_Metric_S07_Event_Lead.csv"
FINAL_SUMMARY = ROOT / "results/g7/G7R6_A2_Metric_Derivation_Summary.json"
FINAL_EXECUTION_AUDIT = (
    ROOT / "results/g7/G7R6_A2_Metric_Derivation_Execution_Audit.json"
)

EXPECTED_DR0065_SHA256 = (
    "b813fac5bedb5d316ef6163df00299a9d6849e3523c0a926aa996cfcdc58efb1"
)
EXPECTED_MATERIALIZED_DB_SHA256 = (
    "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"
)
EXPECTED_PRODUCTION_CSV_SHA256 = (
    "a0217011e0695c240cbc1209f671c2ad7a8cb1948093b1f8c075146819e9efdd"
)

EXPECTED_EVENTS = 6_927
EXPECTED_OBJECTS = 13_854
EXPECTED_PUBLICATION_OPPORTUNITIES = 2_020_081
EXPECTED_SUPPORT_REPLAYS = 107_740
EXPECTED_NON_SUPPORT_REPLAYS = 1_912_341
EXPECTED_HISTORY_RECORDS = 3_510_547
EXPECTED_SUPPORT_PAYLOADS = 152_407

WINDOW_DAYS = (30,)
PRIMARY_WINDOW_DAYS = 30
US_PER_HOUR = 3_600_000_000
US_PER_DAY = 86_400_000_000

S05_S06_MISSING_REASON = "INSUFFICIENT_SUCCESSIVE_VALID_ASSOCIATED_RECONSTRUCTIONS"
S07_MISSING_STATUS = "NO PRE-TCA RECONSTRUCTION"

EXPECTED_PRODUCTION_FIELDS = (
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

CHANGE_FIELDS = (
    "event_index",
    "conj_id",
    "g6_temporal_stratum",
    "change_sequence",
    "previous_replay_sequence",
    "previous_replay_us",
    "current_replay_sequence",
    "current_replay_us",
    "publication_gap_h",
    "previous_predicted_tca_utc",
    "current_predicted_tca_utc",
    "signed_tca_change_s",
    "absolute_tca_change_s",
    "previous_predicted_miss_distance_km",
    "current_predicted_miss_distance_km",
    "signed_miss_distance_change_km",
    "absolute_miss_distance_change_km",
)

EVENT_STATUS_FIELDS = (
    "event_index",
    "conj_id",
    "g6_temporal_stratum",
    "publication_replay_opportunities",
    "temporal_support_replays",
    "valid_associated_reconstructions",
    "s05_s06_change_count",
    "s05_status",
    "s06_status",
)

S07_FIELDS = (
    "event_index",
    "conj_id",
    "g6_temporal_stratum",
    "s07_status",
    "first_valid_associated_replay_sequence",
    "first_valid_associated_replay_us",
    "first_valid_associated_replay_utc",
    "reference_tca_utc",
    "lead_us",
    "lead_h",
)


class A2MetricError(RuntimeError):
    pass


class A2MetricExecutionNotAuthorized(A2MetricError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise A2MetricError(message)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
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


def tag_target(tag: str) -> str | None:
    result = git("rev-list", "-n", "1", tag, check=False)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def parse_utc_us(text: str) -> int:
    value = str(text).strip()
    require(bool(value), "empty UTC timestamp")
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    require(dt.tzinfo is not None, f"naive UTC timestamp: {text!r}")
    dt = dt.astimezone(UTC)
    return int(round(dt.timestamp() * 1_000_000))


def iso_from_us(value: int) -> str:
    return (
        datetime.fromtimestamp(int(value) / 1_000_000.0, tz=UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def float_text(value: float | None) -> str:
    if value is None:
        return ""
    require(math.isfinite(value), "nonfinite float serialization")
    return format(value, ".17g")


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


def connect_materialized_ro() -> sqlite3.Connection:
    require(MATERIALIZED_DB.is_file(), "materialized A2 database missing")
    uri = MATERIALIZED_DB.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def verify_frozen_inputs(*, hash_materialized_db: bool) -> dict:
    require(tag_target(PARENT_TAG) == PARENT_COMMIT, "DR-0065 tag target changed")
    require(DR0065.is_file(), "DR-0065 missing")
    require(
        sha256(DR0065) == EXPECTED_DR0065_SHA256,
        "DR-0065 SHA mismatch",
    )
    require(PRODUCTION_CSV.is_file(), "frozen production CSV missing")
    require(
        sha256(PRODUCTION_CSV) == EXPECTED_PRODUCTION_CSV_SHA256,
        "production CSV SHA mismatch",
    )
    require(PRODUCTION_SUMMARY.is_file(), "production summary missing")

    materialized_sha = None
    if hash_materialized_db:
        materialized_sha = sha256(MATERIALIZED_DB)
        require(
            materialized_sha == EXPECTED_MATERIALIZED_DB_SHA256,
            "materialized DB SHA mismatch",
        )

    with PRODUCTION_SUMMARY.open("r", encoding="utf-8") as stream:
        summary = json.load(stream)
    require(
        summary.get("status") == "G7R6_A2_PRODUCTION_EXECUTION_COMPLETED",
        "production summary lost completed status",
    )
    require(
        int(summary["replay_results_written"]) == EXPECTED_SUPPORT_REPLAYS,
        "production replay count changed",
    )

    with PRODUCTION_CSV.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        fields = tuple(next(reader))
    require(fields == EXPECTED_PRODUCTION_FIELDS, "production CSV schema changed")

    connection = connect_materialized_ro()
    try:
        require(
            connection.execute("PRAGMA quick_check").fetchone()[0] == "ok",
            "materialized DB quick_check failed",
        )
        counts = {
            "events": int(
                connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            ),
            "objects": int(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT object_id)
                    FROM history_records
                    """
                ).fetchone()[0]
            ),
            "publication_opportunities": int(
                connection.execute(
                    "SELECT COUNT(*) FROM replay_updates"
                ).fetchone()[0]
            ),
            "support_replays": int(
                connection.execute(
                    "SELECT COUNT(*) FROM replay_updates WHERE temporal_support=1"
                ).fetchone()[0]
            ),
            "non_support_replays": int(
                connection.execute(
                    "SELECT COUNT(*) FROM replay_updates WHERE temporal_support=0"
                ).fetchone()[0]
            ),
            "history_records": int(
                connection.execute("SELECT COUNT(*) FROM history_records").fetchone()[0]
            ),
            "support_payloads": int(
                connection.execute("SELECT COUNT(*) FROM support_payloads").fetchone()[0]
            ),
            "invalid_state_signatures": int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM history_records
                    WHERE state_signature_valid <> 1
                       OR state_signature IS NULL
                    """
                ).fetchone()[0]
            ),
            "distinct_event_objects": int(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT object_id)
                    FROM (
                        SELECT obj1 AS object_id FROM events
                        UNION
                        SELECT obj2 AS object_id FROM events
                    )
                    """
                ).fetchone()[0]
            ),
        }

        require(counts["events"] == EXPECTED_EVENTS, "event census changed")
        require(counts["objects"] == EXPECTED_OBJECTS, "history object census changed")
        require(
            counts["distinct_event_objects"] == EXPECTED_OBJECTS,
            "event object-disjoint census changed",
        )
        require(
            counts["publication_opportunities"] == EXPECTED_PUBLICATION_OPPORTUNITIES,
            "publication-opportunity schedule changed",
        )
        require(
            counts["support_replays"] == EXPECTED_SUPPORT_REPLAYS,
            "support-replay census changed",
        )
        require(
            counts["non_support_replays"] == EXPECTED_NON_SUPPORT_REPLAYS,
            "non-support census changed",
        )
        require(
            counts["history_records"] == EXPECTED_HISTORY_RECORDS,
            "history-record census changed",
        )
        require(
            counts["support_payloads"] == EXPECTED_SUPPORT_PAYLOADS,
            "support-payload census changed",
        )
        require(
            counts["invalid_state_signatures"] == 0,
            "materialized history contains invalid/missing state signatures",
        )

        boundary = connection.execute(
            """
            SELECT
                MIN(reference_us-report_start_us) AS min_report,
                MAX(reference_us-report_start_us) AS max_report,
                MIN(reference_us-quality_start_us) AS min_quality,
                MAX(reference_us-quality_start_us) AS max_quality
            FROM events
            """
        ).fetchone()
        require(
            int(boundary["min_report"]) == 90 * US_PER_DAY
            and int(boundary["max_report"]) == 90 * US_PER_DAY,
            "reporting-window boundary is not exactly 90 d",
        )
        require(
            int(boundary["min_quality"]) == 120 * US_PER_DAY
            and int(boundary["max_quality"]) == 120 * US_PER_DAY,
            "materialized primary-quality lower boundary is not exactly t_ref-120 d",
        )
        require(
            int(boundary["min_quality"]) - int(boundary["min_report"])
            == 30 * US_PER_DAY
            and int(boundary["max_quality"]) - int(boundary["max_report"])
            == 30 * US_PER_DAY,
            "materialized primary-quality guard before reporting is not exactly 30 d",
        )
        primary_window_censor_candidates = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM replay_updates r
                JOIN events e USING(event_index)
                WHERE e.quality_start_us > r.replay_us - 30 * ?
                """,
                (US_PER_DAY,),
            ).fetchone()[0]
        )
        require(
            primary_window_censor_candidates == 0,
            "materialized history does not fully support every primary 30 d replay window",
        )
    finally:
        connection.close()

    return {
        "materialized_db_sha256": materialized_sha,
        "production_csv_sha256": EXPECTED_PRODUCTION_CSV_SHA256,
        "dr0065_sha256": EXPECTED_DR0065_SHA256,
        "counts": counts,
        "materialized_boundary_semantics": {
            "report_start": "t_ref-90d",
            "quality_start": "t_ref-120d",
            "primary_quality_guard_days": 30,
            "primary_30d_windows_fully_supported": True,
            "secondary_14d_60d_execution": "DEFERRED",
        },
    }


class RollingWindowState:
    """Exact open-left/closed-right rolling-history metrics.

    Records must be sorted by (creation_us, epoch_us, stable_tie_key).
    Each record is (creation_us, state_signature).
    """

    def __init__(
        self,
        records: list[tuple[int, str]],
        lower_bound_us: int,
        window_days: int,
    ) -> None:
        self.records = records
        self.lower_bound_us = int(lower_bound_us)
        self.window_us = int(window_days) * US_PER_DAY
        self.left = 0
        self.right = 0
        self.last_replay_us: int | None = None
        self.state_counts: Counter[str] = Counter()
        self.sorted_gaps_us: list[int] = []

    def _add_gap(self, value: int) -> None:
        require(value >= 0, "negative publication cadence interval")
        bisect.insort(self.sorted_gaps_us, int(value))

    def _remove_gap(self, value: int) -> None:
        pos = bisect.bisect_left(self.sorted_gaps_us, int(value))
        require(
            pos < len(self.sorted_gaps_us)
            and self.sorted_gaps_us[pos] == int(value),
            "rolling-window gap multiset inconsistency",
        )
        self.sorted_gaps_us.pop(pos)

    def advance(self, replay_us: int) -> dict:
        replay_us = int(replay_us)
        if self.last_replay_us is not None:
            require(
                replay_us >= self.last_replay_us,
                "rolling-window replay order is not monotone",
            )
        self.last_replay_us = replay_us

        while (
            self.right < len(self.records)
            and int(self.records[self.right][0]) <= replay_us
        ):
            creation_us, signature = self.records[self.right]
            if self.right > self.left:
                previous_us = int(self.records[self.right - 1][0])
                self._add_gap(int(creation_us) - previous_us)
            self.state_counts[str(signature)] += 1
            self.right += 1

        start_us = replay_us - self.window_us
        while (
            self.left < self.right
            and int(self.records[self.left][0]) <= start_us
        ):
            _, signature = self.records[self.left]
            self.state_counts[str(signature)] -= 1
            if self.state_counts[str(signature)] == 0:
                del self.state_counts[str(signature)]
            if self.left + 1 < self.right:
                gap = (
                    int(self.records[self.left + 1][0])
                    - int(self.records[self.left][0])
                )
                self._remove_gap(gap)
            self.left += 1

        count = self.right - self.left
        require(count >= 0, "negative rolling publication count")
        require(
            sum(self.state_counts.values()) == count,
            "rolling state-count multiplicity mismatch",
        )
        require(
            len(self.sorted_gaps_us) == max(0, count - 1),
            "rolling cadence gap count mismatch",
        )

        median_gap_h = None
        max_gap_h = None
        if self.sorted_gaps_us:
            median_gap_h = (
                float(statistics.median(self.sorted_gaps_us)) / US_PER_HOUR
            )
            max_gap_h = float(self.sorted_gaps_us[-1]) / US_PER_HOUR

        return {
            "left_censored": self.lower_bound_us > start_us,
            "publication_count": count,
            "distinct_state_count": len(self.state_counts),
            "median_cadence_h": median_gap_h,
            "max_gap_h": max_gap_h,
        }


def derive_geometry_for_event(
    event: dict,
    production_rows: list[dict],
) -> tuple[list[dict], dict, dict]:
    """Derive S05/S06 changes and S07 from one event.

    Only VALID_ASSOCIATED rows enter the S05/S06 filtered sequence.
    Non-associated/failure rows neither contribute geometry nor reset it.
    """

    ordered = sorted(
        production_rows,
        key=lambda r: (int(r["replay_us"]), int(r["replay_sequence"])),
    )
    valid = [r for r in ordered if r["final_status"] == "VALID_ASSOCIATED"]

    changes: list[dict] = []
    for change_sequence, (previous, current) in enumerate(
        zip(valid, valid[1:]),
        start=1,
    ):
        previous_tca_us = parse_utc_us(previous["predicted_tca_utc"])
        current_tca_us = parse_utc_us(current["predicted_tca_utc"])
        signed_tca_change_s = (current_tca_us - previous_tca_us) / 1_000_000.0

        previous_miss = float(previous["predicted_miss_distance_km"])
        current_miss = float(current["predicted_miss_distance_km"])
        require(
            math.isfinite(previous_miss) and previous_miss >= 0.0,
            "invalid previous associated miss distance",
        )
        require(
            math.isfinite(current_miss) and current_miss >= 0.0,
            "invalid current associated miss distance",
        )
        signed_miss_change = current_miss - previous_miss

        changes.append(
            {
                "event_index": int(event["event_index"]),
                "conj_id": str(event["conj_id"]),
                "g6_temporal_stratum": str(event["stratum"]),
                "change_sequence": change_sequence,
                "previous_replay_sequence": int(previous["replay_sequence"]),
                "previous_replay_us": int(previous["replay_us"]),
                "current_replay_sequence": int(current["replay_sequence"]),
                "current_replay_us": int(current["replay_us"]),
                "publication_gap_h":
                    (int(current["replay_us"]) - int(previous["replay_us"]))
                    / US_PER_HOUR,
                "previous_predicted_tca_utc": previous["predicted_tca_utc"],
                "current_predicted_tca_utc": current["predicted_tca_utc"],
                "signed_tca_change_s": signed_tca_change_s,
                "absolute_tca_change_s": abs(signed_tca_change_s),
                "previous_predicted_miss_distance_km": previous_miss,
                "current_predicted_miss_distance_km": current_miss,
                "signed_miss_distance_change_km": signed_miss_change,
                "absolute_miss_distance_change_km": abs(signed_miss_change),
            }
        )

    status = "VALID" if len(valid) >= 2 else S05_S06_MISSING_REASON
    event_status = {
        "event_index": int(event["event_index"]),
        "conj_id": str(event["conj_id"]),
        "g6_temporal_stratum": str(event["stratum"]),
        "publication_replay_opportunities": int(
            event["expected_eligible_replays"]
        ),
        "temporal_support_replays": int(event["expected_support_replays"]),
        "valid_associated_reconstructions": len(valid),
        "s05_s06_change_count": len(changes),
        "s05_status": status,
        "s06_status": status,
    }

    reference_us = int(event["reference_us"])
    pre_tca_valid = [
        row
        for row in valid
        if int(row["replay_us"]) < reference_us
    ]

    if pre_tca_valid:
        first = min(
            pre_tca_valid,
            key=lambda r: (int(r["replay_us"]), int(r["replay_sequence"])),
        )
        lead_us = reference_us - int(first["replay_us"])
        require(lead_us > 0, "S07 lead is not positive")
        s07 = {
            "event_index": int(event["event_index"]),
            "conj_id": str(event["conj_id"]),
            "g6_temporal_stratum": str(event["stratum"]),
            "s07_status": "VALID",
            "first_valid_associated_replay_sequence":
                int(first["replay_sequence"]),
            "first_valid_associated_replay_us": int(first["replay_us"]),
            "first_valid_associated_replay_utc":
                iso_from_us(int(first["replay_us"])),
            "reference_tca_utc": iso_from_us(reference_us),
            "lead_us": lead_us,
            "lead_h": lead_us / US_PER_HOUR,
        }
    else:
        s07 = {
            "event_index": int(event["event_index"]),
            "conj_id": str(event["conj_id"]),
            "g6_temporal_stratum": str(event["stratum"]),
            "s07_status": S07_MISSING_STATUS,
            "first_valid_associated_replay_sequence": None,
            "first_valid_associated_replay_us": None,
            "first_valid_associated_replay_utc": None,
            "reference_tca_utc": iso_from_us(reference_us),
            "lead_us": None,
            "lead_h": None,
        }

    return changes, event_status, s07


def quality_base(
    replay_us: int,
    creation_us: int,
    epoch_us: int,
    associated_tca_us: int | None,
) -> dict:
    state_age_us = int(replay_us) - int(epoch_us)
    publication_age_us = int(replay_us) - int(creation_us)
    require(
        publication_age_us >= 0,
        "negative Q02 publication age is a causal-filter violation",
    )

    if associated_tca_us is None:
        q06_status = "NOT_APPLICABLE_NON_ASSOCIATED"
        propagation_span_us = None
    else:
        q06_status = "VALID_ASSOCIATED"
        propagation_span_us = int(associated_tca_us) - int(epoch_us)

    return {
        "q01_state_age_us": state_age_us,
        "q01_state_age_h": state_age_us / US_PER_HOUR,
        "q01_negative_flag": state_age_us < 0,
        "q02_publication_age_us": publication_age_us,
        "q02_publication_age_h": publication_age_us / US_PER_HOUR,
        "q06_status": q06_status,
        "q06_propagation_span_us": propagation_span_us,
        "q06_propagation_span_h":
            None
            if propagation_span_us is None
            else propagation_span_us / US_PER_HOUR,
    }


def load_production_by_event() -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = {}
    row_count = 0
    with PRODUCTION_CSV.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        require(
            tuple(reader.fieldnames or ()) == EXPECTED_PRODUCTION_FIELDS,
            "production CSV field order changed",
        )
        for row in reader:
            event_index = int(row["event_index"])
            result.setdefault(event_index, []).append(row)
            row_count += 1
    require(row_count == EXPECTED_SUPPORT_REPLAYS, "production CSV row count changed")
    return result


def create_work_db(executor_sha256: str) -> sqlite3.Connection:
    WORK_DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(WORK_DB)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS event_completion (
            event_index INTEGER PRIMARY KEY,
            publication_replays INTEGER NOT NULL,
            q_object_rows INTEGER NOT NULL,
            change_rows INTEGER NOT NULL,
            valid_associated_reconstructions INTEGER NOT NULL,
            s07_status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS s05_s06_changes (
            event_index INTEGER NOT NULL,
            conj_id TEXT NOT NULL,
            g6_temporal_stratum TEXT NOT NULL,
            change_sequence INTEGER NOT NULL,
            previous_replay_sequence INTEGER NOT NULL,
            previous_replay_us INTEGER NOT NULL,
            current_replay_sequence INTEGER NOT NULL,
            current_replay_us INTEGER NOT NULL,
            publication_gap_h REAL NOT NULL,
            previous_predicted_tca_utc TEXT NOT NULL,
            current_predicted_tca_utc TEXT NOT NULL,
            signed_tca_change_s REAL NOT NULL,
            absolute_tca_change_s REAL NOT NULL,
            previous_predicted_miss_distance_km REAL NOT NULL,
            current_predicted_miss_distance_km REAL NOT NULL,
            signed_miss_distance_change_km REAL NOT NULL,
            absolute_miss_distance_change_km REAL NOT NULL,
            PRIMARY KEY (event_index, change_sequence)
        );

        CREATE TABLE IF NOT EXISTS s05_s06_event_status (
            event_index INTEGER PRIMARY KEY,
            conj_id TEXT NOT NULL,
            g6_temporal_stratum TEXT NOT NULL,
            publication_replay_opportunities INTEGER NOT NULL,
            temporal_support_replays INTEGER NOT NULL,
            valid_associated_reconstructions INTEGER NOT NULL,
            s05_s06_change_count INTEGER NOT NULL,
            s05_status TEXT NOT NULL,
            s06_status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS s07_event_lead (
            event_index INTEGER PRIMARY KEY,
            conj_id TEXT NOT NULL,
            g6_temporal_stratum TEXT NOT NULL,
            s07_status TEXT NOT NULL,
            first_valid_associated_replay_sequence INTEGER,
            first_valid_associated_replay_us INTEGER,
            first_valid_associated_replay_utc TEXT,
            reference_tca_utc TEXT NOT NULL,
            lead_us INTEGER,
            lead_h REAL
        );

        CREATE TABLE IF NOT EXISTS q_object_updates (
            event_index INTEGER NOT NULL,
            replay_sequence INTEGER NOT NULL,
            replay_us INTEGER NOT NULL,
            conj_id TEXT NOT NULL,
            g6_temporal_stratum TEXT NOT NULL,
            object_role TEXT NOT NULL,
            object_id TEXT NOT NULL,
            current_creation_us INTEGER NOT NULL,
            current_epoch_us INTEGER NOT NULL,
            current_gp_id TEXT NOT NULL,
            q01_state_age_us INTEGER NOT NULL,
            q01_state_age_h REAL NOT NULL,
            q01_negative_flag INTEGER NOT NULL,
            q02_publication_age_us INTEGER NOT NULL,
            q02_publication_age_h REAL NOT NULL,
            w30_left_censored INTEGER NOT NULL,
            w30_publication_count INTEGER NOT NULL,
            w30_distinct_state_count INTEGER NOT NULL,
            w30_median_cadence_h REAL,
            w30_max_gap_h REAL,
            q06_status TEXT NOT NULL,
            q06_propagation_span_us INTEGER,
            q06_propagation_span_h REAL,
            PRIMARY KEY (event_index, replay_sequence, object_role)
        );

        CREATE INDEX IF NOT EXISTS idx_q_object_updates_object
        ON q_object_updates(object_id, replay_us);

        CREATE INDEX IF NOT EXISTS idx_q_object_updates_status
        ON q_object_updates(q06_status, event_index, replay_sequence);
        """
    )

    expected_metadata = {
        "schema_version": "1",
        "executor_sha256": executor_sha256,
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "materialized_db_sha256": EXPECTED_MATERIALIZED_DB_SHA256,
        "production_csv_sha256": EXPECTED_PRODUCTION_CSV_SHA256,
        "events": str(EXPECTED_EVENTS),
        "objects": str(EXPECTED_OBJECTS),
        "publication_opportunities": str(EXPECTED_PUBLICATION_OPPORTUNITIES),
        "support_replays": str(EXPECTED_SUPPORT_REPLAYS),
        "s05_s06_sequence": "FILTERED_VALID_ASSOCIATED_CHRONOLOGICAL",
        "intervening_failure_resets_sequence": "false",
        "s07": "EARLIEST_PRE_TCA_VALID_ASSOCIATED",
        "quality_windows_days_executed": "30",
        "primary_quality_window_days": "30",
        "secondary_quality_windows_days_deferred": "14,60",
        "q06_requires_valid_associated": "true",
        "event_transaction_atomic": "true",
    }

    observed_metadata = dict(
        connection.execute("SELECT key, value FROM metadata").fetchall()
    )
    if observed_metadata:
        require(
            observed_metadata == expected_metadata,
            "existing metric checkpoint metadata mismatch",
        )
    else:
        connection.executemany(
            "INSERT INTO metadata(key,value) VALUES (?,?)",
            sorted(expected_metadata.items()),
        )
        connection.commit()

    return connection


def history_for_object(
    connection: sqlite3.Connection,
    object_id: str,
) -> list[tuple[int, str]]:
    rows = connection.execute(
        """
        SELECT creation_us, state_signature
        FROM history_records
        WHERE object_id=?
        ORDER BY creation_us, epoch_us, stable_tie_key
        """,
        (str(object_id),),
    ).fetchall()
    require(bool(rows), f"no materialized history for object {object_id}")
    return [(int(row["creation_us"]), str(row["state_signature"])) for row in rows]


def production_status_map(rows: list[dict]) -> dict[int, dict]:
    result = {}
    for row in rows:
        sequence = int(row["replay_sequence"])
        require(sequence not in result, "duplicate production replay identity")
        result[sequence] = row
    return result


def insert_event(
    work: sqlite3.Connection,
    materialized: sqlite3.Connection,
    event: sqlite3.Row,
    production_rows: list[dict],
) -> dict:
    event_index = int(event["event_index"])

    replay_rows = materialized.execute(
        """
        SELECT *
        FROM replay_updates
        WHERE event_index=?
        ORDER BY replay_us, replay_sequence
        """,
        (event_index,),
    ).fetchall()

    require(
        len(replay_rows) == int(event["expected_eligible_replays"]),
        f"event {event_index}: replay schedule cardinality mismatch",
    )

    prod_by_sequence = production_status_map(production_rows)
    support_sequences = {
        int(row["replay_sequence"])
        for row in replay_rows
        if int(row["temporal_support"]) == 1
    }
    require(
        set(prod_by_sequence) == support_sequences,
        f"event {event_index}: production/support replay identity mismatch",
    )

    event_dict = dict(event)
    changes, event_status, s07 = derive_geometry_for_event(
        event_dict,
        production_rows,
    )

    obj1 = str(event["obj1"])
    obj2 = str(event["obj2"])
    histories = {
        "obj1": history_for_object(materialized, obj1),
        "obj2": history_for_object(materialized, obj2),
    }
    states = {
        role: {
            days: RollingWindowState(
                histories[role],
                int(event["quality_start_us"]),
                days,
            )
            for days in WINDOW_DAYS
        }
        for role in ("obj1", "obj2")
    }

    q_rows = []
    for replay in replay_rows:
        sequence = int(replay["replay_sequence"])
        replay_us = int(replay["replay_us"])
        production = prod_by_sequence.get(sequence)

        associated_tca_us = None
        if production is not None and production["final_status"] == "VALID_ASSOCIATED":
            associated_tca_us = parse_utc_us(production["predicted_tca_utc"])

        for role, object_id in (("obj1", obj1), ("obj2", obj2)):
            creation_us = int(replay[f"{role}_creation_us"])
            epoch_us = int(replay[f"{role}_epoch_us"])
            gp_id = str(replay[f"{role}_gp_id"])

            base = quality_base(
                replay_us,
                creation_us,
                epoch_us,
                associated_tca_us,
            )
            windows = {
                days: states[role][days].advance(replay_us)
                for days in WINDOW_DAYS
            }

            q_rows.append(
                (
                    event_index,
                    sequence,
                    replay_us,
                    str(event["conj_id"]),
                    str(event["stratum"]),
                    role,
                    object_id,
                    creation_us,
                    epoch_us,
                    gp_id,
                    int(base["q01_state_age_us"]),
                    float(base["q01_state_age_h"]),
                    int(bool(base["q01_negative_flag"])),
                    int(base["q02_publication_age_us"]),
                    float(base["q02_publication_age_h"]),
                    int(bool(windows[30]["left_censored"])),
                    int(windows[30]["publication_count"]),
                    int(windows[30]["distinct_state_count"]),
                    windows[30]["median_cadence_h"],
                    windows[30]["max_gap_h"],
                    str(base["q06_status"]),
                    base["q06_propagation_span_us"],
                    base["q06_propagation_span_h"],
                )
            )

    require(
        len(q_rows) == 2 * len(replay_rows),
        f"event {event_index}: Q object-row cardinality mismatch",
    )

    with work:
        work.executemany(
            """
            INSERT INTO s05_s06_changes VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            [
                (
                    row["event_index"],
                    row["conj_id"],
                    row["g6_temporal_stratum"],
                    row["change_sequence"],
                    row["previous_replay_sequence"],
                    row["previous_replay_us"],
                    row["current_replay_sequence"],
                    row["current_replay_us"],
                    row["publication_gap_h"],
                    row["previous_predicted_tca_utc"],
                    row["current_predicted_tca_utc"],
                    row["signed_tca_change_s"],
                    row["absolute_tca_change_s"],
                    row["previous_predicted_miss_distance_km"],
                    row["current_predicted_miss_distance_km"],
                    row["signed_miss_distance_change_km"],
                    row["absolute_miss_distance_change_km"],
                )
                for row in changes
            ],
        )

        work.execute(
            """
            INSERT INTO s05_s06_event_status VALUES (?,?,?,?,?,?,?,?,?)
            """,
            tuple(event_status[field] for field in EVENT_STATUS_FIELDS),
        )

        work.execute(
            """
            INSERT INTO s07_event_lead VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            tuple(s07[field] for field in S07_FIELDS),
        )

        work.executemany(
            """
            INSERT INTO q_object_updates VALUES (
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?
            )
            """,
            q_rows,
        )

        work.execute(
            """
            INSERT INTO event_completion VALUES (?,?,?,?,?,?)
            """,
            (
                event_index,
                len(replay_rows),
                len(q_rows),
                len(changes),
                int(event_status["valid_associated_reconstructions"]),
                str(s07["s07_status"]),
            ),
        )

    return {
        "event_index": event_index,
        "publication_replays": len(replay_rows),
        "q_object_rows": len(q_rows),
        "change_rows": len(changes),
        "valid_associated_reconstructions":
            int(event_status["valid_associated_reconstructions"]),
        "s07_status": str(s07["s07_status"]),
    }


def write_csv_from_query(
    connection: sqlite3.Connection,
    path: Path,
    fields: tuple[str, ...],
    query: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(fields)
        for row in connection.execute(query):
            values = []
            for value in row:
                if value is None:
                    values.append("")
                elif isinstance(value, float):
                    values.append(float_text(value))
                else:
                    values.append(str(value))
            writer.writerow(values)


def finalize_outputs(
    work: sqlite3.Connection,
    executor_sha256: str,
    resumed_events: int,
) -> dict:
    completed_events = int(
        work.execute("SELECT COUNT(*) FROM event_completion").fetchone()[0]
    )
    require(completed_events == EXPECTED_EVENTS, "metric derivation incomplete")

    q_rows = int(
        work.execute("SELECT COUNT(*) FROM q_object_updates").fetchone()[0]
    )
    require(
        q_rows == 2 * EXPECTED_PUBLICATION_OPPORTUNITIES,
        "Q object-update output cardinality changed",
    )

    q02_violations = int(
        work.execute(
            "SELECT COUNT(*) FROM q_object_updates WHERE q02_publication_age_us < 0"
        ).fetchone()[0]
    )
    require(q02_violations == 0, "negative Q02 values reached finalized output")

    event_status_rows = int(
        work.execute("SELECT COUNT(*) FROM s05_s06_event_status").fetchone()[0]
    )
    s07_rows = int(
        work.execute("SELECT COUNT(*) FROM s07_event_lead").fetchone()[0]
    )
    require(
        event_status_rows == EXPECTED_EVENTS and s07_rows == EXPECTED_EVENTS,
        "event-level metric output cardinality changed",
    )

    change_rows = int(
        work.execute("SELECT COUNT(*) FROM s05_s06_changes").fetchone()[0]
    )

    valid_associated_rows = int(
        work.execute(
            """
            SELECT COALESCE(SUM(valid_associated_reconstructions),0)
            FROM s05_s06_event_status
            """
        ).fetchone()[0]
    )
    q06_defined_rows = int(
        work.execute(
            """
            SELECT COUNT(*)
            FROM q_object_updates
            WHERE q06_status='VALID_ASSOCIATED'
            """
        ).fetchone()[0]
    )
    require(
        q06_defined_rows == 2 * valid_associated_rows,
        "Q06 valid-associated object-row accounting mismatch",
    )

    write_csv_from_query(
        work,
        S05_S06_CHANGES_CSV,
        CHANGE_FIELDS,
        """
        SELECT
            event_index, conj_id, g6_temporal_stratum, change_sequence,
            previous_replay_sequence, previous_replay_us,
            current_replay_sequence, current_replay_us, publication_gap_h,
            previous_predicted_tca_utc, current_predicted_tca_utc,
            signed_tca_change_s, absolute_tca_change_s,
            previous_predicted_miss_distance_km,
            current_predicted_miss_distance_km,
            signed_miss_distance_change_km,
            absolute_miss_distance_change_km
        FROM s05_s06_changes
        ORDER BY event_index, change_sequence
        """,
    )

    write_csv_from_query(
        work,
        S05_S06_EVENT_STATUS_CSV,
        EVENT_STATUS_FIELDS,
        """
        SELECT
            event_index, conj_id, g6_temporal_stratum,
            publication_replay_opportunities, temporal_support_replays,
            valid_associated_reconstructions, s05_s06_change_count,
            s05_status, s06_status
        FROM s05_s06_event_status
        ORDER BY event_index
        """,
    )

    write_csv_from_query(
        work,
        S07_EVENT_CSV,
        S07_FIELDS,
        """
        SELECT
            event_index, conj_id, g6_temporal_stratum, s07_status,
            first_valid_associated_replay_sequence,
            first_valid_associated_replay_us,
            first_valid_associated_replay_utc,
            reference_tca_utc, lead_us, lead_h
        FROM s07_event_lead
        ORDER BY event_index
        """,
    )

    summary = {
        "status": "G7R6_A2_METRIC_DERIVATION_COMPLETED",
        "executor_sha256": executor_sha256,
        "materialized_db_sha256": EXPECTED_MATERIALIZED_DB_SHA256,
        "production_csv_sha256": EXPECTED_PRODUCTION_CSV_SHA256,
        "events": EXPECTED_EVENTS,
        "publication_replay_opportunities": EXPECTED_PUBLICATION_OPPORTUNITIES,
        "q_object_update_rows": q_rows,
        "s05_s06_change_rows": change_rows,
        "valid_associated_reconstructions": valid_associated_rows,
        "q06_defined_object_rows": q06_defined_rows,
        "q01_negative_state_age_rows": int(
            work.execute(
                "SELECT COUNT(*) FROM q_object_updates WHERE q01_negative_flag=1"
            ).fetchone()[0]
        ),
        "q02_causal_violations": q02_violations,
        "primary_30d_left_censored_object_rows": int(
            work.execute(
                "SELECT COUNT(*) FROM q_object_updates WHERE w30_left_censored=1"
            ).fetchone()[0]
        ),
        "secondary_quality_window_execution": {
            "14d": "DEFERRED_NOT_EXECUTED_IN_PRIMARY_A2_METRIC_PASS",
            "60d": "DEFERRED_NOT_EXECUTED_IN_PRIMARY_A2_METRIC_PASS",
            "reason": (
                "The frozen A2 materialization carries t_ref-120 d history, "
                "which is complete for the primary 30 d window over the "
                "t_ref-90 d reporting interval. The secondary 14/60 d "
                "sensitivity is not required for this primary execution; "
                "it remains separately frozen and must not be silently "
                "approximated from a truncated 60 d history."
            ),
        },
        "s05_s06_event_status_counts": dict(
            work.execute(
                """
                SELECT s05_status, COUNT(*)
                FROM s05_s06_event_status
                GROUP BY s05_status
                ORDER BY s05_status
                """
            ).fetchall()
        ),
        "s07_status_counts": dict(
            work.execute(
                """
                SELECT s07_status, COUNT(*)
                FROM s07_event_lead
                GROUP BY s07_status
                ORDER BY s07_status
                """
            ).fetchall()
        ),
        "resumed_from_completed_events": resumed_events,
        "outputs": {
            S05_S06_CHANGES_CSV.relative_to(ROOT).as_posix():
                sha256(S05_S06_CHANGES_CSV),
            S05_S06_EVENT_STATUS_CSV.relative_to(ROOT).as_posix():
                sha256(S05_S06_EVENT_STATUS_CSV),
            S07_EVENT_CSV.relative_to(ROOT).as_posix():
                sha256(S07_EVENT_CSV),
        },
        "operations": {
            "orbital_propagation_performed": False,
            "candidate_generation_performed": False,
            "candidate_refinement_performed": False,
            "reference_association_recomputed": False,
            "raw_GP_HISTORY_opened": False,
            "CAL0_refit_performed": False,
            "network_requests": 0,
            "A0_execution_authorized": False,
        },
        "interpretation_authorized": False,
        "next_safe_action":
            "INDEPENDENT_A2_METRIC_DERIVATION_OUTPUT_AUDIT_BEFORE_INTERPRETATION",
    }
    atomic_json(FINAL_SUMMARY, summary)

    work.commit()
    work.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    work.commit()

    work_db_sha = sha256(WORK_DB)
    execution_audit = {
        "status": "G7R6_A2_METRIC_DERIVATION_EXECUTION_AUDIT",
        "executor_sha256": executor_sha256,
        "work_db_sha256": work_db_sha,
        "materialized_db_sha256": EXPECTED_MATERIALIZED_DB_SHA256,
        "production_csv_sha256": EXPECTED_PRODUCTION_CSV_SHA256,
        "completed_events": completed_events,
        "q_object_update_rows": q_rows,
        "s05_s06_change_rows": change_rows,
        "summary_sha256": sha256(FINAL_SUMMARY),
        "tracked_output_hashes": summary["outputs"],
        "operations": summary["operations"],
        "interpretation_authorized": False,
        "A0_execution_authorized": False,
    }
    atomic_json(FINAL_EXECUTION_AUDIT, execution_audit)
    return summary


def synthetic_validation() -> dict:
    # S05/S06: an intervening UNASSOCIATED row must not reset the sequence.
    event = {
        "event_index": 1,
        "conj_id": "synthetic",
        "stratum": "S",
        "reference_us": 20_000_000,
        "expected_eligible_replays": 3,
        "expected_support_replays": 3,
    }
    rows = [
        {
            "replay_sequence": "1",
            "replay_us": "1000000",
            "final_status": "VALID_ASSOCIATED",
            "predicted_tca_utc": "1970-01-01T00:00:10.000000Z",
            "predicted_miss_distance_km": "5.0",
        },
        {
            "replay_sequence": "2",
            "replay_us": "2000000",
            "final_status": "UNASSOCIATED_REFERENCE_EVENT",
            "predicted_tca_utc": "",
            "predicted_miss_distance_km": "",
        },
        {
            "replay_sequence": "3",
            "replay_us": "3000000",
            "final_status": "VALID_ASSOCIATED",
            "predicted_tca_utc": "1970-01-01T00:00:12.500000Z",
            "predicted_miss_distance_km": "8.25",
        },
    ]
    changes, status, s07 = derive_geometry_for_event(event, rows)
    require(len(changes) == 1, "synthetic filtered sequence failed")
    require(
        abs(changes[0]["signed_tca_change_s"] - 2.5) < 1e-12,
        "synthetic S05 arithmetic failed",
    )
    require(
        abs(changes[0]["signed_miss_distance_change_km"] - 3.25) < 1e-12,
        "synthetic S06 arithmetic failed",
    )
    require(status["s05_s06_change_count"] == 1, "synthetic change count failed")
    require(s07["first_valid_associated_replay_sequence"] == 1, "synthetic S07 failed")

    # Open-left rolling window: the record exactly on the left boundary is excluded.
    day = US_PER_DAY
    records = [
        (0 * day, "A"),
        (5 * day, "A"),
        (10 * day, "B"),
    ]
    window = RollingWindowState(records, -100 * day, 10)
    result = window.advance(10 * day)
    require(result["publication_count"] == 2, "open-left window count failed")
    require(result["distinct_state_count"] == 2, "distinct-state count failed")
    require(
        abs(float(result["median_cadence_h"]) - 120.0) < 1e-12,
        "cadence median failed",
    )
    require(
        abs(float(result["max_gap_h"]) - 120.0) < 1e-12,
        "cadence max failed",
    )

    q = quality_base(
        replay_us=10 * US_PER_HOUR,
        creation_us=8 * US_PER_HOUR,
        epoch_us=7 * US_PER_HOUR,
        associated_tca_us=20 * US_PER_HOUR,
    )
    require(q["q01_state_age_us"] == 3 * US_PER_HOUR, "Q01 arithmetic failed")
    require(q["q02_publication_age_us"] == 2 * US_PER_HOUR, "Q02 arithmetic failed")
    require(
        q["q06_propagation_span_us"] == 13 * US_PER_HOUR,
        "Q06 arithmetic failed",
    )

    negative_state = quality_base(
        replay_us=10 * US_PER_HOUR,
        creation_us=10 * US_PER_HOUR,
        epoch_us=11 * US_PER_HOUR,
        associated_tca_us=None,
    )
    require(negative_state["q01_negative_flag"] is True, "negative Q01 not flagged")

    causal_violation_blocked = False
    try:
        quality_base(
            replay_us=10 * US_PER_HOUR,
            creation_us=11 * US_PER_HOUR,
            epoch_us=9 * US_PER_HOUR,
            associated_tca_us=None,
        )
    except A2MetricError:
        causal_violation_blocked = True
    require(causal_violation_blocked, "negative Q02 was not blocked")

    return {
        "filtered_valid_associated_sequence": "PASS",
        "intervening_unassociated_does_not_reset": "PASS",
        "s05_arithmetic": "PASS",
        "s06_arithmetic": "PASS",
        "s07_earliest_pre_tca": "PASS",
        "rolling_open_left_closed_right": "PASS",
        "distinct_state_counter": "PASS",
        "cadence_gap_arithmetic": "PASS",
        "q01_q02_q06_arithmetic": "PASS",
        "negative_q01_flag": "PASS",
        "negative_q02_block": "PASS",
    }


def offline_validate() -> dict:
    require(
        git_value("rev-parse", "HEAD") == PARENT_COMMIT,
        "offline validation must run at DR-0065 commit",
    )
    require(
        tag_target(PARENT_TAG) == PARENT_COMMIT,
        "metric-semantics freeze tag mismatch",
    )
    require(
        not OFFLINE_VALIDATION.exists(),
        "metric implementation offline-validation output already exists",
    )

    for path in (
        WORK_DB,
        Path(str(WORK_DB) + "-wal"),
        Path(str(WORK_DB) + "-shm"),
        S05_S06_CHANGES_CSV,
        S05_S06_EVENT_STATUS_CSV,
        S07_EVENT_CSV,
        FINAL_SUMMARY,
        FINAL_EXECUTION_AUDIT,
    ):
        require(
            not path.exists(),
            f"metric execution artifact already exists before implementation freeze: {path}",
        )

    ignore = git("check-ignore", "--quiet", str(WORK_DB), check=False)
    require(ignore.returncode == 0, "metric derived/checkpoint DB is not Git-ignored")

    frozen = verify_frozen_inputs(hash_materialized_db=True)
    synthetic = synthetic_validation()

    blocked = False
    try:
        execute_metrics(authorization=False)
    except A2MetricExecutionNotAuthorized:
        blocked = True
    require(blocked, "metric executor did not block authorization=False")

    result = {
        "status": "G7R6_A2_METRIC_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "executor_sha256": sha256(Path(__file__)),
        "frozen_inputs": frozen,
        "synthetic_validation": synthetic,
        "output_plan": {
            "local_derived_db": WORK_DB.relative_to(ROOT).as_posix(),
            "tracked_outputs": [
                S05_S06_CHANGES_CSV.relative_to(ROOT).as_posix(),
                S05_S06_EVENT_STATUS_CSV.relative_to(ROOT).as_posix(),
                S07_EVENT_CSV.relative_to(ROOT).as_posix(),
                FINAL_SUMMARY.relative_to(ROOT).as_posix(),
                FINAL_EXECUTION_AUDIT.relative_to(ROOT).as_posix(),
            ],
            "q_object_update_rows_expected":
                2 * EXPECTED_PUBLICATION_OPPORTUNITIES,
            "event_transactions_atomic": True,
            "resume_supported": True,
        },
        "semantics": {
            "s05_s06_sequence": "FILTERED_VALID_ASSOCIATED_CHRONOLOGICAL",
            "intervening_failure_resets_sequence": False,
            "s07": "EARLIEST_PRE_TCA_VALID_ASSOCIATED",
            "q01_q05_schedule_rows": EXPECTED_PUBLICATION_OPPORTUNITIES,
            "q03_q05_primary_window_days": PRIMARY_WINDOW_DAYS,
            "q03_q05_secondary_windows_days_frozen": [14, 60],
            "q03_q05_secondary_execution": "DEFERRED_NOT_PART_OF_PRIMARY_IMPLEMENTATION",
            "q03_q05_window_semantics": "(t_r-W,t_r]",
            "materialized_quality_start": "t_ref-120d",
            "report_start": "t_ref-90d",
            "primary_quality_guard_days": 30,
            "q06_requires_valid_associated": True,
        },
        "operations": {
            "real_metric_execution_performed": False,
            "orbital_propagation_performed": False,
            "candidate_generation_performed": False,
            "reference_association_recomputed": False,
            "raw_GP_HISTORY_opened": False,
            "network_requests": 0,
            "A0_execution_authorized": False,
        },
        "metric_execution_blocked_without_authorization": blocked,
        "next_safe_action":
            "INDEPENDENT_REVIEW_THEN_FREEZE_A2_METRIC_IMPLEMENTATION",
    }
    atomic_json(OFFLINE_VALIDATION, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def execute_metrics(*, authorization: bool = False) -> dict:
    if authorization is not True:
        raise A2MetricExecutionNotAuthorized(
            "A2_METRIC_EXECUTION_NOT_AUTHORIZED"
        )

    # A later authorization wrapper must bind the frozen executor hash and tag.
    verify_frozen_inputs(hash_materialized_db=True)

    for path in (
        S05_S06_CHANGES_CSV,
        S05_S06_EVENT_STATUS_CSV,
        S07_EVENT_CSV,
        FINAL_SUMMARY,
        FINAL_EXECUTION_AUDIT,
    ):
        require(
            not path.exists(),
            f"final metric artifact already exists; refusing overwrite: {path}",
        )

    executor_sha = sha256(Path(__file__))
    production_by_event = load_production_by_event()
    materialized = connect_materialized_ro()
    work = create_work_db(executor_sha)

    try:
        completed = {
            int(row[0])
            for row in work.execute(
                "SELECT event_index FROM event_completion"
            ).fetchall()
        }
        resumed_events = len(completed)

        events = materialized.execute(
            "SELECT * FROM events ORDER BY event_index"
        ).fetchall()
        require(len(events) == EXPECTED_EVENTS, "event census changed")

        for ordinal, event in enumerate(events, start=1):
            event_index = int(event["event_index"])
            if event_index in completed:
                continue

            result = insert_event(
                work,
                materialized,
                event,
                production_by_event.get(event_index, []),
            )

            if ordinal % 100 == 0 or ordinal == EXPECTED_EVENTS:
                done = int(
                    work.execute(
                        "SELECT COUNT(*) FROM event_completion"
                    ).fetchone()[0]
                )
                q_rows = int(
                    work.execute(
                        "SELECT COALESCE(SUM(q_object_rows),0) FROM event_completion"
                    ).fetchone()[0]
                )
                print(
                    "A2_METRIC_DERIVATION_PROGRESS "
                    f"events={done}/{EXPECTED_EVENTS} "
                    f"q_object_rows={q_rows}",
                    flush=True,
                )

        summary = finalize_outputs(work, executor_sha, resumed_events)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return summary
    finally:
        work.close()
        materialized.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--offline-validate", action="store_true")
    group.add_argument("--execute-metrics", action="store_true")
    args = parser.parse_args()

    if args.offline_validate:
        offline_validate()
        return 0

    if args.execute_metrics:
        # Direct CLI real execution is deliberately blocked.
        execute_metrics(authorization=False)
        return 0

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
