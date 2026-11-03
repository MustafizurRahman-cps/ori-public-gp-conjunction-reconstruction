#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

CAMPAIGN_PATH = (
    ROOT
    / "scripts/g6/acquire_spacetrack_gp_predecessors_campaign.py"
)

EXPECTED_CAMPAIGN_SHA256 = (
    "91bd7278400c34904d92bb3e50341de4ddbe270764f58a17c93dcaccf2c270e2"
)

CAMPAIGN_VALIDATION = (
    ROOT
    / "results/g6/G6_Predecessor_Campaign_Offline_Validation.json"
)

EXPECTED_CAMPAIGN_VALIDATION_SHA256 = (
    "8fcdf4e508abe530384644e5ebf9a25a529ec21ca37cd8b19959c83d498eeb91"
)

RECOVERY_CLOSURE_COMMIT = (
    "1d4aa61fe552c753072d30881c281ce9ab8b4a1a"
)

RECOVERY_CLOSURE_AUDIT = (
    ROOT
    / "results/g6/G6_Predecessor_987_Recovery_Closure_Audit.json"
)

EXPECTED_RECOVERY_CLOSURE_AUDIT_SHA256 = (
    "68066504cb48bc777bce19cb539afe3384316b0f94f64511c28406497da135c6"
)

RECOVERY_CLOSURE_REPORT = (
    ROOT
    / "docs/research/G6_Predecessor_987_Recovery_Closure_Report.md"
)

EXPECTED_RECOVERY_CLOSURE_REPORT_SHA256 = (
    "14821305452b0ec1835b25c4d3d3e75bc0fab06ce095acad0c4a04c3c7ab0cac"
)

HISTORICAL_CAMPAIGN_TAG = (
    "g6-predecessor-campaign-approved"
)

EXPECTED_HISTORICAL_CAMPAIGN_TAG_TARGET = (
    "a520a8f85d5a7108e37d2f934c62010164f4e8e6"
)

CONTINUATION_APPROVAL_TAG = (
    "g6-predecessor-campaign-continuation-approved"
)

CONTINUATION_START_SEQUENCE = 988
CAMPAIGN_END_SEQUENCE = 6347
MAX_NEW_REQUESTS_PER_INVOCATION = 500

EXPECTED_INITIAL_STATE_SHA256 = (
    "cededc8535aaa533fbb8c42594a2e3d59b007376b2882ebd87331e9c4c21bd4c"
)

EXPECTED_CAMPAIGN_UNIVERSE_DIGEST = (
    "27a7954974ce58cb5955e298a30f6e16c743e17b0a149b15b0f096e323d6b5ae"
)

EXPECTED_CLOSED_PREFIX_SHA256 = (
    "72ce90115b9353d9cd83144c57c7402ec911c9852d6476971e498870955c7a28"
)

EXPECTED_INITIAL_NEXT_DIGEST = (
    "aba7d16068b60b88be5e6efa6a9ed397755c9e3c473329536673ead4eef35c5c"
)

OFFLINE_VALIDATION = (
    ROOT
    / "results/g6/G6_Predecessor_Campaign_Continuation_Offline_Validation.json"
)


def sha_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def git(
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def tag_target(
    tag: str,
) -> str | None:
    result = git(
        "rev-parse",
        "--verify",
        f"refs/tags/{tag}^{{commit}}",
        check=False,
    )

    if result.returncode != 0:
        return None

    return result.stdout.strip()


def tracked_tree_clean() -> bool:
    return not git(
        "status",
        "--porcelain",
        "--untracked-files=no",
    ).stdout.strip()


def atomic_json(
    path: Path,
    payload: dict,
) -> None:
    if path.exists():
        raise RuntimeError(
            f"refusing to overwrite validation artifact: {path}"
        )

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
        ) as f:
            json.dump(
                payload,
                f,
                indent=2,
                sort_keys=True,
            )

            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        os.replace(
            temp,
            path,
        )

    finally:
        temp.unlink(
            missing_ok=True
        )


def load_module():
    if (
        sha_file(CAMPAIGN_PATH)
        != EXPECTED_CAMPAIGN_SHA256
    ):
        raise RuntimeError(
            "frozen campaign-wrapper SHA mismatch"
        )

    name = "g6_campaign_continuation_parent"

    spec = importlib.util.spec_from_file_location(
        name,
        CAMPAIGN_PATH,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            "cannot import frozen campaign wrapper"
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    return module


CAMPAIGN = load_module()


def verify_static_boundary() -> None:
    if (
        sha_file(CAMPAIGN_VALIDATION)
        != EXPECTED_CAMPAIGN_VALIDATION_SHA256
    ):
        raise RuntimeError(
            "frozen campaign validation SHA mismatch"
        )

    if (
        sha_file(RECOVERY_CLOSURE_AUDIT)
        != EXPECTED_RECOVERY_CLOSURE_AUDIT_SHA256
    ):
        raise RuntimeError(
            "recovery closure audit SHA mismatch"
        )

    if (
        sha_file(RECOVERY_CLOSURE_REPORT)
        != EXPECTED_RECOVERY_CLOSURE_REPORT_SHA256
    ):
        raise RuntimeError(
            "recovery closure report SHA mismatch"
        )

    if (
        tag_target(HISTORICAL_CAMPAIGN_TAG)
        != EXPECTED_HISTORICAL_CAMPAIGN_TAG_TARGET
    ):
        raise RuntimeError(
            "historical DR-0016 campaign tag changed"
        )

    ancestor = git(
        "merge-base",
        "--is-ancestor",
        RECOVERY_CLOSURE_COMMIT,
        "HEAD",
        check=False,
    )

    if ancestor.returncode != 0:
        raise RuntimeError(
            "sequence-987 recovery closure is not an ancestor"
        )

    frozen = (
        CAMPAIGN._load_frozen_campaign_validation()
    )

    if (
        frozen.get(
            "campaign_universe_request_digest"
        )
        != EXPECTED_CAMPAIGN_UNIVERSE_DIGEST
    ):
        raise RuntimeError(
            "campaign-universe digest changed"
        )

    if (
        frozen.get(
            "closed_prefix_records_sha256"
        )
        != EXPECTED_CLOSED_PREFIX_SHA256
    ):
        raise RuntimeError(
            "closed 1-128 prefix fingerprint changed"
        )


def authorization_snapshot() -> dict:
    head = git(
        "rev-parse",
        "HEAD",
    ).stdout.strip()

    target = tag_target(
        CONTINUATION_APPROVAL_TAG
    )

    clean = tracked_tree_clean()

    if target is None:
        state = "INACTIVE_TAG_ABSENT"

    elif target != head:
        raise CAMPAIGN.PARENT.AuthorizationError(
            f"{CONTINUATION_APPROVAL_TAG} resolves to "
            f"{target}; current HEAD is {head}"
        )

    elif not clean:
        raise CAMPAIGN.PARENT.AuthorizationError(
            "tracked working tree is not clean"
        )

    else:
        state = "ACTIVE"

    return {
        "approval_tag_present":
            target is not None,
        "approval_tag_target":
            target,
        "approval_tag_matches_head":
            (
                target == head
                if target is not None
                else False
            ),
        "tracked_tree_clean":
            clean,
        "network_authorization_state":
            state,
    }


def load_continuation_validation() -> dict:
    if not OFFLINE_VALIDATION.is_file():
        raise RuntimeError(
            "continuation offline-validation artifact missing"
        )

    with OFFLINE_VALIDATION.open(
        "r",
        encoding="utf-8",
    ) as f:
        frozen = json.load(f)

    if frozen.get("status") != "PASS":
        raise RuntimeError(
            "continuation validation is not PASS"
        )

    if (
        frozen.get(
            "initial_state_sha256"
        )
        != EXPECTED_INITIAL_STATE_SHA256
    ):
        raise RuntimeError(
            "continuation initial-state SHA changed"
        )

    if (
        frozen.get(
            "initial_next_invocation_digest"
        )
        != EXPECTED_INITIAL_NEXT_DIGEST
    ):
        raise RuntimeError(
            "continuation initial block digest changed"
        )

    return frozen


def verify_continuation_authorization() -> None:
    verify_static_boundary()

    snap = authorization_snapshot()

    if (
        snap["network_authorization_state"]
        != "ACTIVE"
    ):
        raise CAMPAIGN.PARENT.AuthorizationError(
            "continuation network acquisition is unauthorized"
        )

    load_continuation_validation()


def progress() -> dict:
    verify_static_boundary()

    return CAMPAIGN._validate_progress(
        require_frozen_validation=True,
        verify_raw=True,
    )


def next_digest(
    p: dict,
) -> str | None:
    specs = p["next_specs"]

    if not specs:
        return None

    return CAMPAIGN._request_digest(
        specs
    )


def offline_tests() -> dict:
    calls = {
        "authorization": 0,
        "credentials": 0,
        "authentication": 0,
        "network": 0,
    }

    def denied():
        calls["authorization"] += 1

        raise CAMPAIGN.PARENT.AuthorizationError(
            "synthetic continuation authorization denied"
        )

    def credentials():
        calls["credentials"] += 1
        return "x", "y"

    def authenticate(*_args):
        calls["authentication"] += 1
        raise AssertionError(
            "authentication must not occur"
        )

    def stream(*_args):
        calls["network"] += 1
        raise AssertionError(
            "network must not occur"
        )

    try:
        CAMPAIGN.run_next(
            authorization_fn=denied,
            credentials_fn=credentials,
            authenticate_fn=authenticate,
            stream_fn=stream,
        )

        raise RuntimeError(
            "authorization-first regression"
        )

    except CAMPAIGN.PARENT.AuthorizationError:
        pass

    if calls != {
        "authorization": 1,
        "credentials": 0,
        "authentication": 0,
        "network": 0,
    }:
        raise RuntimeError(
            f"authorization-first guard failed: {calls}"
        )

    return {
        "authorization_precedes_credentials_and_network":
            "PASS",
        "frozen_campaign_wrapper_sha":
            "PASS",
        "historical_campaign_tag_preserved":
            "PASS",
        "recovery_closure_frozen":
            "PASS",
        "max_500_new_requests_per_invocation":
            "PASS",
    }


def offline_validate() -> dict:
    snap = authorization_snapshot()
    p = progress()
    digest = next_digest(p)

    if not snap["approval_tag_present"]:
        if (
            p["state_sha256"]
            != EXPECTED_INITIAL_STATE_SHA256
        ):
            raise RuntimeError(
                "preauthorization continuation state SHA mismatch"
            )

        if (
            p["completed_dedicated_requests"]
            != 987
        ):
            raise RuntimeError(
                "continuation must begin at 987 completed requests"
            )

        if (
            p["remaining_dedicated_requests"]
            != 5360
        ):
            raise RuntimeError(
                "continuation remaining-request count mismatch"
            )

        if p["blocker"] is not None:
            raise RuntimeError(
                "continuation cannot begin with a blocker"
            )

        specs = p["next_specs"]

        if len(specs) != 500:
            raise RuntimeError(
                "initial continuation block is not 500 requests"
            )

        if [
            specs[0].request_sequence,
            specs[-1].request_sequence,
        ] != [988, 1487]:
            raise RuntimeError(
                "initial continuation sequences are not 988-1487"
            )

        if digest != EXPECTED_INITIAL_NEXT_DIGEST:
            raise RuntimeError(
                "initial continuation request digest mismatch"
            )

        tests = offline_tests()

        payload = {
            "status":
                "PASS",
            "continuation_start_sequence":
                CONTINUATION_START_SEQUENCE,
            "campaign_end_sequence":
                CAMPAIGN_END_SEQUENCE,
            "initial_completed_dedicated_requests":
                987,
            "initial_remaining_dedicated_requests":
                5360,
            "initial_state_sha256":
                EXPECTED_INITIAL_STATE_SHA256,
            "initial_next_invocation_request_count":
                500,
            "initial_next_invocation_sequences":
                [988, 1487],
            "initial_next_invocation_digest":
                EXPECTED_INITIAL_NEXT_DIGEST,
            "campaign_universe_request_digest":
                EXPECTED_CAMPAIGN_UNIVERSE_DIGEST,
            "closed_prefix_records_sha256":
                EXPECTED_CLOSED_PREFIX_SHA256,
            "max_new_requests_per_invocation":
                MAX_NEW_REQUESTS_PER_INVOCATION,
            "automatic_retry":
                False,
            "failure_or_hold_blocks_future_invocations":
                True,
            "historical_campaign_tag_target":
                EXPECTED_HISTORICAL_CAMPAIGN_TAG_TARGET,
            "recovery_closure_commit":
                RECOVERY_CLOSURE_COMMIT,
            "approval_tag_required":
                CONTINUATION_APPROVAL_TAG,
            "approval_tag_present":
                False,
            "network_authorization_state":
                "INACTIVE_TAG_ABSENT",
            "network_requests":
                0,
            "protected_outcome_access":
                False,
            "offline_tests":
                tests,
            "offline_validation_artifact_write":
                "WRITTEN_PREAUTHORIZATION",
        }

        atomic_json(
            OFFLINE_VALIDATION,
            payload,
        )

        return payload

    frozen = load_continuation_validation()

    return {
        **frozen,
        **snap,
        "continuation_acquisition_authorized":
            (
                snap[
                    "network_authorization_state"
                ]
                == "ACTIVE"
            ),
        "current_state_sha256":
            p["state_sha256"],
        "current_completed_dedicated_requests":
            p["completed_dedicated_requests"],
        "current_remaining_dedicated_requests":
            p["remaining_dedicated_requests"],
        "current_blocker":
            p["blocker"],
        "campaign_completed":
            p["campaign_completed"],
        "next_invocation_request_count":
            len(p["next_specs"]),
        "next_invocation_sequences":
            (
                [
                    p["next_specs"][0].request_sequence,
                    p["next_specs"][-1].request_sequence,
                ]
                if p["next_specs"]
                else []
            ),
        "next_invocation_request_digest":
            digest,
        "network_requests":
            0,
        "protected_outcome_access":
            False,
        "offline_validation_artifact_write":
            (
                "SKIPPED_TAGGED_RUNTIME_TO_"
                "PRESERVE_CLEAN_TRACKED_TREE"
            ),
    }


def acquire_next() -> int:
    verify_static_boundary()

    return CAMPAIGN.run_next(
        authorization_fn=
            verify_continuation_authorization
    )


def audit_progress() -> int:
    verify_static_boundary()

    return CAMPAIGN.audit_progress()


def main() -> int:
    parser = argparse.ArgumentParser()

    modes = parser.add_mutually_exclusive_group(
        required=True
    )

    modes.add_argument(
        "--offline-validate",
        action="store_true",
    )

    modes.add_argument(
        "--audit-progress",
        action="store_true",
    )

    modes.add_argument(
        "--acquire-next",
        action="store_true",
    )

    args = parser.parse_args()

    if args.offline_validate:
        result = offline_validate()

        print(
            json.dumps(
                result,
                indent=2,
                sort_keys=True,
            )
        )

        return 0

    if args.audit_progress:
        return audit_progress()

    return acquire_next()


if __name__ == "__main__":
    raise SystemExit(main())
