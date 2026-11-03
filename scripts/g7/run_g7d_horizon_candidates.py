"""Inactive G7-D H=3/H=7 real-event candidate executor.

The executor reuses the frozen G6 event-input, parsing, SGP4, event-status,
candidate-serialization, and failure-handling machinery.  Only the candidate
search horizon is replaced by the independently frozen G7-D numerical kernel.

Direct CLI execution is intentionally unauthorized.  A separately frozen
authorization launcher must call execute_horizon(..., authorization=True).

No protected reference conjunction source is read here.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


G6_IMPLEMENTATION = (
    ROOT
    / "scripts/g6/generate_g6_preoutcome_candidates.py"
)

HORIZON_KERNEL = (
    ROOT
    / "scripts/g7/g7d_horizon_numerics.py"
)


SELECTION = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Selection.csv"
)

CACHE = (
    ROOT
    / "data/raw/g6/G6_48H_Replay_Selected_GP_Cache.jsonl"
)


OFFLINE_VALIDATION = (
    ROOT
    / "results/g7/G7D_Horizon_Executor_Offline_Validation.json"
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


EXPECTED_EVENTS = 6927
EXPECTED_OBJECTS = 13854


AUTHORIZED_HORIZONS_DAYS = (
    3,
    7,
)


HORIZON_PATHS = {
    3: {
        "work_db":
            ROOT
            / "data/raw/g7/G7D_H3_Candidate_Work.sqlite3",

        "event_out":
            ROOT
            / "results/g7/G7D_H3_Event_Status.csv",

        "candidate_out":
            ROOT
            / "results/g7/G7D_H3_Candidates.csv",

        "summary_out":
            ROOT
            / "results/g7/G7D_H3_Candidate_Summary.json",
    },

    7: {
        "work_db":
            ROOT
            / "data/raw/g7/G7D_H7_Candidate_Work.sqlite3",

        "event_out":
            ROOT
            / "results/g7/G7D_H7_Event_Status.csv",

        "candidate_out":
            ROOT
            / "results/g7/G7D_H7_Candidates.csv",

        "summary_out":
            ROOT
            / "results/g7/G7D_H7_Candidate_Summary.json",
    },
}


class G7DExecutorError(RuntimeError):
    pass


class G7DExecutionNotAuthorized(G7DExecutorError):
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


def utc_now_text(
) -> str:

    return (
        datetime.now(
            UTC
        )
        .isoformat(
            timespec="microseconds"
        )
        .replace(
            "+00:00",
            "Z",
        )
    )


def load_module(
    name: str,
    path: Path,
):

    spec = (
        importlib.util
        .spec_from_file_location(
            name,
            path,
        )
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise G7DExecutorError(
            "cannot load module: "
            + path.as_posix()
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


def verify_dependency_hashes(
) -> dict[str, str]:

    expected = {
        G6_IMPLEMENTATION:
            EXPECTED_G6_SHA256,

        HORIZON_KERNEL:
            EXPECTED_KERNEL_SHA256,

        SELECTION:
            EXPECTED_SELECTION_SHA256,

        CACHE:
            EXPECTED_CACHE_SHA256,
    }

    observed = {}

    for path, expected_sha in (
        expected.items()
    ):

        if not path.is_file():
            raise G7DExecutorError(
                "required frozen dependency missing: "
                + path.relative_to(
                    ROOT
                ).as_posix()
            )

        actual = sha256(
            path
        )

        if actual != expected_sha:
            raise G7DExecutorError(
                "frozen dependency SHA mismatch: "
                + path.relative_to(
                    ROOT
                ).as_posix()
            )

        observed[
            path.relative_to(
                ROOT
            ).as_posix()
        ] = actual

    return observed


def validate_horizon(
    horizon_days: int,
) -> int:

    if (
        isinstance(
            horizon_days,
            bool,
        )
        or horizon_days
        not in AUTHORIZED_HORIZONS_DAYS
    ):
        raise G7DExecutorError(
            "real G7-D candidate execution permits only "
            "H=3 d or H=7 d; frozen H=5 d must not be rerun"
        )

    return int(
        horizon_days
    )


def path_spec(
    horizon_days: int,
) -> dict[str, Path]:

    horizon_days = (
        validate_horizon(
            horizon_days
        )
    )

    return dict(
        HORIZON_PATHS[
            horizon_days
        ]
    )


def relative(
    path: Path,
) -> str:

    return (
        path
        .relative_to(
            ROOT
        )
        .as_posix()
    )


def git_check_ignore(
    path: Path,
) -> bool:

    result = subprocess.run(
        [
            "git",
            "check-ignore",
            "-q",
            relative(
                path
            ),
        ],
        cwd=ROOT,
        check=False,
    )

    return (
        result.returncode
        == 0
    )


class HorizonControlflowProxy:

    def __init__(
        self,
        frozen_controlflow,
        kernel,
        horizon_days: int,
    ):

        self._frozen = (
            frozen_controlflow
        )

        self._kernel = (
            kernel
        )

        self._horizon_days = (
            validate_horizon(
                horizon_days
            )
        )


    def __getattr__(
        self,
        name: str,
    ):

        return getattr(
            self._frozen,
            name,
        )


    def enumerate_candidates_redundancy_safe(
        self,
        quantity,
    ):

        return (
            self._kernel
            .enumerate_candidates_redundancy_safe_horizon(
                quantity,
                self._horizon_days,
            )
        )


def configuration_snapshot(
) -> dict[str, object]:

    dependencies = (
        verify_dependency_hashes()
    )

    path_payload = {}

    seen_paths = set()

    for horizon in (
        AUTHORIZED_HORIZONS_DAYS
    ):

        spec = path_spec(
            horizon
        )

        serialized = {
            key:
                relative(
                    value
                )
            for key, value
            in spec.items()
        }

        for value in (
            serialized.values()
        ):

            if value in seen_paths:
                raise G7DExecutorError(
                    "H3/H7 path namespaces overlap: "
                    + value
                )

            seen_paths.add(
                value
            )

        if not git_check_ignore(
            spec[
                "work_db"
            ]
        ):
            raise G7DExecutorError(
                "local work DB is not Git-ignored: "
                + serialized[
                    "work_db"
                ]
            )

        path_payload[
            str(
                horizon
            )
        ] = serialized


    if 5 in HORIZON_PATHS:
        raise G7DExecutorError(
            "H=5 execution namespace exists unexpectedly"
        )


    return {
        "status":
            "G7D_HORIZON_EXECUTOR_CONFIGURATION_VALID",

        "authorized_real_execution_horizons_days":
            list(
                AUTHORIZED_HORIZONS_DAYS
            ),

        "frozen_H5_rerun_permitted":
            False,

        "dependency_hashes":
            dependencies,

        "horizon_paths":
            path_payload,

        "separate_checkpoint_namespaces":
            True,

        "separate_final_output_namespaces":
            True,

        "direct_CLI_real_execution_authorized":
            False,

        "protected_reference_access_configured":
            False,
    }


def expected_checkpoint_metadata(
    horizon_days: int,
    executor_sha: str,
) -> dict[str, str]:

    horizon_days = (
        validate_horizon(
            horizon_days
        )
    )

    return {
        "executor_sha256":
            executor_sha,

        "g6_implementation_sha256":
            EXPECTED_G6_SHA256,

        "g7d_horizon_kernel_sha256":
            EXPECTED_KERNEL_SHA256,

        "selection_sha256":
            EXPECTED_SELECTION_SHA256,

        "selected_TLE_cache_sha256":
            EXPECTED_CACHE_SHA256,

        "horizon_days":
            str(
                horizon_days
            ),

        "expected_events":
            str(
                EXPECTED_EVENTS
            ),

        "expected_objects":
            str(
                EXPECTED_OBJECTS
            ),

        "reference_association":
            "PROHIBITED_IN_CANDIDATE_GENERATION",

        "CAL0_refit":
            "PROHIBITED",

        "method_retuning":
            "PROHIBITED",
    }


def open_checkpoint(
    horizon_days: int,
    executor_sha: str,
):

    spec = path_spec(
        horizon_days
    )

    work_db = (
        spec[
            "work_db"
        ]
    )

    work_db.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = (
        sqlite3.connect(
            work_db
        )
    )

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
        CREATE TABLE IF NOT EXISTS event_results (
            final_g6_sample_index INTEGER PRIMARY KEY,
            payload_json TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_results (
            final_g6_sample_index INTEGER NOT NULL,
            candidate_index INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (
                final_g6_sample_index,
                candidate_index
            )
        )
        """
    )

    expected = (
        expected_checkpoint_metadata(
            horizon_days,
            executor_sha,
        )
    )

    existing = dict(
        connection.execute(
            "SELECT key, value "
            "FROM metadata"
        ).fetchall()
    )

    if existing:

        if existing != expected:

            connection.close()

            raise G7DExecutorError(
                "checkpoint metadata does not match "
                "the frozen H"
                + str(
                    horizon_days
                )
                + " execution boundary"
            )

    else:

        connection.executemany(
            "INSERT INTO metadata(key,value) "
            "VALUES (?,?)",
            sorted(
                expected.items()
            ),
        )

        connection.commit()

    return connection


def execute_horizon(
    horizon_days: int,
    *,
    authorization: bool = False,
) -> dict[str, object]:

    # Horizon validation intentionally precedes every input read.
    horizon_days = (
        validate_horizon(
            horizon_days
        )
    )

    # Real execution cannot occur through the direct CLI.
    if authorization is not True:

        raise G7DExecutionNotAuthorized(
            "G7D_REAL_HORIZON_EXECUTION_NOT_AUTHORIZED"
        )


    started_utc = (
        utc_now_text()
    )

    wall_start = (
        time.perf_counter()
    )


    dependency_hashes = (
        verify_dependency_hashes()
    )


    g6 = load_module(
        "g7d_frozen_g6_executor_dependency",
        G6_IMPLEMENTATION,
    )

    kernel = load_module(
        "g7d_frozen_horizon_kernel_dependency",
        HORIZON_KERNEL,
    )


    # Retain the G6 environment constraint.
    environment = (
        g6.verify_environment()
    )


    (
        _selection_by_object,
        cache_by_object,
        event_rows,
    ) = (
        g6.verify_compact_inputs()
    )


    if (
        len(
            cache_by_object
        )
        != EXPECTED_OBJECTS
    ):
        raise G7DExecutorError(
            "selected-TLE object census changed"
        )


    events = (
        g6.build_events(
            event_rows,
            cache_by_object,
        )
    )


    if len(
        events
    ) != EXPECTED_EVENTS:

        raise G7DExecutorError(
            "frozen event census changed"
        )


    frozen_controlflow = (
        g6.import_frozen_controlflow()
    )


    proxy = (
        HorizonControlflowProxy(
            frozen_controlflow,
            kernel,
            horizon_days,
        )
    )


    executor_sha = (
        sha256(
            Path(
                __file__
            )
        )
    )


    spec = path_spec(
        horizon_days
    )


    for output_name in (
        "event_out",
        "candidate_out",
        "summary_out",
    ):

        if spec[
            output_name
        ].exists():

            raise G7DExecutorError(
                "refusing to overwrite existing H"
                + str(
                    horizon_days
                )
                + " final output: "
                + relative(
                    spec[
                        output_name
                    ]
                )
            )


    connection = (
        open_checkpoint(
            horizon_days,
            executor_sha,
        )
    )


    completed_at_start = 0


    try:

        completed = {
            int(
                row[0]
            )
            for row
            in connection.execute(
                "SELECT final_g6_sample_index "
                "FROM event_results"
            )
        }


        completed_at_start = (
            len(
                completed
            )
        )


        for event in events:

            event_index = int(
                event[
                    "final_g6_sample_index"
                ]
            )


            if event_index in completed:
                continue


            (
                event_result,
                candidate_results,
            ) = (
                g6.process_event(
                    proxy,
                    event,
                    cache_by_object,
                )
            )


            status = (
                event_result[
                    "preoutcome_status"
                ]
            )


            if (
                status
                not in g6.ALLOWED_PREOUTCOME_STATUSES
            ):

                raise G7DExecutorError(
                    "unexpected pre-outcome status "
                    "for event "
                    + str(
                        event_index
                    )
                    + ": "
                    + str(
                        status
                    )
                )


            with connection:

                connection.execute(
                    "INSERT INTO event_results("
                    "final_g6_sample_index,"
                    "payload_json"
                    ") VALUES (?,?)",
                    (
                        event_index,

                        json.dumps(
                            event_result,
                            sort_keys=True,
                            separators=(
                                ",",
                                ":",
                            ),
                        ),
                    ),
                )


                for candidate in (
                    candidate_results
                ):

                    connection.execute(
                        "INSERT INTO candidate_results("
                        "final_g6_sample_index,"
                        "candidate_index,"
                        "payload_json"
                        ") VALUES (?,?,?)",
                        (
                            event_index,

                            int(
                                candidate[
                                    "candidate_index"
                                ]
                            ),

                            json.dumps(
                                candidate,
                                sort_keys=True,
                                separators=(
                                    ",",
                                    ":",
                                ),
                            ),
                        ),
                    )


        event_payloads = [
            json.loads(
                row[0]
            )
            for row
            in connection.execute(
                "SELECT payload_json "
                "FROM event_results "
                "ORDER BY final_g6_sample_index"
            )
        ]


        if (
            len(
                event_payloads
            )
            != EXPECTED_EVENTS
        ):

            raise G7DExecutorError(
                "checkpoint incomplete after event loop"
            )


        candidate_payloads = [
            json.loads(
                row[0]
            )
            for row
            in connection.execute(
                "SELECT payload_json "
                "FROM candidate_results "
                "ORDER BY "
                "final_g6_sample_index,"
                "candidate_index"
            )
        ]


    finally:

        connection.close()


    # Recheck overwrite boundary immediately before final export.
    for output_name in (
        "event_out",
        "candidate_out",
        "summary_out",
    ):

        if spec[
            output_name
        ].exists():

            raise G7DExecutorError(
                "final H"
                + str(
                    horizon_days
                )
                + " output appeared during execution: "
                + relative(
                    spec[
                        output_name
                    ]
                )
            )


    g6.atomic_csv(
        spec[
            "event_out"
        ],
        g6.EVENT_FIELDS,
        event_payloads,
    )


    g6.atomic_csv(
        spec[
            "candidate_out"
        ],
        g6.CANDIDATE_FIELDS,
        candidate_payloads,
    )


    status_counts = Counter(
        row[
            "preoutcome_status"
        ]
        for row
        in event_payloads
    )


    stratum_counts = Counter(
        row[
            "g6_temporal_stratum"
        ]
        for row
        in event_payloads
    )


    candidate_total = (
        len(
            candidate_payloads
        )
    )


    wall_seconds = (
        time.perf_counter()
        - wall_start
    )


    summary = {
        "status":
            "G7D_H"
            + str(
                horizon_days
            )
            + "_CANDIDATE_GENERATION_PASS",

        "analysis_role":
            "POST_CONFIRMATORY_PROPAGATION_HORIZON_SENSITIVITY",

        "horizon_days":
            horizon_days,

        "frozen_H5_anchor_rerun":
            False,

        "events":
            EXPECTED_EVENTS,

        "objects":
            EXPECTED_OBJECTS,

        "completed_checkpoint_events_at_start":
            completed_at_start,

        "event_status_counts":
            dict(
                sorted(
                    status_counts.items()
                )
            ),

        "temporal_stratum_counts":
            dict(
                sorted(
                    stratum_counts.items()
                )
            ),

        "candidate_rows":
            candidate_total,

        "started_utc":
            started_utc,

        "completed_utc":
            utc_now_text(),

        "wall_seconds_this_invocation":
            wall_seconds,

        "environment":
            environment,

        "dependency_hashes":
            dependency_hashes,

        "executor_sha256":
            executor_sha,

        "event_output":
            relative(
                spec[
                    "event_out"
                ]
            ),

        "event_output_sha256":
            sha256(
                spec[
                    "event_out"
                ]
            ),

        "candidate_output":
            relative(
                spec[
                    "candidate_out"
                ]
            ),

        "candidate_output_sha256":
            sha256(
                spec[
                    "candidate_out"
                ]
            ),

        "local_work_db":
            relative(
                spec[
                    "work_db"
                ]
            ),

        "local_work_db_committable":
            False,

        "reference_association_performed":
            False,

        "protected_reference_accessed":
            False,

        "GP_HISTORY_accessed":
            False,

        "CAL0_refit_performed":
            False,

        "method_retuning_performed":
            False,

        "network_requests":
            0,

        "automatic_retry_performed":
            False,

        "next_safe_action":
            "INDEPENDENT_AUDIT_BEFORE_REFERENCE_ASSOCIATION",
    }


    g6.atomic_json(
        spec[
            "summary_out"
        ],
        summary,
    )


    print(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
    )


    return summary


def offline_validate(
) -> dict[str, object]:

    configuration = (
        configuration_snapshot()
    )


    # No real-output/checkpoint artifact may exist before authorization.
    for horizon in (
        AUTHORIZED_HORIZONS_DAYS
    ):

        spec = path_spec(
            horizon
        )

        for path in (
            spec.values()
        ):

            if path.exists():

                raise G7DExecutorError(
                    "unexpected real-execution artifact exists "
                    "before authorization: "
                    + relative(
                        path
                    )
                )


    unauthorized_H3_blocked = False


    try:

        execute_horizon(
            3,
            authorization=False,
        )

    except G7DExecutionNotAuthorized:

        unauthorized_H3_blocked = True


    if not unauthorized_H3_blocked:

        raise G7DExecutorError(
            "unauthorized H=3 execution was not blocked"
        )


    frozen_H5_blocked = False


    try:

        execute_horizon(
            5,
            authorization=True,
        )

    except G7DExecutorError:

        frozen_H5_blocked = True


    if not frozen_H5_blocked:

        raise G7DExecutorError(
            "frozen H=5 rerun was not blocked"
        )


    # Authorization-guard tests above must not have created files.
    for horizon in (
        AUTHORIZED_HORIZONS_DAYS
    ):

        spec = path_spec(
            horizon
        )

        for path in (
            spec.values()
        ):

            if path.exists():

                raise G7DExecutorError(
                    "authorization-guard validation created "
                    "real-execution artifact: "
                    + relative(
                        path
                    )
                )


    payload = {
        "status":
            "G7D_HORIZON_EXECUTOR_OFFLINE_VALIDATION_PASS",

        "configuration":
            configuration,

        "unauthorized_H3_execution_blocked":
            unauthorized_H3_blocked,

        "frozen_H5_rerun_blocked":
            frozen_H5_blocked,

        "real_G6_events_processed":
            0,

        "orbital_propagation_performed":
            False,

        "real_candidate_generation_performed":
            False,

        "reference_association_performed":
            False,

        "protected_reference_accessed":
            False,

        "GP_HISTORY_accessed":
            False,

        "CAL0_refit_performed":
            False,

        "method_retuning_performed":
            False,

        "network_requests":
            0,

        "real_execution_authorized":
            False,

        "next_safe_action":
            "INDEPENDENT_REVIEW_AND_FREEZE_INACTIVE_G7D_EXECUTOR",
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
        argparse.ArgumentParser()
    )

    group = (
        parser
        .add_mutually_exclusive_group(
            required=True
        )
    )

    group.add_argument(
        "--offline-validate",
        action="store_true",
    )

    group.add_argument(
        "--execute",
        action="store_true",
    )

    parser.add_argument(
        "--horizon-days",
        type=int,
        choices=(
            3,
            7,
        ),
    )


    args = (
        parser.parse_args()
    )


    if args.offline_validate:

        if (
            args.horizon_days
            is not None
        ):

            raise G7DExecutorError(
                "--horizon-days is invalid "
                "with --offline-validate"
            )

        offline_validate()

        return 0


    if args.execute:

        if (
            args.horizon_days
            is None
        ):

            raise G7DExecutorError(
                "--execute requires "
                "--horizon-days"
            )

        # Intentionally false. Real execution must occur through
        # a separately frozen authorization launcher.
        execute_horizon(
            args.horizon_days,
            authorization=False,
        )


    return 0


if __name__ == "__main__":

    raise SystemExit(
        main()
    )