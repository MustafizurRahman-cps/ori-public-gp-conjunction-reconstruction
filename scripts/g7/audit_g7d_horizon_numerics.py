"""Independent offline audit of the G7-D horizon numerical kernel."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]

IMPL = (
    ROOT
    / "scripts/g7/g7d_horizon_numerics.py"
)

VALIDATION = (
    ROOT
    / "results/g7/G7D_Horizon_Numerics_Offline_Validation.json"
)

AUDIT_OUT = (
    ROOT
    / "results/g7/G7D_Horizon_Numerics_Independent_Audit.json"
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


class AuditError(RuntimeError):
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


def load_impl():

    spec = (
        importlib.util
        .spec_from_file_location(
            "g7d_horizon_numerics_audited",
            IMPL,
        )
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise AuditError(
            "cannot load G7-D implementation"
        )

    module = (
        importlib.util
        .module_from_spec(
            spec
        )
    )

    spec.loader.exec_module(
        module
    )

    return module


class QuadraticQuantity:

    def __init__(
        self,
        target_time_s: float,
        target_distance_km: float,
        curvature:
            float = 1.0e-6,
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
    ):

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
    ):

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
    ):

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
    ):

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
):

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


def compare(
    left,
    right,
):

    scalar_match = (
        left.unresolved_boundary_count
        == right.unresolved_boundary_count
        and left.sampled_count
        == right.sampled_count
        and left.rejected_local_inconsistency_count
        == right.rejected_local_inconsistency_count
        and left.recovered_local_inconsistency_count
        == right.recovered_local_inconsistency_count
    )

    (
        raw_match,
        raw_dt,
        raw_dd,
    ) = candidate_sequence_match(
        left.raw_candidates,
        right.raw_candidates,
    )

    (
        candidate_match,
        dt,
        dd,
    ) = candidate_sequence_match(
        left.candidates,
        right.candidates,
    )

    return (
        bool(
            scalar_match
            and raw_match
            and candidate_match
        ),

        raw_dt,
        raw_dd,
        dt,
        dd,
    )


def main() -> int:

    if not IMPL.is_file():

        raise AuditError(
            "G7-D implementation is missing"
        )

    if not VALIDATION.is_file():

        raise AuditError(
            "G7-D offline validation artifact is missing"
        )


    source = (
        IMPL.read_text(
            encoding="utf-8"
        )
    )


    forbidden_source_tokens = (
        "g4.PRIMARY_HORIZON_S",
        "Satrec.twoline2rv",
        "Sgp4PairQuantity(",
        "requests.",
        "urllib.request",
        "socket.",
        "sqlite3",
        "run_generation_to_work_db(",
        "associate_reference_event(",
    )


    present_forbidden = [
        token
        for token
        in forbidden_source_tokens
        if token
        in source
    ]


    if present_forbidden:

        raise AuditError(
            "forbidden implementation coupling detected: "
            + ", ".join(
                present_forbidden
            )
        )


    module = (
        load_impl()
    )


    if (
        tuple(
            module
            .VALID_HORIZONS_DAYS
        )
        != (
            3,
            5,
            7,
        )
    ):

        raise AuditError(
            "G7-D horizon set changed"
        )


    dependency_hashes = (
        module
        .validate_frozen_dependencies()
    )


    if (
        dependency_hashes[
            "scripts/g4/numerics.py"
        ]
        != EXPECTED_G4_SHA256
    ):

        raise AuditError(
            "G4 hash verification failed"
        )


    if (
        dependency_hashes[
            "scripts/g5/select_lstar_controlflow_amended.py"
        ]
        != EXPECTED_G5_SHA256
    ):

        raise AuditError(
            "G5 hash verification failed"
        )


    if (
        dependency_hashes[
            "scripts/g6/generate_g6_preoutcome_candidates.py"
        ]
        != EXPECTED_G6_SHA256
    ):

        raise AuditError(
            "G6 hash verification failed"
        )


    validation = json.loads(
        VALIDATION.read_text(
            encoding="utf-8"
        )
    )


    if (
        validation.get(
            "status"
        )
        !=
        "G7D_HORIZON_NUMERICS_OFFLINE_VALIDATION_PASS"
    ):

        raise AuditError(
            "offline validation status is not PASS"
        )


    equivalence_cases = {
        "single_interior":
            QuadraticQuantity(
                123450.0,
                5.0,
            ),

        "near_upper_interior":
            QuadraticQuantity(
                5.0
                * module.SECONDS_PER_DAY
                - 5.25,

                3.0,
            ),

        "two_interior_minima":
            DoubleWellQuantity(
                1.0
                * module.SECONDS_PER_DAY,

                4.0
                * module.SECONDS_PER_DAY,

                2.0,
            ),
    }


    equivalence_results = {}


    for (
        name,
        quantity,
    ) in (
        equivalence_cases.items()
    ):

        frozen_result = (
            module
            .frozen
            .enumerate_candidates_redundancy_safe(
                quantity
            )
        )

        audited_result = (
            module
            .enumerate_candidates_redundancy_safe_horizon(
                quantity,
                5,
            )
        )

        (
            passed,
            raw_dt,
            raw_dd,
            dt,
            dd,
        ) = compare(
            frozen_result,
            audited_result,
        )


        if not passed:

            raise AuditError(
                "independent H=5 equivalence failed for "
                + name
            )


        equivalence_results[
            name
        ] = {
            "pass":
                True,

            "raw_maximum_time_difference_s":
                raw_dt,

            "raw_maximum_distance_difference_km":
                raw_dd,

            "maximum_time_difference_s":
                dt,

            "maximum_distance_difference_km":
                dd,

            "candidate_count":
                len(
                    audited_result.candidates
                ),
        }


    h3_boundary = (
        module
        .enumerate_candidates_redundancy_safe_horizon(
            QuadraticQuantity(
                3.0
                * module.SECONDS_PER_DAY,

                4.0,
            ),

            3,
        )
    )


    if h3_boundary.candidates:

        raise AuditError(
            "H=3 endpoint was retained"
        )


    h7_extension = (
        module
        .enumerate_candidates_redundancy_safe_horizon(
            QuadraticQuantity(
                6.0
                * module.SECONDS_PER_DAY,

                7.0,
            ),

            7,
        )
    )


    if len(
        h7_extension.candidates
    ) != 1:

        raise AuditError(
            "H=7 extension case failed"
        )


    if (
        abs(
            h7_extension
            .candidates[0]
            .time_s
            - 6.0
            * module.SECONDS_PER_DAY
        )
        > module.REFINEMENT_TOLERANCE_S
    ):

        raise AuditError(
            "H=7 extension TCA error too large"
        )


    invalid_horizon_rejected = False


    try:

        module.horizon_seconds(
            4
        )

    except module.G7DHorizonError:

        invalid_horizon_rejected = True


    if not invalid_horizon_rejected:

        raise AuditError(
            "non-prespecified H=4 was accepted"
        )


    payload = {
        "status":
            "G7D_HORIZON_NUMERICS_INDEPENDENT_AUDIT_PASS",

        "implementation_sha256":
            sha256(
                IMPL
            ),

        "offline_validation_sha256":
            sha256(
                VALIDATION
            ),

        "frozen_dependency_hashes":
            dependency_hashes,

        "five_day_algorithm_equivalence":
            equivalence_results,

        "prespecified_horizons_exact":
            list(
                module
                .VALID_HORIZONS_DAYS
            )
            == [
                3,
                5,
                7,
            ],

        "nonprespecified_horizon_rejected":
            invalid_horizon_rejected,

        "h3_endpoint_candidate_count":
            len(
                h3_boundary.candidates
            ),

        "h3_endpoint_unresolved_boundary_count":
            h3_boundary
            .unresolved_boundary_count,

        "h7_six_day_candidate_count":
            len(
                h7_extension.candidates
            ),

        "orbital_propagation_performed":
            False,

        "real_G6_events_processed":
            0,

        "candidate_generation_on_G6_performed":
            False,

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
            "FREEZE_G7D_HORIZON_NUMERICS_WITH_DECISION_RECORD",
    }


    AUDIT_OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )


    with AUDIT_OUT.open(
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


    return 0


if __name__ == "__main__":

    raise SystemExit(
        main()
    )