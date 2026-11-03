from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EVALUATOR = (
    ROOT
    / "scripts/g7/run_g7d_horizon_association.py"
)

VALIDATION_OUT = (
    ROOT
    / "results/g7/"
      "G7D_Horizon_Association_Authorization_Launcher_Offline_Validation.json"
)

AUTHORIZATION_TAG = (
    "g7d-horizon-association-execution-authorized"
)

AUTHORIZATION_TOKEN = (
    "DR0048_G7D_HORIZON_ASSOCIATION_EXECUTION_AUTHORIZED"
)

DR0047_COMMIT = (
    "198d002c74d1f9ddeff67a02e0a5e710b74cd7fb"
)

DR0047_TAG = (
    "g7d-horizon-association-evaluator-frozen"
)

EVALUATOR_SHA256 = (
    "ee9c867ed6b70c40fec23de0f8ddbb2c5a6e46885aec2dc99e66a8f8440987c6"
)

SAMPLE_SHA256 = (
    "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef"
)

TRACSS_SHA256 = (
    "b8e49b9c40f974e5ff37e26ade3a855813436bb4c6dab4d7ddeccc8ff5752d39"
)

H3_EVENT_INPUT_SHA256 = (
    "5689d7a8b279a6d6a7f7ccd7941d977f2ce9ff04928a29201b82c385c3e94b43"
)

H3_CANDIDATE_INPUT_SHA256 = (
    "a64c500a73a2b66df44642066337d1cdd039404cc72147ada65f0862dad77aae"
)

H7_EVENT_INPUT_SHA256 = (
    "418348e3389b47462d196ccbc53d0a4a0e90b0c7a4a9f17e93de51a3a75c2595"
)

H7_CANDIDATE_INPUT_SHA256 = (
    "1f32718dc5607ed3d5b8d6a75c6bdd728fe6518b39e49bba607339cf71793791"
)

N_EVENTS = 6927

EXPECTED_REFERENCE_ROWS = 283595

ASSOCIATION_TOLERANCE_MIN = 5


OUTPUTS = {
    3: {
        "event":
            ROOT
            / "results/g7/G7D_H3_Horizon_Association_Event.csv",

        "summary":
            ROOT
            / "results/g7/G7D_H3_Horizon_Association_Summary.json",

        "audit":
            ROOT
            / "results/g7/G7D_H3_Horizon_Association_Execution_Audit.json",
    },

    7: {
        "event":
            ROOT
            / "results/g7/G7D_H7_Horizon_Association_Event.csv",

        "summary":
            ROOT
            / "results/g7/G7D_H7_Horizon_Association_Summary.json",

        "audit":
            ROOT
            / "results/g7/G7D_H7_Horizon_Association_Execution_Audit.json",
    },
}


EVENT_FIELDS = [
    "horizon_days",
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "preoutcome_status",
    "candidate_ready",
    "candidate_count",
    "association_status",
    "associated",
    "selected_candidate_index",
    "selected_candidate_time_utc",
    "selected_candidate_distance_km",
    "absolute_reference_tca_offset_s",
    "signed_tca_residual_s",
    "absolute_tca_error_s",
    "tca_covered",
    "signed_miss_distance_residual_km",
    "absolute_miss_distance_error_km",
    "miss_distance_covered",
]


class AuthorizationError(RuntimeError):
    pass


def sha256(path: Path) -> str:

    digest = hashlib.sha256()

    with path.open("rb") as stream:

        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def git(*arguments: str) -> str:

    process = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if process.returncode != 0:
        raise AuthorizationError(
            process.stderr.strip()
            or "git command failed"
        )

    return process.stdout.strip()


def git_tag_target(tag: str) -> str:

    return git(
        "rev-parse",
        f"refs/tags/{tag}^{{commit}}",
    )


def current_head() -> str:

    return git(
        "rev-parse",
        "HEAD",
    )


def load_evaluator():

    if not EVALUATOR.is_file():
        raise AuthorizationError(
            "frozen evaluator is missing"
        )

    actual = sha256(
        EVALUATOR
    )

    if actual != EVALUATOR_SHA256:
        raise AuthorizationError(
            "frozen evaluator SHA-256 mismatch"
        )

    spec = importlib.util.spec_from_file_location(
        "g7d_horizon_association_frozen",
        EVALUATOR,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise AuthorizationError(
            "cannot import frozen evaluator"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(
        module
    )

    return module


def serialize_value(value):

    if value is None:
        return ""

    if isinstance(
        value,
        datetime,
    ):

        return (
            value
            .astimezone(timezone.utc)
            .isoformat(
                timespec="microseconds"
            )
            .replace(
                "+00:00",
                "Z",
            )
        )

    if isinstance(
        value,
        bool,
    ):
        return (
            "true"
            if value
            else "false"
        )

    return value


def atomic_write_csv(
    path: Path,
    rows,
):

    temporary = Path(
        str(path) + ".tmp"
    )

    if temporary.exists():
        raise AuthorizationError(
            f"unexpected temporary file: {temporary}"
        )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        with temporary.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as stream:

            writer = csv.DictWriter(
                stream,
                fieldnames=EVENT_FIELDS,
                extrasaction="raise",
                lineterminator="\n",
            )

            writer.writeheader()

            for index in range(
                1,
                N_EVENTS + 1,
            ):

                if index not in rows:
                    raise AuthorizationError(
                        "event result missing: "
                        f"{index}"
                    )

                row = rows[
                    index
                ]

                writer.writerow(
                    {
                        field:
                            serialize_value(
                                row.get(
                                    field
                                )
                            )
                        for field
                        in EVENT_FIELDS
                    }
                )

        os.replace(
            temporary,
            path,
        )

    finally:

        if temporary.exists():
            temporary.unlink()


def atomic_write_json(
    path: Path,
    payload,
):

    temporary = Path(
        str(path) + ".tmp"
    )

    if temporary.exists():
        raise AuthorizationError(
            f"unexpected temporary file: {temporary}"
        )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        with temporary.open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as stream:

            json.dump(
                payload,
                stream,
                indent=2,
                sort_keys=True,
            )

            stream.write("\n")

        os.replace(
            temporary,
            path,
        )

    finally:

        if temporary.exists():
            temporary.unlink()


def expected_untracked_for_h7():

    return {
        str(
            OUTPUTS[3][key]
            .relative_to(ROOT)
        ).replace("\\", "/")
        for key in (
            "event",
            "summary",
            "audit",
        )
    }


def working_tree_untracked():

    status = git(
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )

    if not status:
        return set(), []

    untracked = set()

    other = []

    for line in status.splitlines():

        if line.startswith("?? "):

            untracked.add(
                line[3:]
            )

        else:

            other.append(
                line
            )

    return (
        untracked,
        other,
    )


def verify_execution_boundary(
    horizon_days: int,
    token: str,
):

    if token != AUTHORIZATION_TOKEN:
        raise AuthorizationError(
            "invalid DR-0048 authorization token"
        )

    if horizon_days not in (3, 7):
        raise AuthorizationError(
            "authorized horizon must be exactly 3 or 7 days"
        )

    head = current_head()

    try:
        tag_target = git_tag_target(
            AUTHORIZATION_TAG
        )

    except AuthorizationError as exc:
        raise AuthorizationError(
            "DR-0048 authorization tag is absent"
        ) from exc

    if tag_target != head:
        raise AuthorizationError(
            "DR-0048 authorization tag does not equal HEAD"
        )

    git(
        "merge-base",
        "--is-ancestor",
        DR0047_COMMIT,
        head,
    )

    if (
        git_tag_target(
            DR0047_TAG
        )
        != DR0047_COMMIT
    ):
        raise AuthorizationError(
            "DR-0047 freeze tag changed"
        )

    current_outputs = OUTPUTS[
        horizon_days
    ]

    for path in current_outputs.values():

        if path.exists():
            raise AuthorizationError(
                "requested horizon already has "
                f"an output: {path}"
            )

        temporary = Path(
            str(path) + ".tmp"
        )

        if temporary.exists():
            raise AuthorizationError(
                "unexpected temporary output exists: "
                f"{temporary}"
            )

    untracked, other = (
        working_tree_untracked()
    )

    if other:
        raise AuthorizationError(
            "tracked working tree modification exists: "
            + repr(other)
        )

    if horizon_days == 3:

        if untracked:
            raise AuthorizationError(
                "H3 requires a clean working tree"
            )

        for path in OUTPUTS[7].values():

            if path.exists():
                raise AuthorizationError(
                    "H7 output exists before H3 execution"
                )

    else:

        required = (
            expected_untracked_for_h7()
        )

        if untracked != required:
            raise AuthorizationError(
                "H7 requires exactly the three "
                "completed H3 outputs as untracked files; "
                f"actual={sorted(untracked)!r}"
            )

        h3_event = (
            OUTPUTS[3]["event"]
        )

        h3_summary = (
            OUTPUTS[3]["summary"]
        )

        h3_audit_path = (
            OUTPUTS[3]["audit"]
        )

        execution_audit = json.loads(
            h3_audit_path.read_text(
                encoding="utf-8"
            )
        )

        if (
            execution_audit.get(
                "status"
            )
            !=
            "G7D_H3_HORIZON_ASSOCIATION_EXECUTION_AUDIT_PASS"
        ):
            raise AuthorizationError(
                "H3 execution audit is not PASS"
            )

        if (
            int(
                execution_audit.get(
                    "horizon_days",
                    -1,
                )
            )
            != 3
        ):
            raise AuthorizationError(
                "H3 execution-audit horizon mismatch"
            )

        if (
            execution_audit.get(
                "authorization_commit"
            )
            != head
        ):
            raise AuthorizationError(
                "H3 authorization commit mismatch"
            )

        if (
            execution_audit.get(
                "authorization_tag"
            )
            != AUTHORIZATION_TAG
        ):
            raise AuthorizationError(
                "H3 authorization tag mismatch"
            )

        if (
            execution_audit.get(
                "evaluator_sha256"
            )
            != EVALUATOR_SHA256
        ):
            raise AuthorizationError(
                "H3 evaluator SHA mismatch"
            )

        if (
            execution_audit.get(
                "sample_sha256"
            )
            != SAMPLE_SHA256
        ):
            raise AuthorizationError(
                "H3 sample SHA mismatch"
            )

        if (
            execution_audit.get(
                "protected_reference_sha256"
            )
            != TRACSS_SHA256
        ):
            raise AuthorizationError(
                "H3 protected-reference SHA mismatch"
            )

        if (
            execution_audit.get(
                "candidate_event_input_sha256"
            )
            != H3_EVENT_INPUT_SHA256
        ):
            raise AuthorizationError(
                "H3 event-input SHA mismatch"
            )

        if (
            execution_audit.get(
                "candidate_input_sha256"
            )
            != H3_CANDIDATE_INPUT_SHA256
        ):
            raise AuthorizationError(
                "H3 candidate-input SHA mismatch"
            )

        if (
            execution_audit.get(
                "event_output_sha256"
            )
            != sha256(
                h3_event
            )
        ):
            raise AuthorizationError(
                "H3 event output changed after execution"
            )

        if (
            execution_audit.get(
                "summary_output_sha256"
            )
            != sha256(
                h3_summary
            )
        ):
            raise AuthorizationError(
                "H3 summary output changed after execution"
            )

        if (
            int(
                execution_audit.get(
                    "events",
                    -1,
                )
            )
            != N_EVENTS
        ):
            raise AuthorizationError(
                "H3 execution-audit event census mismatch"
            )

        if (
            int(
                execution_audit.get(
                    "candidate_rows_scanned",
                    -1,
                )
            )
            != 558469
        ):
            raise AuthorizationError(
                "H3 candidate-row census mismatch"
            )

        if (
            int(
                execution_audit.get(
                    "protected_reference_rows_scanned",
                    -1,
                )
            )
            != EXPECTED_REFERENCE_ROWS
        ):
            raise AuthorizationError(
                "H3 protected-reference row census mismatch"
            )

        for flag in (
            "H5_primary_rerun",
            "orbital_propagation_performed",
            "candidate_generation_performed",
            "CAL0_refit_performed",
            "method_retuning_performed",
            "automatic_retry_performed",
        ):

            if (
                execution_audit.get(
                    flag
                )
                is not False
            ):
                raise AuthorizationError(
                    "H3 execution-audit control "
                    f"violation: {flag}"
                )

        if (
            int(
                execution_audit.get(
                    "network_requests",
                    -1,
                )
            )
            != 0
        ):
            raise AuthorizationError(
                "H3 execution-audit network count changed"
            )

        summary = json.loads(
            h3_summary.read_text(
                encoding="utf-8"
            )
        )

        if (
            summary.get(
                "status"
            )
            != "G7D_H3_HORIZON_ASSOCIATION_PASS"
        ):
            raise AuthorizationError(
                "H3 summary is not PASS"
            )

        if (
            int(
                summary.get(
                    "horizon_days",
                    -1,
                )
            )
            != 3
        ):
            raise AuthorizationError(
                "H3 summary horizon mismatch"
            )

        if (
            summary.get(
                "authorization_commit"
            )
            != head
        ):
            raise AuthorizationError(
                "H3 summary authorization commit mismatch"
            )

        if (
            summary.get(
                "authorization_tag"
            )
            != AUTHORIZATION_TAG
        ):
            raise AuthorizationError(
                "H3 summary authorization tag mismatch"
            )

        if (
            summary.get(
                "evaluator_sha256"
            )
            != EVALUATOR_SHA256
        ):
            raise AuthorizationError(
                "H3 summary evaluator SHA mismatch"
            )

        if (
            int(
                summary.get(
                    "candidate_rows_scanned",
                    -1,
                )
            )
            != 558469
        ):
            raise AuthorizationError(
                "H3 summary candidate-row census mismatch"
            )

        if (
            int(
                summary.get(
                    "protected_reference_rows_scanned",
                    -1,
                )
            )
            != EXPECTED_REFERENCE_ROWS
        ):
            raise AuthorizationError(
                "H3 summary reference-row census mismatch"
            )

        if (
            summary.get(
                "association_tolerance_min"
            )
            != ASSOCIATION_TOLERANCE_MIN
        ):
            raise AuthorizationError(
                "H3 association tolerance changed"
            )

        if (
            summary.get(
                "association_threshold_inclusive"
            )
            is not True
        ):
            raise AuthorizationError(
                "H3 association threshold convention changed"
            )


def execute_horizon(
    horizon_days: int,
    token: str,
):

    verify_execution_boundary(
        horizon_days,
        token,
    )

    evaluator = load_evaluator()

    started = (
        datetime.now(
            timezone.utc
        )
    )

    result = evaluator.evaluate_horizon(
        horizon_days,
        authorization=True,
    )

    completed = (
        datetime.now(
            timezone.utc
        )
    )

    events = result[
        "events"
    ]

    summary = dict(
        result[
            "summary"
        ]
    )

    candidate_rows_scanned = int(
        result[
            "candidate_rows_scanned"
        ]
    )

    reference_rows_scanned = int(
        result[
            "protected_reference_rows_scanned"
        ]
    )

    if len(events) != N_EVENTS:
        raise AuthorizationError(
            "evaluator returned wrong event count"
        )

    if (
        reference_rows_scanned
        != EXPECTED_REFERENCE_ROWS
    ):
        raise AuthorizationError(
            "protected-reference row census mismatch"
        )

    expected_candidate_rows = {
        3: 558469,
        7: 1299383,
    }[
        horizon_days
    ]

    if (
        candidate_rows_scanned
        != expected_candidate_rows
    ):
        raise AuthorizationError(
            "candidate row census mismatch"
        )

    outputs = OUTPUTS[
        horizon_days
    ]

    status = (
        f"G7D_H{horizon_days}_"
        "HORIZON_ASSOCIATION_PASS"
    )

    summary.update(
        {
            "status":
                status,

            "analysis_role":
                "POST_CONFIRMATORY_PROPAGATION_HORIZON_SENSITIVITY",

            "association_tolerance_min":
                ASSOCIATION_TOLERANCE_MIN,

            "association_threshold_inclusive":
                True,

            "evaluator_sha256":
                EVALUATOR_SHA256,

            "candidate_rows_scanned":
                candidate_rows_scanned,

            "protected_reference_rows_scanned":
                reference_rows_scanned,

            "protected_reference_sha256":
                TRACSS_SHA256,

            "authorization_tag":
                AUTHORIZATION_TAG,

            "authorization_commit":
                current_head(),

            "started_utc":
                started
                .isoformat(
                    timespec="microseconds"
                )
                .replace(
                    "+00:00",
                    "Z",
                ),

            "completed_utc":
                completed
                .isoformat(
                    timespec="microseconds"
                )
                .replace(
                    "+00:00",
                    "Z",
                ),

            "H5_primary_rerun":
                False,

            "orbital_propagation_performed":
                False,

            "candidate_generation_performed":
                False,

            "CAL0_refit_performed":
                False,

            "method_retuning_performed":
                False,

            "network_requests":
                0,

            "automatic_retry_performed":
                False,
        }
    )

    # Write event and summary first.
    # If interruption occurs after either write, preserve the
    # partial output and do not automatically rerun.

    atomic_write_csv(
        outputs[
            "event"
        ],
        events,
    )

    atomic_write_json(
        outputs[
            "summary"
        ],
        summary,
    )

    event_sha = sha256(
        outputs[
            "event"
        ]
    )

    summary_sha = sha256(
        outputs[
            "summary"
        ]
    )

    audit = {
        "status":
            (
                f"G7D_H{horizon_days}_"
                "HORIZON_ASSOCIATION_EXECUTION_AUDIT_PASS"
            ),

        "horizon_days":
            horizon_days,

        "analysis_role":
            "POST_CONFIRMATORY_PROPAGATION_HORIZON_SENSITIVITY",

        "authorization_commit":
            current_head(),

        "authorization_tag":
            AUTHORIZATION_TAG,

        "authorization_token_required":
            True,

        "evaluator_sha256":
            EVALUATOR_SHA256,

        "sample_sha256":
            SAMPLE_SHA256,

        "protected_reference_sha256":
            TRACSS_SHA256,

        "candidate_event_input_sha256":
            (
                H3_EVENT_INPUT_SHA256
                if horizon_days == 3
                else H7_EVENT_INPUT_SHA256
            ),

        "candidate_input_sha256":
            (
                H3_CANDIDATE_INPUT_SHA256
                if horizon_days == 3
                else H7_CANDIDATE_INPUT_SHA256
            ),

        "events":
            N_EVENTS,

        "candidate_rows_scanned":
            candidate_rows_scanned,

        "protected_reference_rows_scanned":
            reference_rows_scanned,

        "event_output_sha256":
            event_sha,

        "summary_output_sha256":
            summary_sha,

        "H5_primary_rerun":
            False,

        "orbital_propagation_performed":
            False,

        "candidate_generation_performed":
            False,

        "CAL0_refit_performed":
            False,

        "method_retuning_performed":
            False,

        "network_requests":
            0,

        "automatic_retry_performed":
            False,
    }

    atomic_write_json(
        outputs[
            "audit"
        ],
        audit,
    )

    print(
        json.dumps(
            {
                "status":
                    status,

                "horizon_days":
                    horizon_days,

                "events":
                    N_EVENTS,

                "candidate_rows_scanned":
                    candidate_rows_scanned,

                "protected_reference_rows_scanned":
                    reference_rows_scanned,

                "event_output_sha256":
                    event_sha,

                "summary_output_sha256":
                    summary_sha,

                "execution_audit_sha256":
                    sha256(
                        outputs[
                            "audit"
                        ]
                    ),

                "H5_primary_rerun":
                    False,

                "orbital_propagation_performed":
                    False,

                "candidate_generation_performed":
                    False,

                "CAL0_refit_performed":
                    False,

                "method_retuning_performed":
                    False,

                "network_requests":
                    0,

                "automatic_retry_performed":
                    False,
            },
            indent=2,
            sort_keys=True,
        )
    )


def offline_validate():

    if sha256(EVALUATOR) != EVALUATOR_SHA256:
        raise AuthorizationError(
            "frozen evaluator SHA mismatch"
        )

    if (
        git_tag_target(
            DR0047_TAG
        )
        != DR0047_COMMIT
    ):
        raise AuthorizationError(
            "DR-0047 freeze tag mismatch"
        )

    if set(OUTPUTS) != {3, 7}:
        raise AuthorizationError(
            "authorized horizon set changed"
        )

    for horizon in (3, 7):

        paths = OUTPUTS[
            horizon
        ]

        if len(
            set(
                paths.values()
            )
        ) != 3:
            raise AuthorizationError(
                "output namespace collision"
            )

    all_paths = [
        path
        for horizon
        in (3, 7)
        for path
        in OUTPUTS[
            horizon
        ].values()
    ]

    if len(set(all_paths)) != 6:
        raise AuthorizationError(
            "H3/H7 output namespaces collide"
        )

    if (
        AUTHORIZATION_TOKEN
        !=
        "DR0048_G7D_HORIZON_ASSOCIATION_EXECUTION_AUTHORIZED"
    ):
        raise AuthorizationError(
            "authorization token changed"
        )

    if ASSOCIATION_TOLERANCE_MIN != 5:
        raise AuthorizationError(
            "association tolerance changed"
        )

    payload = {
        "status":
            "G7D_HORIZON_ASSOCIATION_AUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS",

        "analysis_role":
            "POST_CONFIRMATORY_PROPAGATION_HORIZON_SENSITIVITY",

        "evaluator_sha256":
            EVALUATOR_SHA256,

        "DR0047_commit":
            DR0047_COMMIT,

        "DR0047_tag":
            DR0047_TAG,

        "future_authorization_tag":
            AUTHORIZATION_TAG,

        "authorization_token":
            AUTHORIZATION_TOKEN,

        "authorized_horizons_days":
            [3, 7],

        "H5_execution_permitted":
            False,

        "association_tolerance_min":
            5,

        "association_threshold_inclusive":
            True,

        "H3_before_H7_required":
            True,

        "H3_execution_audit_integrity_required_before_H7":
            True,

        "H3_event_hash_reverified_before_H7":
            True,

        "H3_summary_hash_reverified_before_H7":
            True,

        "DR0047_ancestry_required":
            True,

        "H3_output_files":
            [
                str(
                    OUTPUTS[3][key]
                    .relative_to(ROOT)
                ).replace(
                    "\\",
                    "/",
                )
                for key in (
                    "event",
                    "summary",
                    "audit",
                )
            ],

        "H7_output_files":
            [
                str(
                    OUTPUTS[7][key]
                    .relative_to(ROOT)
                ).replace(
                    "\\",
                    "/",
                )
                for key in (
                    "event",
                    "summary",
                    "audit",
                )
            ],

        "direct_execution_currently_authorized":
            False,

        "real_H3_execution_performed":
            False,

        "real_H7_execution_performed":
            False,

        "protected_reference_accessed":
            False,

        "orbital_propagation_performed":
            False,

        "candidate_generation_performed":
            False,

        "CAL0_refit_performed":
            False,

        "method_retuning_performed":
            False,

        "network_requests":
            0,

        "automatic_retry":
            False,

        "next_safe_action":
            "INDEPENDENT_AUDIT_THEN_FREEZE_DR0048_AUTHORIZATION_LAUNCHER",
    }

    VALIDATION_OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    atomic_write_json(
        VALIDATION_OUT,
        payload,
    )

    print(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
    )


def main():

    parser = argparse.ArgumentParser()

    group = parser.add_mutually_exclusive_group(
        required=True
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
        choices=(3, 7),
    )

    parser.add_argument(
        "--authorization-token",
        default="",
    )

    arguments = parser.parse_args()

    if arguments.offline_validate:

        if (
            arguments.horizon_days
            is not None
            or arguments.authorization_token
        ):
            raise AuthorizationError(
                "offline validation accepts no execution arguments"
            )

        offline_validate()

        return

    if arguments.horizon_days is None:
        raise AuthorizationError(
            "--execute requires --horizon-days 3 or 7"
        )

    execute_horizon(
        arguments.horizon_days,
        arguments.authorization_token,
    )


if __name__ == "__main__":
    main()