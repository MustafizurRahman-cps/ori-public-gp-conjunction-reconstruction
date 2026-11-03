"""G7-D horizon-parameterized numerical candidate kernel.

Post-confirmatory sensitivity support only.

This module does not read G6 event/TLE inputs, propagate real objects,
associate reference events, fit CAL0, or change the frozen G6 H=5 d
implementation.

It exposes the already-frozen numerical algorithm with candidate-search
horizon H made explicit for the prespecified G7 sensitivity set {3,5,7} d.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar


ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(
    0,
    str(ROOT / "scripts" / "g5"),
)

import select_lstar_controlflow_amended as frozen  # noqa: E402


g4 = frozen.g4


G4_NUMERICS = (
    ROOT
    / "scripts/g4/numerics.py"
)

G5_ENUMERATOR = (
    ROOT
    / "scripts/g5/select_lstar_controlflow_amended.py"
)

G6_GENERATOR = (
    ROOT
    / "scripts/g6/generate_g6_preoutcome_candidates.py"
)


EXPECTED_G4_SHA256 = (
    "dc8de410b89e05ffd829563b4996b06703c31236e9a10fb56bd7f4e690048406"
)

EXPECTED_G5_SHA256 = (
    "553578f4e90b044c6a783209a756ea3accc009ecbb7a09853e8cae46503f33c2"
)

EXPECTED_G6_SHA256 = (
    "bce2a4b5e4dab8d0c4e461e2acb4dbac8afe22d3ac412922ba3670a832adcecb"
)


VALID_HORIZONS_DAYS = (
    3,
    5,
    7,
)

SECONDS_PER_DAY = 86400.0


COARSE_STEP_S = (
    frozen.COARSE_STEP_S
)

REFINEMENT_TOLERANCE_S = (
    frozen.REFINEMENT_TOLERANCE_S
)

DEDUP_TIME_TOLERANCE_S = (
    frozen.DEDUP_TIME_TOLERANCE_S
)

DEDUP_DISTANCE_TOLERANCE_KM = (
    frozen.DEDUP_DISTANCE_TOLERANCE_KM
)

RECOVERY_GRID_STEP_S = (
    frozen.RECOVERY_GRID_STEP_S
)

RECOVERY_WINDOW_EXPANSION_S = (
    frozen.RECOVERY_WINDOW_EXPANSION_S
)

RECOVERY_TCA_TOLERANCE_S = (
    frozen.RECOVERY_TCA_TOLERANCE_S
)

RECOVERY_DISTANCE_TOLERANCE_KM = (
    frozen.RECOVERY_DISTANCE_TOLERANCE_KM
)


OFFLINE_VALIDATION = (
    ROOT
    / "results/g7/G7D_Horizon_Numerics_Offline_Validation.json"
)


class G7DHorizonError(RuntimeError):
    pass


def sha256(
    path: Path,
) -> str:

    digest = hashlib.sha256()

    with path.open(
        "rb"
    ) as stream:

        for block in iter(
            lambda:
                stream.read(
                    1024 * 1024
                ),
            b"",
        ):
            digest.update(
                block
            )

    return digest.hexdigest()


def validate_frozen_dependencies(
) -> dict[str, str]:

    expected = {
        G4_NUMERICS:
            EXPECTED_G4_SHA256,

        G5_ENUMERATOR:
            EXPECTED_G5_SHA256,

        G6_GENERATOR:
            EXPECTED_G6_SHA256,
    }

    observed = {}

    for path, expected_sha in (
        expected.items()
    ):

        actual = sha256(
            path
        )

        if actual != expected_sha:

            raise G7DHorizonError(
                "frozen dependency SHA-256 mismatch for "
                + path.relative_to(
                    ROOT
                ).as_posix()
                + ": "
                + actual
            )

        observed[
            path.relative_to(
                ROOT
            ).as_posix()
        ] = actual

    return observed


def horizon_seconds(
    horizon_days: int,
) -> float:

    if (
        isinstance(
            horizon_days,
            bool,
        )
        or horizon_days
        not in VALID_HORIZONS_DAYS
    ):

        raise G7DHorizonError(
            "horizon_days must be one of "
            + str(
                VALID_HORIZONS_DAYS
            )
        )

    return (
        float(
            horizon_days
        )
        * SECONDS_PER_DAY
    )


def refine_bracket_horizon(
    quantity: g4.ScalarQuantity,
    bracket_s:
        tuple[
            float,
            float,
        ],
    time_tolerance_s: float,
    bracket_index: int,
    horizon_s: float,
) -> g4.Candidate:
    """
    Frozen G4 bounded bracket refinement with only H made explicit.
    """

    lower, upper = (
        bracket_s
    )

    if not (
        0.0
        <= lower
        < upper
        <= horizon_s
    ):
        raise g4.OptimizationError(
            "INVALID_BRACKET"
        )

    result = minimize_scalar(
        lambda t:
            quantity.evaluate(
                float(t)
            )[0],

        bounds=(
            lower,
            upper,
        ),

        method="bounded",

        options={
            "xatol":
                float(
                    time_tolerance_s
                ),

            "maxiter":
                1000,
        },
    )

    if (
        not result.success
        or not math.isfinite(
            float(
                result.x
            )
        )
        or not math.isfinite(
            float(
                result.fun
            )
        )
    ):

        raise g4.OptimizationError(
            "UNRESOLVED_OPTIMIZER: "
            + str(
                result.message
            )
        )

    time_s = float(
        result.x
    )

    q_km2 = max(
        float(
            result.fun
        ),
        0.0,
    )

    probe = max(
        time_tolerance_s
        * 4.0,
        1.0e-6,
    )

    left = max(
        lower,
        time_s - probe,
    )

    right = min(
        upper,
        time_s + probe,
    )

    q_left = (
        quantity.evaluate(
            left
        )[0]
    )

    q_right = (
        quantity.evaluate(
            right
        )[0]
    )

    local_slack = (
        64.0
        * np.finfo(
            float
        ).eps
        * max(
            1.0,
            q_km2,
            abs(
                q_left
            ),
            abs(
                q_right
            ),
        )
    )

    neighbor_values = (
        (
            left,
            q_left,
        ),
        (
            right,
            q_right,
        ),
    )

    better_neighbors = [
        (
            probe_time,
            probe_q,
        )
        for (
            probe_time,
            probe_q,
        )
        in neighbor_values
        if (
            probe_q
            + local_slack
            < q_km2
        )
    ]

    if (
        better_neighbors
        and min(
            abs(
                time_s
                - probe_time
            )
            for (
                probe_time,
                _
            )
            in better_neighbors
        )
        > time_tolerance_s
    ):

        raise g4.OptimizationError(
            "LOCAL_NEIGHBORHOOD_INCONSISTENT"
        )

    return g4.Candidate(
        time_s=time_s,

        distance_km=
            math.sqrt(
                q_km2
            ),

        bracket_s=(
            lower,
            upper,
        ),

        bracket_index=
            bracket_index,

        converged=True,

        iterations=int(
            getattr(
                result,
                "nit",
                0,
            )
        ),

        function_evaluations=int(
            getattr(
                result,
                "nfev",
                0,
            )
        ),

        termination=
            str(
                result.message
            ),
    )


def independent_recovery_reference_horizon(
    quantity: g4.ScalarQuantity,
    lower: float,
    upper: float,
    horizon_s: float,
) -> tuple[
    float,
    float,
] | None:
    """
    Frozen objective-only recovery-reference construction with explicit H.
    """

    start = max(
        0.0,
        lower
        - RECOVERY_WINDOW_EXPANSION_S,
    )

    stop = min(
        horizon_s,
        upper
        + RECOVERY_WINDOW_EXPANSION_S,
    )

    times = np.arange(
        start,

        stop
        + RECOVERY_GRID_STEP_S
        / 2.0,

        RECOVERY_GRID_STEP_S,

        dtype=float,
    )

    q, _ = (
        quantity.evaluate_many(
            times
        )
    )

    if not np.all(
        np.isfinite(
            q
        )
    ):
        raise g4.PropagationError(
            "NONFINITE_RECOVERY_REFERENCE"
        )

    minima = (
        np.flatnonzero(
            (
                q[1:-1]
                < q[:-2]
            )
            & (
                q[1:-1]
                <= q[2:]
            )
        )
        + 1
    )

    if len(
        minima
    ) != 1:
        return None

    index = int(
        minima[0]
    )

    if (
        index == 0
        or index
        == len(times) - 1
    ):
        return None

    y0 = float(
        q[
            index - 1
        ]
    )

    y1 = float(
        q[
            index
        ]
    )

    y2 = float(
        q[
            index + 1
        ]
    )

    denominator = (
        y0
        - 2.0 * y1
        + y2
    )

    offset = (
        0.0
        if denominator == 0.0
        else (
            0.5
            * (
                y0 - y2
            )
            / denominator
            * RECOVERY_GRID_STEP_S
        )
    )

    time_s = float(
        times[
            index
        ]
        + offset
    )

    if not (
        0.0
        < time_s
        < horizon_s
    ):
        return None

    q_value = float(
        quantity.evaluate(
            time_s
        )[0]
    )

    if not math.isfinite(
        q_value
    ):
        raise g4.PropagationError(
            "NONFINITE_RECOVERY_REFERENCE"
        )

    return (
        time_s,
        math.sqrt(
            max(
                q_value,
                0.0,
            )
        ),
    )


def enumerate_candidates_redundancy_safe_horizon(
    quantity: g4.ScalarQuantity,
    horizon_days: int,
) -> frozen.RedundancySafeEnumerationResult:
    """
    Frozen redundancy-safe enumerator with explicit H in {3,5,7} d.
    """

    horizon_s = (
        horizon_seconds(
            horizon_days
        )
    )

    times = np.arange(
        0.0,
        horizon_s,
        COARSE_STEP_S,
        dtype=float,
    )

    if (
        times[-1]
        != horizon_s
    ):
        times = np.append(
            times,
            horizon_s,
        )

    q, qdot = (
        quantity.evaluate_many(
            times
        )
    )

    if (
        not np.all(
            np.isfinite(
                q
            )
        )
        or not np.all(
            np.isfinite(
                qdot
            )
        )
    ):

        raise g4.PropagationError(
            "NONFINITE_MINIMIZATION_QUANTITY"
        )

    brackets = (
        g4._candidate_brackets(
            times,
            q,
            qdot,
        )
    )

    raw = []

    rejected = []

    unresolved_boundary_count = 0

    boundary_margin = max(
        REFINEMENT_TOLERANCE_S,

        16.0
        * np.finfo(
            float
        ).eps
        * horizon_s,
    )

    for (
        lower,
        upper,
        index,
    ) in brackets:

        if (
            upper
            == horizon_s
            and q[-1]
            <= q[-2]
            and qdot[-1]
            <= 0.0
        ):

            unresolved_boundary_count += 1

            continue

        try:

            candidate = (
                refine_bracket_horizon(
                    quantity,

                    (
                        lower,
                        upper,
                    ),

                    REFINEMENT_TOLERANCE_S,

                    index,

                    horizon_s,
                )
            )

        except g4.OptimizationError as exc:

            if (
                str(exc)
                == "LOCAL_NEIGHBORHOOD_INCONSISTENT"
            ):

                rejected.append(
                    (
                        lower,
                        upper,
                        index,
                    )
                )

                continue

            raise

        if (
            candidate.time_s
            <= boundary_margin
            or candidate.time_s
            >= horizon_s
            - boundary_margin
        ):

            unresolved_boundary_count += 1

            continue

        raw.append(
            candidate
        )

    candidates = (
        g4.deduplicate_candidates(
            raw,

            DEDUP_TIME_TOLERANCE_S,

            DEDUP_DISTANCE_TOLERANCE_KM,
        )
    )

    recovered = 0

    for (
        lower,
        upper,
        index,
    ) in rejected:

        reference = (
            independent_recovery_reference_horizon(
                quantity,

                lower,

                upper,

                horizon_s,
            )
        )

        if reference is None:

            raise (
                frozen
                .UnrecoveredRefinementFailure(
                    "UNRECOVERED_REFINEMENT_FAILURE: "
                    + "bracket_index="
                    + str(index)
                    + "; independent objective minimum "
                    + "absent or ambiguous"
                )
            )

        (
            reference_time_s,
            reference_distance_km,
        ) = reference

        if not any(
            (
                abs(
                    candidate.time_s
                    - reference_time_s
                )
                <= RECOVERY_TCA_TOLERANCE_S
            )
            and (
                abs(
                    candidate.distance_km
                    - reference_distance_km
                )
                <= RECOVERY_DISTANCE_TOLERANCE_KM
            )
            for candidate
            in candidates
        ):

            raise (
                frozen
                .UnrecoveredRefinementFailure(
                    "UNRECOVERED_REFINEMENT_FAILURE: "
                    + "bracket_index="
                    + str(index)
                    + "; no final candidate recovers "
                    + "independent reference"
                )
            )

        recovered += 1

    return (
        frozen
        .RedundancySafeEnumerationResult(
            raw_candidates=
                tuple(
                    raw
                ),

            candidates=
                tuple(
                    candidates
                ),

            unresolved_boundary_count=
                unresolved_boundary_count,

            sampled_count=
                len(
                    times
                ),

            rejected_local_inconsistency_count=
                len(
                    rejected
                ),

            recovered_local_inconsistency_count=
                recovered,
        )
    )


class QuadraticQuantity:

    def __init__(
        self,
        target_time_s: float,
        target_distance_km: float,
        curvature: float = 1.0e-6,
    ):

        self.target_time_s = float(
            target_time_s
        )

        self.target_distance_km = float(
            target_distance_km
        )

        self.curvature = float(
            curvature
        )


    def evaluate(
        self,
        time_s: float,
    ) -> tuple[
        float,
        float,
    ]:

        dt = (
            float(
                time_s
            )
            - self.target_time_s
        )

        q = (
            self.target_distance_km**2
            + self.curvature
            * dt**2
        )

        qdot = (
            2.0
            * self.curvature
            * dt
        )

        return (
            q,
            qdot,
        )


    def evaluate_many(
        self,
        times_s: np.ndarray,
    ) -> tuple[
        np.ndarray,
        np.ndarray,
    ]:

        dt = (
            np.asarray(
                times_s,
                dtype=float,
            )
            - self.target_time_s
        )

        q = (
            self.target_distance_km**2
            + self.curvature
            * dt**2
        )

        qdot = (
            2.0
            * self.curvature
            * dt
        )

        return (
            q,
            qdot,
        )


class DoubleWellQuantity:

    def __init__(
        self,
        first_time_s: float,
        second_time_s: float,
        minimum_distance_km:
            float = 2.0,
        scale:
            float = 1.0e-18,
    ):

        self.first_time_s = float(
            first_time_s
        )

        self.second_time_s = float(
            second_time_s
        )

        self.minimum_distance_km = float(
            minimum_distance_km
        )

        self.scale = float(
            scale
        )


    def evaluate(
        self,
        time_s: float,
    ) -> tuple[
        float,
        float,
    ]:

        t = float(
            time_s
        )

        a = (
            t
            - self.first_time_s
        )

        b = (
            t
            - self.second_time_s
        )

        q = (
            self.minimum_distance_km**2
            + self.scale
            * a**2
            * b**2
        )

        qdot = (
            2.0
            * self.scale
            * a
            * b
            * (
                2.0 * t
                - self.first_time_s
                - self.second_time_s
            )
        )

        return (
            q,
            qdot,
        )


    def evaluate_many(
        self,
        times_s: np.ndarray,
    ) -> tuple[
        np.ndarray,
        np.ndarray,
    ]:

        t = np.asarray(
            times_s,
            dtype=float,
        )

        a = (
            t
            - self.first_time_s
        )

        b = (
            t
            - self.second_time_s
        )

        q = (
            self.minimum_distance_km**2
            + self.scale
            * a**2
            * b**2
        )

        qdot = (
            2.0
            * self.scale
            * a
            * b
            * (
                2.0 * t
                - self.first_time_s
                - self.second_time_s
            )
        )

        return (
            q,
            qdot,
        )


def candidate_sequence_match(
    left,
    right,
) -> tuple[
    bool,
    float | None,
    float | None,
]:

    if len(left) != len(
        right
    ):

        return (
            False,
            None,
            None,
        )

    time_differences = [
        abs(
            a.time_s
            - b.time_s
        )
        for a, b
        in zip(
            left,
            right,
        )
    ]

    distance_differences = [
        abs(
            a.distance_km
            - b.distance_km
        )
        for a, b
        in zip(
            left,
            right,
        )
    ]

    maximum_time_difference_s = max(
        time_differences,
        default=0.0,
    )

    maximum_distance_difference_km = max(
        distance_differences,
        default=0.0,
    )

    metadata_match = all(
        (
            a.bracket_s
            == b.bracket_s
        )
        and (
            a.bracket_index
            == b.bracket_index
        )
        and (
            a.converged
            == b.converged
        )
        for a, b
        in zip(
            left,
            right,
        )
    )

    return (
        bool(
            metadata_match
            and maximum_time_difference_s
            <= 1.0e-12
            and maximum_distance_difference_km
            <= 1.0e-12
        ),

        maximum_time_difference_s,

        maximum_distance_difference_km,
    )


def compare_enumerations(
    left:
        frozen.RedundancySafeEnumerationResult,
    right:
        frozen.RedundancySafeEnumerationResult,
) -> dict[
    str,
    object,
]:

    scalar_fields = (
        "unresolved_boundary_count",
        "sampled_count",
        "rejected_local_inconsistency_count",
        "recovered_local_inconsistency_count",
    )

    scalar_match = all(
        getattr(
            left,
            field,
        )
        == getattr(
            right,
            field,
        )
        for field
        in scalar_fields
    )

    (
        raw_match,
        raw_max_time,
        raw_max_distance,
    ) = candidate_sequence_match(
        left.raw_candidates,
        right.raw_candidates,
    )

    (
        candidate_match,
        max_time,
        max_distance,
    ) = candidate_sequence_match(
        left.candidates,
        right.candidates,
    )

    return {
        "scalar_diagnostics_match":
            scalar_match,

        "raw_candidate_count_left":
            len(
                left.raw_candidates
            ),

        "raw_candidate_count_right":
            len(
                right.raw_candidates
            ),

        "raw_candidate_values_match":
            raw_match,

        "raw_maximum_time_difference_s":
            raw_max_time,

        "raw_maximum_distance_difference_km":
            raw_max_distance,

        "candidate_count_left":
            len(
                left.candidates
            ),

        "candidate_count_right":
            len(
                right.candidates
            ),

        "candidate_values_match":
            candidate_match,

        "maximum_time_difference_s":
            max_time,

        "maximum_distance_difference_km":
            max_distance,

        "pass":
            bool(
                scalar_match
                and raw_match
                and candidate_match
            ),
    }


def offline_validate(
) -> dict[
    str,
    object,
]:

    dependencies = (
        validate_frozen_dependencies()
    )

    five_day_cases = {
        "single_interior":
            QuadraticQuantity(
                123450.0,
                5.0,
            ),

        "near_upper_interior":
            QuadraticQuantity(
                5.0
                * SECONDS_PER_DAY
                - 5.25,

                3.0,
            ),

        "two_interior_minima":
            DoubleWellQuantity(
                1.0
                * SECONDS_PER_DAY,

                4.0
                * SECONDS_PER_DAY,

                2.0,
            ),
    }

    equivalence = {}

    for (
        name,
        quantity,
    ) in (
        five_day_cases.items()
    ):

        frozen_result = (
            frozen
            .enumerate_candidates_redundancy_safe(
                quantity
            )
        )

        g7d_result = (
            enumerate_candidates_redundancy_safe_horizon(
                quantity,
                5,
            )
        )

        comparison = (
            compare_enumerations(
                frozen_result,
                g7d_result,
            )
        )

        if not comparison[
            "pass"
        ]:

            raise G7DHorizonError(
                "H=5 numerical equivalence failed for "
                + name
            )

        equivalence[
            name
        ] = comparison


    h3_boundary = (
        enumerate_candidates_redundancy_safe_horizon(
            QuadraticQuantity(
                3.0
                * SECONDS_PER_DAY,

                4.0,
            ),

            3,
        )
    )

    if h3_boundary.candidates:

        raise G7DHorizonError(
            "H=3 endpoint minimum was incorrectly retained"
        )


    h3_inside = (
        enumerate_candidates_redundancy_safe_horizon(
            QuadraticQuantity(
                2.0
                * SECONDS_PER_DAY,

                4.0,
            ),

            3,
        )
    )

    if len(
        h3_inside.candidates
    ) != 1:

        raise G7DHorizonError(
            "H=3 interior synthetic minimum was not retained"
        )


    beyond_five_quantity = (
        QuadraticQuantity(
            6.0
            * SECONDS_PER_DAY,

            7.0,
        )
    )

    h5_beyond = (
        enumerate_candidates_redundancy_safe_horizon(
            beyond_five_quantity,
            5,
        )
    )

    h7_beyond = (
        enumerate_candidates_redundancy_safe_horizon(
            beyond_five_quantity,
            7,
        )
    )

    if h5_beyond.candidates:

        raise G7DHorizonError(
            "synthetic >5 d minimum appeared inside H=5"
        )

    if len(
        h7_beyond.candidates
    ) != 1:

        raise G7DHorizonError(
            "synthetic 6 d minimum was not recovered inside H=7"
        )

    if (
        abs(
            h7_beyond
            .candidates[0]
            .time_s
            - 6.0
            * SECONDS_PER_DAY
        )
        > REFINEMENT_TOLERANCE_S
    ):

        raise G7DHorizonError(
            "H=7 synthetic 6 d minimum time error exceeds tolerance"
        )


    payload = {
        "status":
            "G7D_HORIZON_NUMERICS_OFFLINE_VALIDATION_PASS",

        "frozen_dependency_hashes":
            dependencies,

        "valid_horizons_days":
            list(
                VALID_HORIZONS_DAYS
            ),

        "five_day_exact_algorithm_equivalence":
            equivalence,

        "h3_endpoint_candidate_count":
            len(
                h3_boundary.candidates
            ),

        "h3_endpoint_unresolved_boundary_count":
            h3_boundary
            .unresolved_boundary_count,

        "h3_interior_candidate_count":
            len(
                h3_inside.candidates
            ),

        "h5_six_day_candidate_count":
            len(
                h5_beyond.candidates
            ),

        "h7_six_day_candidate_count":
            len(
                h7_beyond.candidates
            ),

        "h7_six_day_candidate_time_s":
            h7_beyond
            .candidates[0]
            .time_s,

        "orbital_propagation_performed":
            False,

        "real_G6_events_processed":
            0,

        "reference_association_performed":
            False,

        "GP_HISTORY_accessed":
            False,

        "TraCSS_reference_accessed":
            False,

        "CAL0_refit_performed":
            False,

        "method_retuning_performed":
            False,

        "network_requests":
            0,

        "next_safe_action":
            "INDEPENDENT_AUDIT_AND_FREEZE_G7D_HORIZON_NUMERICS",
    }


    OFFLINE_VALIDATION.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OFFLINE_VALIDATION.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as handle:

        json.dump(
            payload,
            handle,
            indent=2,
            sort_keys=True,
        )

        handle.write(
            "\n"
        )


    print(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
    )

    return payload


def main(
) -> int:

    parser = (
        argparse
        .ArgumentParser()
    )

    parser.add_argument(
        "--offline-validate",
        action="store_true",
        required=True,
    )

    args = (
        parser.parse_args()
    )

    if args.offline_validate:

        offline_validate()

    return 0


if __name__ == "__main__":

    raise SystemExit(
        main()
    )