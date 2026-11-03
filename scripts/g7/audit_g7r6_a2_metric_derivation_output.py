#!/usr/bin/env python3
"""Independent post-execution audit for G7 R6 A2 primary metric derivation.

This audit does not interpret scientific performance. It independently checks
the frozen execution boundary, output identities, complete event/replay/Q-row
accounting, S05/S06/S07 derivation semantics, Q01/Q02/Q06 arithmetic, and
primary 30-day Q03-Q05 semantics.

No orbital propagation, candidate generation/refinement, reference
reassociation, raw GP_HISTORY access, network access, CAL0 refit, secondary
14/60-day execution, or A0 execution is performed.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
import sqlite3
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

AUTH_COMMIT = "368381ee9ab6194d3f51753c202143d0cb0296bb"
AUTH_TAG = "g7-r6-a2-primary-metric-execution-authorized"

EXECUTOR = ROOT / "scripts/g7/run_g7r6_a2_metric_analysis.py"
AUTH_WRAPPER = ROOT / "scripts/g7/run_g7r6_a2_metric_execution_authorization.py"
MATERIALIZED_DB = ROOT / "data/raw/g7/G7R6_A2_Materialized.sqlite3"
PRODUCTION_CSV = ROOT / "results/g7/G7R6_A2_Production_Replay_Results.csv"
WORK_DB = ROOT / "data/raw/g7/G7R6_A2_Metric_Derived.sqlite3"

CHANGES_CSV = ROOT / "results/g7/G7R6_A2_Metric_S05_S06_Changes.csv"
EVENT_STATUS_CSV = ROOT / "results/g7/G7R6_A2_Metric_S05_S06_Event_Status.csv"
S07_CSV = ROOT / "results/g7/G7R6_A2_Metric_S07_Event_Lead.csv"
SUMMARY_JSON = ROOT / "results/g7/G7R6_A2_Metric_Derivation_Summary.json"
EXECUTOR_AUDIT_JSON = ROOT / "results/g7/G7R6_A2_Metric_Derivation_Execution_Audit.json"
AUTH_AUDIT_JSON = ROOT / "results/g7/G7R6_A2_Metric_Execution_Authorization_Audit.json"

OUT = ROOT / "results/g7/G7R6_A2_Metric_Derivation_Independent_Audit.json"

EXPECTED_EXECUTOR_SHA256 = "6fc04230f230c79053bc1e9e5a9f2887bd9842fa07f234667122a1f051b2cc51"
EXPECTED_AUTH_WRAPPER_SHA256 = "80666b0b915a5a003f3eefc1a02cd0433abf329bd2e78d1bd2fb2ef84551b4bc"
EXPECTED_MATERIALIZED_SHA256 = "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"
EXPECTED_PRODUCTION_SHA256 = "a0217011e0695c240cbc1209f671c2ad7a8cb1948093b1f8c075146819e9efdd"

EXPECTED_OUTPUT_HASHES = {
    CHANGES_CSV: "166ff82ef9587631abaa092a07e8035824093338102faac56c1dce2ae266a202",
    EVENT_STATUS_CSV: "5444c3091f4ff51db37aff343f84b9340fcda30b10098c1b7fa2d988f42e0733",
    S07_CSV: "79bcd812d9c4e00453f6cbf43c2df5534c56fdc4a4278348696c9eeedd3fab77",
    SUMMARY_JSON: "155aaa3ad1cebdfd14b7eb8879577b8099f46b4ce88021bd5eac96216e1c6c4f",
    EXECUTOR_AUDIT_JSON: "f8716820041918ae52461f08fa71963c78ad666094b48c42dcf0daf08bf317e2",
    AUTH_AUDIT_JSON: "de4f8b28913b2876432419f8a1bbd6556e68bf924afbe92e76ce7de1646dd147",
}
EXPECTED_WORK_DB_SHA256 = "6bdea76ca02bffe9cc33e50d8e899f898e27445198d2ed244cc0cd1692850001"

EXPECTED_EVENTS = 6_927
EXPECTED_OBJECTS = 13_854
EXPECTED_PUBLICATION_OPPORTUNITIES = 2_020_081
EXPECTED_SUPPORT_REPLAYS = 107_740
EXPECTED_Q_OBJECT_ROWS = 4_040_162
EXPECTED_VALID_ASSOCIATED = 106_446
EXPECTED_CHANGE_ROWS = 99_545
EXPECTED_S05_S06_VALID_EVENTS = 6_886
EXPECTED_S05_S06_INSUFFICIENT_EVENTS = 41
EXPECTED_S07_VALID_EVENTS = 6_901
EXPECTED_S07_NO_PRE_TCA_EVENTS = 26

US_PER_HOUR = 3_600_000_000
US_PER_DAY = 86_400_000_000
WINDOW_US = 30 * US_PER_DAY

MISSING_CHANGE_STATUS = "INSUFFICIENT_SUCCESSIVE_VALID_ASSOCIATED_RECONSTRUCTIONS"
S07_MISSING = "NO PRE-TCA RECONSTRUCTION"


class AuditError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


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


def parse_utc_us(text: str) -> int:
    value = str(text).strip()
    require(bool(value), "empty UTC timestamp")
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    require(dt.tzinfo is not None, f"naive timestamp: {text!r}")
    return int(round(dt.astimezone(UTC).timestamp() * 1_000_000))


def almost_equal(a: float, b: float, atol: float = 1e-11) -> bool:
    return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=atol)


def ro_connect(path: Path) -> sqlite3.Connection:
    require(path.is_file(), f"missing SQLite database: {path}")
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def verify_git_boundary() -> dict:
    require(git_value("rev-parse", "HEAD") == AUTH_COMMIT, "unexpected audit HEAD")
    require(
        git_value("rev-list", "-n", "1", AUTH_TAG) == AUTH_COMMIT,
        "authorization tag target changed",
    )
    require(
        sha256(EXECUTOR) == EXPECTED_EXECUTOR_SHA256,
        "frozen metric executor SHA changed",
    )
    require(
        sha256(AUTH_WRAPPER) == EXPECTED_AUTH_WRAPPER_SHA256,
        "frozen authorization wrapper SHA changed",
    )
    return {
        "head": AUTH_COMMIT,
        "authorization_tag": AUTH_TAG,
        "executor_sha256": EXPECTED_EXECUTOR_SHA256,
        "authorization_wrapper_sha256": EXPECTED_AUTH_WRAPPER_SHA256,
    }


def verify_output_hashes() -> dict:
    observed = {}
    for path, expected in EXPECTED_OUTPUT_HASHES.items():
        require(path.is_file(), f"expected final output missing: {path}")
        actual = sha256(path)
        require(
            actual == expected,
            f"final output SHA mismatch: {path.relative_to(ROOT).as_posix()}",
        )
        observed[path.relative_to(ROOT).as_posix()] = actual

    require(WORK_DB.is_file(), "metric derived SQLite DB missing")
    work_sha = sha256(WORK_DB)
    require(work_sha == EXPECTED_WORK_DB_SHA256, "metric derived DB SHA mismatch")

    return {
        "tracked_output_hashes": observed,
        "work_db_sha256": work_sha,
    }


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def verify_reported_execution() -> dict:
    summary = load_json(SUMMARY_JSON)
    executor_audit = load_json(EXECUTOR_AUDIT_JSON)
    auth_audit = load_json(AUTH_AUDIT_JSON)

    require(
        summary["status"] == "G7R6_A2_METRIC_DERIVATION_COMPLETED",
        "metric summary status changed",
    )
    require(
        auth_audit["status"] == "G7R6_A2_PRIMARY_METRIC_AUTHORIZED_EXECUTION_COMPLETED",
        "authorization execution audit status changed",
    )
    require(int(summary["events"]) == EXPECTED_EVENTS, "summary event count changed")
    require(
        int(summary["publication_replay_opportunities"])
        == EXPECTED_PUBLICATION_OPPORTUNITIES,
        "summary publication-opportunity count changed",
    )
    require(
        int(summary["q_object_update_rows"]) == EXPECTED_Q_OBJECT_ROWS,
        "summary Q object-row count changed",
    )
    require(
        int(summary["valid_associated_reconstructions"])
        == EXPECTED_VALID_ASSOCIATED,
        "summary valid-associated count changed",
    )
    require(
        int(summary["s05_s06_change_rows"]) == EXPECTED_CHANGE_ROWS,
        "summary S05/S06 change count changed",
    )
    require(
        int(summary["primary_30d_left_censored_object_rows"]) == 0,
        "primary 30 d left-censor count changed",
    )
    require(int(summary["q01_negative_state_age_rows"]) == 0, "Q01 negative-row count changed")
    require(int(summary["q02_causal_violations"]) == 0, "Q02 causal violation count changed")
    require(
        int(summary["q06_defined_object_rows"]) == 2 * EXPECTED_VALID_ASSOCIATED,
        "Q06 defined-row accounting changed",
    )
    require(
        summary["s05_s06_event_status_counts"]
        == {
            MISSING_CHANGE_STATUS: EXPECTED_S05_S06_INSUFFICIENT_EVENTS,
            "VALID": EXPECTED_S05_S06_VALID_EVENTS,
        },
        "S05/S06 event-status counts changed",
    )
    require(
        summary["s07_status_counts"]
        == {
            S07_MISSING: EXPECTED_S07_NO_PRE_TCA_EVENTS,
            "VALID": EXPECTED_S07_VALID_EVENTS,
        },
        "S07 status counts changed",
    )
    require(
        summary["secondary_quality_window_execution"]["14d"]
        == "DEFERRED_NOT_EXECUTED_IN_PRIMARY_A2_METRIC_PASS",
        "14 d secondary-window status changed",
    )
    require(
        summary["secondary_quality_window_execution"]["60d"]
        == "DEFERRED_NOT_EXECUTED_IN_PRIMARY_A2_METRIC_PASS",
        "60 d secondary-window status changed",
    )

    require(
        executor_audit["work_db_sha256"] == EXPECTED_WORK_DB_SHA256,
        "executor audit work-DB hash changed",
    )
    require(
        executor_audit["summary_sha256"] == EXPECTED_OUTPUT_HASHES[SUMMARY_JSON],
        "executor audit summary hash changed",
    )
    require(int(executor_audit["completed_events"]) == EXPECTED_EVENTS, "executor-audit event count changed")
    require(
        int(executor_audit["q_object_update_rows"]) == EXPECTED_Q_OBJECT_ROWS,
        "executor-audit Q row count changed",
    )

    require(
        auth_audit["authorization_commit"] == AUTH_COMMIT,
        "authorization audit commit changed",
    )
    require(
        auth_audit["authorization_tag_target"] == AUTH_COMMIT,
        "authorization audit tag target changed",
    )
    require(
        auth_audit["work_db_sha256"] == EXPECTED_WORK_DB_SHA256,
        "authorization audit work-DB hash changed",
    )
    require(
        auth_audit["large_input_hashes"][
            "data/raw/g7/G7R6_A2_Materialized.sqlite3"
        ] == EXPECTED_MATERIALIZED_SHA256,
        "authorization audit materialized-DB execution-time hash changed",
    )
    require(
        auth_audit["large_input_hashes"][
            "results/g7/G7R6_A2_Production_Replay_Results.csv"
        ] == EXPECTED_PRODUCTION_SHA256,
        "authorization audit production-CSV execution-time hash changed",
    )
    require(
        auth_audit["checkpoint_before_invocation"]["checkpoint_exists"] is False,
        "execution unexpectedly resumed from a pre-existing checkpoint",
    )

    for source in (summary["operations"], executor_audit["operations"]):
        require(source["orbital_propagation_performed"] is False, "orbital propagation reported")
        require(source["candidate_generation_performed"] is False, "candidate generation reported")
        require(source["reference_association_recomputed"] is False, "reassociation reported")
        require(source["raw_GP_HISTORY_opened"] is False, "raw GP_HISTORY access reported")
        require(int(source["network_requests"]) == 0, "network request reported")
        require(source["A0_execution_authorized"] is False, "A0 authorization reported")

    require(auth_audit["secondary_14d_60d_execution_performed"] is False, "secondary execution reported")
    require(auth_audit["interpretation_authorized"] is False, "interpretation unexpectedly authorized")

    return {
        "summary_status": summary["status"],
        "authorization_execution_status": auth_audit["status"],
        "execution_was_fresh_not_resumed": True,
        "execution_time_materialized_db_sha256": EXPECTED_MATERIALIZED_SHA256,
        "execution_time_production_csv_sha256": EXPECTED_PRODUCTION_SHA256,
    }


def verify_work_db_structure() -> dict:
    con = ro_connect(WORK_DB)
    try:
        require(con.execute("PRAGMA quick_check").fetchone()[0] == "ok", "work DB quick_check failed")

        metadata = dict(con.execute("SELECT key,value FROM metadata").fetchall())
        expected_metadata = {
            "schema_version": "1",
            "executor_sha256": EXPECTED_EXECUTOR_SHA256,
            "parent_commit": "68e1a188db9ecc18d80c65655e75e05c4cb123b1",
            "parent_tag": "g7-r6-a2-metric-semantics-frozen",
            "materialized_db_sha256": EXPECTED_MATERIALIZED_SHA256,
            "production_csv_sha256": EXPECTED_PRODUCTION_SHA256,
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
        require(metadata == expected_metadata, "work DB metadata differs from frozen execution semantics")

        counts = {
            "event_completion": int(con.execute("SELECT COUNT(*) FROM event_completion").fetchone()[0]),
            "s05_s06_changes": int(con.execute("SELECT COUNT(*) FROM s05_s06_changes").fetchone()[0]),
            "s05_s06_event_status": int(con.execute("SELECT COUNT(*) FROM s05_s06_event_status").fetchone()[0]),
            "s07_event_lead": int(con.execute("SELECT COUNT(*) FROM s07_event_lead").fetchone()[0]),
            "q_object_updates": int(con.execute("SELECT COUNT(*) FROM q_object_updates").fetchone()[0]),
        }
        require(counts["event_completion"] == EXPECTED_EVENTS, "event_completion row count changed")
        require(counts["s05_s06_changes"] == EXPECTED_CHANGE_ROWS, "work-DB change count changed")
        require(counts["s05_s06_event_status"] == EXPECTED_EVENTS, "work-DB event-status count changed")
        require(counts["s07_event_lead"] == EXPECTED_EVENTS, "work-DB S07 event count changed")
        require(counts["q_object_updates"] == EXPECTED_Q_OBJECT_ROWS, "work-DB Q row count changed")

        sums = con.execute(
            """
            SELECT
                SUM(publication_replays),
                SUM(q_object_rows),
                SUM(change_rows),
                SUM(valid_associated_reconstructions)
            FROM event_completion
            """
        ).fetchone()
        require(int(sums[0]) == EXPECTED_PUBLICATION_OPPORTUNITIES, "event-completion replay sum changed")
        require(int(sums[1]) == EXPECTED_Q_OBJECT_ROWS, "event-completion Q-row sum changed")
        require(int(sums[2]) == EXPECTED_CHANGE_ROWS, "event-completion change sum changed")
        require(int(sums[3]) == EXPECTED_VALID_ASSOCIATED, "event-completion valid-associated sum changed")

        per_event_bad = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM s05_s06_event_status
                WHERE
                    s05_s06_change_count <>
                        CASE
                            WHEN valid_associated_reconstructions >= 1
                            THEN valid_associated_reconstructions - 1
                            ELSE 0
                        END
                    OR s05_status <> s06_status
                    OR (
                        valid_associated_reconstructions >= 2
                        AND s05_status <> 'VALID'
                    )
                    OR (
                        valid_associated_reconstructions < 2
                        AND s05_status <>
                            'INSUFFICIENT_SUCCESSIVE_VALID_ASSOCIATED_RECONSTRUCTIONS'
                    )
                """
            ).fetchone()[0]
        )
        require(per_event_bad == 0, "S05/S06 per-event accounting invariant failed")

        q_identity_groups_bad = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT
                        event_index,
                        replay_sequence,
                        COUNT(*) AS c,
                        SUM(CASE WHEN object_role='obj1' THEN 1 ELSE 0 END) AS n1,
                        SUM(CASE WHEN object_role='obj2' THEN 1 ELSE 0 END) AS n2
                    FROM q_object_updates
                    GROUP BY event_index,replay_sequence
                    HAVING c<>2 OR n1<>1 OR n2<>1
                )
                """
            ).fetchone()[0]
        )
        require(q_identity_groups_bad == 0, "Q rows are not exactly two roles per replay")

        q_arithmetic_bad = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM q_object_updates
                WHERE
                    q01_state_age_us <> replay_us-current_epoch_us
                    OR q01_negative_flag <>
                        CASE WHEN replay_us-current_epoch_us < 0 THEN 1 ELSE 0 END
                    OR q02_publication_age_us <> replay_us-current_creation_us
                    OR q02_publication_age_us < 0
                    OR ABS(q01_state_age_h - q01_state_age_us/3600000000.0) > 1e-11
                    OR ABS(q02_publication_age_h - q02_publication_age_us/3600000000.0) > 1e-11
                """
            ).fetchone()[0]
        )
        require(q_arithmetic_bad == 0, "Q01/Q02 arithmetic invariant failed")

        q_window_invariant_bad = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM q_object_updates
                WHERE
                    w30_left_censored <> 0
                    OR w30_distinct_state_count > w30_publication_count
                    OR (w30_publication_count=0 AND w30_distinct_state_count<>0)
                    OR (w30_publication_count>0 AND w30_distinct_state_count<1)
                    OR (
                        w30_publication_count<2
                        AND (w30_median_cadence_h IS NOT NULL OR w30_max_gap_h IS NOT NULL)
                    )
                    OR (
                        w30_publication_count>=2
                        AND (w30_median_cadence_h IS NULL OR w30_max_gap_h IS NULL)
                    )
                    OR (w30_median_cadence_h IS NOT NULL AND w30_median_cadence_h<0)
                    OR (w30_max_gap_h IS NOT NULL AND w30_max_gap_h<0)
                    OR (
                        w30_median_cadence_h IS NOT NULL
                        AND w30_max_gap_h + 1e-12 < w30_median_cadence_h
                    )
                """
            ).fetchone()[0]
        )
        require(q_window_invariant_bad == 0, "Q03-Q05 whole-table logical invariant failed")

        return {
            "database_quick_check": "ok",
            "table_counts": counts,
            "event_completion_sums": {
                "publication_replays": int(sums[0]),
                "q_object_rows": int(sums[1]),
                "change_rows": int(sums[2]),
                "valid_associated_reconstructions": int(sums[3]),
            },
            "s05_s06_per_event_accounting_mismatches": per_event_bad,
            "q_two_roles_per_replay_mismatches": q_identity_groups_bad,
            "q01_q02_arithmetic_mismatches": q_arithmetic_bad,
            "q03_q05_whole_table_invariant_mismatches": q_window_invariant_bad,
        }
    finally:
        con.close()


def load_production() -> tuple[dict[int, list[dict]], Counter]:
    by_event: dict[int, list[dict]] = defaultdict(list)
    statuses = Counter()
    total = 0
    with PRODUCTION_CSV.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            event_index = int(row["event_index"])
            by_event[event_index].append(row)
            statuses[row["final_status"]] += 1
            total += 1
    require(total == EXPECTED_SUPPORT_REPLAYS, "production CSV row count changed")
    require(
        statuses
        == Counter(
            {
                "VALID_ASSOCIATED": 106_446,
                "UNASSOCIATED_REFERENCE_EVENT": 950,
                "UNRECOVERED_REFINEMENT_FAILURE": 335,
                "PROPAGATION_FAILURE": 6,
                "NO_VALID_CANDIDATE": 3,
            }
        ),
        f"frozen production status census changed: {dict(statuses)}",
    )
    return by_event, statuses


def load_csv_by_event(path: Path) -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = defaultdict(list)
    with path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            result[int(row["event_index"])].append(row)
    return result


def verify_s05_s06_s07_against_production(production: dict[int, list[dict]]) -> dict:
    changes = load_csv_by_event(CHANGES_CSV)
    event_status = load_csv_by_event(EVENT_STATUS_CSV)
    s07 = load_csv_by_event(S07_CSV)

    require(sum(map(len, changes.values())) == EXPECTED_CHANGE_ROWS, "changes CSV row count changed")
    require(sum(map(len, event_status.values())) == EXPECTED_EVENTS, "event-status CSV row count changed")
    require(sum(map(len, s07.values())) == EXPECTED_EVENTS, "S07 CSV row count changed")

    mat = ro_connect(MATERIALIZED_DB)
    try:
        events = {
            int(row["event_index"]): dict(row)
            for row in mat.execute("SELECT * FROM events ORDER BY event_index")
        }
    finally:
        mat.close()

    require(len(events) == EXPECTED_EVENTS, "materialized event census changed")

    mismatch = Counter()
    events_with_valid = 0
    events_with_two_plus = 0
    zero_valid = 0
    exactly_one_valid = 0
    expected_changes_total = 0

    for event_index in range(1, EXPECTED_EVENTS + 1):
        event = events[event_index]
        rows = sorted(
            production.get(event_index, []),
            key=lambda r: (int(r["replay_us"]), int(r["replay_sequence"])),
        )
        valid = [r for r in rows if r["final_status"] == "VALID_ASSOCIATED"]

        if valid:
            events_with_valid += 1
        else:
            zero_valid += 1
        if len(valid) >= 2:
            events_with_two_plus += 1
        elif len(valid) == 1:
            exactly_one_valid += 1

        expected_change_count = max(0, len(valid) - 1)
        expected_changes_total += expected_change_count

        status_rows = event_status.get(event_index, [])
        if len(status_rows) != 1:
            mismatch["event_status_cardinality"] += 1
        else:
            status = status_rows[0]
            expected_status = "VALID" if len(valid) >= 2 else MISSING_CHANGE_STATUS
            if int(status["valid_associated_reconstructions"]) != len(valid):
                mismatch["event_status_valid_associated"] += 1
            if int(status["s05_s06_change_count"]) != expected_change_count:
                mismatch["event_status_change_count"] += 1
            if status["s05_status"] != expected_status or status["s06_status"] != expected_status:
                mismatch["event_status_semantics"] += 1
            if int(status["publication_replay_opportunities"]) != int(event["expected_eligible_replays"]):
                mismatch["event_status_replay_opportunities"] += 1
            if int(status["temporal_support_replays"]) != int(event["expected_support_replays"]):
                mismatch["event_status_support_replays"] += 1

        actual_changes = sorted(
            changes.get(event_index, []),
            key=lambda r: int(r["change_sequence"]),
        )
        if len(actual_changes) != expected_change_count:
            mismatch["change_cardinality"] += 1
        else:
            for seq, (previous, current, actual) in enumerate(
                zip(valid, valid[1:], actual_changes),
                start=1,
            ):
                if int(actual["change_sequence"]) != seq:
                    mismatch["change_sequence"] += 1
                    continue
                if int(actual["previous_replay_sequence"]) != int(previous["replay_sequence"]):
                    mismatch["previous_replay_sequence"] += 1
                if int(actual["current_replay_sequence"]) != int(current["replay_sequence"]):
                    mismatch["current_replay_sequence"] += 1
                if int(actual["previous_replay_us"]) != int(previous["replay_us"]):
                    mismatch["previous_replay_us"] += 1
                if int(actual["current_replay_us"]) != int(current["replay_us"]):
                    mismatch["current_replay_us"] += 1

                expected_gap_h = (
                    int(current["replay_us"]) - int(previous["replay_us"])
                ) / US_PER_HOUR
                if not almost_equal(float(actual["publication_gap_h"]), expected_gap_h):
                    mismatch["publication_gap_h"] += 1

                prev_tca_us = parse_utc_us(previous["predicted_tca_utc"])
                curr_tca_us = parse_utc_us(current["predicted_tca_utc"])
                signed_tca = (curr_tca_us - prev_tca_us) / 1_000_000.0
                if not almost_equal(float(actual["signed_tca_change_s"]), signed_tca):
                    mismatch["signed_tca_change_s"] += 1
                if not almost_equal(float(actual["absolute_tca_change_s"]), abs(signed_tca)):
                    mismatch["absolute_tca_change_s"] += 1

                prev_miss = float(previous["predicted_miss_distance_km"])
                curr_miss = float(current["predicted_miss_distance_km"])
                signed_miss = curr_miss - prev_miss
                if not almost_equal(float(actual["signed_miss_distance_change_km"]), signed_miss):
                    mismatch["signed_miss_change"] += 1
                if not almost_equal(float(actual["absolute_miss_distance_change_km"]), abs(signed_miss)):
                    mismatch["absolute_miss_change"] += 1

        s07_rows = s07.get(event_index, [])
        if len(s07_rows) != 1:
            mismatch["s07_cardinality"] += 1
        else:
            actual = s07_rows[0]
            pre_tca_valid = [
                row for row in valid
                if int(row["replay_us"]) < int(event["reference_us"])
            ]
            if pre_tca_valid:
                expected_first = min(
                    pre_tca_valid,
                    key=lambda r: (int(r["replay_us"]), int(r["replay_sequence"])),
                )
                expected_lead_us = int(event["reference_us"]) - int(expected_first["replay_us"])
                if actual["s07_status"] != "VALID":
                    mismatch["s07_status"] += 1
                if int(actual["first_valid_associated_replay_sequence"]) != int(expected_first["replay_sequence"]):
                    mismatch["s07_first_sequence"] += 1
                if int(actual["first_valid_associated_replay_us"]) != int(expected_first["replay_us"]):
                    mismatch["s07_first_replay_us"] += 1
                if int(actual["lead_us"]) != expected_lead_us:
                    mismatch["s07_lead_us"] += 1
                if expected_lead_us <= 0:
                    mismatch["s07_nonpositive_expected_lead"] += 1
                if not almost_equal(float(actual["lead_h"]), expected_lead_us / US_PER_HOUR):
                    mismatch["s07_lead_h"] += 1
            else:
                if actual["s07_status"] != S07_MISSING:
                    mismatch["s07_missing_status"] += 1
                for field in (
                    "first_valid_associated_replay_sequence",
                    "first_valid_associated_replay_us",
                    "first_valid_associated_replay_utc",
                    "lead_us",
                    "lead_h",
                ):
                    if actual[field] != "":
                        mismatch[f"s07_missing_nonblank_{field}"] += 1

    require(not mismatch, f"S05/S06/S07 independent reconstruction mismatches: {dict(mismatch)}")
    require(events_with_valid == EXPECTED_S07_VALID_EVENTS, "events-with-valid accounting changed")
    require(events_with_two_plus == EXPECTED_S05_S06_VALID_EVENTS, "events-with-two-plus accounting changed")
    require(zero_valid == EXPECTED_S07_NO_PRE_TCA_EVENTS, "zero-valid event count changed")
    require(exactly_one_valid == 15, "exactly-one-valid event count changed")
    require(expected_changes_total == EXPECTED_CHANGE_ROWS, "independent change total changed")

    return {
        "mismatches": 0,
        "events_with_at_least_one_valid_associated": events_with_valid,
        "events_with_at_least_two_valid_associated": events_with_two_plus,
        "events_with_zero_valid_associated": zero_valid,
        "events_with_exactly_one_valid_associated": exactly_one_valid,
        "independently_reconstructed_change_rows": expected_changes_total,
    }


def verify_q_identity_against_materialized() -> dict:
    con = ro_connect(WORK_DB)
    try:
        # Attach the already frozen materialized database read-only.
        mat_uri = MATERIALIZED_DB.resolve().as_uri() + "?mode=ro"
        con.execute("ATTACH DATABASE ? AS mat", (mat_uri,))

        joined = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM q_object_updates q
                JOIN mat.replay_updates r
                  ON r.event_index=q.event_index
                 AND r.replay_sequence=q.replay_sequence
                """
            ).fetchone()[0]
        )
        require(joined == EXPECTED_Q_OBJECT_ROWS, "Q/materialized replay join coverage changed")

        mismatches = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM q_object_updates q
                JOIN mat.replay_updates r
                  ON r.event_index=q.event_index
                 AND r.replay_sequence=q.replay_sequence
                JOIN mat.events e
                  ON e.event_index=q.event_index
                WHERE
                    q.replay_us <> r.replay_us
                    OR (
                        q.object_role='obj1'
                        AND (
                            q.object_id <> e.obj1
                            OR q.current_creation_us <> r.obj1_creation_us
                            OR q.current_epoch_us <> r.obj1_epoch_us
                            OR q.current_gp_id <> r.obj1_gp_id
                        )
                    )
                    OR (
                        q.object_role='obj2'
                        AND (
                            q.object_id <> e.obj2
                            OR q.current_creation_us <> r.obj2_creation_us
                            OR q.current_epoch_us <> r.obj2_epoch_us
                            OR q.current_gp_id <> r.obj2_gp_id
                        )
                    )
                    OR q.object_role NOT IN ('obj1','obj2')
                """
            ).fetchone()[0]
        )
        require(mismatches == 0, "Q selected-GP identity mismatch against materialized schedule")
        return {
            "joined_q_rows": joined,
            "selected_gp_identity_mismatches": mismatches,
        }
    finally:
        con.close()


def verify_q06_against_production(production: dict[int, list[dict]]) -> dict:
    associated_tca = {}
    for event_index, rows in production.items():
        for row in rows:
            if row["final_status"] == "VALID_ASSOCIATED":
                associated_tca[(event_index, int(row["replay_sequence"]))] = parse_utc_us(
                    row["predicted_tca_utc"]
                )

    require(len(associated_tca) == EXPECTED_VALID_ASSOCIATED, "valid-associated map count changed")

    con = ro_connect(WORK_DB)
    mismatches = Counter()
    rows_checked = 0
    valid_rows = 0
    try:
        cursor = con.execute(
            """
            SELECT
                event_index,replay_sequence,current_epoch_us,
                q06_status,q06_propagation_span_us,q06_propagation_span_h
            FROM q_object_updates
            ORDER BY event_index,replay_sequence,object_role
            """
        )
        for row in cursor:
            rows_checked += 1
            key = (int(row["event_index"]), int(row["replay_sequence"]))
            tca_us = associated_tca.get(key)
            if tca_us is None:
                if row["q06_status"] != "NOT_APPLICABLE_NON_ASSOCIATED":
                    mismatches["nonassociated_status"] += 1
                if row["q06_propagation_span_us"] is not None:
                    mismatches["nonassociated_span_us"] += 1
                if row["q06_propagation_span_h"] is not None:
                    mismatches["nonassociated_span_h"] += 1
            else:
                valid_rows += 1
                expected_us = tca_us - int(row["current_epoch_us"])
                if row["q06_status"] != "VALID_ASSOCIATED":
                    mismatches["associated_status"] += 1
                if int(row["q06_propagation_span_us"]) != expected_us:
                    mismatches["associated_span_us"] += 1
                if not almost_equal(
                    float(row["q06_propagation_span_h"]),
                    expected_us / US_PER_HOUR,
                ):
                    mismatches["associated_span_h"] += 1
    finally:
        con.close()

    require(rows_checked == EXPECTED_Q_OBJECT_ROWS, "Q06 audit row coverage changed")
    require(valid_rows == 2 * EXPECTED_VALID_ASSOCIATED, "Q06 defined row count changed")
    require(not mismatches, f"Q06 independent arithmetic mismatches: {dict(mismatches)}")

    return {
        "q_rows_checked": rows_checked,
        "q06_defined_rows_checked": valid_rows,
        "q06_mismatches": 0,
    }


def expected_window_metrics(
    records: list[tuple[int, str]],
    replay_us: int,
    quality_start_us: int,
) -> dict:
    creations = [item[0] for item in records]
    start_us = replay_us - WINDOW_US
    left = bisect.bisect_right(creations, start_us)
    right = bisect.bisect_right(creations, replay_us)
    selected = records[left:right]

    publication_count = len(selected)
    distinct_state_count = len({signature for _, signature in selected})

    gaps = [
        selected[i][0] - selected[i - 1][0]
        for i in range(1, len(selected))
    ]
    require(all(gap >= 0 for gap in gaps), "negative independent cadence gap")

    return {
        "left_censored": quality_start_us > start_us,
        "publication_count": publication_count,
        "distinct_state_count": distinct_state_count,
        "median_cadence_h":
            None if not gaps else statistics.median(gaps) / US_PER_HOUR,
        "max_gap_h":
            None if not gaps else max(gaps) / US_PER_HOUR,
    }


def verify_primary_window_deterministic_sample() -> dict:
    mat = ro_connect(MATERIALIZED_DB)
    work = ro_connect(WORK_DB)
    sample_rows = 0
    mismatches = Counter()

    try:
        events = mat.execute(
            """
            SELECT
                event_index,quality_start_us,expected_eligible_replays,obj1,obj2
            FROM events
            ORDER BY event_index
            """
        ).fetchall()

        require(len(events) == EXPECTED_EVENTS, "materialized event census changed")

        for event in events:
            event_index = int(event["event_index"])
            n = int(event["expected_eligible_replays"])
            require(n >= 1, f"event {event_index} has no publication opportunity")

            sequences = sorted({1, (n + 1) // 2, n})
            replay_rows = {
                int(row["replay_sequence"]): row
                for row in mat.execute(
                    f"""
                    SELECT *
                    FROM replay_updates
                    WHERE event_index=?
                      AND replay_sequence IN ({",".join("?" for _ in sequences)})
                    ORDER BY replay_sequence
                    """,
                    (event_index, *sequences),
                )
            }
            if set(replay_rows) != set(sequences):
                mismatches["sample_replay_identity"] += 1
                continue

            histories = {}
            for role in ("obj1", "obj2"):
                object_id = str(event[role])
                histories[role] = [
                    (int(row["creation_us"]), str(row["state_signature"]))
                    for row in mat.execute(
                        """
                        SELECT creation_us,state_signature
                        FROM history_records
                        WHERE object_id=?
                        ORDER BY creation_us,epoch_us,stable_tie_key
                        """,
                        (object_id,),
                    )
                ]

            for sequence in sequences:
                replay = replay_rows[sequence]
                q_rows = {
                    str(row["object_role"]): row
                    for row in work.execute(
                        """
                        SELECT *
                        FROM q_object_updates
                        WHERE event_index=? AND replay_sequence=?
                        """,
                        (event_index, sequence),
                    )
                }
                if set(q_rows) != {"obj1", "obj2"}:
                    mismatches["sample_q_role_identity"] += 1
                    continue

                for role in ("obj1", "obj2"):
                    sample_rows += 1
                    expected = expected_window_metrics(
                        histories[role],
                        int(replay["replay_us"]),
                        int(event["quality_start_us"]),
                    )
                    actual = q_rows[role]

                    if bool(int(actual["w30_left_censored"])) != bool(expected["left_censored"]):
                        mismatches["left_censored"] += 1
                    if int(actual["w30_publication_count"]) != int(expected["publication_count"]):
                        mismatches["publication_count"] += 1
                    if int(actual["w30_distinct_state_count"]) != int(expected["distinct_state_count"]):
                        mismatches["distinct_state_count"] += 1

                    for field, expected_key in (
                        ("w30_median_cadence_h", "median_cadence_h"),
                        ("w30_max_gap_h", "max_gap_h"),
                    ):
                        a = actual[field]
                        e = expected[expected_key]
                        if e is None:
                            if a is not None:
                                mismatches[field] += 1
                        else:
                            if a is None or not almost_equal(float(a), float(e)):
                                mismatches[field] += 1

        require(
            sample_rows >= 2 * EXPECTED_EVENTS,
            "deterministic primary-window sample unexpectedly small",
        )
        require(
            not mismatches,
            f"Q03-Q05 deterministic sample mismatches: {dict(mismatches)}",
        )
        return {
            "selection_rule": "FIRST_MIDDLE_LAST_REPLAY_PER_EVENT_X_BOTH_OBJECT_ROLES",
            "sample_q_object_rows": sample_rows,
            "mismatches": 0,
            "window_days": 30,
            "window_semantics": "(t_r-30d,t_r]",
        }
    finally:
        mat.close()
        work.close()


def main() -> int:
    require(not OUT.exists(), "independent post-execution audit output already exists")

    boundary = verify_git_boundary()
    hashes = verify_output_hashes()
    reported = verify_reported_execution()
    db_structure = verify_work_db_structure()
    production, production_statuses = load_production()
    sequence_audit = verify_s05_s06_s07_against_production(production)
    q_identity = verify_q_identity_against_materialized()
    q06 = verify_q06_against_production(production)
    q_window = verify_primary_window_deterministic_sample()

    # Final cross-accounting identity:
    # total valid-associated updates minus one per event with >=1 valid update
    # equals the number of successive valid-associated differences.
    require(
        EXPECTED_VALID_ASSOCIATED - EXPECTED_S07_VALID_EVENTS
        == EXPECTED_CHANGE_ROWS,
        "global S05/S06 adjacency accounting identity failed",
    )

    result = {
        "status": "G7R6_A2_METRIC_DERIVATION_INDEPENDENT_POST_EXECUTION_AUDIT_PASS",
        "boundary": boundary,
        "hash_verification": hashes,
        "reported_execution_verification": reported,
        "work_db_verification": db_structure,
        "production_status_counts": dict(sorted(production_statuses.items())),
        "s05_s06_s07_independent_reconstruction": sequence_audit,
        "q_selected_gp_identity_audit": q_identity,
        "q06_independent_arithmetic_audit": q06,
        "q03_q05_primary_window_deterministic_audit": q_window,
        "global_accounting_identity": {
            "valid_associated_reconstructions": EXPECTED_VALID_ASSOCIATED,
            "events_with_at_least_one_valid_associated": EXPECTED_S07_VALID_EVENTS,
            "successive_change_rows":
                EXPECTED_VALID_ASSOCIATED - EXPECTED_S07_VALID_EVENTS,
            "expected_successive_change_rows": EXPECTED_CHANGE_ROWS,
            "status": "PASS",
        },
        "operations": {
            "scientific_interpretation_performed": False,
            "orbital_propagation_performed": False,
            "candidate_generation_performed": False,
            "candidate_refinement_performed": False,
            "reference_association_recomputed": False,
            "raw_GP_HISTORY_opened": False,
            "network_requests": 0,
            "CAL0_refit_performed": False,
            "secondary_14d_60d_metric_execution_performed": False,
            "A0_execution_performed": False,
        },
        "audit_scope_note": (
            "Q01/Q02 arithmetic and whole-table Q03-Q05 logical invariants were "
            "checked over all 4,040,162 object-update rows. Selected-GP identity "
            "was checked over all 4,040,162 rows against the frozen materialized "
            "schedule. Q06 was independently recomputed for all object-update rows. "
            "Q03-Q05 exact history-window values were independently recomputed for "
            "a deterministic outcome-independent first/middle/last replay sample "
            "from every event and both object roles."
        ),
        "scientific_interpretation_authorized": False,
        "next_safe_action":
            "FREEZE_A2_PRIMARY_METRIC_EXECUTION_AND_AUDIT_BEFORE_INTERPRETATION",
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")

    print(json.dumps(
        {
            **result,
            "independent_post_execution_audit_sha256": sha256(OUT),
        },
        indent=2,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
