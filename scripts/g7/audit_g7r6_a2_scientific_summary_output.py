#!/usr/bin/env python3
"""Independent post-execution audit of G7 R6 A2 scientific-summary outputs.

The audit recomputes the frozen summary statistics from the frozen source
artifacts without importing the scientific-summary executor.

It intentionally does NOT print or serialize the scientific numeric results.
Only integrity/accounting/mismatch evidence is emitted. Scientific
interpretation remains unauthorized.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[2]

AUTHORIZATION_HEAD = "47bfac34117ae3e8e4360b65840d832445e4525d"
AUTHORIZATION_TAG = "g7-r6-a2-scientific-summary-execution-authorized"
IMPLEMENTATION_TAG = "g7-r6-a2-scientific-summary-implementation-frozen"

EXPECTED_EVENTS = 6_927
EXPECTED_OPPORTUNITIES = 2_020_081
EXPECTED_Q_ROWS = 4_040_162
EXPECTED_CHANGE_ROWS = 99_545
EXPECTED_S05S06_VALID_EVENTS = 6_886
EXPECTED_S07_VALID = 6_901
EXPECTED_S07_NO_PRE = 26
EXPECTED_ASSOCIATIONS = 8
BOOTSTRAP_REPLICATES = 2_000
BOOTSTRAP_SEED = 20_260_908

TEMPORAL_STRATA = ("G6-T1", "G6-T2", "G6-T3", "G6-T4", "G6-T5")

CHANGES = ROOT / "results/g7/G7R6_A2_Metric_S05_S06_Changes.csv"
EVENT_STATUS = ROOT / "results/g7/G7R6_A2_Metric_S05_S06_Event_Status.csv"
S07_SOURCE = ROOT / "results/g7/G7R6_A2_Metric_S07_Event_Lead.csv"
DERIVED_DB = ROOT / "data/raw/g7/G7R6_A2_Metric_Derived.sqlite3"
MATERIALIZED_DB = ROOT / "data/raw/g7/G7R6_A2_Materialized.sqlite3"

SUMMARY = ROOT / "results/g7/G7R6_A2_Scientific_Summary.json"
S05S06_OUT = ROOT / "results/g7/G7R6_A2_S05_S06_EventEqual_Summary.csv"
S07_OUT = ROOT / "results/g7/G7R6_A2_S07_Summary.csv"
QUALITY_OUT = ROOT / "results/g7/G7R6_A2_Quality_Descriptor_Summary.csv"
ASSOC_OUT = ROOT / "results/g7/G7R6_A2_Quality_Stability_Associations.csv"
EXEC_AUDIT = ROOT / "results/g7/G7R6_A2_Scientific_Summary_Execution_Audit.json"

AUDIT_OUT = ROOT / "results/g7/G7R6_A2_Scientific_Summary_Independent_PostExecution_Audit.json"

EXPECTED_OUTPUT_HASHES = {
    "results/g7/G7R6_A2_Quality_Descriptor_Summary.csv":
        "a75ae6e759598e6ca4cbb27bca1ed7240981dc4c398eab4e5d57cbb919f0d741",
    "results/g7/G7R6_A2_Quality_Stability_Associations.csv":
        "7d0f05372ec53291c7ccdcc854922582bb11166fbb77a8e69efa11df89e4b8f5",
    "results/g7/G7R6_A2_S05_S06_EventEqual_Summary.csv":
        "9ecf8232fed180131832ce359c7e23766870d92e055a87cbf7b7cabeea713d99",
    "results/g7/G7R6_A2_S07_Summary.csv":
        "ab1959722a6ad6ab593e38fa116695d2688944d6d724e6c42307bf159296f7a3",
    "results/g7/G7R6_A2_Scientific_Summary.json":
        "70dfef3ab95eab217cd55880b4d149be279c618960c3b48c6be251bcc061033a",
    "results/g7/G7R6_A2_Scientific_Summary_Execution_Audit.json":
        "c331e9278484c873652b60a6383b6cbe07e6d07ae327bffef7f7fb6bc466e858",
}

EXPECTED_INPUT_HASHES = {
    "docs/research/G7R6_A2_Scientific_Summary_Interpretation_Spec.md":
        "7fd8befe8f7e24e990b4e513d8dbd50b0f62e776000aa86b949fd53b33204615",
    "docs/research/DR-0069_G7_R6_A2_Scientific_Summary_Interpretation_Spec_Freeze.md":
        "090e34525332bb6f8194b8dc535ade96467c4f1c930e7e5f6d6113972bed518e",
    "results/g7/G7R6_A2_Metric_S05_S06_Changes.csv":
        "166ff82ef9587631abaa092a07e8035824093338102faac56c1dce2ae266a202",
    "results/g7/G7R6_A2_Metric_S05_S06_Event_Status.csv":
        "5444c3091f4ff51db37aff343f84b9340fcda30b10098c1b7fa2d988f42e0733",
    "results/g7/G7R6_A2_Metric_S07_Event_Lead.csv":
        "79bcd812d9c4e00453f6cbf43c2df5534c56fdc4a4278348696c9eeedd3fab77",
    "data/raw/g7/G7R6_A2_Metric_Derived.sqlite3":
        "6bdea76ca02bffe9cc33e50d8e899f898e27445198d2ed244cc0cd1692850001",
    "data/raw/g7/G7R6_A2_Materialized.sqlite3":
        "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c",
}


class AuditError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
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


def tag_target(tag: str) -> str | None:
    result = subprocess.run(
        ["git", "rev-list", "-n", "1", tag],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def ro_connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def inv_quantile(values: Sequence[float], p: float) -> float:
    arr = np.asarray(values, dtype=float)
    require(arr.ndim == 1 and arr.size > 0 and np.all(np.isfinite(arr)),
            "invalid empirical-quantile input")
    require(0.0 < p <= 1.0, "invalid quantile probability")
    arr.sort(kind="mergesort")
    idx = int(math.ceil(p * arr.size)) - 1
    return float(arr[min(max(idx, 0), arr.size - 1)])


def event_equal_weight_vector(event_ids: Sequence[int]) -> np.ndarray:
    ids = np.asarray(event_ids, dtype=np.int64)
    require(ids.size > 0, "event-equal weighting requires data")
    unique, inverse, counts = np.unique(ids, return_inverse=True, return_counts=True)
    require(unique.size > 0, "empty event support")
    return 1.0 / counts[inverse].astype(float)


def weighted_inv_quantile(values: Sequence[float], weights: Sequence[float], p: float) -> float:
    x = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    require(x.size == w.size and x.size > 0, "weighted quantile length mismatch")
    require(np.all(np.isfinite(x)) and np.all(np.isfinite(w)) and np.all(w >= 0.0),
            "weighted quantile invalid values")
    order = np.argsort(x, kind="mergesort")
    xs = x[order]
    ws = w[order]
    total = float(ws.sum())
    require(total > 0.0, "zero weighted support")
    cumulative = np.cumsum(ws)
    idx = int(np.searchsorted(cumulative, p * total, side="left"))
    return float(xs[min(idx, xs.size - 1)])


def event_equal_summary(values: Sequence[float], event_ids: Sequence[int]) -> dict:
    x = np.asarray(values, dtype=float)
    ids = np.asarray(event_ids, dtype=np.int64)
    require(x.size == ids.size and x.size > 0, "summary input mismatch")
    weights = event_equal_weight_vector(ids)
    return {
        "support_rows": int(x.size),
        "support_events": int(np.unique(ids).size),
        "minimum": float(np.min(x)),
        "p50": weighted_inv_quantile(x, weights, 0.50),
        "p90": weighted_inv_quantile(x, weights, 0.90),
        "p95": weighted_inv_quantile(x, weights, 0.95),
        "p99": weighted_inv_quantile(x, weights, 0.99),
        "maximum": float(np.max(x)),
    }


def plain_summary(values: Sequence[float]) -> dict:
    x = np.asarray(values, dtype=float)
    require(x.size > 0, "plain summary requires data")
    return {
        "support_rows": int(x.size),
        "minimum": float(np.min(x)),
        "p50": inv_quantile(x, 0.50),
        "p90": inv_quantile(x, 0.90),
        "p95": inv_quantile(x, 0.95),
        "p99": inv_quantile(x, 0.99),
        "maximum": float(np.max(x)),
    }


def wilson(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    require(total > 0 and 0 <= successes <= total, "invalid Wilson inputs")
    n = float(total)
    p = successes / n
    z2 = z * z
    den = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / den
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / den
    return max(0.0, center - half), min(1.0, center + half)


def average_ranks(values: Sequence[float]) -> np.ndarray:
    x = np.asarray(values, dtype=float)
    require(x.size > 0 and np.all(np.isfinite(x)), "invalid rank input")
    order = np.argsort(x, kind="mergesort")
    sx = x[order]
    ranks = np.empty(x.size, dtype=float)
    i = 0
    while i < x.size:
        j = i + 1
        while j < x.size and sx[j] == sx[i]:
            j += 1
        rank = ((i + 1) + j) / 2.0
        ranks[order[i:j]] = rank
        i = j
    return ranks


def pearson(x: Sequence[float], y: Sequence[float], weights: Sequence[float] | None = None) -> float:
    a = np.asarray(x, dtype=float)
    b = np.asarray(y, dtype=float)
    require(a.size == b.size and a.size >= 2, "invalid correlation input")
    if weights is None:
        da = a - a.mean()
        db = b - b.mean()
        den = math.sqrt(float(np.dot(da, da)) * float(np.dot(db, db)))
        require(den > 0.0, "constant correlation vector")
        return float(np.dot(da, db) / den)
    w = np.asarray(weights, dtype=float)
    require(w.size == a.size and np.all(w >= 0.0) and float(w.sum()) > 0.0,
            "invalid weighted correlation input")
    sw = float(w.sum())
    ma = float(np.dot(w, a) / sw)
    mb = float(np.dot(w, b) / sw)
    da = a - ma
    db = b - mb
    va = float(np.dot(w, da * da))
    vb = float(np.dot(w, db * db))
    den = math.sqrt(va * vb)
    require(den > 0.0, "constant weighted correlation vector")
    return float(np.dot(w, da * db) / den)


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    return pearson(average_ranks(x), average_ranks(y))


def tie_groups(values: np.ndarray) -> tuple[np.ndarray, int]:
    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    gid_sorted = np.zeros(values.size, dtype=np.int64)
    gid = 0
    for i in range(1, values.size):
        if sorted_values[i] != sorted_values[i - 1]:
            gid += 1
        gid_sorted[i] = gid
    gid_original = np.empty_like(gid_sorted)
    gid_original[order] = gid_sorted
    return gid_original, gid + 1


def duplicated_midrank(values: np.ndarray, counts: np.ndarray,
                       cached: tuple[np.ndarray, int]) -> np.ndarray:
    gids, ngroups = cached
    group_weights = np.bincount(gids, weights=counts.astype(float), minlength=ngroups)
    before = np.cumsum(group_weights) - group_weights
    group_mid = before + (group_weights + 1.0) / 2.0
    return group_mid[gids]


def bootstrap_spearman(x: Sequence[float], y: Sequence[float]) -> tuple[float, float, int]:
    a = np.asarray(x, dtype=float)
    b = np.asarray(y, dtype=float)
    require(a.size == b.size and a.size >= 2, "invalid bootstrap correlation data")
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    probs = np.full(a.size, 1.0 / a.size)
    cache_a = tie_groups(a)
    cache_b = tie_groups(b)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        counts = rng.multinomial(a.size, probs)
        if np.count_nonzero(counts) < 2:
            continue
        ra = duplicated_midrank(a, counts, cache_a)
        rb = duplicated_midrank(b, counts, cache_b)
        try:
            rho = pearson(ra, rb, weights=counts)
        except AuditError:
            continue
        if math.isfinite(rho):
            draws.append(rho)
    require(draws, "no valid bootstrap correlations")
    return inv_quantile(draws, 0.025), inv_quantile(draws, 0.975), len(draws)


def bootstrap_event_equal_quantile(event_values: dict[int, list[float]], p: float,
                                   batch_size: int = 25) -> tuple[float, float]:
    event_ids = sorted(event_values)
    require(event_ids, "no events for bootstrap")
    n_events = len(event_ids)

    flat_values: list[float] = []
    flat_event_position: list[int] = []
    rows_per_event = np.empty(n_events, dtype=np.int64)
    for pos, event_id in enumerate(event_ids):
        vals = np.asarray(event_values[event_id], dtype=float)
        require(vals.size > 0 and np.all(np.isfinite(vals)), "invalid within-event data")
        rows_per_event[pos] = vals.size
        flat_values.extend(vals.tolist())
        flat_event_position.extend([pos] * vals.size)

    x = np.asarray(flat_values, dtype=float)
    event_pos = np.asarray(flat_event_position, dtype=np.int64)
    order = np.argsort(x, kind="mergesort")
    sorted_x = x[order]
    sorted_pos = event_pos[order]
    row_scale = 1.0 / rows_per_event[sorted_pos].astype(float)

    rng = np.random.default_rng(BOOTSTRAP_SEED)
    probs = np.full(n_events, 1.0 / n_events)
    estimates: list[float] = []

    done = 0
    while done < BOOTSTRAP_REPLICATES:
        b = min(batch_size, BOOTSTRAP_REPLICATES - done)
        counts = rng.multinomial(n_events, probs, size=b)
        row_weights = counts[:, sorted_pos].astype(float) * row_scale[None, :]
        cumulative = np.cumsum(row_weights, axis=1)
        threshold = (p * row_weights.sum(axis=1))[:, None]
        idx = np.argmax(cumulative >= threshold, axis=1)
        estimates.extend(sorted_x[idx].tolist())
        done += b

    require(len(estimates) == BOOTSTRAP_REPLICATES, "bootstrap replicate count mismatch")
    return inv_quantile(estimates, 0.025), inv_quantile(estimates, 0.975)


def as_float(value: str) -> float:
    return float(value)


def close(actual: float, expected: float, *, atol: float = 1e-11, rtol: float = 1e-10) -> bool:
    return math.isclose(float(actual), float(expected), abs_tol=atol, rel_tol=rtol)


def require_numeric(row: dict[str, str], field: str, expected: float,
                    mismatch_counter: list[str], key: str) -> None:
    try:
        actual = float(row[field])
    except Exception:
        mismatch_counter.append(f"{key}:{field}:parse")
        return
    if not close(actual, expected):
        mismatch_counter.append(f"{key}:{field}")


def verify_hash_boundaries() -> tuple[dict[str, str], dict[str, str]]:
    observed_outputs = {}
    for rel, expected in EXPECTED_OUTPUT_HASHES.items():
        path = ROOT / rel
        require(path.is_file(), f"summary output missing: {rel}")
        actual = sha256(path)
        require(actual == expected, f"summary output SHA mismatch: {rel}")
        observed_outputs[rel] = actual

    observed_inputs = {}
    for rel, expected in EXPECTED_INPUT_HASHES.items():
        path = ROOT / rel
        require(path.is_file(), f"frozen input missing: {rel}")
        actual = sha256(path)
        require(actual == expected, f"frozen input SHA mismatch: {rel}")
        observed_inputs[rel] = actual
    return observed_outputs, observed_inputs


def verify_summary_jsons(observed_outputs: dict[str, str],
                         observed_inputs: dict[str, str]) -> None:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    audit = json.loads(EXEC_AUDIT.read_text(encoding="utf-8"))

    require(summary["status"] == "G7R6_A2_SCIENTIFIC_SUMMARY_EXECUTION_COMPLETED",
            "summary status changed")
    require(audit["status"] == "G7R6_A2_SCIENTIFIC_SUMMARY_EXECUTION_AUDIT",
            "execution-audit status changed")

    for data in (summary, audit):
        require(int(data["events"]) == EXPECTED_EVENTS, "event count changed")
        require(int(data["publication_replay_opportunities"]) == EXPECTED_OPPORTUNITIES,
                "opportunity count changed")
        require(int(data["q_object_update_rows"]) == EXPECTED_Q_ROWS, "Q row count changed")
        require(int(data["s05_s06_change_rows"]) == EXPECTED_CHANGE_ROWS,
                "change-row count changed")
        require(int(data["quality_stability_associations"]) == EXPECTED_ASSOCIATIONS,
                "association count changed")
        require(int(data["bootstrap_replicates"]) == BOOTSTRAP_REPLICATES,
                "bootstrap replicate count changed")
        require(int(data["bootstrap_seed"]) == BOOTSTRAP_SEED, "bootstrap seed changed")
        require(data["quantile_convention"] == "INVERSE_EMPIRICAL_CDF_NO_INTERPOLATION",
                "quantile convention changed")
        require(data["grouped_weighting"] == "EVENT_EQUAL", "weighting changed")
        require(data["scientific_interpretation_performed"] is False,
                "scientific interpretation was performed")
        require(data["secondary_14d_60d_execution_performed"] is False,
                "secondary windows were executed")
        require(data["A0_execution_performed"] is False, "A0 was executed")
        require(data["orbital_propagation_performed"] is False, "orbital propagation occurred")
        require(data["reference_association_recomputed"] is False,
                "reference association was recomputed")
        require(data["raw_GP_HISTORY_opened"] is False, "raw GP_HISTORY was opened")
        require(int(data["network_requests"]) == 0, "network requests occurred")

    require(summary["authorization"]["authorization_head"] == AUTHORIZATION_HEAD,
            "executor authorization HEAD mismatch")
    require(summary["authorization"]["authorization_tag"] == AUTHORIZATION_TAG,
            "executor authorization tag mismatch")
    require(
        summary["authorization"]["implementation_freeze_commit"]
        == "04854de46a4b663a2ccf92b7f15984a2b100b51d",
        "executor implementation-freeze commit mismatch",
    )
    require(
        set(summary["authorization"]) == {
            "authorization_head",
            "implementation_freeze_commit",
            "authorization_tag",
        },
        "executor summary authorization schema changed",
    )
    # The frozen executor's summary JSON intentionally records its own
    # authorization-head / implementation-freeze / authorization-tag tuple.
    # The wrapper's separately printed authorization object contains
    # authorization_tag_target; that field is not part of the summary JSON.
    # Verify tag target independently from Git rather than requiring a
    # nonexistent summary-JSON field.
    require(tag_target(AUTHORIZATION_TAG) == AUTHORIZATION_HEAD,
            "authorization tag target changed")

    for rel, digest in observed_inputs.items():
        require(summary["input_hashes"].get(rel) == digest,
                f"summary input-hash record mismatch: {rel}")

    for rel, digest in observed_outputs.items():
        if rel.endswith("_Execution_Audit.json"):
            continue
        if rel == "results/g7/G7R6_A2_Scientific_Summary.json":
            # The execution-audit hash map includes the summary JSON.
            require(audit["output_hashes"].get(rel) == digest,
                    "execution audit summary-JSON hash mismatch")
        else:
            require(audit["output_hashes"].get(rel) == digest,
                    f"execution audit output-hash mismatch: {rel}")


def load_events() -> tuple[dict[int, str], int]:
    con = ro_connect(MATERIALIZED_DB)
    try:
        rows = con.execute(
            "SELECT event_index,stratum,expected_eligible_replays "
            "FROM events ORDER BY event_index"
        ).fetchall()
    finally:
        con.close()
    require(len(rows) == EXPECTED_EVENTS, "materialized event count changed")
    strata = {int(r["event_index"]): str(r["stratum"]) for r in rows}
    require(set(strata.values()) == set(TEMPORAL_STRATA), "temporal strata changed")
    opportunities = sum(int(r["expected_eligible_replays"]) for r in rows)
    require(opportunities == EXPECTED_OPPORTUNITIES, "materialized opportunity count changed")
    return strata, opportunities


def load_changes() -> tuple[dict[int, list[float]], dict[int, list[float]], dict[int, list[int]]]:
    fields, rows = read_csv(CHANGES)
    required = {
        "event_index", "current_replay_sequence",
        "absolute_tca_change_s", "absolute_miss_distance_change_km",
    }
    require(required.issubset(fields), "change CSV schema missing required fields")
    require(len(rows) == EXPECTED_CHANGE_ROWS, "change-row count changed")
    s05: dict[int, list[float]] = defaultdict(list)
    s06: dict[int, list[float]] = defaultdict(list)
    endpoints: dict[int, list[int]] = defaultdict(list)
    for r in rows:
        e = int(r["event_index"])
        s05[e].append(float(r["absolute_tca_change_s"]))
        s06[e].append(float(r["absolute_miss_distance_change_km"]))
        endpoints[e].append(int(r["current_replay_sequence"]))
    require(len(s05) == EXPECTED_S05S06_VALID_EVENTS, "eligible S05 event count changed")
    require(len(s06) == EXPECTED_S05S06_VALID_EVENTS, "eligible S06 event count changed")
    return s05, s06, endpoints


def flatten(event_values: dict[int, list[float]], selected: set[int] | None = None):
    values: list[float] = []
    ids: list[int] = []
    for e in sorted(event_values):
        if selected is not None and e not in selected:
            continue
        values.extend(event_values[e])
        ids.extend([e] * len(event_values[e]))
    return values, ids


def audit_s05_s06(strata: dict[int, str], s05: dict[int, list[float]],
                  s06: dict[int, list[float]]) -> tuple[int, int]:
    fields, rows = read_csv(S05S06_OUT)
    expected_fields = [
        "metric","unit","scope","temporal_stratum","support_rows","support_events",
        "weighting","minimum","p50","p90","p95","p99","maximum",
        "p50_bootstrap_95_lower","p50_bootstrap_95_upper",
        "p90_bootstrap_95_lower","p90_bootstrap_95_upper",
        "bootstrap_replicates","bootstrap_seed","interpretation",
    ]
    require(fields == expected_fields, "S05/S06 output schema changed")
    require(len(rows) == 12, "S05/S06 output row count changed")
    by_key = {(r["metric"], r["scope"], r["temporal_stratum"]): r for r in rows}
    require(len(by_key) == 12, "duplicate S05/S06 output key")

    mismatches: list[str] = []
    bootstrap_checks = 0

    for metric, unit, data in (
        ("S05_ABSOLUTE_TCA_UPDATE_CHANGE", "s", s05),
        ("S06_ABSOLUTE_MISS_DISTANCE_UPDATE_CHANGE", "km", s06),
    ):
        values, ids = flatten(data)
        expected = event_equal_summary(values, ids)
        row = by_key[(metric, "OVERALL", "ALL")]
        if row["unit"] != unit or row["weighting"] != "EVENT_EQUAL":
            mismatches.append(f"{metric}:labels")
        for f in ("support_rows", "support_events"):
            if int(row[f]) != int(expected[f]):
                mismatches.append(f"{metric}:{f}")
        for f in ("minimum","p50","p90","p95","p99","maximum"):
            require_numeric(row, f, expected[f], mismatches, metric)

        lo50, hi50 = bootstrap_event_equal_quantile(data, 0.50)
        lo90, hi90 = bootstrap_event_equal_quantile(data, 0.90)
        for f, val in (
            ("p50_bootstrap_95_lower", lo50),
            ("p50_bootstrap_95_upper", hi50),
            ("p90_bootstrap_95_lower", lo90),
            ("p90_bootstrap_95_upper", hi90),
        ):
            require_numeric(row, f, val, mismatches, metric)
        if int(row["bootstrap_replicates"]) != BOOTSTRAP_REPLICATES:
            mismatches.append(f"{metric}:bootstrap_replicates")
        if int(row["bootstrap_seed"]) != BOOTSTRAP_SEED:
            mismatches.append(f"{metric}:bootstrap_seed")
        bootstrap_checks += 4

        for stratum in TEMPORAL_STRATA:
            selected = {e for e in data if strata[e] == stratum}
            vals, eids = flatten(data, selected)
            expected_s = event_equal_summary(vals, eids)
            srow = by_key[(metric, "TEMPORAL_STRATUM", stratum)]
            for f in ("support_rows", "support_events"):
                if int(srow[f]) != int(expected_s[f]):
                    mismatches.append(f"{metric}:{stratum}:{f}")
            for f in ("minimum","p50","p90","p95","p99","maximum"):
                require_numeric(srow, f, expected_s[f], mismatches, f"{metric}:{stratum}")
            for f in (
                "p50_bootstrap_95_lower","p50_bootstrap_95_upper",
                "p90_bootstrap_95_lower","p90_bootstrap_95_upper",
                "bootstrap_replicates","bootstrap_seed",
            ):
                if srow[f] != "":
                    mismatches.append(f"{metric}:{stratum}:{f}:expected_blank")

    require(not mismatches, f"S05/S06 audit mismatches: {mismatches[:10]}")
    return len(rows), bootstrap_checks


def audit_s07(strata: dict[int, str]) -> tuple[int, int]:
    src_fields, src = read_csv(S07_SOURCE)
    require({"event_index","s07_status","lead_h"}.issubset(src_fields),
            "S07 source schema missing fields")
    require(len(src) == EXPECTED_EVENTS, "S07 source event count changed")
    by_event = {int(r["event_index"]): r for r in src}
    require(len(by_event) == EXPECTED_EVENTS, "duplicate S07 source event")

    fields, rows = read_csv(S07_OUT)
    expected_fields = [
        "scope","temporal_stratum","denominator_events","valid_events","valid_proportion",
        "valid_wilson_95_lower","valid_wilson_95_upper",
        "no_pre_tca_events","no_pre_tca_proportion",
        "no_pre_tca_wilson_95_lower","no_pre_tca_wilson_95_upper",
        "lead_support_events","lead_min_h","lead_p50_h","lead_p90_h","lead_p95_h",
        "lead_p99_h","lead_max_h","lead_p50_bootstrap_95_lower_h",
        "lead_p50_bootstrap_95_upper_h","bootstrap_replicates","bootstrap_seed",
        "interpretation",
    ]
    require(fields == expected_fields, "S07 output schema changed")
    require(len(rows) == 6, "S07 output row count changed")
    by_key = {(r["scope"], r["temporal_stratum"]): r for r in rows}

    scopes = [("OVERALL", "ALL", set(by_event))]
    scopes.extend(
        ("TEMPORAL_STRATUM", s, {e for e in by_event if strata[e] == s})
        for s in TEMPORAL_STRATA
    )
    mismatches: list[str] = []
    bootstrap_checks = 0

    for scope, stratum, event_set in scopes:
        selected = [by_event[e] for e in sorted(event_set)]
        valid = [r for r in selected if r["s07_status"] == "VALID"]
        no_pre = [r for r in selected if r["s07_status"] == "NO PRE-TCA RECONSTRUCTION"]
        require(len(valid) + len(no_pre) == len(selected), "unexpected S07 status")
        lead = [float(r["lead_h"]) for r in valid]
        require(all(v >= 0.0 for v in lead), "negative S07 lead found")
        desc = plain_summary(lead)
        vlo, vhi = wilson(len(valid), len(selected))
        nlo, nhi = wilson(len(no_pre), len(selected))
        row = by_key[(scope, stratum)]

        integer_expect = {
            "denominator_events": len(selected),
            "valid_events": len(valid),
            "no_pre_tca_events": len(no_pre),
            "lead_support_events": len(valid),
        }
        for f, val in integer_expect.items():
            if int(row[f]) != val:
                mismatches.append(f"S07:{scope}:{stratum}:{f}")

        numeric_expect = {
            "valid_proportion": len(valid)/len(selected),
            "valid_wilson_95_lower": vlo,
            "valid_wilson_95_upper": vhi,
            "no_pre_tca_proportion": len(no_pre)/len(selected),
            "no_pre_tca_wilson_95_lower": nlo,
            "no_pre_tca_wilson_95_upper": nhi,
            "lead_min_h": desc["minimum"],
            "lead_p50_h": desc["p50"],
            "lead_p90_h": desc["p90"],
            "lead_p95_h": desc["p95"],
            "lead_p99_h": desc["p99"],
            "lead_max_h": desc["maximum"],
        }
        for f, val in numeric_expect.items():
            require_numeric(row, f, val, mismatches, f"S07:{scope}:{stratum}")

        if scope == "OVERALL":
            event_values = {int(r["event_index"]): [float(r["lead_h"])] for r in valid}
            lo, hi = bootstrap_event_equal_quantile(event_values, 0.50)
            require_numeric(row, "lead_p50_bootstrap_95_lower_h", lo, mismatches, "S07:overall")
            require_numeric(row, "lead_p50_bootstrap_95_upper_h", hi, mismatches, "S07:overall")
            if int(row["bootstrap_replicates"]) != BOOTSTRAP_REPLICATES:
                mismatches.append("S07:bootstrap_replicates")
            if int(row["bootstrap_seed"]) != BOOTSTRAP_SEED:
                mismatches.append("S07:bootstrap_seed")
            bootstrap_checks += 2
        else:
            for f in (
                "lead_p50_bootstrap_95_lower_h","lead_p50_bootstrap_95_upper_h",
                "bootstrap_replicates","bootstrap_seed",
            ):
                if row[f] != "":
                    mismatches.append(f"S07:{stratum}:{f}:expected_blank")

    overall = by_key[("OVERALL","ALL")]
    require(int(overall["valid_events"]) == EXPECTED_S07_VALID, "S07 valid-event count changed")
    require(int(overall["no_pre_tca_events"]) == EXPECTED_S07_NO_PRE, "S07 no-pre count changed")
    require(not mismatches, f"S07 audit mismatches: {mismatches[:10]}")
    return len(rows), bootstrap_checks


def load_object_descriptor(column: str, where: str | None = None):
    con = ro_connect(DERIVED_DB)
    try:
        sql = f"SELECT event_index,{column} AS value FROM q_object_updates"
        if where:
            sql += " WHERE " + where
        values: list[float] = []
        events: list[int] = []
        missing = 0
        for row in con.execute(sql):
            if row["value"] is None:
                missing += 1
            else:
                events.append(int(row["event_index"]))
                values.append(float(row["value"]))
    finally:
        con.close()
    return values, events, missing


def build_pair_descriptors(endpoint_keys: set[tuple[int,int]]):
    con = ro_connect(DERIVED_DB)
    names = (
        "PAIR_MAX_STATE_AGE_H",
        "PAIR_MAX_PUBLICATION_AGE_H",
        "PAIR_MIN_DISTINCT_STATE_COUNT",
        "PAIR_MAX_RECENT_PUBLICATION_GAP_H",
        "PAIR_MAX_PROPAGATION_SPAN_H",
    )
    values = {n: np.empty(EXPECTED_OPPORTUNITIES, dtype=np.float64) for n in names}
    events = np.empty(EXPECTED_OPPORTUNITIES, dtype=np.int64)
    defined = {n: np.zeros(EXPECTED_OPPORTUNITIES, dtype=bool) for n in names}
    selected: dict[tuple[int,int], dict[str,float|None]] = {}

    idx = 0
    pending_key = None
    pending = []

    def finish(key, pair_rows, position):
        require(len(pair_rows) == 2, f"Q pair role cardinality !=2 at {key}")
        by_role = {str(r["object_role"]): r for r in pair_rows}
        require(set(by_role) == {"obj1","obj2"}, f"Q pair roles invalid at {key}")
        a = by_role["obj1"]
        b = by_role["obj2"]
        d = {
            "PAIR_MAX_STATE_AGE_H":
                max(float(a["q01_state_age_h"]), float(b["q01_state_age_h"])),
            "PAIR_MAX_PUBLICATION_AGE_H":
                max(float(a["q02_publication_age_h"]), float(b["q02_publication_age_h"])),
            "PAIR_MIN_DISTINCT_STATE_COUNT":
                min(float(a["w30_distinct_state_count"]), float(b["w30_distinct_state_count"])),
            "PAIR_MAX_RECENT_PUBLICATION_GAP_H":
                None if a["w30_max_gap_h"] is None or b["w30_max_gap_h"] is None
                else max(float(a["w30_max_gap_h"]), float(b["w30_max_gap_h"])),
            "PAIR_MAX_PROPAGATION_SPAN_H":
                None if (
                    str(a["q06_status"]) != "VALID_ASSOCIATED"
                    or str(b["q06_status"]) != "VALID_ASSOCIATED"
                    or a["q06_propagation_span_h"] is None
                    or b["q06_propagation_span_h"] is None
                ) else max(float(a["q06_propagation_span_h"]),
                           float(b["q06_propagation_span_h"])),
        }
        events[position] = key[0]
        for name, val in d.items():
            if val is not None:
                values[name][position] = float(val)
                defined[name][position] = True
        if key in endpoint_keys:
            selected[key] = {
                "pair_max_state_age_h": d["PAIR_MAX_STATE_AGE_H"],
                "pair_min_distinct_state_count": d["PAIR_MIN_DISTINCT_STATE_COUNT"],
                "pair_max_recent_publication_gap_h": d["PAIR_MAX_RECENT_PUBLICATION_GAP_H"],
                "pair_max_propagation_span_h": d["PAIR_MAX_PROPAGATION_SPAN_H"],
            }

    try:
        cur = con.execute(
            """
            SELECT event_index,replay_sequence,object_role,
                   q01_state_age_h,q02_publication_age_h,
                   w30_distinct_state_count,w30_max_gap_h,
                   q06_status,q06_propagation_span_h
            FROM q_object_updates
            ORDER BY event_index,replay_sequence,object_role
            """
        )
        for row in cur:
            key = (int(row["event_index"]), int(row["replay_sequence"]))
            if pending_key is None:
                pending_key = key
            if key != pending_key:
                finish(pending_key, pending, idx)
                idx += 1
                pending_key = key
                pending = []
            pending.append(row)
        if pending_key is not None:
            finish(pending_key, pending, idx)
            idx += 1
    finally:
        con.close()

    require(idx == EXPECTED_OPPORTUNITIES, "pair descriptor opportunity count mismatch")
    require(len(selected) == len(endpoint_keys), "missing current-endpoint pair descriptor")
    return values, events, defined, selected


def audit_quality(pair_values, pair_events, pair_defined) -> int:
    fields, rows = read_csv(QUALITY_OUT)
    expected_fields = [
        "level","descriptor","support_events","support_rows","missing_or_not_applicable_rows",
        "weighting","minimum","p50","p90","p95","p99","maximum","interpretation",
    ]
    require(fields == expected_fields, "quality summary schema changed")
    require(len(rows) == 12, "quality summary row count changed")
    by_key = {(r["level"], r["descriptor"]): r for r in rows}
    require(len(by_key) == 12, "duplicate quality summary key")
    mismatches: list[str] = []

    object_specs = (
        ("Q01_STATE_AGE_H","q01_state_age_h",None),
        ("Q02_PUBLICATION_AGE_H","q02_publication_age_h",None),
        ("Q03_30D_PUBLICATION_COUNT","w30_publication_count",None),
        ("Q04_30D_DISTINCT_STATE_COUNT","w30_distinct_state_count",None),
        ("Q05_30D_MEDIAN_PUBLICATION_CADENCE_H","w30_median_cadence_h",None),
        ("Q05_30D_MAX_PUBLICATION_GAP_H","w30_max_gap_h",None),
        ("Q06_PROPAGATION_SPAN_H","q06_propagation_span_h","q06_status='VALID_ASSOCIATED'"),
    )
    for label, column, where in object_specs:
        vals, ids, missing = load_object_descriptor(column, where)
        desc = event_equal_summary(vals, ids)
        row = by_key[("OBJECT_EVENT_UPDATE",label)]
        expected_missing = missing if where is None else EXPECTED_Q_ROWS - desc["support_rows"]
        integer_expect = {
            "support_events": desc["support_events"],
            "support_rows": desc["support_rows"],
            "missing_or_not_applicable_rows": expected_missing,
        }
        for f,val in integer_expect.items():
            if int(row[f]) != int(val):
                mismatches.append(f"{label}:{f}")
        for f in ("minimum","p50","p90","p95","p99","maximum"):
            require_numeric(row, f, desc[f], mismatches, label)
        if row["weighting"] != "EVENT_EQUAL":
            mismatches.append(f"{label}:weighting")

    for label in (
        "PAIR_MAX_STATE_AGE_H",
        "PAIR_MAX_PUBLICATION_AGE_H",
        "PAIR_MIN_DISTINCT_STATE_COUNT",
        "PAIR_MAX_RECENT_PUBLICATION_GAP_H",
        "PAIR_MAX_PROPAGATION_SPAN_H",
    ):
        mask = pair_defined[label]
        vals = pair_values[label][mask]
        ids = pair_events[mask]
        desc = event_equal_summary(vals, ids)
        row = by_key[("PAIR_REPLAY",label)]
        integer_expect = {
            "support_events": desc["support_events"],
            "support_rows": desc["support_rows"],
            "missing_or_not_applicable_rows": EXPECTED_OPPORTUNITIES - desc["support_rows"],
        }
        for f,val in integer_expect.items():
            if int(row[f]) != int(val):
                mismatches.append(f"{label}:{f}")
        for f in ("minimum","p50","p90","p95","p99","maximum"):
            require_numeric(row, f, desc[f], mismatches, label)
        if row["weighting"] != "EVENT_EQUAL":
            mismatches.append(f"{label}:weighting")

    require(not mismatches, f"quality-summary audit mismatches: {mismatches[:10]}")
    return len(rows)


def audit_associations(s05: dict[int,list[float]], s06: dict[int,list[float]],
                       endpoints: dict[int,list[int]],
                       selected_pair: dict[tuple[int,int],dict[str,float|None]]) -> tuple[int,int]:
    fields, rows = read_csv(ASSOC_OUT)
    expected_fields = [
        "stability_metric","quality_descriptor","n_events","spearman_rho",
        "bootstrap_95_lower","bootstrap_95_upper","bootstrap_replicates_requested",
        "bootstrap_replicates_valid","bootstrap_seed","analysis_unit","interpretation",
    ]
    require(fields == expected_fields, "association output schema changed")
    require(len(rows) == EXPECTED_ASSOCIATIONS, "association row count changed")
    by_key = {(r["stability_metric"],r["quality_descriptor"]):r for r in rows}
    require(len(by_key) == EXPECTED_ASSOCIATIONS, "duplicate association key")

    stability = {
        "S05_EVENT_P50_ABSOLUTE_TCA_UPDATE_CHANGE_S":
            {e: inv_quantile(vals,0.50) for e,vals in s05.items()},
        "S06_EVENT_P50_ABSOLUTE_MISS_DISTANCE_UPDATE_CHANGE_KM":
            {e: inv_quantile(vals,0.50) for e,vals in s06.items()},
    }
    qnames = (
        "pair_max_state_age_h",
        "pair_min_distinct_state_count",
        "pair_max_recent_publication_gap_h",
        "pair_max_propagation_span_h",
    )
    event_quality = {q:{} for q in qnames}
    for e in sorted(endpoints):
        for q in qnames:
            vals = []
            for seq in endpoints[e]:
                d = selected_pair[(e,seq)]
                if d[q] is not None:
                    vals.append(float(d[q]))
            if vals:
                event_quality[q][e] = inv_quantile(vals,0.50)

    mismatches: list[str] = []
    bootstrap_checks = 0
    for sname, smap in stability.items():
        for q in qnames:
            qlabel = q.upper()
            row = by_key[(sname,qlabel)]
            common = sorted(set(smap) & set(event_quality[q]))
            x = [smap[e] for e in common]
            y = [event_quality[q][e] for e in common]
            rho = spearman(x,y)
            lo,hi,nvalid = bootstrap_spearman(x,y)
            if int(row["n_events"]) != len(common):
                mismatches.append(f"{sname}:{qlabel}:n_events")
            require_numeric(row,"spearman_rho",rho,mismatches,f"{sname}:{qlabel}")
            require_numeric(row,"bootstrap_95_lower",lo,mismatches,f"{sname}:{qlabel}")
            require_numeric(row,"bootstrap_95_upper",hi,mismatches,f"{sname}:{qlabel}")
            if int(row["bootstrap_replicates_requested"]) != BOOTSTRAP_REPLICATES:
                mismatches.append(f"{sname}:{qlabel}:bootstrap_requested")
            if int(row["bootstrap_replicates_valid"]) != nvalid:
                mismatches.append(f"{sname}:{qlabel}:bootstrap_valid")
            if int(row["bootstrap_seed"]) != BOOTSTRAP_SEED:
                mismatches.append(f"{sname}:{qlabel}:seed")
            if row["analysis_unit"] != "EVENT":
                mismatches.append(f"{sname}:{qlabel}:analysis_unit")
            bootstrap_checks += 2

    require(not mismatches, f"association audit mismatches: {mismatches[:10]}")
    return len(rows), bootstrap_checks


def verify_event_status() -> dict[str,int]:
    fields, rows = read_csv(EVENT_STATUS)
    require("s05_status" in fields, "event-status schema missing s05_status")
    require(len(rows) == EXPECTED_EVENTS, "event-status row count changed")
    counts = Counter(r["s05_status"] for r in rows)
    require(counts["VALID"] == EXPECTED_S05S06_VALID_EVENTS,
            "S05/S06 valid-event count changed")
    require(
        counts["INSUFFICIENT_SUCCESSIVE_VALID_ASSOCIATED_RECONSTRUCTIONS"]
        == EXPECTED_EVENTS - EXPECTED_S05S06_VALID_EVENTS,
        "S05/S06 insufficient-event count changed",
    )
    return dict(counts)


def self_test() -> None:
    require(inv_quantile([1,2,3,4],0.5)==2.0, "self-test quantile failed")
    ids=[1]*4+[2]
    vals=[0,0,0,0,10]
    w=event_equal_weight_vector(ids)
    require(close(float(w[:4].sum()),float(w[4]),atol=1e-14,rtol=0),
            "self-test event weights failed")
    require(weighted_inv_quantile(vals,w,0.75)==10.0,
            "self-test weighted quantile failed")
    require(close(spearman([1,2,3],[3,2,1]),-1.0,atol=1e-12,rtol=0),
            "self-test Spearman failed")


def main() -> int:
    require(git_value("rev-parse","HEAD") == AUTHORIZATION_HEAD,
            "post-execution audit must run at authorization HEAD")
    require(tag_target(AUTHORIZATION_TAG) == AUTHORIZATION_HEAD,
            "authorization tag target changed")
    require(tag_target(IMPLEMENTATION_TAG) is not None,
            "implementation freeze tag missing")
    require(not AUDIT_OUT.exists(), "post-execution audit output already exists")

    self_test()

    observed_outputs, observed_inputs = verify_hash_boundaries()
    verify_summary_jsons(observed_outputs, observed_inputs)

    strata, _ = load_events()
    status_counts = verify_event_status()
    s05,s06,endpoints = load_changes()

    s05s06_rows, s05s06_bootstrap_checks = audit_s05_s06(strata,s05,s06)
    s07_rows, s07_bootstrap_checks = audit_s07(strata)

    endpoint_keys={(e,seq) for e,seqs in endpoints.items() for seq in seqs}
    pair_values,pair_events,pair_defined,selected_pair = build_pair_descriptors(endpoint_keys)
    quality_rows = audit_quality(pair_values,pair_events,pair_defined)
    association_rows, association_bootstrap_checks = audit_associations(
        s05,s06,endpoints,selected_pair
    )

    # SQLite logical/integrity checks after independent arithmetic recomputation.
    con = ro_connect(DERIVED_DB)
    try:
        quick = con.execute("PRAGMA quick_check").fetchone()[0]
        q_count = int(con.execute("SELECT COUNT(*) FROM q_object_updates").fetchone()[0])
        q_roles_bad = int(con.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT event_index,replay_sequence,
                       COUNT(*) AS n,
                       COUNT(DISTINCT object_role) AS nr
                FROM q_object_updates
                GROUP BY event_index,replay_sequence
                HAVING n<>2 OR nr<>2
            )
            """
        ).fetchone()[0])
    finally:
        con.close()
    require(str(quick).lower()=="ok","derived DB quick_check failed")
    require(q_count==EXPECTED_Q_ROWS,"derived DB Q row count changed")
    require(q_roles_bad==0,"derived DB pair role/cardinality mismatch")

    result = {
        "status":"G7R6_A2_SCIENTIFIC_SUMMARY_INDEPENDENT_POST_EXECUTION_AUDIT_PASS",
        "audit_version":"v2",
        "audit_correction":{
            "scientific_outputs_changed":False,
            "v1_failure_class":"SUMMARY_AUTHORIZATION_SCHEMA_MISMATCH",
            "v1_failure_detail":"v1 incorrectly expected authorization_tag_target inside the executor summary JSON; that field belongs to the wrapper execution report, while the summary JSON records authorization_head, implementation_freeze_commit, and authorization_tag.",
            "v2_correction":"verify the exact executor summary authorization schema and verify the authorization tag target independently from Git",
        },
        "authorization_head":AUTHORIZATION_HEAD,
        "authorization_tag":AUTHORIZATION_TAG,
        "output_hashes_verified":observed_outputs,
        "input_hashes_independently_verified":observed_inputs,
        "events":EXPECTED_EVENTS,
        "publication_replay_opportunities":EXPECTED_OPPORTUNITIES,
        "q_object_update_rows":q_count,
        "s05_s06_change_rows":EXPECTED_CHANGE_ROWS,
        "s05_s06_event_status_counts":status_counts,
        "s07_valid_events":EXPECTED_S07_VALID,
        "s07_no_pre_tca_events":EXPECTED_S07_NO_PRE,
        "scientific_output_row_counts":{
            "s05_s06_summary":s05s06_rows,
            "s07_summary":s07_rows,
            "quality_summary":quality_rows,
            "quality_stability_associations":association_rows,
        },
        "independent_recomputation":{
            "s05_s06_all_numeric_fields_mismatch_count":0,
            "s07_all_numeric_fields_mismatch_count":0,
            "quality_all_numeric_fields_mismatch_count":0,
            "association_all_numeric_fields_mismatch_count":0,
            "s05_s06_bootstrap_ci_endpoint_checks":s05s06_bootstrap_checks,
            "s07_bootstrap_ci_endpoint_checks":s07_bootstrap_checks,
            "association_bootstrap_ci_endpoint_checks":association_bootstrap_checks,
            "quantile_convention":"INVERSE_EMPIRICAL_CDF_NO_INTERPOLATION",
            "grouped_weighting":"EVENT_EQUAL",
            "bootstrap_replicates":BOOTSTRAP_REPLICATES,
            "bootstrap_seed":BOOTSTRAP_SEED,
            "association_family_count":EXPECTED_ASSOCIATIONS,
        },
        "derived_db_quick_check":"ok",
        "q_pair_role_cardinality_mismatch_count":q_roles_bad,
        "scientific_numeric_values_exposed_by_audit":False,
        "scientific_interpretation_performed":False,
        "secondary_14d_60d_execution_performed":False,
        "A0_execution_performed":False,
        "orbital_propagation_performed":False,
        "candidate_generation_performed":False,
        "reference_association_recomputed":False,
        "raw_GP_HISTORY_opened":False,
        "network_requests":0,
        "next_safe_action":
            "FREEZE_A2_SCIENTIFIC_SUMMARY_EXECUTION_AND_INDEPENDENT_AUDIT_BEFORE_INTERPRETATION",
    }

    AUDIT_OUT.parent.mkdir(parents=True,exist_ok=True)
    with AUDIT_OUT.open("w",encoding="utf-8",newline="\n") as stream:
        json.dump(result,stream,indent=2,sort_keys=True)
        stream.write("\n")

    print(json.dumps({
        "status":result["status"],
        "authorization_head":AUTHORIZATION_HEAD,
        "events":EXPECTED_EVENTS,
        "publication_replay_opportunities":EXPECTED_OPPORTUNITIES,
        "q_object_update_rows":q_count,
        "s05_s06_change_rows":EXPECTED_CHANGE_ROWS,
        "quality_stability_associations":association_rows,
        "numeric_mismatch_count":0,
        "derived_db_quick_check":"ok",
        "q_pair_role_cardinality_mismatch_count":q_roles_bad,
        "audit_output_sha256":sha256(AUDIT_OUT),
        "scientific_numeric_values_exposed_by_audit":False,
        "scientific_interpretation_performed":False,
        "secondary_14d_60d_execution_performed":False,
        "A0_execution_performed":False,
        "next_safe_action":result["next_safe_action"],
    },indent=2,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
