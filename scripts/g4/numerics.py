"""Minimal numerical machinery for G4 verification.

Units are explicit at each public function. Candidate time is seconds from a
timezone-aware UTC replay cutoff, position is km, and velocity is km/s.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import calendar
import math
from typing import Protocol, Sequence

import numpy as np
from scipy.optimize import minimize_scalar
from sgp4.api import SGP4_ERRORS, Satrec, jday


EARTH_RADIUS_KM = 6378.0
PRIMARY_HORIZON_S = 5.0 * 86400.0


class VerificationError(RuntimeError):
    """Base class for explicit numerical verification failures."""


class ParseError(VerificationError):
    """Raised when a required timestamp cannot be interpreted unambiguously."""


class PropagationError(VerificationError):
    """Raised when SGP4 returns an error or an invalid physical state."""


class OptimizationError(VerificationError):
    """Raised when a bounded local refinement does not converge."""


class UnauthorizedPathError(VerificationError):
    """Raised when conditional vector work has not been numerically authorized."""


@dataclass(frozen=True)
class GPRecord:
    object_id: str
    epoch_utc: datetime
    creation_date_utc: datetime
    stable_tie_key: str
    payload: object | None = None


@dataclass(frozen=True)
class State:
    position_km: tuple[float, float, float]
    velocity_km_s: tuple[float, float, float]


@dataclass(frozen=True)
class Candidate:
    time_s: float
    distance_km: float
    bracket_s: tuple[float, float]
    bracket_index: int
    converged: bool
    iterations: int
    function_evaluations: int
    termination: str


@dataclass(frozen=True)
class EnumerationResult:
    raw_candidates: tuple[Candidate, ...]
    candidates: tuple[Candidate, ...]
    unresolved_boundary_count: int
    sampled_count: int


@dataclass(frozen=True)
class AssociationResult:
    status: str
    candidate: Candidate | None
    tca_error_s: float | None
    miss_distance_error_km: float | None


class ScalarQuantity(Protocol):
    def evaluate(self, time_s: float) -> tuple[float, float]:
        """Return q=distance^2 [km^2] and dq/dt [km^2/s]."""

    def evaluate_many(self, times_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Vector form of evaluate()."""


def parse_utc(value: str, *, schema_declares_utc: bool = False) -> datetime:
    """Parse an ISO-8601 instant and return an aware UTC datetime.

    A naive timestamp is rejected unless its source schema explicitly declares UTC.
    Python datetime does not represent the leap-second spelling ``23:59:60``.
    """

    text = value.strip()
    if not text:
        raise ParseError("empty timestamp")
    if ":60" in text:
        raise ParseError("leap-second spelling 23:59:60 is unsupported by datetime")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ParseError(f"malformed timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        if not schema_declares_utc:
            raise ParseError("naive timestamp without an explicit UTC schema")
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def tle_epoch_to_utc(two_digit_year: int, day_of_year: float) -> datetime:
    """Convert NORAD TLE YYDDD.DDD epoch semantics to UTC."""

    if not 0 <= two_digit_year <= 99 or not math.isfinite(day_of_year):
        raise ParseError("invalid TLE epoch fields")
    year = 1900 + two_digit_year if two_digit_year >= 57 else 2000 + two_digit_year
    max_day = 366 if calendar.isleap(year) else 365
    if not 1.0 <= day_of_year < max_day + 1.0:
        raise ParseError("TLE day-of-year outside the selected year")
    return datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=day_of_year - 1.0)


def select_current_gp(records: Sequence[GPRecord], replay_utc: datetime) -> GPRecord:
    """Return max(CREATION_DATE, EPOCH, stable_tie_key) subject to causality."""

    if replay_utc.tzinfo is None:
        raise ParseError("replay cutoff must be timezone-aware")
    cutoff = replay_utc.astimezone(UTC)
    admissible = [r for r in records if r.creation_date_utc <= cutoff]
    if not admissible:
        raise VerificationError("MISSING_CURRENT_GP")
    selected = max(
        admissible,
        key=lambda r: (r.creation_date_utc, r.epoch_utc, r.stable_tie_key),
    )
    if selected.creation_date_utc > cutoff:
        raise VerificationError("CAUSALITY_VIOLATION")
    return selected


def datetime_to_jd_fr(utc: datetime) -> tuple[float, float]:
    """Convert an aware UTC datetime to split Julian date for SGP4."""

    if utc.tzinfo is None:
        raise ParseError("SGP4 evaluation time must be timezone-aware")
    t = utc.astimezone(UTC)
    second = t.second + t.microsecond / 1_000_000.0
    return jday(t.year, t.month, t.day, t.hour, t.minute, second)


def propagate_sgp4(satellite: Satrec, utc: datetime) -> State:
    """Propagate one SGP4 record to UTC, returning TEME km and km/s."""

    jd, fr = datetime_to_jd_fr(utc)
    error, position, velocity = satellite.sgp4(jd, fr)
    if error:
        message = SGP4_ERRORS.get(error, "unknown SGP4 error")
        raise PropagationError(f"SGP4_ERROR_{error}: {message}")
    values = np.asarray((*position, *velocity), dtype=float)
    if not np.all(np.isfinite(values)):
        raise PropagationError("NONFINITE_PROPAGATED_STATE")
    if float(np.linalg.norm(position)) <= EARTH_RADIUS_KM:
        raise PropagationError("RADIUS_AT_OR_BELOW_FROZEN_EARTH_RADIUS")
    return State(tuple(float(x) for x in position), tuple(float(x) for x in velocity))


def scalar_relative_geometry(first: State, second: State) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Return common-frame Delta r [km], Delta v [km/s], q [km^2], d [km]."""

    r1 = np.asarray(first.position_km, dtype=float)
    r2 = np.asarray(second.position_km, dtype=float)
    v1 = np.asarray(first.velocity_km_s, dtype=float)
    v2 = np.asarray(second.velocity_km_s, dtype=float)
    values = np.concatenate((r1, r2, v1, v2))
    if not np.all(np.isfinite(values)):
        raise PropagationError("NONFINITE_RELATIVE_STATE")
    if np.linalg.norm(r1) <= EARTH_RADIUS_KM or np.linalg.norm(r2) <= EARTH_RADIUS_KM:
        raise PropagationError("RADIUS_AT_OR_BELOW_FROZEN_EARTH_RADIUS")
    delta_r = r2 - r1
    delta_v = v2 - v1
    q_km2 = float(np.dot(delta_r, delta_r))
    return delta_r, delta_v, q_km2, math.sqrt(max(q_km2, 0.0))


class Sgp4PairQuantity:
    """Scalar separation quantity for two SGP4 objects in common TEME/UTC."""

    def __init__(self, first: Satrec, second: Satrec, replay_utc: datetime):
        self.first = first
        self.second = second
        if replay_utc.tzinfo is None:
            raise ParseError("replay time must be timezone-aware")
        self.replay_utc = replay_utc.astimezone(UTC)
        self.base_jd, self.base_fr = datetime_to_jd_fr(self.replay_utc)

    def evaluate(self, time_s: float) -> tuple[float, float]:
        instant = self.replay_utc + timedelta(seconds=float(time_s))
        first = propagate_sgp4(self.first, instant)
        second = propagate_sgp4(self.second, instant)
        delta_r, delta_v, q_km2, _ = scalar_relative_geometry(first, second)
        return q_km2, float(2.0 * np.dot(delta_r, delta_v))

    def evaluate_many(self, times_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        times = np.asarray(times_s, dtype=float)
        jd = np.full(times.shape, self.base_jd, dtype=float)
        fr = self.base_fr + times / 86400.0
        e1, r1, v1 = self.first.sgp4_array(jd, fr)
        e2, r2, v2 = self.second.sgp4_array(jd, fr)
        if np.any(e1) or np.any(e2):
            first_error = int(e1[np.flatnonzero(e1)[0]]) if np.any(e1) else int(e2[np.flatnonzero(e2)[0]])
            raise PropagationError(f"SGP4_ERROR_{first_error}: {SGP4_ERRORS.get(first_error, 'unknown SGP4 error')}")
        values = np.concatenate((r1, r2, v1, v2), axis=1)
        if not np.all(np.isfinite(values)):
            raise PropagationError("NONFINITE_PROPAGATED_STATE")
        if np.any(np.linalg.norm(r1, axis=1) <= EARTH_RADIUS_KM) or np.any(np.linalg.norm(r2, axis=1) <= EARTH_RADIUS_KM):
            raise PropagationError("RADIUS_AT_OR_BELOW_FROZEN_EARTH_RADIUS")
        delta_r = r2 - r1
        delta_v = v2 - v1
        q = np.einsum("ij,ij->i", delta_r, delta_r)
        qdot = 2.0 * np.einsum("ij,ij->i", delta_r, delta_v)
        return q, qdot


def _candidate_brackets(times: np.ndarray, q: np.ndarray, qdot: np.ndarray) -> list[tuple[float, float, int]]:
    brackets: list[tuple[float, float, int]] = []
    for index in range(len(times) - 1):
        if qdot[index] < 0.0 <= qdot[index + 1]:
            brackets.append((float(times[index]), float(times[index + 1]), index))
    for index in range(1, len(times) - 1):
        if q[index] <= q[index - 1] and q[index] <= q[index + 1] and (
            q[index] < q[index - 1] or q[index] < q[index + 1]
        ):
            brackets.append((float(times[index - 1]), float(times[index + 1]), index))
    return brackets


def refine_bracket(
    quantity: ScalarQuantity,
    bracket_s: tuple[float, float],
    time_tolerance_s: float,
    bracket_index: int,
) -> Candidate:
    """Bounded Brent-style scalar refinement of q(t) inside one bracket."""

    lower, upper = bracket_s
    if not (0.0 <= lower < upper <= PRIMARY_HORIZON_S):
        raise OptimizationError("INVALID_BRACKET")
    result = minimize_scalar(
        lambda t: quantity.evaluate(float(t))[0],
        bounds=(lower, upper),
        method="bounded",
        options={"xatol": float(time_tolerance_s), "maxiter": 1000},
    )
    if not result.success or not math.isfinite(float(result.x)) or not math.isfinite(float(result.fun)):
        raise OptimizationError(f"UNRESOLVED_OPTIMIZER: {result.message}")
    time_s = float(result.x)
    q_km2 = max(float(result.fun), 0.0)
    probe = max(time_tolerance_s * 4.0, 1e-6)
    left = max(lower, time_s - probe)
    right = min(upper, time_s + probe)
    q_left = quantity.evaluate(left)[0]
    q_right = quantity.evaluate(right)[0]
    local_slack = 64.0 * np.finfo(float).eps * max(1.0, q_km2, abs(q_left), abs(q_right))
    neighbor_values = ((left, q_left), (right, q_right))
    better_neighbors = [
        (probe_time, probe_q)
        for probe_time, probe_q in neighbor_values
        if probe_q + local_slack < q_km2
    ]
    # A bounded solver may return any point within xatol of the numerical
    # minimizer.  Treat a lower probe inside that documented uncertainty as
    # consistent; reject only a lower value farther away than the requested
    # time tolerance.  Independent truth comparisons below still determine
    # whether the resulting TCA and distance satisfy the G4 acceptance limits.
    if better_neighbors and min(abs(time_s - probe_time) for probe_time, _ in better_neighbors) > time_tolerance_s:
        raise OptimizationError("LOCAL_NEIGHBORHOOD_INCONSISTENT")
    return Candidate(
        time_s=time_s,
        distance_km=math.sqrt(q_km2),
        bracket_s=(lower, upper),
        bracket_index=bracket_index,
        converged=True,
        iterations=int(getattr(result, "nit", 0)),
        function_evaluations=int(getattr(result, "nfev", 0)),
        termination=str(result.message),
    )


def deduplicate_candidates(
    candidates: Sequence[Candidate],
    time_tolerance_s: float,
    distance_tolerance_km: float,
) -> tuple[Candidate, ...]:
    """Merge numerical duplicates only when both time and distance agree."""

    if time_tolerance_s < 0.0 or distance_tolerance_km < 0.0:
        raise VerificationError("NEGATIVE_DEDUPLICATION_TOLERANCE")
    ordered = sorted(candidates, key=lambda c: (c.time_s, c.distance_km, c.bracket_index))
    groups: list[list[Candidate]] = []
    for candidate in ordered:
        matching = next(
            (
                group
                for group in groups
                if abs(candidate.time_s - group[0].time_s) <= time_tolerance_s
                and abs(candidate.distance_km - group[0].distance_km) <= distance_tolerance_km
            ),
            None,
        )
        if matching is None:
            groups.append([candidate])
        else:
            matching.append(candidate)
    retained = [min(group, key=lambda c: (c.time_s, c.distance_km, c.bracket_index)) for group in groups]
    return tuple(sorted(retained, key=lambda c: (c.time_s, c.distance_km, c.bracket_index)))


def enumerate_candidates(
    quantity: ScalarQuantity,
    coarse_step_s: float,
    time_tolerance_s: float,
    *,
    dedup_time_tolerance_s: float | None = None,
    dedup_distance_tolerance_km: float | None = None,
) -> EnumerationResult:
    """Enumerate and refine every detected interior minimum over frozen H=5 d."""

    times = np.arange(0.0, PRIMARY_HORIZON_S, float(coarse_step_s), dtype=float)
    if times[-1] != PRIMARY_HORIZON_S:
        times = np.append(times, PRIMARY_HORIZON_S)
    q, qdot = quantity.evaluate_many(times)
    if not np.all(np.isfinite(q)) or not np.all(np.isfinite(qdot)):
        raise PropagationError("NONFINITE_MINIMIZATION_QUANTITY")
    brackets = _candidate_brackets(times, q, qdot)
    raw: list[Candidate] = []
    unresolved_boundary_count = 0
    boundary_margin = max(float(time_tolerance_s), 16.0 * np.finfo(float).eps * PRIMARY_HORIZON_S)
    for lower, upper, index in brackets:
        # Endpoints are not interior conjunction candidates.  A monotone
        # descent into t_r+H can create a derivative crossing exactly at the
        # upper endpoint; classify it without asking an interior optimizer to
        # approximate a boundary solution.
        if upper == PRIMARY_HORIZON_S and q[-1] <= q[-2] and qdot[-1] <= 0.0:
            unresolved_boundary_count += 1
            continue
        candidate = refine_bracket(quantity, (lower, upper), time_tolerance_s, index)
        if candidate.time_s <= boundary_margin or candidate.time_s >= PRIMARY_HORIZON_S - boundary_margin:
            unresolved_boundary_count += 1
            continue
        raw.append(candidate)
    if (dedup_time_tolerance_s is None) != (dedup_distance_tolerance_km is None):
        raise VerificationError("BOTH_DEDUPLICATION_TOLERANCES_REQUIRED")
    candidates = (
        tuple(raw)
        if dedup_time_tolerance_s is None
        else deduplicate_candidates(raw, dedup_time_tolerance_s, float(dedup_distance_tolerance_km))
    )
    return EnumerationResult(tuple(raw), candidates, unresolved_boundary_count, len(times))


def associate_reference_event(
    frozen_candidates: Sequence[Candidate],
    reference_time_s: float,
    delta_assoc_min: float,
    *,
    reference_distance_km: float | None = None,
) -> AssociationResult:
    """Associate only after candidate generation; never substitutes an unrelated event."""

    tolerance_s = float(delta_assoc_min) * 60.0
    qualifying = [
        candidate
        for candidate in tuple(frozen_candidates)
        if abs(candidate.time_s - reference_time_s) <= tolerance_s
    ]
    if not qualifying:
        return AssociationResult("UNASSOCIATED_REFERENCE_EVENT", None, None, None)
    selected = min(
        qualifying,
        key=lambda c: (
            abs(c.time_s - reference_time_s),
            c.time_s,
            c.distance_km,
            c.bracket_index,
        ),
    )
    miss_error = None if reference_distance_km is None else selected.distance_km - reference_distance_km
    return AssociationResult("VALID_ASSOCIATED", selected, selected.time_s - reference_time_s, miss_error)


def rotate_positions_common(rotation: np.ndarray, first_km: np.ndarray, second_km: np.ndarray) -> tuple[float, float]:
    """Return relative-position norms before/after one common pure rotation."""

    matrix = np.asarray(rotation, dtype=float)
    identity_error = float(np.linalg.norm(matrix.T @ matrix - np.eye(3), ord=2))
    if identity_error > 1e-12 or np.linalg.det(matrix) <= 0.0:
        raise VerificationError("INVALID_ROTATION_MATRIX")
    before = float(np.linalg.norm(np.asarray(second_km) - np.asarray(first_km)))
    after = float(np.linalg.norm(matrix @ np.asarray(second_km) - matrix @ np.asarray(first_km)))
    return before, after


def construct_encounter_plane_vector(*_args, **_kwargs):
    """L30 has no deterministic tolerance; vector construction is unauthorized."""

    raise UnauthorizedPathError("ENCOUNTER-PLANE VECTOR CONSTRUCTION NOT AUTHORIZED IN G4")
