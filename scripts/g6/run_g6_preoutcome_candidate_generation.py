#!/usr/bin/env python3
"""Authorization wrapper for frozen G6 pre-outcome candidate generation.

--offline-validate
    Verifies the DR-0027 boundary, frozen dependencies, compact replay inputs,
    numerical environment, and local checkpoint governance. Performs no G6
    orbital propagation or candidate generation.

--execute-or-resume
    Requires the DR-0028 authorization tag and explicit token. Calls only the
    already-frozen run_generation_to_work_db() implementation. Existing
    compatible local work DB state may be resumed after interruption.

Reference association and protected TraCSS outcomes remain outside scope.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PARENT_COMMIT = (
    "6ee71bcc5069638535d3f2f77329c35de5ec1aba"
)

PARENT_TAG = (
    "g6-preoutcome-candidate-implementation-frozen"
)

EXECUTION_TAG = (
    "g6-preoutcome-candidate-generation-authorized"
)

AUTHORIZATION_TOKEN = (
    "DR-0028_G6_PREOUTCOME_CANDIDATE_GENERATION"
)

TARGET = (
    ROOT
    / "scripts/g6/generate_g6_preoutcome_candidates.py"
)

TARGET_VALIDATION = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Implementation_Offline_Validation.json"
)

TARGET_REVIEW = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Implementation_Independent_Review.json"
)

EXPECTED_TARGET_SHA = (
    "bce2a4b5e4dab8d0c4e461e2acb4dbac8afe22d3ac412922ba3670a832adcecb"
)

EXPECTED_TARGET_VALIDATION_SHA = (
    "f605f66c8525413ff0e62ad4a0456e9485051513805d9eb58b7263b64a4c6ba2"
)

EXPECTED_TARGET_REVIEW_SHA = (
    "d66ceac3c2d3f28077de7d5d7c4a503a70dda2427fe832dbeee5fafb023b5f75"
)

EXPECTED_EVENTS = 6927
EXPECTED_OBJECTS = 13854

OFFLINE_VALIDATION = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Authorization_Launcher_Offline_Validation.json"
)

EXECUTION_AUDIT = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Generation_Execution_Audit.json"
)


class AuthorizationError(RuntimeError):
    pass


def sha256(path: Path) -> str:

    digest = hashlib.sha256()

    with path.open("rb") as stream:

        for block in iter(
            lambda: stream.read(
                1024 * 1024
            ),
            b"",
        ):
            digest.update(
                block
            )

    return digest.hexdigest()


def git(
    *args: str,
    check: bool = True,
):

    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def git_value(
    *args: str,
) -> str:

    return git(
        *args
    ).stdout.strip()


def atomic_json(
    path: Path,
    payload: dict,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    temp = Path(
        temp_name
    )

    try:

        with os.fdopen(
            fd,
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

            stream.write(
                "\n"
            )

            stream.flush()
            os.fsync(
                stream.fileno()
            )

        os.replace(
            temp,
            path,
        )

    finally:

        temp.unlink(
            missing_ok=True
        )


def verify_frozen_dependencies() -> None:

    expected = {
        TARGET:
            EXPECTED_TARGET_SHA,

        TARGET_VALIDATION:
            EXPECTED_TARGET_VALIDATION_SHA,

        TARGET_REVIEW:
            EXPECTED_TARGET_REVIEW_SHA,
    }

    for path, expected_sha in (
        expected.items()
    ):

        if not path.is_file():

            raise AuthorizationError(
                f"missing frozen dependency: {path}"
            )

        actual = sha256(
            path
        )

        if actual != expected_sha:

            raise AuthorizationError(
                "frozen dependency SHA mismatch\n"
                f"path={path}\n"
                f"actual={actual}\n"
                f"expected={expected_sha}"
            )


def verify_parent_boundary() -> None:

    if (
        git_value(
            "rev-parse",
            "HEAD",
        )
        != PARENT_COMMIT
    ):
        raise AuthorizationError(
            "offline-validation HEAD is not DR-0027"
        )

    if (
        git_value(
            "rev-parse",
            f"refs/tags/{PARENT_TAG}^{{commit}}",
        )
        != PARENT_COMMIT
    ):
        raise AuthorizationError(
            "DR-0027 freeze tag target changed"
        )

    if git_value(
        "status",
        "--porcelain",
        "--untracked-files=no",
    ):
        raise AuthorizationError(
            "tracked working tree is not clean"
        )


def verify_execution_boundary() -> str:

    head = git_value(
        "rev-parse",
        "HEAD",
    )

    tag_result = git(
        "rev-parse",
        f"refs/tags/{EXECUTION_TAG}^{{commit}}",
        check=False,
    )

    if tag_result.returncode != 0:

        raise AuthorizationError(
            "candidate-generation authorization tag is absent"
        )

    tag_target = (
        tag_result.stdout.strip()
    )

    if tag_target != head:

        raise AuthorizationError(
            "authorization tag does not target current HEAD"
        )

    parent = git_value(
        "rev-parse",
        "HEAD^",
    )

    if parent != PARENT_COMMIT:

        raise AuthorizationError(
            "authorization commit is not direct child of DR-0027"
        )

    if git_value(
        "status",
        "--porcelain",
        "--untracked-files=no",
    ):

        raise AuthorizationError(
            "tracked working tree is not clean"
        )

    return head


def import_target():

    spec = importlib.util.spec_from_file_location(
        "g6_frozen_candidate_implementation",
        TARGET,
    )

    if (
        spec is None
        or spec.loader is None
    ):

        raise AuthorizationError(
            "could not construct target module spec"
        )

    module = (
        importlib.util.module_from_spec(
            spec
        )
    )

    sys.modules[
        spec.name
    ] = module

    spec.loader.exec_module(
        module
    )

    return module


def verify_local_checkpoint_governance(
    target,
) -> None:

    relative = (
        target.WORK_DB.relative_to(
            ROOT
        ).as_posix()
    )

    ignored = git(
        "check-ignore",
        "-v",
        relative,
        check=False,
    )

    if ignored.returncode != 0:

        raise AuthorizationError(
            "candidate work DB is not Git-ignored"
        )

    if git_value(
        "ls-files",
        "--",
        relative,
    ):

        raise AuthorizationError(
            "candidate work DB is unexpectedly tracked"
        )


def verify_final_outputs_absent(
    target,
) -> None:

    for path in (
        target.EVENT_OUT,
        target.CANDIDATE_OUT,
        target.SUMMARY_OUT,
        EXECUTION_AUDIT,
    ):

        if path.exists():

            raise AuthorizationError(
                "refusing to overwrite existing final output: "
                + str(path)
            )


def offline_validate() -> None:

    if OFFLINE_VALIDATION.exists():

        raise AuthorizationError(
            "launcher offline-validation artifact already exists"
        )

    verify_parent_boundary()
    verify_frozen_dependencies()

    target = import_target()

    verify_local_checkpoint_governance(
        target
    )

    if target.WORK_DB.exists():

        raise AuthorizationError(
            "candidate work DB unexpectedly exists before authorization freeze"
        )

    verify_final_outputs_absent(
        target
    )

    frozen_hashes = (
        target.verify_frozen_hashes()
    )

    environment = (
        target.verify_environment()
    )

    (
        selection_by_object,
        cache_by_object,
        event_rows,
    ) = target.verify_compact_inputs()

    events = target.build_events(
        event_rows,
        cache_by_object,
    )

    frozen = (
        target.import_frozen_controlflow()
    )

    numerical_constants = (
        target.verify_numerical_constants(
            frozen
        )
    )

    if len(
        selection_by_object
    ) != EXPECTED_OBJECTS:

        raise AuthorizationError(
            "selection object count changed"
        )

    if len(
        cache_by_object
    ) != EXPECTED_OBJECTS:

        raise AuthorizationError(
            "selected-TLE cache count changed"
        )

    if len(
        events
    ) != EXPECTED_EVENTS:

        raise AuthorizationError(
            "event count changed"
        )

    result = {
        "status":
            "G6_PREOUTCOME_CANDIDATE_AUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS",

        "launcher_sha256":
            sha256(
                Path(__file__)
            ),

        "frozen_target_sha256":
            EXPECTED_TARGET_SHA,

        "frozen_target_validation_sha256":
            EXPECTED_TARGET_VALIDATION_SHA,

        "frozen_target_review_sha256":
            EXPECTED_TARGET_REVIEW_SHA,

        "events_verified":
            len(
                events
            ),

        "objects_verified":
            len(
                cache_by_object
            ),

        "frozen_input_hashes":
            frozen_hashes,

        "environment":
            environment,

        "numerical_constants":
            numerical_constants,

        "candidate_work_db_gitignored":
            True,

        "candidate_work_db_tracked":
            False,

        "candidate_work_db_exists":
            False,

        "generation_executed":
            False,

        "G6_orbital_propagation_performed":
            False,

        "G6_candidate_generation_executed":
            False,

        "reference_association_performed":
            False,

        "TraCSS_reference_rows_opened":
            0,

        "protected_outcome_access":
            False,

        "G6_error_metrics_computed":
            False,

        "full_GP_HISTORY_reread":
            False,

        "network_requests":
            0,

        "execution_authorized_in_this_mode":
            False,

        "execution_tag_required":
            EXECUTION_TAG,

        "authorization_token_required":
            AUTHORIZATION_TOKEN,

        "next_safe_action":
            "INDEPENDENT_REVIEW_AND_FREEZE_DR0028_AUTHORIZATION_LAYER",
    }

    atomic_json(
        OFFLINE_VALIDATION,
        result,
    )

    print(
        json.dumps(
            {
                **result,

                "offline_validation_sha256":
                    sha256(
                        OFFLINE_VALIDATION
                    ),
            },
            indent=2,
            sort_keys=True,
        )
    )


def execute_or_resume(
    authorization: str,
) -> None:

    if authorization != AUTHORIZATION_TOKEN:

        raise AuthorizationError(
            "explicit DR-0028 authorization token is required"
        )

    authorization_commit = (
        verify_execution_boundary()
    )

    verify_frozen_dependencies()

    target = import_target()

    verify_local_checkpoint_governance(
        target
    )

    verify_final_outputs_absent(
        target
    )

    work_db_preexisting = (
        target.WORK_DB.exists()
    )

    # The frozen DR-0027 implementation owns all scientific numerical logic.
    # This launcher intentionally does not reimplement process_event().
    summary = (
        target.run_generation_to_work_db()
    )

    if (
        summary.get(
            "status"
        )
        != "G6_PREOUTCOME_CANDIDATE_GENERATION_PASS"
    ):

        raise AuthorizationError(
            "frozen candidate generation did not return PASS"
        )

    if (
        summary.get(
            "events"
        )
        != EXPECTED_EVENTS
    ):

        raise AuthorizationError(
            "candidate-generation event count changed"
        )

    status_counts = (
        summary.get(
            "event_status_counts",
            {}
        )
    )

    if sum(
        int(value)
        for value in status_counts.values()
    ) != EXPECTED_EVENTS:

        raise AuthorizationError(
            "candidate terminal-status accounting does not sum to 6927"
        )

    allowed = {
        "GP_PARSE_FAILURE",
        "PROPAGATION_FAILURE",
        "UNRECOVERED_REFINEMENT_FAILURE",
        "NO_VALID_CANDIDATE",
        "CANDIDATE_SET_READY",
    }

    if not set(
        status_counts
    ).issubset(
        allowed
    ):

        raise AuthorizationError(
            "unexpected pre-outcome event status emitted"
        )

    if (
        summary.get(
            "reference_association_performed"
        )
        is not False
    ):

        raise AuthorizationError(
            "reference association occurred"
        )

    if (
        summary.get(
            "TraCSS_reference_rows_opened"
        )
        != 0
    ):

        raise AuthorizationError(
            "TraCSS reference rows were opened"
        )

    if (
        summary.get(
            "protected_outcome_access"
        )
        is not False
    ):

        raise AuthorizationError(
            "protected outcome access occurred"
        )

    if (
        summary.get(
            "G6_error_metrics_computed"
        )
        is not False
    ):

        raise AuthorizationError(
            "G6 error metrics were computed"
        )

    if (
        summary.get(
            "network_requests"
        )
        != 0
    ):

        raise AuthorizationError(
            "network requests occurred"
        )

    for path in (
        target.EVENT_OUT,
        target.CANDIDATE_OUT,
        target.SUMMARY_OUT,
        target.WORK_DB,
    ):

        if not path.is_file():

            raise AuthorizationError(
                "required generation output missing: "
                + str(path)
            )

    verify_local_checkpoint_governance(
        target
    )

    with target.EVENT_OUT.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        event_rows = list(
            csv.DictReader(
                stream
            )
        )

    if len(
        event_rows
    ) != EXPECTED_EVENTS:

        raise AuthorizationError(
            "event output does not contain exactly 6927 rows"
        )

    observed = {}

    for row in event_rows:

        status = row[
            "preoutcome_status"
        ].strip()

        observed[
            status
        ] = (
            observed.get(
                status,
                0,
            )
            + 1
        )

    if observed != {
        key:
            int(value)
        for key, value
        in status_counts.items()
    }:

        raise AuthorizationError(
            "event CSV status census differs from summary"
        )

    execution = {
        "status":
            "G6_PREOUTCOME_CANDIDATE_GENERATION_EXECUTION_PASS",

        "authorization_commit":
            authorization_commit,

        "authorization_tag":
            EXECUTION_TAG,

        "authorization_token":
            AUTHORIZATION_TOKEN,

        "frozen_implementation_sha256":
            EXPECTED_TARGET_SHA,

        "work_db_preexisting_at_invocation":
            work_db_preexisting,

        "work_db_resume_supported":
            True,

        "events":
            EXPECTED_EVENTS,

        "event_status_counts":
            dict(
                sorted(
                    observed.items()
                )
            ),

        "candidate_rows":
            int(
                summary[
                    "candidate_rows"
                ]
            ),

        "events_with_candidate_set":
            int(
                summary[
                    "events_with_candidate_set"
                ]
            ),

        "event_output_sha256":
            sha256(
                target.EVENT_OUT
            ),

        "candidate_output_sha256":
            sha256(
                target.CANDIDATE_OUT
            ),

        "summary_output_sha256":
            sha256(
                target.SUMMARY_OUT
            ),

        "work_db_sha256":
            sha256(
                target.WORK_DB
            ),

        "work_db_committable":
            False,

        "reference_association_performed":
            False,

        "TraCSS_reference_rows_opened":
            0,

        "protected_outcome_access":
            False,

        "G6_error_metrics_computed":
            False,

        "full_GP_HISTORY_reread":
            False,

        "network_requests":
            0,

        "next_safe_action":
            "INDEPENDENT_AUDIT_AND_FREEZE_PREOUTCOME_CANDIDATE_OUTPUTS_BEFORE_REFERENCE_ASSOCIATION",
    }

    atomic_json(
        EXECUTION_AUDIT,
        execution,
    )

    print(
        json.dumps(
            {
                **execution,

                "execution_audit_sha256":
                    sha256(
                        EXECUTION_AUDIT
                    ),
            },
            indent=2,
            sort_keys=True,
        )
    )


def main() -> int:

    parser = argparse.ArgumentParser()

    group = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    group.add_argument(
        "--offline-validate",
        action="store_true",
    )

    group.add_argument(
        "--execute-or-resume",
        action="store_true",
    )

    parser.add_argument(
        "--authorization",
        default="",
    )

    args = parser.parse_args()

    if args.offline_validate:

        offline_validate()
        return 0

    if args.execute_or_resume:

        execute_or_resume(
            args.authorization
        )

        return 0

    raise AuthorizationError(
        "unreachable command state"
    )


if __name__ == "__main__":

    raise SystemExit(
        main()
    )
