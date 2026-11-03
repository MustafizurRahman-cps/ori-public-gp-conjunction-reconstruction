#!/usr/bin/env python3
"""Authorization wrapper for frozen G6 48 h replay-input preparation.

The scientific implementation is immutable and imported by frozen SHA-256.

--offline-validate:
    No raw GP_HISTORY reads, no propagation, no protected outcomes.

--execute-once:
    Requires the dedicated authorization tag and exact authorization token.
    Executes only the already-frozen helper sequence.
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
    "138ab478495b7ef106bd0f3078fe92a572758099"
)

PARENT_TAG = (
    "g6-48h-replay-input-implementation-frozen"
)

EXECUTION_TAG = (
    "g6-48h-replay-input-preparation-authorized"
)

AUTHORIZATION_TOKEN = (
    "DR-0024_G6_48H_REPLAY_INPUT_PREPARATION"
)

TARGET = (
    ROOT / "scripts/g6/prepare_g6_48h_replay_inputs.py"
)

TARGET_VALIDATION = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Implementation_Offline_Validation.json"
)

TARGET_REVIEW = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Implementation_Independent_Review.json"
)

EXPECTED_TARGET_SHA = (
    "87ed8e4139d6d8de9324d621acfc2d283ffccb716fe57914814f5890a90bfc3f"
)

EXPECTED_TARGET_VALIDATION_SHA = (
    "7e09cb9f5e95efc804af1ea170a1b6efb647b79ab485a99ed991cf4f736dafb6"
)

EXPECTED_TARGET_REVIEW_SHA = (
    "206615099f14623a4331dc0a4a4475909dcfd6dd4c0c3ea9ea18dfa8586aa672"
)

OFFLINE_VALIDATION = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Authorization_Launcher_Offline_Validation.json"
)

EXECUTION_AUDIT = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Preparation_Execution_Audit.json"
)


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


def git_value(*args: str) -> str:
    return git(*args).stdout.strip()


def atomic_json(
    path: Path,
    payload: dict,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    temp = Path(name)

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

            stream.write("\n")
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

    for path, digest in expected.items():

        if not path.is_file():
            raise AuthorizationError(
                f"missing frozen dependency: {path}"
            )

        actual = sha256(path)

        if actual != digest:
            raise AuthorizationError(
                "frozen dependency SHA mismatch\n"
                f"path={path}\n"
                f"actual={actual}\n"
                f"expected={digest}"
            )


def verify_parent_boundary() -> None:

    if git_value(
        "rev-parse",
        "HEAD",
    ) != PARENT_COMMIT:
        raise AuthorizationError(
            "offline-validation HEAD is not DR-0023 freeze commit"
        )

    if git_value(
        "rev-parse",
        f"refs/tags/{PARENT_TAG}^{{commit}}",
    ) != PARENT_COMMIT:
        raise AuthorizationError(
            "DR-0023 freeze tag changed"
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
            "execution authorization tag is absent"
        )

    tag_target = (
        tag_result.stdout.strip()
    )

    if tag_target != head:
        raise AuthorizationError(
            "execution authorization tag does not target current HEAD"
        )

    parent = git_value(
        "rev-parse",
        "HEAD^",
    )

    if parent != PARENT_COMMIT:
        raise AuthorizationError(
            "authorization commit is not a direct child of DR-0023 freeze"
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
        "g6_frozen_replay_input_implementation",
        TARGET,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise AuthorizationError(
            "could not construct frozen implementation module spec"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    sys.modules[
        spec.name
    ] = module

    spec.loader.exec_module(
        module
    )

    return module


def verify_cache_ignored(
    cache_path: Path,
) -> None:

    relative = cache_path.relative_to(
        ROOT
    ).as_posix()

    result = git(
        "check-ignore",
        "-v",
        relative,
        check=False,
    )

    if result.returncode != 0:
        raise AuthorizationError(
            "local selected-TLE cache is not Git-ignored"
        )


def offline_validate() -> None:

    if OFFLINE_VALIDATION.exists():
        raise AuthorizationError(
            "launcher offline-validation artifact already exists"
        )

    verify_parent_boundary()
    verify_frozen_dependencies()

    target = import_target()

    if target.LSTAR_HOURS != 48:
        raise AuthorizationError(
            "frozen target LSTAR_HOURS changed"
        )

    # Deliberately DO NOT call target.verify_repository_boundary().
    # It belongs to the earlier pre-DR-0023 development boundary.
    verify_cache_ignored(
        target.LOCAL_CACHE
    )

    result = {
        "status":
            "G6_48H_REPLAY_INPUT_AUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS",

        "network_requests":
            0,

        "SpaceTrack_raw_files_opened":
            0,

        "TraCSS_reference_rows_opened":
            0,

        "protected_outcome_access":
            False,

        "propagation_performed":
            False,

        "reconstruction_performed":
            False,

        "replay_input_preparation_executed":
            False,

        "selected_L_star_hours":
            48,

        "frozen_target_sha256":
            EXPECTED_TARGET_SHA,

        "frozen_target_validation_sha256":
            EXPECTED_TARGET_VALIDATION_SHA,

        "frozen_target_review_sha256":
            EXPECTED_TARGET_REVIEW_SHA,

        "frozen_target_internal_boundary_checker_called":
            False,

        "launcher_boundary_strategy":
            "DR0023_PARENT_FOR_OFFLINE_VALIDATION; "
            "DR0024_AUTHORIZATION_TAG_FOR_EXECUTION",

        "execution_tag_required":
            EXECUTION_TAG,

        "authorization_token_required":
            AUTHORIZATION_TOKEN,

        "local_TLE_cache_gitignored":
            True,

        "execution_authorized_in_this_mode":
            False,

        "next_safe_action":
            "INDEPENDENT_REVIEW_AND_FREEZE_LAUNCHER",
    }

    result[
        "launcher_sha256"
    ] = sha256(
        Path(__file__)
    )

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


def validate_selected_tles(
    replay_selected,
    replay_by_object,
) -> int:

    verified = 0

    for object_id in replay_by_object:

        record = replay_selected.get(
            object_id
        )

        if record is None:
            raise AuthorizationError(
                "replay-selected GP missing for executable object "
                + object_id
            )

        if not record.gp_id:
            raise AuthorizationError(
                "selected executable GP lacks GP_ID for "
                + object_id
            )

        if (
            not record.tle_line1
            or not record.tle_line2
        ):
            raise AuthorizationError(
                "selected executable GP lacks TLE lines for "
                + object_id
            )

        if (
            not record.tle_line1.startswith(
                "1 "
            )
            or not record.tle_line2.startswith(
                "2 "
            )
        ):
            raise AuthorizationError(
                "selected executable GP has malformed TLE line prefixes for "
                + object_id
            )

        verified += 1

    return verified


def execute_once(
    authorization: str,
) -> None:

    if authorization != AUTHORIZATION_TOKEN:
        raise AuthorizationError(
            "explicit DR-0024 authorization token is required"
        )

    authorization_commit = (
        verify_execution_boundary()
    )

    verify_frozen_dependencies()

    target = import_target()

    # The wrapper owns the post-DR-0023 repository authorization boundary.
    # Do not call target.verify_repository_boundary().
    target.verify_small_inputs()
    target.verify_environment()
    target.validate_frozen_metadata()

    for path in (
        target.LOCAL_CACHE,
        target.SELECTION_OUT,
        target.SUMMARY_OUT,
        EXECUTION_AUDIT,
    ):
        if path.exists():
            raise AuthorizationError(
                "refusing to overwrite existing preparation output: "
                + str(path)
            )

    verify_cache_ignored(
        target.LOCAL_CACHE
    )

    (
        sample_rows,
        manifest_by_object,
        event_by_object,
    ) = target.load_execution_inputs()

    first_pass = (
        target.stream_main_first_pass(
            manifest_by_object,
            event_by_object,
        )
    )

    queried_files_verified = (
        target.add_queried_predecessors(
            manifest_by_object,
            first_pass[
                "predecessor_records"
            ],
        )
    )

    (
        event_replay,
        replay_by_object,
        availability,
    ) = target.derive_cutoff_and_replay_times(
        sample_rows,
        event_by_object,
        first_pass[
            "cutoff_selected"
        ],
        first_pass[
            "predecessor_records"
        ],
    )

    (
        replay_selected,
        second_pass_files,
    ) = target.stream_main_reselection_pass(
        manifest_by_object,
        event_by_object,
        replay_by_object,
    )

    target.finalize_reselection(
        replay_selected,
        replay_by_object,
        first_pass[
            "predecessor_records"
        ],
    )

    selected_tles_verified = (
        validate_selected_tles(
            replay_selected,
            replay_by_object,
        )
    )

    target.write_prepared_outputs(
        sample_rows,
        manifest_by_object,
        event_by_object,
        first_pass[
            "cutoff_selected"
        ],
        replay_selected,
        event_replay,
        first_pass,
        queried_files_verified,
        second_pass_files,
    )

    if not (
        target.LOCAL_CACHE.is_file()
        and target.SELECTION_OUT.is_file()
        and target.SUMMARY_OUT.is_file()
    ):
        raise AuthorizationError(
            "frozen preparation did not create all expected outputs"
        )

    verify_cache_ignored(
        target.LOCAL_CACHE
    )

    summary = json.loads(
        target.SUMMARY_OUT.read_text(
            encoding="utf-8"
        )
    )

    if (
        summary.get(
            "status"
        )
        != "G6_48H_REPLAY_INPUT_PREPARATION_PASS"
    ):
        raise AuthorizationError(
            "frozen preparation summary is not PASS"
        )

    if (
        summary.get(
            "network_requests"
        )
        != 0
        or summary.get(
            "protected_outcome_access"
        )
        is not False
        or summary.get(
            "propagation_performed"
        )
        is not False
        or summary.get(
            "reconstruction_performed"
        )
        is not False
    ):
        raise AuthorizationError(
            "pre-outcome preparation controls failed"
        )

    if (
        summary.get(
            "main_raw_files_sha256_verified"
        )
        != 1386
    ):
        raise AuthorizationError(
            "not all 1386 main raw files were SHA-verified"
        )

    if (
        summary.get(
            "queried_predecessor_files_sha256_verified"
        )
        != 5032
    ):
        raise AuthorizationError(
            "not all 5032 queried predecessor files were SHA-verified"
        )

    if (
        summary.get(
            "identity_reselection_verified_objects"
        )
        != selected_tles_verified
    ):
        raise AuthorizationError(
            "selected-TLE count differs from identity-verified count"
        )

    with target.SELECTION_OUT.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        header = next(
            csv.reader(
                stream
            )
        )

    if (
        "TLE_LINE1" in header
        or "TLE_LINE2" in header
    ):
        raise AuthorizationError(
            "tracked selection manifest unexpectedly contains TLE lines"
        )

    cache_rows = 0

    with target.LOCAL_CACHE.open(
        "r",
        encoding="utf-8",
    ) as stream:

        for line in stream:
            if line.strip():
                cache_rows += 1

    if cache_rows != selected_tles_verified:
        raise AuthorizationError(
            "local selected-TLE cache row count mismatch"
        )

    result = {
        "status":
            "G6_48H_REPLAY_INPUT_PREPARATION_EXECUTION_PASS",

        "authorization_commit":
            authorization_commit,

        "authorization_tag":
            EXECUTION_TAG,

        "authorization_token":
            AUTHORIZATION_TOKEN,

        "frozen_implementation_sha256":
            EXPECTED_TARGET_SHA,

        "network_requests":
            0,

        "protected_outcome_access":
            False,

        "propagation_performed":
            False,

        "reconstruction_performed":
            False,

        "main_raw_files_sha256_verified":
            summary[
                "main_raw_files_sha256_verified"
            ],

        "main_raw_bytes_sha256_verified":
            summary[
                "main_raw_bytes_sha256_verified"
            ],

        "main_raw_rows_verified":
            summary[
                "main_raw_rows_verified"
            ],

        "queried_predecessor_files_sha256_verified":
            queried_files_verified,

        "main_raw_files_read_in_reselection_pass":
            second_pass_files,

        "availability_counts":
            dict(
                sorted(
                    availability.items()
                )
            ),

        "selected_TLE_rows_verified":
            selected_tles_verified,

        "selected_TLE_cache_rows":
            cache_rows,

        "selection_manifest_sha256":
            sha256(
                target.SELECTION_OUT
            ),

        "preparation_summary_sha256":
            sha256(
                target.SUMMARY_OUT
            ),

        "local_selected_TLE_cache_sha256":
            sha256(
                target.LOCAL_CACHE
            ),

        "local_selected_TLE_cache_committable":
            False,

        "next_safe_action":
            "INDEPENDENT_AUDIT_AND_FREEZE_REPLAY_INPUT_SELECTION_BEFORE_PROPAGATION",
    }

    atomic_json(
        EXECUTION_AUDIT,
        result,
    )

    print(
        json.dumps(
            {
                **result,
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

    group = parser.add_mutually_exclusive_group(
        required=True
    )

    group.add_argument(
        "--offline-validate",
        action="store_true",
    )

    group.add_argument(
        "--execute-once",
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

    if args.execute_once:
        execute_once(
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
