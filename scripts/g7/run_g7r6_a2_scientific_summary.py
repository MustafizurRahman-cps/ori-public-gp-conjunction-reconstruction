#!/usr/bin/env python3
"""Frozen-design implementation candidate for the G7 R6 A2 scientific summary.

This implementation is blocked from real scientific-summary execution until a
later explicit authorization commit/tag. The offline-validation mode runs only
synthetic arithmetic/semantic tests and does not open A2 result data or the
derived A2 metric database.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[2]

SPEC_FREEZE_COMMIT = "b11d867c4df3633f37fdc35a4940591bad32a92a"
SPEC_FREEZE_TAG = "g7-r6-a2-scientific-summary-spec-frozen"
IMPLEMENTATION_FREEZE_TAG = "g7-r6-a2-scientific-summary-implementation-frozen"
EXECUTION_AUTHORIZATION_TAG = "g7-r6-a2-scientific-summary-execution-authorized"

SPEC = ROOT / "docs/research/G7R6_A2_Scientific_Summary_Interpretation_Spec.md"
DR0069 = ROOT / "docs/research/DR-0069_G7_R6_A2_Scientific_Summary_Interpretation_Spec_Freeze.md"

CHANGES_CSV = ROOT / "results/g7/G7R6_A2_Metric_S05_S06_Changes.csv"
EVENT_STATUS_CSV = ROOT / "results/g7/G7R6_A2_Metric_S05_S06_Event_Status.csv"
S07_CSV = ROOT / "results/g7/G7R6_A2_Metric_S07_Event_Lead.csv"
DERIVED_DB = ROOT / "data/raw/g7/G7R6_A2_Metric_Derived.sqlite3"
MATERIALIZED_DB = ROOT / "data/raw/g7/G7R6_A2_Materialized.sqlite3"

SUMMARY_JSON = ROOT / "results/g7/G7R6_A2_Scientific_Summary.json"
S05_S06_SUMMARY_CSV = ROOT / "results/g7/G7R6_A2_S05_S06_EventEqual_Summary.csv"
S07_SUMMARY_CSV = ROOT / "results/g7/G7R6_A2_S07_Summary.csv"
QUALITY_SUMMARY_CSV = ROOT / "results/g7/G7R6_A2_Quality_Descriptor_Summary.csv"
ASSOCIATIONS_CSV = ROOT / "results/g7/G7R6_A2_Quality_Stability_Associations.csv"
EXECUTION_AUDIT_JSON = ROOT / "results/g7/G7R6_A2_Scientific_Summary_Execution_Audit.json"

OFFLINE_VALIDATION_JSON = ROOT / "results/g7/G7R6_A2_Scientific_Summary_Implementation_Offline_Validation.json"

EXPECTED_SPEC_SHA256 = "7fd8befe8f7e24e990b4e513d8dbd50b0f62e776000aa86b949fd53b33204615"
EXPECTED_DR0069_SHA256 = "090e34525332bb6f8194b8dc535ade96467c4f1c930e7e5f6d6113972bed518e"
EXPECTED_CHANGES_SHA256 = "166ff82ef9587631abaa092a07e8035824093338102faac56c1dce2ae266a202"
EXPECTED_EVENT_STATUS_SHA256 = "5444c3091f4ff51db37aff343f84b9340fcda30b10098c1b7fa2d988f42e0733"
EXPECTED_S07_SHA256 = "79bcd812d9c4e00453f6cbf43c2df5534c56fdc4a4278348696c9eeedd3fab77"
EXPECTED_DERIVED_DB_SHA256 = "6bdea76ca02bffe9cc33e50d8e899f898e27445198d2ed244cc0cd1692850001"
EXPECTED_MATERIALIZED_DB_SHA256 = "48c7052ec4599eb55e23f174cbe548b7488f0d886e2b690f50aef71902f92c8c"

EXPECTED_EVENTS = 6_927
EXPECTED_PUBLICATION_OPPORTUNITIES = 2_020_081
EXPECTED_Q_ROWS = 4_040_162
EXPECTED_CHANGE_ROWS = 99_545
EXPECTED_VALID_ASSOCIATED = 106_446
EXPECTED_S05_S06_VALID_EVENTS = 6_886
EXPECTED_S07_VALID_EVENTS = 6_901
EXPECTED_S07_NO_PRE_TCA = 26

BOOTSTRAP_REPLICATES = 2_000
BOOTSTRAP_SEED = 20_260_908

TEMPORAL_STRATA = ("G6-T1", "G6-T2", "G6-T3", "G6-T4", "G6-T5")

SUMMARY_OUTPUTS = (
    SUMMARY_JSON,
    S05_S06_SUMMARY_CSV,
    S07_SUMMARY_CSV,
    QUALITY_SUMMARY_CSV,
    ASSOCIATIONS_CSV,
    EXECUTION_AUDIT_JSON,
)


class A2SummaryError(RuntimeError):
    pass


class A2SummaryExecutionNotAuthorized(A2SummaryError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise A2SummaryError(message)


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
    return result.stdout.strip() or None


def inverse_ecdf_quantile(values: Sequence[float], p: float) -> float:
    """Smallest ordered value whose empirical CDF is >= p; no interpolation."""
    require(0.0 < float(p) <= 1.0, "quantile probability must be in (0,1]")
    arr = np.asarray(values, dtype=float)
    require(arr.ndim == 1 and arr.size > 0, "quantile requires nonempty 1-D values")
    require(np.all(np.isfinite(arr)), "quantile values must be finite")
    ordered = np.sort(arr, kind="mergesort")
    k = int(math.ceil(float(p) * ordered.size)) - 1
    k = min(max(k, 0), ordered.size - 1)
    return float(ordered[k])


def weighted_inverse_ecdf_quantile(
    values: Sequence[float],
    weights: Sequence[float],
    p: float,
) -> float:
    """Weighted inverse empirical CDF; no interpolation."""
    require(0.0 < float(p) <= 1.0, "quantile probability must be in (0,1]")
    x = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    require(x.ndim == 1 and w.ndim == 1 and x.size == w.size and x.size > 0,
            "weighted quantile requires aligned nonempty vectors")
    require(np.all(np.isfinite(x)), "weighted quantile values must be finite")
    require(np.all(np.isfinite(w)) and np.all(w >= 0.0),
            "weighted quantile weights must be finite and nonnegative")
    total = float(np.sum(w))
    require(total > 0.0, "weighted quantile requires positive total weight")
    order = np.argsort(x, kind="mergesort")
    xs = x[order]
    ws = w[order]
    cumulative = np.cumsum(ws)
    threshold = float(p) * total
    idx = int(np.searchsorted(cumulative, threshold, side="left"))
    idx = min(idx, xs.size - 1)
    return float(xs[idx])


def event_equal_weights(event_ids: Sequence[int]) -> np.ndarray:
    """Equal total mass per event; equal row mass within each event."""
    ids = np.asarray(event_ids, dtype=np.int64)
    require(ids.ndim == 1 and ids.size > 0, "event-equal weights require rows")
    unique, inverse, counts = np.unique(ids, return_inverse=True, return_counts=True)
    require(unique.size > 0 and np.all(counts > 0), "invalid event grouping")
    # Scaling by 1/N_events is unnecessary for quantiles; normalized downstream.
    return 1.0 / counts[inverse].astype(float)


def distribution_summary(
    values: Sequence[float],
    event_ids: Sequence[int] | None = None,
) -> dict:
    x = np.asarray(values, dtype=float)
    require(x.ndim == 1 and x.size > 0 and np.all(np.isfinite(x)),
            "distribution summary requires finite values")
    if event_ids is None:
        q = lambda p: inverse_ecdf_quantile(x, p)
        support_events = None
        weighting = "UNWEIGHTED"
    else:
        ids = np.asarray(event_ids, dtype=np.int64)
        require(ids.size == x.size, "event/value length mismatch")
        w = event_equal_weights(ids)
        q = lambda p: weighted_inverse_ecdf_quantile(x, w, p)
        support_events = int(np.unique(ids).size)
        weighting = "EVENT_EQUAL"
    return {
        "support_rows": int(x.size),
        "support_events": support_events,
        "weighting": weighting,
        "minimum": float(np.min(x)),
        "p50": q(0.50),
        "p90": q(0.90),
        "p95": q(0.95),
        "p99": q(0.99),
        "maximum": float(np.max(x)),
    }


def wilson_interval(successes: int, denominator: int, z: float = 1.959963984540054) -> tuple[float, float]:
    require(denominator > 0, "Wilson denominator must be positive")
    require(0 <= successes <= denominator, "Wilson successes outside denominator")
    n = float(denominator)
    phat = float(successes) / n
    z2 = z * z
    center = (phat + z2 / (2.0 * n)) / (1.0 + z2 / n)
    half = (
        z
        * math.sqrt((phat * (1.0 - phat) / n) + z2 / (4.0 * n * n))
        / (1.0 + z2 / n)
    )
    return max(0.0, center - half), min(1.0, center + half)


def average_ranks(values: Sequence[float]) -> np.ndarray:
    x = np.asarray(values, dtype=float)
    require(x.ndim == 1 and x.size > 0 and np.all(np.isfinite(x)),
            "ranks require finite nonempty values")
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(x.size, dtype=float)
    sorted_x = x[order]
    start = 0
    while start < x.size:
        end = start + 1
        while end < x.size and sorted_x[end] == sorted_x[start]:
            end += 1
        # 1-based average rank.
        rank = ((start + 1) + end) / 2.0
        ranks[order[start:end]] = rank
        start = end
    return ranks


def pearson(x: Sequence[float], y: Sequence[float], weights: Sequence[float] | None = None) -> float:
    a = np.asarray(x, dtype=float)
    b = np.asarray(y, dtype=float)
    require(a.ndim == b.ndim == 1 and a.size == b.size and a.size >= 2,
            "Pearson requires aligned vectors with n>=2")
    if weights is None:
        da = a - np.mean(a)
        db = b - np.mean(b)
        denom = math.sqrt(float(np.dot(da, da)) * float(np.dot(db, db)))
        require(denom > 0.0, "Pearson undefined for constant vector")
        return float(np.dot(da, db) / denom)

    w = np.asarray(weights, dtype=float)
    require(w.size == a.size and np.all(w >= 0.0) and float(np.sum(w)) > 0.0,
            "invalid Pearson weights")
    sw = float(np.sum(w))
    ma = float(np.dot(w, a) / sw)
    mb = float(np.dot(w, b) / sw)
    da = a - ma
    db = b - mb
    va = float(np.dot(w, da * da))
    vb = float(np.dot(w, db * db))
    denom = math.sqrt(va * vb)
    require(denom > 0.0, "weighted Pearson undefined for constant vector")
    return float(np.dot(w, da * db) / denom)


def spearman_rho(x: Sequence[float], y: Sequence[float]) -> float:
    return pearson(average_ranks(x), average_ranks(y))


def _tie_groups(values: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    order = np.argsort(values, kind="mergesort")
    sx = values[order]
    gid_sorted = np.zeros(values.size, dtype=np.int64)
    gid = 0
    for i in range(1, values.size):
        if sx[i] != sx[i - 1]:
            gid += 1
        gid_sorted[i] = gid
    gid_original = np.empty_like(gid_sorted)
    gid_original[order] = gid_sorted
    return order, gid_original, gid + 1


def weighted_midrank_for_bootstrap(
    values: np.ndarray,
    counts: np.ndarray,
    cached: tuple[np.ndarray, np.ndarray, int] | None = None,
) -> np.ndarray:
    """Average ranks of a duplicated bootstrap sample represented by integer counts."""
    require(values.ndim == counts.ndim == 1 and values.size == counts.size,
            "weighted midrank vectors misaligned")
    require(np.all(counts >= 0) and int(np.sum(counts)) >= 2,
            "bootstrap counts invalid")
    order, gid_original, ngroups = cached or _tie_groups(values)
    group_weights = np.bincount(
        gid_original,
        weights=counts.astype(float),
        minlength=ngroups,
    )
    before = np.cumsum(group_weights) - group_weights
    group_midrank = before + (group_weights + 1.0) / 2.0
    return group_midrank[gid_original]


def bootstrap_spearman_interval(
    x: Sequence[float],
    y: Sequence[float],
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float, int]:
    a = np.asarray(x, dtype=float)
    b = np.asarray(y, dtype=float)
    require(a.size == b.size and a.size >= 2, "Spearman bootstrap requires n>=2")
    require(np.all(np.isfinite(a)) and np.all(np.isfinite(b)), "nonfinite association input")
    n = a.size
    rng = np.random.default_rng(seed)
    cache_a = _tie_groups(a)
    cache_b = _tie_groups(b)
    values: list[float] = []
    probabilities = np.full(n, 1.0 / n, dtype=float)

    for _ in range(replicates):
        counts = rng.multinomial(n, probabilities)
        if np.count_nonzero(counts) < 2:
            continue
        ra = weighted_midrank_for_bootstrap(a, counts, cache_a)
        rb = weighted_midrank_for_bootstrap(b, counts, cache_b)
        try:
            rho = pearson(ra, rb, weights=counts)
        except A2SummaryError:
            continue
        if math.isfinite(rho):
            values.append(rho)

    require(values, "no valid Spearman bootstrap replicates")
    return (
        inverse_ecdf_quantile(values, 0.025),
        inverse_ecdf_quantile(values, 0.975),
        len(values),
    )


def bootstrap_event_equal_quantile_interval(
    event_values: dict[int, Sequence[float]],
    p: float,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = BOOTSTRAP_SEED,
    batch_size: int = 25,
) -> tuple[float, float]:
    """Bootstrap a pooled event-equal weighted quantile while retaining each event's rows."""
    event_ids = sorted(event_values)
    require(event_ids, "event bootstrap requires eligible events")
    flat_values: list[float] = []
    flat_event_pos: list[int] = []
    row_count_per_event = np.zeros(len(event_ids), dtype=np.int64)
    for pos, event_id in enumerate(event_ids):
        vals = np.asarray(event_values[event_id], dtype=float)
        require(vals.size > 0 and np.all(np.isfinite(vals)), "event contains no finite values")
        row_count_per_event[pos] = vals.size
        flat_values.extend(vals.tolist())
        flat_event_pos.extend([pos] * vals.size)

    x = np.asarray(flat_values, dtype=float)
    event_pos = np.asarray(flat_event_pos, dtype=np.int64)
    order = np.argsort(x, kind="mergesort")
    xs = x[order]
    eps = event_pos[order]
    row_factor = 1.0 / row_count_per_event[eps].astype(float)

    n_events = len(event_ids)
    probs = np.full(n_events, 1.0 / n_events, dtype=float)
    rng = np.random.default_rng(seed)
    estimates: list[float] = []

    remaining = replicates
    while remaining:
        b = min(batch_size, remaining)
        counts = rng.multinomial(n_events, probs, size=b)
        weights = counts[:, eps].astype(float) * row_factor[None, :]
        cumulative = np.cumsum(weights, axis=1)
        thresholds = (float(p) * np.sum(weights, axis=1))[:, None]
        reached = cumulative >= thresholds
        idx = np.argmax(reached, axis=1)
        estimates.extend(xs[idx].astype(float).tolist())
        remaining -= b

    require(len(estimates) == replicates, "event bootstrap replicate count mismatch")
    return (
        inverse_ecdf_quantile(estimates, 0.025),
        inverse_ecdf_quantile(estimates, 0.975),
    )


def pair_descriptors(row1: dict, row2: dict) -> dict[str, float | None]:
    """Build only the five pair descriptors frozen in DR-0069."""
    state_age = max(float(row1["q01_state_age_h"]), float(row2["q01_state_age_h"]))
    publication_age = max(float(row1["q02_publication_age_h"]), float(row2["q02_publication_age_h"]))
    distinct_state = min(float(row1["w30_distinct_state_count"]), float(row2["w30_distinct_state_count"]))

    gap1 = row1.get("w30_max_gap_h")
    gap2 = row2.get("w30_max_gap_h")
    if gap1 is None or gap2 is None:
        recent_gap = None
    else:
        recent_gap = max(float(gap1), float(gap2))

    q61 = row1.get("q06_propagation_span_h")
    q62 = row2.get("q06_propagation_span_h")
    if (
        row1.get("q06_status") == "VALID_ASSOCIATED"
        and row2.get("q06_status") == "VALID_ASSOCIATED"
        and q61 is not None
        and q62 is not None
    ):
        propagation_span = max(float(q61), float(q62))
    else:
        propagation_span = None

    return {
        "pair_max_state_age_h": state_age,
        "pair_max_publication_age_h": publication_age,
        "pair_min_distinct_state_count": distinct_state,
        "pair_max_recent_publication_gap_h": recent_gap,
        "pair_max_propagation_span_h": propagation_span,
    }


def ro_connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def verify_scientific_input_hashes() -> dict[str, str]:
    expected = {
        SPEC: EXPECTED_SPEC_SHA256,
        DR0069: EXPECTED_DR0069_SHA256,
        CHANGES_CSV: EXPECTED_CHANGES_SHA256,
        EVENT_STATUS_CSV: EXPECTED_EVENT_STATUS_SHA256,
        S07_CSV: EXPECTED_S07_SHA256,
        DERIVED_DB: EXPECTED_DERIVED_DB_SHA256,
        MATERIALIZED_DB: EXPECTED_MATERIALIZED_DB_SHA256,
    }
    observed = {}
    for path, expected_sha in expected.items():
        require(path.is_file(), f"frozen summary input missing: {path}")
        actual = sha256(path)
        require(actual == expected_sha, f"frozen summary input SHA mismatch: {path}")
        observed[path.relative_to(ROOT).as_posix()] = actual
    return observed


def verify_execution_authorization() -> dict:
    current_head = git_value("rev-parse", "HEAD")
    impl_target = tag_target(IMPLEMENTATION_FREEZE_TAG)
    auth_target = tag_target(EXECUTION_AUTHORIZATION_TAG)
    if impl_target is None or auth_target is None:
        raise A2SummaryExecutionNotAuthorized(
            "A2_SCIENTIFIC_SUMMARY_EXECUTION_AUTHORIZATION_TAG_MISSING"
        )
    require(auth_target == current_head, "summary execution authorization tag must target HEAD")
    require(git_value("rev-parse", "HEAD^") == impl_target,
            "summary authorization commit must be direct child of implementation freeze")
    require(tag_target(SPEC_FREEZE_TAG) == SPEC_FREEZE_COMMIT,
            "scientific-summary specification freeze tag changed")
    require(
        not git_value("status", "--porcelain=v1", "--untracked-files=no"),
        "tracked working tree not clean before summary execution",
    )
    for output in SUMMARY_OUTPUTS:
        require(not output.exists(), f"summary output already exists; refusing overwrite: {output}")
    return {
        "authorization_head": current_head,
        "implementation_freeze_commit": impl_target,
        "authorization_tag": EXECUTION_AUTHORIZATION_TAG,
    }


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _event_map_and_strata() -> tuple[dict[int, dict], dict[int, str]]:
    con = ro_connect(MATERIALIZED_DB)
    try:
        rows = con.execute(
            "SELECT event_index,conj_id,stratum,expected_eligible_replays,expected_support_replays "
            "FROM events ORDER BY event_index"
        ).fetchall()
    finally:
        con.close()
    require(len(rows) == EXPECTED_EVENTS, "materialized event census changed")
    events = {int(r["event_index"]): dict(r) for r in rows}
    strata = {i: str(events[i]["stratum"]) for i in events}
    require(set(strata.values()) == set(TEMPORAL_STRATA), "unexpected temporal strata")
    return events, strata


def _load_change_data() -> tuple[dict[int, list[float]], dict[int, list[float]], dict[int, list[int]]]:
    rows = read_csv(CHANGES_CSV)
    require(len(rows) == EXPECTED_CHANGE_ROWS, "S05/S06 change-row count changed")
    s05: dict[int, list[float]] = defaultdict(list)
    s06: dict[int, list[float]] = defaultdict(list)
    endpoints: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        e = int(row["event_index"])
        s05[e].append(float(row["absolute_tca_change_s"]))
        s06[e].append(float(row["absolute_miss_distance_change_km"]))
        endpoints[e].append(int(row["current_replay_sequence"]))
    require(len(s05) == EXPECTED_S05_S06_VALID_EVENTS, "S05 eligible-event count changed")
    require(len(s06) == EXPECTED_S05_S06_VALID_EVENTS, "S06 eligible-event count changed")
    return s05, s06, endpoints


def _flatten_events(event_values: dict[int, Sequence[float]], eligible: set[int] | None = None):
    values: list[float] = []
    event_ids: list[int] = []
    for e in sorted(event_values):
        if eligible is not None and e not in eligible:
            continue
        vals = event_values[e]
        values.extend(float(v) for v in vals)
        event_ids.extend([e] * len(vals))
    return values, event_ids


def _s05_s06_rows(
    s05: dict[int, list[float]],
    s06: dict[int, list[float]],
    strata: dict[int, str],
) -> list[dict]:
    out = []
    for metric, unit, data in (
        ("S05_ABSOLUTE_TCA_UPDATE_CHANGE", "s", s05),
        ("S06_ABSOLUTE_MISS_DISTANCE_UPDATE_CHANGE", "km", s06),
    ):
        values, ids = _flatten_events(data)
        overall = distribution_summary(values, ids)
        lo50, hi50 = bootstrap_event_equal_quantile_interval(data, 0.50)
        lo90, hi90 = bootstrap_event_equal_quantile_interval(data, 0.90)
        out.append({
            "metric": metric,
            "unit": unit,
            "scope": "OVERALL",
            "temporal_stratum": "ALL",
            **overall,
            "p50_bootstrap_95_lower": lo50,
            "p50_bootstrap_95_upper": hi50,
            "p90_bootstrap_95_lower": lo90,
            "p90_bootstrap_95_upper": hi90,
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "interpretation": "STABILITY_NOT_ACCURACY",
        })
        for stratum in TEMPORAL_STRATA:
            selected = {e for e in data if strata[e] == stratum}
            vals, eids = _flatten_events(data, selected)
            desc = distribution_summary(vals, eids)
            out.append({
                "metric": metric,
                "unit": unit,
                "scope": "TEMPORAL_STRATUM",
                "temporal_stratum": stratum,
                **desc,
                "p50_bootstrap_95_lower": "",
                "p50_bootstrap_95_upper": "",
                "p90_bootstrap_95_lower": "",
                "p90_bootstrap_95_upper": "",
                "bootstrap_replicates": "",
                "bootstrap_seed": "",
                "interpretation": "DESCRIPTIVE_STABILITY_NOT_ACCURACY",
            })
    return out


def _s07_rows(strata: dict[int, str]) -> list[dict]:
    rows = read_csv(S07_CSV)
    require(len(rows) == EXPECTED_EVENTS, "S07 event-row count changed")
    by_event = {int(r["event_index"]): r for r in rows}
    require(len(by_event) == EXPECTED_EVENTS, "duplicate/missing S07 event index")

    out = []
    scopes = [("OVERALL", "ALL", set(by_event))]
    scopes.extend(
        ("TEMPORAL_STRATUM", s, {e for e in by_event if strata[e] == s})
        for s in TEMPORAL_STRATA
    )

    for scope, stratum, event_set in scopes:
        selected = [by_event[e] for e in sorted(event_set)]
        n = len(selected)
        valid = [r for r in selected if r["s07_status"] == "VALID"]
        missing = [r for r in selected if r["s07_status"] == "NO PRE-TCA RECONSTRUCTION"]
        require(len(valid) + len(missing) == n, "unexpected S07 status")
        vlo, vhi = wilson_interval(len(valid), n)
        mlo, mhi = wilson_interval(len(missing), n)

        lead_values = [float(r["lead_h"]) for r in valid]
        desc = distribution_summary(lead_values)
        if scope == "OVERALL":
            event_values = {int(r["event_index"]): [float(r["lead_h"])] for r in valid}
            blo, bhi = bootstrap_event_equal_quantile_interval(event_values, 0.50)
            reps, seed = BOOTSTRAP_REPLICATES, BOOTSTRAP_SEED
        else:
            blo = bhi = ""
            reps = seed = ""

        out.append({
            "scope": scope,
            "temporal_stratum": stratum,
            "denominator_events": n,
            "valid_events": len(valid),
            "valid_proportion": len(valid) / n,
            "valid_wilson_95_lower": vlo,
            "valid_wilson_95_upper": vhi,
            "no_pre_tca_events": len(missing),
            "no_pre_tca_proportion": len(missing) / n,
            "no_pre_tca_wilson_95_lower": mlo,
            "no_pre_tca_wilson_95_upper": mhi,
            "lead_support_events": desc["support_rows"],
            "lead_min_h": desc["minimum"],
            "lead_p50_h": desc["p50"],
            "lead_p90_h": desc["p90"],
            "lead_p95_h": desc["p95"],
            "lead_p99_h": desc["p99"],
            "lead_max_h": desc["maximum"],
            "lead_p50_bootstrap_95_lower_h": blo,
            "lead_p50_bootstrap_95_upper_h": bhi,
            "bootstrap_replicates": reps,
            "bootstrap_seed": seed,
            "interpretation": "REFERENCE_EVENT_RECONSTRUCTION_LEAD_NOT_WARNING_DISCOVERY_SCREENING_ALERT_LEAD",
        })
    return out


def _load_q_rows_for_descriptor(
    value_column: str,
    where_clause: str | None = None,
) -> tuple[np.ndarray, np.ndarray, int]:
    con = ro_connect(DERIVED_DB)
    try:
        sql = f"SELECT event_index,{value_column} AS value FROM q_object_updates"
        if where_clause:
            sql += " WHERE " + where_clause
        cur = con.execute(sql)
        event_ids: list[int] = []
        values: list[float] = []
        missing = 0
        for row in cur:
            if row["value"] is None:
                missing += 1
                continue
            event_ids.append(int(row["event_index"]))
            values.append(float(row["value"]))
        return (
            np.asarray(values, dtype=float),
            np.asarray(event_ids, dtype=np.int64),
            missing,
        )
    finally:
        con.close()


def _load_pair_descriptors(
    endpoint_keys: set[tuple[int, int]] | None = None,
) -> tuple[dict[str, np.ndarray], np.ndarray, dict[tuple[int, int], dict[str, float | None]], int]:
    """One ordered pass over Q rows; optionally retain descriptors for selected replay keys."""
    con = ro_connect(DERIVED_DB)
    values: dict[str, list[float]] = defaultdict(list)
    events: dict[str, list[int]] = defaultdict(list)
    selected: dict[tuple[int, int], dict[str, float | None]] = {}
    total_pairs = 0
    try:
        cur = con.execute(
            """
            SELECT
                event_index,replay_sequence,object_role,
                q01_state_age_h,q02_publication_age_h,
                w30_distinct_state_count,w30_max_gap_h,
                q06_status,q06_propagation_span_h
            FROM q_object_updates
            ORDER BY event_index,replay_sequence,object_role
            """
        )
        pending_key = None
        pending = []
        for row in cur:
            key = (int(row["event_index"]), int(row["replay_sequence"]))
            if pending_key is None:
                pending_key = key
            if key != pending_key:
                require(len(pending) == 2, f"pair role cardinality !=2 at {pending_key}")
                by_role = {str(r["object_role"]): dict(r) for r in pending}
                require(set(by_role) == {"obj1", "obj2"}, f"pair roles invalid at {pending_key}")
                desc = pair_descriptors(by_role["obj1"], by_role["obj2"])
                total_pairs += 1
                for name, val in desc.items():
                    if val is not None:
                        values[name].append(float(val))
                        events[name].append(pending_key[0])
                if endpoint_keys is not None and pending_key in endpoint_keys:
                    selected[pending_key] = desc
                pending_key = key
                pending = []
            pending.append(row)

        if pending_key is not None:
            require(len(pending) == 2, f"pair role cardinality !=2 at {pending_key}")
            by_role = {str(r["object_role"]): dict(r) for r in pending}
            require(set(by_role) == {"obj1", "obj2"}, f"pair roles invalid at {pending_key}")
            desc = pair_descriptors(by_role["obj1"], by_role["obj2"])
            total_pairs += 1
            for name, val in desc.items():
                if val is not None:
                    values[name].append(float(val))
                    events[name].append(pending_key[0])
            if endpoint_keys is not None and pending_key in endpoint_keys:
                selected[pending_key] = desc
    finally:
        con.close()

    packed = {name: np.asarray(vals, dtype=float) for name, vals in values.items()}
    # event arrays are returned separately by descriptor via an encoded object container
    packed_events = {name: np.asarray(events[name], dtype=np.int64) for name in packed}
    packed["_EVENT_IDS_CONTAINER"] = np.asarray([packed_events], dtype=object)
    return packed, np.empty(0, dtype=np.int64), selected, total_pairs


def _quality_summary_rows(
    pair_pack: dict[str, np.ndarray],
) -> list[dict]:
    out = []
    object_specs = [
        ("Q01_STATE_AGE_H", "q01_state_age_h", None),
        ("Q02_PUBLICATION_AGE_H", "q02_publication_age_h", None),
        ("Q03_30D_PUBLICATION_COUNT", "w30_publication_count", None),
        ("Q04_30D_DISTINCT_STATE_COUNT", "w30_distinct_state_count", None),
        ("Q05_30D_MEDIAN_PUBLICATION_CADENCE_H", "w30_median_cadence_h", None),
        ("Q05_30D_MAX_PUBLICATION_GAP_H", "w30_max_gap_h", None),
        ("Q06_PROPAGATION_SPAN_H", "q06_propagation_span_h", "q06_status='VALID_ASSOCIATED'"),
    ]
    for label, col, where in object_specs:
        values, event_ids, missing = _load_q_rows_for_descriptor(col, where)
        desc = distribution_summary(values, event_ids)
        out.append({
            "level": "OBJECT_EVENT_UPDATE",
            "descriptor": label,
            "support_events": desc["support_events"],
            "support_rows": desc["support_rows"],
            "missing_or_not_applicable_rows": missing if where is None else EXPECTED_Q_ROWS - desc["support_rows"],
            "weighting": desc["weighting"],
            "minimum": desc["minimum"],
            "p50": desc["p50"],
            "p90": desc["p90"],
            "p95": desc["p95"],
            "p99": desc["p99"],
            "maximum": desc["maximum"],
            "interpretation": "DESCRIPTIVE_INFORMATION_OR_PROPAGATION_DESCRIPTOR",
        })

    event_container = pair_pack.pop("_EVENT_IDS_CONTAINER")
    pair_events: dict[str, np.ndarray] = event_container[0]
    for name in (
        "pair_max_state_age_h",
        "pair_max_publication_age_h",
        "pair_min_distinct_state_count",
        "pair_max_recent_publication_gap_h",
        "pair_max_propagation_span_h",
    ):
        vals = pair_pack[name]
        ids = pair_events[name]
        desc = distribution_summary(vals, ids)
        out.append({
            "level": "PAIR_REPLAY",
            "descriptor": name.upper(),
            "support_events": desc["support_events"],
            "support_rows": desc["support_rows"],
            "missing_or_not_applicable_rows": EXPECTED_PUBLICATION_OPPORTUNITIES - desc["support_rows"],
            "weighting": desc["weighting"],
            "minimum": desc["minimum"],
            "p50": desc["p50"],
            "p90": desc["p90"],
            "p95": desc["p95"],
            "p99": desc["p99"],
            "maximum": desc["maximum"],
            "interpretation": "DESCRIPTIVE_PAIR_INFORMATION_OR_PROPAGATION_DESCRIPTOR",
        })
    return out


def _association_rows(
    s05: dict[int, list[float]],
    s06: dict[int, list[float]],
    endpoints: dict[int, list[int]],
    selected_pair_desc: dict[tuple[int, int], dict[str, float | None]],
) -> list[dict]:
    quality_names = (
        "pair_max_state_age_h",
        "pair_min_distinct_state_count",
        "pair_max_recent_publication_gap_h",
        "pair_max_propagation_span_h",
    )
    stability = {
        "S05_EVENT_P50_ABSOLUTE_TCA_UPDATE_CHANGE_S": {
            e: inverse_ecdf_quantile(vals, 0.50) for e, vals in s05.items()
        },
        "S06_EVENT_P50_ABSOLUTE_MISS_DISTANCE_UPDATE_CHANGE_KM": {
            e: inverse_ecdf_quantile(vals, 0.50) for e, vals in s06.items()
        },
    }

    event_quality: dict[str, dict[int, float]] = {q: {} for q in quality_names}
    for e in sorted(endpoints):
        for q in quality_names:
            vals = []
            for seq in endpoints[e]:
                desc = selected_pair_desc.get((e, seq))
                if desc is None:
                    continue
                value = desc[q]
                if value is not None:
                    vals.append(float(value))
            if vals:
                event_quality[q][e] = inverse_ecdf_quantile(vals, 0.50)

    out = []
    for stability_name, stab_map in stability.items():
        for q in quality_names:
            common = sorted(set(stab_map) & set(event_quality[q]))
            require(len(common) >= 2, f"insufficient support for association {stability_name} x {q}")
            x = np.asarray([stab_map[e] for e in common], dtype=float)
            y = np.asarray([event_quality[q][e] for e in common], dtype=float)
            rho = spearman_rho(x, y)
            blo, bhi, valid_reps = bootstrap_spearman_interval(x, y)
            out.append({
                "stability_metric": stability_name,
                "quality_descriptor": q.upper(),
                "n_events": len(common),
                "spearman_rho": rho,
                "bootstrap_95_lower": blo,
                "bootstrap_95_upper": bhi,
                "bootstrap_replicates_requested": BOOTSTRAP_REPLICATES,
                "bootstrap_replicates_valid": valid_reps,
                "bootstrap_seed": BOOTSTRAP_SEED,
                "analysis_unit": "EVENT",
                "interpretation": "DESCRIPTIVE_NONCAUSAL_NO_THRESHOLD_OR_PREDICTOR_RANKING",
            })
    require(len(out) == 8, "quality-stability association family must contain exactly eight rows")
    return out


def execute_scientific_summary(authorization: bool = False) -> dict:
    if not authorization:
        raise A2SummaryExecutionNotAuthorized(
            "A2_SCIENTIFIC_SUMMARY_EXECUTION_NOT_AUTHORIZED"
        )

    authorization_info = verify_execution_authorization()
    input_hashes = verify_scientific_input_hashes()

    events, strata = _event_map_and_strata()
    require(sum(int(v["expected_eligible_replays"]) for v in events.values())
            == EXPECTED_PUBLICATION_OPPORTUNITIES,
            "publication-opportunity census changed")

    status_rows = read_csv(EVENT_STATUS_CSV)
    require(len(status_rows) == EXPECTED_EVENTS, "event-status census changed")
    status_counts = Counter(r["s05_status"] for r in status_rows)
    require(status_counts["VALID"] == EXPECTED_S05_S06_VALID_EVENTS,
            "S05/S06 VALID event count changed")

    s05, s06, endpoints = _load_change_data()
    endpoint_keys = {
        (e, seq)
        for e, sequences in endpoints.items()
        for seq in sequences
    }

    s05_s06_rows = _s05_s06_rows(s05, s06, strata)
    s07_rows = _s07_rows(strata)

    pair_pack, _, selected_pair_desc, total_pairs = _load_pair_descriptors(endpoint_keys)
    require(total_pairs == EXPECTED_PUBLICATION_OPPORTUNITIES,
            "pair replay opportunity count changed")
    require(len(selected_pair_desc) == len(endpoint_keys),
            "missing pair descriptors at S05/S06 current replay endpoints")

    quality_rows = _quality_summary_rows(pair_pack)
    association_rows = _association_rows(s05, s06, endpoints, selected_pair_desc)

    _write_csv(
        S05_S06_SUMMARY_CSV,
        [
            "metric","unit","scope","temporal_stratum","support_rows","support_events",
            "weighting","minimum","p50","p90","p95","p99","maximum",
            "p50_bootstrap_95_lower","p50_bootstrap_95_upper",
            "p90_bootstrap_95_lower","p90_bootstrap_95_upper",
            "bootstrap_replicates","bootstrap_seed","interpretation",
        ],
        s05_s06_rows,
    )
    _write_csv(
        S07_SUMMARY_CSV,
        [
            "scope","temporal_stratum","denominator_events","valid_events","valid_proportion",
            "valid_wilson_95_lower","valid_wilson_95_upper",
            "no_pre_tca_events","no_pre_tca_proportion",
            "no_pre_tca_wilson_95_lower","no_pre_tca_wilson_95_upper",
            "lead_support_events","lead_min_h","lead_p50_h","lead_p90_h","lead_p95_h",
            "lead_p99_h","lead_max_h","lead_p50_bootstrap_95_lower_h",
            "lead_p50_bootstrap_95_upper_h","bootstrap_replicates","bootstrap_seed",
            "interpretation",
        ],
        s07_rows,
    )
    _write_csv(
        QUALITY_SUMMARY_CSV,
        [
            "level","descriptor","support_events","support_rows","missing_or_not_applicable_rows",
            "weighting","minimum","p50","p90","p95","p99","maximum","interpretation",
        ],
        quality_rows,
    )
    _write_csv(
        ASSOCIATIONS_CSV,
        [
            "stability_metric","quality_descriptor","n_events","spearman_rho",
            "bootstrap_95_lower","bootstrap_95_upper","bootstrap_replicates_requested",
            "bootstrap_replicates_valid","bootstrap_seed","analysis_unit","interpretation",
        ],
        association_rows,
    )

    summary = {
        "status": "G7R6_A2_SCIENTIFIC_SUMMARY_EXECUTION_COMPLETED",
        "authorization": authorization_info,
        "input_hashes": input_hashes,
        "events": EXPECTED_EVENTS,
        "publication_replay_opportunities": EXPECTED_PUBLICATION_OPPORTUNITIES,
        "q_object_update_rows": EXPECTED_Q_ROWS,
        "s05_s06_change_rows": EXPECTED_CHANGE_ROWS,
        "s05_s06_valid_events": EXPECTED_S05_S06_VALID_EVENTS,
        "s07_valid_events": EXPECTED_S07_VALID_EVENTS,
        "s07_no_pre_tca_events": EXPECTED_S07_NO_PRE_TCA,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "quality_stability_associations": len(association_rows),
        "quantile_convention": "INVERSE_EMPIRICAL_CDF_NO_INTERPOLATION",
        "grouped_weighting": "EVENT_EQUAL",
        "scientific_interpretation_performed": False,
        "secondary_14d_60d_execution_performed": False,
        "A0_execution_performed": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_recomputed": False,
        "raw_GP_HISTORY_opened": False,
        "network_requests": 0,
        "next_safe_action":
            "INDEPENDENT_A2_SCIENTIFIC_SUMMARY_OUTPUT_AUDIT_BEFORE_INTERPRETATION",
    }

    with SUMMARY_JSON.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(summary, stream, indent=2, sort_keys=True)
        stream.write("\n")

    output_hashes = {
        path.relative_to(ROOT).as_posix(): sha256(path)
        for path in (
            S05_S06_SUMMARY_CSV,
            S07_SUMMARY_CSV,
            QUALITY_SUMMARY_CSV,
            ASSOCIATIONS_CSV,
            SUMMARY_JSON,
        )
    }
    audit = {
        **summary,
        "status": "G7R6_A2_SCIENTIFIC_SUMMARY_EXECUTION_AUDIT",
        "output_hashes": output_hashes,
    }
    with EXECUTION_AUDIT_JSON.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(audit, stream, indent=2, sort_keys=True)
        stream.write("\n")

    print(json.dumps(
        {
            "status": summary["status"],
            "events": EXPECTED_EVENTS,
            "publication_replay_opportunities": EXPECTED_PUBLICATION_OPPORTUNITIES,
            "s05_s06_change_rows": EXPECTED_CHANGE_ROWS,
            "quality_stability_associations": len(association_rows),
            "output_hashes": {
                **output_hashes,
                EXECUTION_AUDIT_JSON.relative_to(ROOT).as_posix(): sha256(EXECUTION_AUDIT_JSON),
            },
            "scientific_interpretation_performed": False,
            "secondary_14d_60d_execution_performed": False,
            "A0_execution_performed": False,
            "next_safe_action": summary["next_safe_action"],
        },
        indent=2,
        sort_keys=True,
    ))
    return summary


def run_synthetic_validation() -> dict:
    tests = {}

    # Exact inverse empirical CDF, no interpolation.
    x = [1.0, 2.0, 3.0, 4.0]
    tests["inverse_ecdf_p50"] = inverse_ecdf_quantile(x, 0.50) == 2.0
    tests["inverse_ecdf_p90"] = inverse_ecdf_quantile(x, 0.90) == 4.0

    # Event-equal weighting: event 1 has 100 zero-valued rows, event 2 one 10-valued row.
    values = [0.0] * 100 + [10.0]
    ids = [1] * 100 + [2]
    w = event_equal_weights(ids)
    tests["event_equal_each_event_mass"] = math.isclose(float(np.sum(w[:100])), float(w[100]), rel_tol=0, abs_tol=1e-12)
    tests["event_equal_weighted_p75"] = weighted_inverse_ecdf_quantile(values, w, 0.75) == 10.0

    # Wilson interval sanity.
    lo, hi = wilson_interval(5, 10)
    tests["wilson_contains_observed"] = lo < 0.5 < hi
    tests["wilson_bounds"] = 0.0 <= lo <= hi <= 1.0

    # Spearman with ties/directions.
    tests["spearman_positive"] = math.isclose(spearman_rho([1,2,3,4], [10,20,30,40]), 1.0, abs_tol=1e-12)
    tests["spearman_negative"] = math.isclose(spearman_rho([1,2,3,4], [40,30,20,10]), -1.0, abs_tol=1e-12)
    tests["spearman_ties_finite"] = math.isfinite(spearman_rho([1,1,2,3], [2,3,4,5]))

    # Pair descriptor semantics.
    r1 = {
        "q01_state_age_h": 2.0, "q02_publication_age_h": 1.0,
        "w30_distinct_state_count": 5, "w30_max_gap_h": 7.0,
        "q06_status": "VALID_ASSOCIATED", "q06_propagation_span_h": 8.0,
    }
    r2 = {
        "q01_state_age_h": 3.0, "q02_publication_age_h": 0.5,
        "w30_distinct_state_count": 4, "w30_max_gap_h": 6.0,
        "q06_status": "VALID_ASSOCIATED", "q06_propagation_span_h": 9.0,
    }
    pd = pair_descriptors(r1, r2)
    tests["pair_max_state_age"] = pd["pair_max_state_age_h"] == 3.0
    tests["pair_max_publication_age"] = pd["pair_max_publication_age_h"] == 1.0
    tests["pair_min_distinct_state"] = pd["pair_min_distinct_state_count"] == 4.0
    tests["pair_max_recent_gap"] = pd["pair_max_recent_publication_gap_h"] == 7.0
    tests["pair_max_propagation_span"] = pd["pair_max_propagation_span_h"] == 9.0

    r2_missing = dict(r2)
    r2_missing["w30_max_gap_h"] = None
    r2_missing["q06_status"] = "NOT_APPLICABLE_NON_ASSOCIATED"
    pd2 = pair_descriptors(r1, r2_missing)
    tests["pair_gap_missing_propagates"] = pd2["pair_max_recent_publication_gap_h"] is None
    tests["pair_q06_missing_propagates"] = pd2["pair_max_propagation_span_h"] is None

    # Bootstrap determinism and event-level retention.
    synthetic_events = {1: [1.0, 1.5], 2: [2.0], 3: [3.0, 4.0, 5.0]}
    a = bootstrap_event_equal_quantile_interval(synthetic_events, 0.50, replicates=100, seed=123, batch_size=10)
    b = bootstrap_event_equal_quantile_interval(synthetic_events, 0.50, replicates=100, seed=123, batch_size=10)
    tests["event_bootstrap_deterministic"] = a == b

    # Spearman bootstrap determinism using event as the resampling unit.
    c = bootstrap_spearman_interval([1,2,3,4,5], [5,4,3,2,1], replicates=100, seed=123)
    d = bootstrap_spearman_interval([1,2,3,4,5], [5,4,3,2,1], replicates=100, seed=123)
    tests["spearman_bootstrap_deterministic"] = c == d
    tests["spearman_bootstrap_valid_reps"] = c[2] > 0

    require(all(tests.values()), f"synthetic validation failure: {tests}")
    return {
        "status": "PASS",
        "tests": tests,
        "real_result_data_opened": False,
        "derived_metric_database_opened": False,
        "scientific_summary_values_computed": False,
    }


def offline_validate() -> dict:
    require(git_value("rev-parse", "HEAD") == SPEC_FREEZE_COMMIT,
            "offline implementation validation must run at DR-0069 freeze commit")
    require(tag_target(SPEC_FREEZE_TAG) == SPEC_FREEZE_COMMIT,
            "DR-0069 freeze tag target changed")
    require(SPEC.is_file() and sha256(SPEC) == EXPECTED_SPEC_SHA256,
            "frozen summary specification identity changed")
    require(DR0069.is_file() and sha256(DR0069) == EXPECTED_DR0069_SHA256,
            "DR-0069 identity changed")
    require(tag_target(IMPLEMENTATION_FREEZE_TAG) is None,
            "implementation freeze tag already exists during pre-freeze validation")
    require(tag_target(EXECUTION_AUTHORIZATION_TAG) is None,
            "summary execution authorization tag already exists")
    require(not OFFLINE_VALIDATION_JSON.exists(), "offline validation output already exists")
    for output in SUMMARY_OUTPUTS:
        require(not output.exists(), f"scientific summary output exists before authorization: {output}")

    blocked_false = False
    try:
        execute_scientific_summary(authorization=False)
    except A2SummaryExecutionNotAuthorized:
        blocked_false = True
    require(blocked_false, "authorization=False did not block summary execution")

    blocked_missing_tag = False
    try:
        execute_scientific_summary(authorization=True)
    except A2SummaryExecutionNotAuthorized:
        blocked_missing_tag = True
    require(blocked_missing_tag, "missing authorization tag did not block summary execution")

    synthetic = run_synthetic_validation()

    result = {
        "status": "G7R6_A2_SCIENTIFIC_SUMMARY_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",
        "parent_commit": SPEC_FREEZE_COMMIT,
        "parent_tag": SPEC_FREEZE_TAG,
        "spec_sha256": EXPECTED_SPEC_SHA256,
        "dr0069_sha256": EXPECTED_DR0069_SHA256,
        "executor_sha256": sha256(Path(__file__)),
        "execution_guard_authorization_false_verified": blocked_false,
        "execution_guard_missing_tag_verified": blocked_missing_tag,
        "synthetic_validation": synthetic,
        "planned_outputs": [
            path.relative_to(ROOT).as_posix() for path in SUMMARY_OUTPUTS
        ],
        "planned_output_schemas": {
            "s05_s06_summary": [
                "metric","unit","scope","temporal_stratum","support_rows","support_events",
                "weighting","minimum","p50","p90","p95","p99","maximum",
                "p50_bootstrap_95_lower","p50_bootstrap_95_upper",
                "p90_bootstrap_95_lower","p90_bootstrap_95_upper",
                "bootstrap_replicates","bootstrap_seed","interpretation",
            ],
            "s07_summary": [
                "scope","temporal_stratum","denominator_events","valid_events","valid_proportion",
                "valid_wilson_95_lower","valid_wilson_95_upper",
                "no_pre_tca_events","no_pre_tca_proportion",
                "no_pre_tca_wilson_95_lower","no_pre_tca_wilson_95_upper",
                "lead_support_events","lead_min_h","lead_p50_h","lead_p90_h","lead_p95_h",
                "lead_p99_h","lead_max_h","lead_p50_bootstrap_95_lower_h",
                "lead_p50_bootstrap_95_upper_h","bootstrap_replicates","bootstrap_seed",
                "interpretation",
            ],
            "quality_summary": [
                "level","descriptor","support_events","support_rows","missing_or_not_applicable_rows",
                "weighting","minimum","p50","p90","p95","p99","maximum","interpretation",
            ],
            "associations": [
                "stability_metric","quality_descriptor","n_events","spearman_rho",
                "bootstrap_95_lower","bootstrap_95_upper","bootstrap_replicates_requested",
                "bootstrap_replicates_valid","bootstrap_seed","analysis_unit","interpretation",
            ],
        },
        "expected_counts": {
            "events": EXPECTED_EVENTS,
            "publication_replay_opportunities": EXPECTED_PUBLICATION_OPPORTUNITIES,
            "q_object_update_rows": EXPECTED_Q_ROWS,
            "s05_s06_change_rows": EXPECTED_CHANGE_ROWS,
            "s05_s06_valid_events": EXPECTED_S05_S06_VALID_EVENTS,
            "s07_valid_events": EXPECTED_S07_VALID_EVENTS,
            "s07_no_pre_tca_events": EXPECTED_S07_NO_PRE_TCA,
            "quality_stability_associations": 8,
        },
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "quantile_convention": "INVERSE_EMPIRICAL_CDF_NO_INTERPOLATION",
        "grouped_weighting": "EVENT_EQUAL",
        "result_data_opened": False,
        "derived_metric_database_opened": False,
        "scientific_summary_values_computed": False,
        "scientific_interpretation_performed": False,
        "secondary_14d_60d_execution_performed": False,
        "A0_execution_performed": False,
        "orbital_propagation_performed": False,
        "reference_association_recomputed": False,
        "raw_GP_HISTORY_opened": False,
        "network_requests": 0,
        "next_safe_action":
            "INDEPENDENT_REVIEW_THEN_FREEZE_A2_SCIENTIFIC_SUMMARY_IMPLEMENTATION_BEFORE_EXECUTION",
    }

    OFFLINE_VALIDATION_JSON.parent.mkdir(parents=True, exist_ok=True)
    with OFFLINE_VALIDATION_JSON.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--offline-validate", action="store_true")
    group.add_argument("--execute-summary", action="store_true")
    args = parser.parse_args()

    if args.offline_validate:
        offline_validate()
        return 0
    if args.execute_summary:
        execute_scientific_summary(authorization=True)
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
