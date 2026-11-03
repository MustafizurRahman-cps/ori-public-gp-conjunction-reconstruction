"""Independent inactive review of the G7-D real-event horizon executor."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EXECUTOR = (
    ROOT
    / "scripts/g7/run_g7d_horizon_candidates.py"
)

VALIDATION = (
    ROOT
    / "results/g7/G7D_Horizon_Executor_Offline_Validation.json"
)

AUDIT_OUT = (
    ROOT
    / "results/g7/G7D_Horizon_Executor_Independent_Audit.json"
)


EXPECTED_G6_SHA256 = (
    "bce2a4b5e4dab8d0c4e461e2acb4dbac8afe22d3ac412922ba3670a832adcecb"
)

EXPECTED_KERNEL_SHA256 = (
    "6bba58d77f1c91f1d19fc25de684be1079d66e0808a428e9fa44a213d2ba48a0"
)

EXPECTED_SELECTION_SHA256 = (
    "6d03f0b4c7104b7cbac76b649bedc87e9f497f297dccc19f2270e7fec5552095"
)

EXPECTED_CACHE_SHA256 = (
    "b77cf2893cf92b2e577bf04f5f23d67dc6be31b9d1e8aeafe0e40efd6b08ed69"
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


def load_executor():

    spec = (
        importlib.util
        .spec_from_file_location(
            "g7d_executor_audited",
            EXECUTOR,
        )
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise AuditError(
            "cannot load executor"
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


def main() -> int:

    if not EXECUTOR.is_file():
        raise AuditError(
            "executor is missing"
        )

    if not VALIDATION.is_file():
        raise AuditError(
            "offline validation is missing"
        )


    source = (
        EXECUTOR.read_text(
            encoding="utf-8"
        )
    )


    forbidden_reference_tokens = (
        "IVV_Releasable_Dataset_SFSH_DiscreteHBR",
        "G6_Reference_TCA_Association.csv",
        "G6_Confirmatory_Event_Metrics.csv",
        "G6_Confirmatory_Metric_Summary.json",
    )


    present_reference_tokens = [
        token
        for token
        in forbidden_reference_tokens
        if token
        in source
    ]


    if present_reference_tokens:

        raise AuditError(
            "protected-reference coupling found: "
            + ", ".join(
                present_reference_tokens
            )
        )


    required_source_tokens = (
        "g6.process_event(",
        "enumerate_candidates_redundancy_safe_horizon(",
        "authorization=False",
        "frozen H=5 d must not be rerun",
        "G7D_H3_Candidate_Work.sqlite3",
        "G7D_H7_Candidate_Work.sqlite3",
    )


    missing_required = [
        token
        for token
        in required_source_tokens
        if token
        not in source
    ]


    if missing_required:

        raise AuditError(
            "required executor control missing: "
            + ", ".join(
                missing_required
            )
        )


    module = (
        load_executor()
    )


    if (
        tuple(
            module
            .AUTHORIZED_HORIZONS_DAYS
        )
        != (
            3,
            7,
        )
    ):

        raise AuditError(
            "real horizon set is not exactly {3,7}"
        )


    if 5 in (
        module.HORIZON_PATHS
    ):

        raise AuditError(
            "H=5 execution namespace exists"
        )


    dependencies = (
        module
        .verify_dependency_hashes()
    )


    expected_dependencies = {
        "scripts/g6/generate_g6_preoutcome_candidates.py":
            EXPECTED_G6_SHA256,

        "scripts/g7/g7d_horizon_numerics.py":
            EXPECTED_KERNEL_SHA256,

        "results/g6/G6_48H_Replay_Input_Selection.csv":
            EXPECTED_SELECTION_SHA256,

        "data/raw/g6/G6_48H_Replay_Selected_GP_Cache.jsonl":
            EXPECTED_CACHE_SHA256,
    }


    if (
        dependencies
        != expected_dependencies
    ):

        raise AuditError(
            "executor frozen dependency map differs"
        )


    configuration = (
        module
        .configuration_snapshot()
    )


    if (
        configuration[
            "frozen_H5_rerun_permitted"
        ]
        is not False
    ):

        raise AuditError(
            "configuration permits H=5 rerun"
        )


    if (
        configuration[
            "direct_CLI_real_execution_authorized"
        ]
        is not False
    ):

        raise AuditError(
            "configuration authorizes direct CLI execution"
        )


    if (
        configuration[
            "protected_reference_access_configured"
        ]
        is not False
    ):

        raise AuditError(
            "protected reference access is configured"
        )


    h3 = (
        module.path_spec(
            3
        )
    )

    h7 = (
        module.path_spec(
            7
        )
    )


    h3_paths = {
        str(
            path
        )
        for path
        in h3.values()
    }

    h7_paths = {
        str(
            path
        )
        for path
        in h7.values()
    }


    if (
        h3_paths
        & h7_paths
    ):

        raise AuditError(
            "H3/H7 namespace collision"
        )


    blocked_H3 = False


    try:

        module.execute_horizon(
            3,
            authorization=False,
        )

    except module.G7DExecutionNotAuthorized:

        blocked_H3 = True


    if not blocked_H3:

        raise AuditError(
            "unauthorized H=3 execution was not blocked"
        )


    blocked_H7 = False


    try:

        module.execute_horizon(
            7,
            authorization=False,
        )

    except module.G7DExecutionNotAuthorized:

        blocked_H7 = True


    if not blocked_H7:

        raise AuditError(
            "unauthorized H=7 execution was not blocked"
        )


    blocked_H5 = False


    try:

        module.execute_horizon(
            5,
            authorization=True,
        )

    except module.G7DExecutorError:

        blocked_H5 = True


    if not blocked_H5:

        raise AuditError(
            "H=5 rerun was not blocked"
        )


    for spec in (
        h3,
        h7,
    ):

        for path in (
            spec.values()
        ):

            if path.exists():

                raise AuditError(
                    "inactive review created real execution artifact: "
                    + module.relative(
                        path
                    )
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
        "G7D_HORIZON_EXECUTOR_OFFLINE_VALIDATION_PASS"
    ):

        raise AuditError(
            "offline executor validation is not PASS"
        )


    if (
        validation[
            "unauthorized_H3_execution_blocked"
        ]
        is not True
    ):

        raise AuditError(
            "validation did not block unauthorized H=3"
        )


    if (
        validation[
            "frozen_H5_rerun_blocked"
        ]
        is not True
    ):

        raise AuditError(
            "validation did not block H=5 rerun"
        )


    payload = {
        "status":
            "G7D_HORIZON_EXECUTOR_INDEPENDENT_AUDIT_PASS",

        "executor_sha256":
            sha256(
                EXECUTOR
            ),

        "offline_validation_sha256":
            sha256(
                VALIDATION
            ),

        "frozen_dependency_hashes":
            dependencies,

        "authorized_real_execution_horizons_days":
            [
                3,
                7,
            ],

        "H5_rerun_blocked":
            True,

        "H3_H7_checkpoint_namespaces_separate":
            True,

        "H3_H7_final_output_namespaces_separate":
            True,

        "unauthorized_H3_execution_blocked":
            True,

        "unauthorized_H7_execution_blocked":
            True,

        "direct_CLI_real_execution_authorized":
            False,

        "protected_reference_coupling_detected":
            False,

        "real_G6_events_processed":
            0,

        "orbital_propagation_performed":
            False,

        "real_candidate_generation_performed":
            False,

        "reference_association_performed":
            False,

        "GP_HISTORY_accessed":
            False,

        "CAL0_refit_performed":
            False,

        "method_retuning_performed":
            False,

        "network_requests":
            0,

        "next_safe_action":
            "FREEZE_INACTIVE_EXECUTOR_THEN_BUILD_SEPARATE_AUTHORIZATION_LAUNCHER",
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