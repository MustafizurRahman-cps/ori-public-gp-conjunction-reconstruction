#!/usr/bin/env python3
"""Authorization wrapper for frozen G6 reference-TCA association.

--offline-validate
    Verifies DR-0030, immutable implementation/evidence, frozen candidate
    input, and output absence. Performs no real G6 association.

--execute
    Requires the DR-0031 authorization tag and explicit authorization token,
    then invokes only the already-frozen run_association() implementation.

This wrapper does not read physical reference miss distance and does not
compute E_d, E_t, coverage, covariance, Pc, or HBR.
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
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PARENT_COMMIT = (
    "ea0d92d7f1080bcdfb7831adcd41e96f0e70eeff"
)

PARENT_TAG = (
    "g6-reference-tca-association-implementation-frozen"
)

EXECUTION_TAG = (
    "g6-reference-tca-association-authorized"
)

AUTHORIZATION_TOKEN = (
    "DR-0031_G6_REFERENCE_TCA_ASSOCIATION"
)

TARGET = (
    ROOT
    / "scripts/g6/associate_g6_reference_tca.py"
)

TARGET_VALIDATION = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Implementation_Offline_Validation.json"
)

TARGET_REVIEW = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Implementation_Independent_Review.json"
)

CANDIDATES = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidates.csv"
)

ASSOCIATION_OUT = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association.csv"
)

ASSOCIATION_SUMMARY = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Summary.json"
)

EXECUTION_AUDIT = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Execution_Audit.json"
)


EXPECTED_TARGET_SHA = (
    "4bc879120ca6d55769847e3d52e54f00bf3ed74e905112abee0367ffbbee3c79"
)

EXPECTED_TARGET_VALIDATION_SHA = (
    "a629efed90cd8d23d908bc1511c3df315eb21066353db8d2de60c5a6f4df7138"
)

EXPECTED_TARGET_REVIEW_SHA = (
    "88f228b2f27f97c6426411f6729f61702ddcbbd8e43a48f9f93a45d2b34b31f9"
)

EXPECTED_CANDIDATE_SHA = (
    "21b8a9f5c8aab4b3427fad5cd3f3c2823ffc0219bd2a36aeccafc6a0aaa80e36"
)

EXPECTED_CANDIDATE_BYTES = 128418422

EXPECTED_EVENTS = 6927
EXPECTED_READY = 6906
EXPECTED_NONREADY = 21


class AuthorizationError(RuntimeError):
    pass


def sha256(path: Path) -> str:

    h = hashlib.sha256()

    with path.open("rb") as stream:

        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


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

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    tmp = Path(tmp_name)

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
            tmp,
            path,
        )

    finally:

        tmp.unlink(
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

        CANDIDATES:
            EXPECTED_CANDIDATE_SHA,
    }

    for path, expected_sha in expected.items():

        if not path.is_file():
            raise AuthorizationError(
                f"missing frozen dependency: {path}"
            )

        actual = sha256(path)

        if actual != expected_sha:
            raise AuthorizationError(
                "frozen dependency SHA mismatch\n"
                f"path={path}\n"
                f"actual={actual}\n"
                f"expected={expected_sha}"
            )

    if (
        CANDIDATES.stat().st_size
        != EXPECTED_CANDIDATE_BYTES
    ):
        raise AuthorizationError(
            "candidate CSV byte count changed"
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
            "offline-validation HEAD is not DR-0030"
        )

    if (
        git_value(
            "rev-parse",
            f"refs/tags/{PARENT_TAG}^{{commit}}",
        )
        != PARENT_COMMIT
    ):
        raise AuthorizationError(
            "DR-0030 tag target changed"
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

    tag = git(
        "rev-parse",
        f"refs/tags/{EXECUTION_TAG}^{{commit}}",
        check=False,
    )

    if tag.returncode != 0:
        raise AuthorizationError(
            "association authorization tag is absent"
        )

    if tag.stdout.strip() != head:
        raise AuthorizationError(
            "authorization tag does not target current HEAD"
        )

    if (
        git_value(
            "rev-parse",
            "HEAD^",
        )
        != PARENT_COMMIT
    ):
        raise AuthorizationError(
            "authorization commit is not direct child of DR-0030"
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
        "g6_frozen_reference_tca_association",
        TARGET,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise AuthorizationError(
            "could not import frozen association implementation"
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


def verify_outputs_absent() -> None:

    for path in (
        ASSOCIATION_OUT,
        ASSOCIATION_SUMMARY,
        EXECUTION_AUDIT,
    ):

        if path.exists():
            raise AuthorizationError(
                "refusing to overwrite existing association output: "
                + str(path)
            )


def offline_validate() -> None:

    output = (
        ROOT
        / "results/g6/G6_Reference_TCA_Association_Authorization_Launcher_Offline_Validation.json"
    )

    if output.exists():
        raise AuthorizationError(
            "launcher offline-validation artifact already exists"
        )

    verify_parent_boundary()
    verify_frozen_dependencies()
    verify_outputs_absent()

    target = import_target()

    # Verify frozen implementation constants/schema without invoking
    # run_association().
    if (
        float(
            target.ASSOCIATION_TOLERANCE_MIN
        )
        != 5.0
    ):
        raise AuthorizationError(
            "association tolerance changed"
        )

    expected_fields = (
        "final_g6_sample_index",
        "g6_temporal_stratum",
        "conj_id",
        "input_preoutcome_status",
        "association_status",
        "final_status",
        "selected_candidate_index",
    )

    if tuple(
        target.OUTPUT_FIELDS
    ) != expected_fields:
        raise AuthorizationError(
            "association output schema changed"
        )

    result = {
        "status":
            "G6_REFERENCE_TCA_ASSOCIATION_AUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS",

        "launcher_sha256":
            sha256(
                Path(__file__)
            ),

        "frozen_implementation_sha256":
            EXPECTED_TARGET_SHA,

        "frozen_implementation_validation_sha256":
            EXPECTED_TARGET_VALIDATION_SHA,

        "frozen_implementation_review_sha256":
            EXPECTED_TARGET_REVIEW_SHA,

        "candidate_csv_sha256":
            EXPECTED_CANDIDATE_SHA,

        "candidate_csv_byte_count":
            EXPECTED_CANDIDATE_BYTES,

        "events":
            EXPECTED_EVENTS,

        "association_eligible_events":
            EXPECTED_READY,

        "preoutcome_terminal_events":
            EXPECTED_NONREADY,

        "association_tolerance_min":
            5.0,

        "association_execution_performed":
            False,

        "orbital_propagation_performed":
            False,

        "candidate_generation_performed":
            False,

        "reference_miss_distance_access":
            False,

        "TraCSS_physical_outcome_rows_opened":
            0,

        "E_t_computed":
            False,

        "E_d_computed":
            False,

        "coverage_computed":
            False,

        "covariance_access":
            False,

        "Pc_access":
            False,

        "HBR_access":
            False,

        "method_retuning_performed":
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
            "INDEPENDENT_REVIEW_AND_FREEZE_DR0031_ASSOCIATION_AUTHORIZATION_LAYER",
    }

    atomic_json(
        output,
        result,
    )

    print(
        json.dumps(
            {
                **result,

                "offline_validation_sha256":
                    sha256(output),
            },
            indent=2,
            sort_keys=True,
        )
    )


def execute(
    authorization: str,
) -> None:

    if authorization != AUTHORIZATION_TOKEN:
        raise AuthorizationError(
            "explicit DR-0031 authorization token is required"
        )

    authorization_commit = (
        verify_execution_boundary()
    )

    verify_frozen_dependencies()
    verify_outputs_absent()

    target = import_target()

    # The scientific association logic remains owned entirely by
    # the frozen DR-0030 implementation.
    summary = target.run_association()

    if (
        summary.get("status")
        != "G6_REFERENCE_TCA_ASSOCIATION_PASS"
    ):
        raise AuthorizationError(
            "frozen association did not return PASS"
        )

    if (
        summary.get("events")
        != EXPECTED_EVENTS
    ):
        raise AuthorizationError(
            "association event count changed"
        )

    if (
        summary.get(
            "candidate_set_ready_input_events"
        )
        != EXPECTED_READY
    ):
        raise AuthorizationError(
            "association-ready input count changed"
        )

    if (
        summary.get(
            "preoutcome_terminal_events"
        )
        != EXPECTED_NONREADY
    ):
        raise AuthorizationError(
            "preoutcome terminal count changed"
        )

    counts = summary.get(
        "final_status_counts",
        {}
    )

    if (
        int(
            counts.get(
                "PROPAGATION_FAILURE",
                0,
            )
        )
        != 1
        or int(
            counts.get(
                "UNRECOVERED_REFINEMENT_FAILURE",
                0,
            )
        )
        != 20
    ):
        raise AuthorizationError(
            "frozen pre-association failures changed"
        )

    associated = int(
        counts.get(
            "VALID_ASSOCIATED",
            0,
        )
    )

    unassociated = int(
        counts.get(
            "UNASSOCIATED_REFERENCE_EVENT",
            0,
        )
    )

    if (
        associated
        + unassociated
        != EXPECTED_READY
    ):
        raise AuthorizationError(
            "association accounting does not equal 6906 ready events"
        )

    required_false = (
        "reference_miss_distance_read",
        "selected_candidate_distance_exported",
        "selected_candidate_time_exported",
        "signed_TCA_residual_exported",
        "absolute_TCA_error_E_t_computed",
        "miss_distance_error_E_d_computed",
        "coverage_computed",
        "covariance_access",
        "Pc_access",
        "HBR_access",
        "orbital_propagation_performed",
        "candidate_generation_performed",
        "method_retuning_performed",
    )

    for key in required_false:

        if summary.get(key) is not False:
            raise AuthorizationError(
                f"association safety invariant failed: {key}"
            )

    if (
        summary.get(
            "TraCSS_physical_outcome_rows_opened"
        )
        != 0
    ):
        raise AuthorizationError(
            "physical TraCSS outcome rows were opened"
        )

    if summary.get(
        "network_requests"
    ) != 0:
        raise AuthorizationError(
            "network access occurred"
        )

    if not ASSOCIATION_OUT.is_file():
        raise AuthorizationError(
            "association CSV missing"
        )

    if not ASSOCIATION_SUMMARY.is_file():
        raise AuthorizationError(
            "association summary missing"
        )

    # Independent structural census of generated CSV.
    census = Counter()
    rows = 0

    with ASSOCIATION_OUT.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        reader = csv.DictReader(
            stream
        )

        expected_fields = (
            "final_g6_sample_index",
            "g6_temporal_stratum",
            "conj_id",
            "input_preoutcome_status",
            "association_status",
            "final_status",
            "selected_candidate_index",
        )

        if tuple(
            reader.fieldnames or ()
        ) != expected_fields:
            raise AuthorizationError(
                "association CSV schema changed"
            )

        for expected_index, row in enumerate(
            reader,
            start=1,
        ):

            rows += 1

            if int(
                row[
                    "final_g6_sample_index"
                ]
            ) != expected_index:
                raise AuthorizationError(
                    "association CSV is not canonical contiguous order"
                )

            final_status = (
                row[
                    "final_status"
                ].strip()
            )

            census[
                final_status
            ] += 1

            input_status = (
                row[
                    "input_preoutcome_status"
                ].strip()
            )

            association_status = (
                row[
                    "association_status"
                ].strip()
            )

            selected_index = (
                row[
                    "selected_candidate_index"
                ].strip()
            )

            if (
                input_status
                == "CANDIDATE_SET_READY"
            ):

                if final_status not in {
                    "VALID_ASSOCIATED",
                    "UNASSOCIATED_REFERENCE_EVENT",
                }:
                    raise AuthorizationError(
                        "ready event received invalid final status"
                    )

                if association_status != final_status:
                    raise AuthorizationError(
                        "ready event association/final status mismatch"
                    )

                if (
                    final_status
                    == "VALID_ASSOCIATED"
                    and not selected_index
                ):
                    raise AuthorizationError(
                        "associated event lacks selected candidate identity"
                    )

                if (
                    final_status
                    == "UNASSOCIATED_REFERENCE_EVENT"
                    and selected_index
                ):
                    raise AuthorizationError(
                        "unassociated event unexpectedly has selected candidate"
                    )

            else:

                if (
                    association_status
                    != "NOT_APPLICABLE_PREOUTCOME_FAILURE"
                ):
                    raise AuthorizationError(
                        "preoutcome failure entered association"
                    )

                if selected_index:
                    raise AuthorizationError(
                        "preoutcome failure has selected candidate"
                    )

                if final_status != input_status:
                    raise AuthorizationError(
                        "preoutcome terminal status changed"
                    )

    if rows != EXPECTED_EVENTS:
        raise AuthorizationError(
            "association CSV row count changed"
        )

    if dict(
        sorted(
            census.items()
        )
    ) != dict(
        sorted(
            (
                key,
                int(value),
            )
            for key, value
            in counts.items()
        )
    ):
        raise AuthorizationError(
            "association CSV census differs from summary"
        )

    execution = {
        "status":
            "G6_REFERENCE_TCA_ASSOCIATION_EXECUTION_PASS",

        "authorization_commit":
            authorization_commit,

        "authorization_tag":
            EXECUTION_TAG,

        "authorization_token":
            AUTHORIZATION_TOKEN,

        "frozen_implementation_sha256":
            EXPECTED_TARGET_SHA,

        "candidate_csv_sha256":
            EXPECTED_CANDIDATE_SHA,

        "events":
            EXPECTED_EVENTS,

        "association_eligible_events":
            EXPECTED_READY,

        "preoutcome_terminal_events":
            EXPECTED_NONREADY,

        "final_status_counts":
            dict(
                sorted(
                    census.items()
                )
            ),

        "association_output_sha256":
            sha256(
                ASSOCIATION_OUT
            ),

        "association_summary_sha256":
            sha256(
                ASSOCIATION_SUMMARY
            ),

        "reference_miss_distance_access":
            False,

        "TraCSS_physical_outcome_rows_opened":
            0,

        "E_t_computed":
            False,

        "E_d_computed":
            False,

        "coverage_computed":
            False,

        "orbital_propagation_performed":
            False,

        "candidate_generation_performed":
            False,

        "method_retuning_performed":
            False,

        "network_requests":
            0,

        "next_safe_action":
            "INDEPENDENT_AUDIT_AND_FREEZE_REFERENCE_TCA_ASSOCIATION_OUTPUTS_BEFORE_ERROR_METRICS",
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
        "--execute",
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

    if args.execute:
        execute(
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
