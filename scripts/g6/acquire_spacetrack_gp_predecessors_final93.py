from __future__ import annotations

import argparse
import getpass
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

CONTINUATION = (
    ROOT
    / "scripts/g6/acquire_spacetrack_gp_predecessors_continuation.py"
)

CAMPAIGN = (
    ROOT
    / "scripts/g6/acquire_spacetrack_gp_predecessors_campaign.py"
)

PARENT = (
    ROOT
    / "scripts/g6/acquire_spacetrack_gp_predecessors.py"
)

STATE = (
    ROOT
    / "data/raw/g6/G6_SpaceTrack_Predecessor_Acquisition_State.json"
)

VALIDATION = (
    ROOT
    / "results/g6/G6_Predecessor_Final93_Offline_Validation.json"
)

CONTINUATION_SHA256 = (
    "04d94f7a389bee2a25ff55ca1fbd0afcb40cc05ad6cc0e2c9419bd5d9b62f917"
)

CAMPAIGN_SHA256 = (
    "91bd7278400c34904d92bb3e50341de4ddbe270764f58a17c93dcaccf2c270e2"
)

PARENT_SHA256 = (
    "e4251b33d4f264fa3e2e6c413682209e9d55bf36c998ac72d55f3ed2a778187a"
)

RECOVERY_TAG = (
    "g6-predecessor-6254-recovery-approved"
)

RECOVERY_COMMIT = (
    "72944998169cf837cf1499e3dfce1d9c8c0e94a1"
)

FINAL_APPROVAL_TAG = (
    "g6-predecessor-final93-approved"
)

EXPECTED_STATE_SHA256 = (
    "c9c4876f1e2957823b3d11c03d5ab1c7b078b5e5c2aabf64ca1f7243b4772b23"
)

EXPECTED_COMPLETED = 6254
EXPECTED_REMAINING = 93
EXPECTED_TOTAL_STATE = 6254

EXPECTED_SEQUENCE_START = 6255
EXPECTED_SEQUENCE_END = 6347
EXPECTED_REQUEST_COUNT = 93

EXPECTED_REQUEST_DIGEST = (
    "8a514aebb1afc7b83aaa7bc857e483c88215522b875a450e9ebe8ed90d83b1d3"
)


def sha_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def git(*args: str, check: bool = True):
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def git_value(*args: str) -> str:
    return git(*args).stdout.strip()


def tracked_tree_clean() -> bool:
    return not bool(
        git_value(
            "status",
            "--porcelain",
            "--untracked-files=no",
        )
    )


def tag_target(tag: str) -> str | None:
    result = git(
        "rev-parse",
        "--verify",
        f"refs/tags/{tag}^{{commit}}",
        check=False,
    )

    if result.returncode != 0:
        return None

    return result.stdout.strip()


def run_audit() -> dict:
    result = subprocess.run(
        [
            sys.executable,
            str(CONTINUATION),
            "--audit-progress",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, flush=True)

        if result.stderr:
            print(
                result.stderr,
                file=sys.stderr,
                flush=True,
            )

        raise RuntimeError(
            "continuation audit returned "
            f"{result.returncode}"
        )

    payload = json.loads(
        result.stdout
    )

    if (
        payload.get("network_requests_during_audit")
        != 0
    ):
        raise RuntimeError(
            "audit unexpectedly used network"
        )

    if (
        payload.get("protected_outcome_access")
        is not False
    ):
        raise RuntimeError(
            "protected-outcome boundary failed"
        )

    return payload


def verify_static_boundary() -> dict:
    if sha_file(CONTINUATION) != CONTINUATION_SHA256:
        raise RuntimeError(
            "continuation script SHA mismatch"
        )

    if sha_file(CAMPAIGN) != CAMPAIGN_SHA256:
        raise RuntimeError(
            "campaign wrapper SHA mismatch"
        )

    if sha_file(PARENT) != PARENT_SHA256:
        raise RuntimeError(
            "parent predecessor client SHA mismatch"
        )

    if (
        tag_target(RECOVERY_TAG)
        != RECOVERY_COMMIT
    ):
        raise RuntimeError(
            "DR-0019 recovery tag changed"
        )

    if not tracked_tree_clean():
        raise RuntimeError(
            "tracked Git tree is not clean"
        )

    if sha_file(STATE) != EXPECTED_STATE_SHA256:
        raise RuntimeError(
            "pre-final state SHA mismatch"
        )

    audit = run_audit()

    expected = {
        "status":
            "CAMPAIGN_PROGRESS_PASS",
        "blocker":
            None,
        "completed_dedicated_requests":
            EXPECTED_COMPLETED,
        "remaining_dedicated_requests":
            EXPECTED_REMAINING,
        "total_state_records":
            EXPECTED_TOTAL_STATE,
        "next_invocation_request_count":
            EXPECTED_REQUEST_COUNT,
        "next_invocation_request_digest":
            EXPECTED_REQUEST_DIGEST,
        "next_invocation_sequences":
            [
                EXPECTED_SEQUENCE_START,
                EXPECTED_SEQUENCE_END,
            ],
        "state_sha256":
            EXPECTED_STATE_SHA256,
    }

    for key, value in expected.items():
        if audit.get(key) != value:
            raise RuntimeError(
                f"final-93 boundary mismatch for "
                f"{key}: {audit.get(key)!r}"
            )

    return audit


def load_continuation():
    spec = importlib.util.spec_from_file_location(
        "g6_final93_continuation",
        CONTINUATION,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            "cannot import continuation launcher"
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    return module


CONT = load_continuation()


def verify_runtime_authorization() -> None:
    verify_static_boundary()

    target = tag_target(
        FINAL_APPROVAL_TAG
    )

    if target is None:
        raise RuntimeError(
            "final-93 approval tag is absent"
        )

    head = git_value(
        "rev-parse",
        "HEAD",
    )

    if target != head:
        raise RuntimeError(
            "final-93 approval tag does not "
            "target current HEAD"
        )


def offline_validate() -> int:
    audit = verify_static_boundary()

    approval_target = tag_target(
        FINAL_APPROVAL_TAG
    )

    result = {
        "status":
            "PASS",
        "network_requests":
            0,
        "protected_outcome_access":
            False,
        "automatic_retry":
            False,
        "parallel_requests":
            False,
        "maximum_authorized_campaign_invocations":
            1,
        "maximum_authorized_data_requests":
            93,
        "minimum_request_start_spacing_seconds":
            15.0,
        "pre_final_state_sha256":
            EXPECTED_STATE_SHA256,
        "completed_before_final":
            EXPECTED_COMPLETED,
        "remaining_before_final":
            EXPECTED_REMAINING,
        "final_sequence_start":
            EXPECTED_SEQUENCE_START,
        "final_sequence_end":
            EXPECTED_SEQUENCE_END,
        "final_request_count":
            EXPECTED_REQUEST_COUNT,
        "final_request_digest":
            EXPECTED_REQUEST_DIGEST,
        "recovery_tag":
            RECOVERY_TAG,
        "recovery_tag_target":
            RECOVERY_COMMIT,
        "final_approval_tag_required":
            FINAL_APPROVAL_TAG,
        "final_approval_tag_present":
            approval_target is not None,
        "final_approval_tag_target":
            approval_target,
        "runtime_authorization_state":
            (
                "ACTIVE"
                if approval_target is not None
                else "INACTIVE_TAG_ABSENT"
            ),
        "network_requests_during_audit":
            audit[
                "network_requests_during_audit"
            ],
    }

    VALIDATION.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    VALIDATION.write_text(
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
        )
    )

    return 0


def acquire_final() -> int:
    verify_runtime_authorization()

    username = input(
        "Space-Track username/email: "
    ).strip()

    if not username:
        raise RuntimeError(
            "empty Space-Track username"
        )

    password = getpass.getpass(
        "Space-Track password: "
    )

    if not password:
        raise RuntimeError(
            "empty Space-Track password"
        )

    def credentials():
        return username, password

    print(
        json.dumps(
            {
                "status":
                    "FINAL93_INVOCATION_AUTHORIZED",
                "request_sequence_start":
                    EXPECTED_SEQUENCE_START,
                "request_sequence_end":
                    EXPECTED_SEQUENCE_END,
                "request_count":
                    EXPECTED_REQUEST_COUNT,
                "request_digest":
                    EXPECTED_REQUEST_DIGEST,
                "automatic_retry":
                    False,
                "parallel_requests":
                    False,
                "protected_outcome_access":
                    False,
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )

    try:
        rc = CONT.CAMPAIGN.run_next(
            authorization_fn=
                verify_runtime_authorization,
            credentials_fn=
                credentials,
        )
    except Exception as exc:
        print(
            f"FINAL93_EXCEPTION = "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        try:
            print(
                json.dumps(
                    {
                        "POST_ATTEMPT_AUDIT":
                            run_audit()
                    },
                    indent=2,
                    sort_keys=True,
                ),
                flush=True,
            )
        except Exception as audit_exc:
            print(
                f"POST_ATTEMPT_AUDIT_EXCEPTION = "
                f"{type(audit_exc).__name__}: "
                f"{audit_exc}",
                flush=True,
            )

        return 2

    audit = run_audit()

    print(
        json.dumps(
            {
                "POST_ATTEMPT_AUDIT":
                    audit
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )

    if rc != 0:
        return rc

    if audit.get("blocker") is not None:
        return 2

    if (
        audit.get(
            "completed_dedicated_requests"
        )
        != 6347
    ):
        return 2

    if (
        audit.get(
            "remaining_dedicated_requests"
        )
        != 0
    ):
        return 2

    if (
        audit.get(
            "total_state_records"
        )
        != 6347
    ):
        return 2

    print(
        "FINAL93_ACQUISITION = "
        "COMPLETE_AUDITED_PASS",
        flush=True,
    )

    return 0


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
        "--acquire-final",
        action="store_true",
    )

    args = parser.parse_args()

    if args.offline_validate:
        return offline_validate()

    return acquire_final()


if __name__ == "__main__":
    raise SystemExit(main())
