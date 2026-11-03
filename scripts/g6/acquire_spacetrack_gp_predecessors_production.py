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

PARENT_PATH = (
    ROOT
    / "scripts/g6/acquire_spacetrack_gp_predecessors.py"
)

EXPECTED_PARENT_SHA256 = (
    "e4251b33d4f264fa3e2e6c413682209e9d55bf36c998ac72d55f3ed2a778187a"
)

FIRST_SCALING_CLOSURE_COMMIT = (
    "8c0a0a8be7b895387c3d4a3fc52d763cf5df2ec1"
)

HISTORICAL_SCALING_TAG = (
    "g6-predecessor-scaling-approved"
)

EXPECTED_HISTORICAL_SCALING_TAG_TARGET = (
    "41457deb50401b234b8faf08d1d8dcf5f0df60f5"
)

PRODUCTION_APPROVAL_TAG = (
    "g6-predecessor-production-approved"
)

PRODUCTION_MAX_NEW_REQUESTS = 100

EXPECTED_STATE_SHA256 = (
    "ffc1ff6f2048bebd26bea3b98dae6d802446a88d2acce075d9159111dc0e0a9b"
)

EXPECTED_FIRST_SCALING_AUDIT_SHA256 = (
    "46ca934ea8fddaebe33ff28a14dc78530db6630f557a5febf795ec076268aa98"
)

EXPECTED_FIRST_SCALING_REPORT_SHA256 = (
    "c2843d10099ad288dadb5de10e5dddfc9f8669474f5f649f0b47c8623f54ec36"
)

EXPECTED_REQUEST_SET_SHA256 = (
    "976628343077b14cb7017493a08757ce93def34b5966f4b87688d6b106e46bee"
)

FIRST_SCALING_AUDIT = (
    ROOT
    / "results/g6/G6_Predecessor_First_Scaling_Audit.json"
)

FIRST_SCALING_REPORT = (
    ROOT
    / "docs/research/G6_Predecessor_First_Scaling_Report.md"
)

OFFLINE_VALIDATION = (
    ROOT
    / "results/g6/G6_Predecessor_Production_Offline_Validation.json"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def _load_parent():
    if (
        _sha256_file(PARENT_PATH)
        != EXPECTED_PARENT_SHA256
    ):
        raise RuntimeError(
            "frozen predecessor parent-client SHA mismatch"
        )

    name = "g6_predecessor_production_parent"

    spec = importlib.util.spec_from_file_location(
        name,
        PARENT_PATH,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            "cannot construct frozen parent module"
        )

    module = importlib.util.module_from_spec(spec)

    # Required for dataclass resolution.
    sys.modules[name] = module

    spec.loader.exec_module(module)

    return module


PARENT = _load_parent()


def _git(
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


def _git_head() -> str:
    return _git(
        "rev-parse",
        "HEAD",
    ).stdout.strip()


def _tag_target(
    tag: str,
) -> str | None:
    result = _git(
        "rev-parse",
        "--verify",
        f"refs/tags/{tag}^{{commit}}",
        check=False,
    )

    if result.returncode != 0:
        return None

    return result.stdout.strip()


def _tracked_tree_clean() -> bool:
    result = _git(
        "status",
        "--porcelain",
        "--untracked-files=no",
    )

    return not result.stdout.strip()


def _is_ancestor(
    ancestor: str,
) -> bool:
    result = _git(
        "merge-base",
        "--is-ancestor",
        ancestor,
        "HEAD",
        check=False,
    )

    return result.returncode == 0


def _evaluate_authorization(
    tag_target: str | None,
    head: str,
    tracked_clean: bool,
) -> str:
    if tag_target is None:
        return "INACTIVE_TAG_ABSENT"

    if tag_target != head:
        raise PARENT.AuthorizationError(
            f"{PRODUCTION_APPROVAL_TAG} resolves to "
            f"{tag_target}, but HEAD is {head}"
        )

    if not tracked_clean:
        raise PARENT.AuthorizationError(
            "tracked working tree is not clean"
        )

    return "ACTIVE"


def _authorization_snapshot() -> dict:
    head = _git_head()

    target = _tag_target(
        PRODUCTION_APPROVAL_TAG
    )

    clean = _tracked_tree_clean()

    state = _evaluate_authorization(
        target,
        head,
        clean,
    )

    return {
        "approval_tag_present":
            target is not None,
        "approval_tag_target":
            target,
        "approval_tag_matches_head":
            target == head
            if target is not None
            else False,
        "tracked_tree_clean":
            clean,
        "network_authorization_state":
            state,
    }


def _verify_authorization() -> None:
    snapshot = _authorization_snapshot()

    if (
        snapshot[
            "network_authorization_state"
        ]
        != "ACTIVE"
    ):
        raise PARENT.AuthorizationError(
            f"{PRODUCTION_APPROVAL_TAG} is absent; "
            "production predecessor acquisition "
            "is unauthorized"
        )


def _atomic_write_json(
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
    os.close(fd)

    try:
        with temp.open(
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
            os.fsync(stream.fileno())

        os.replace(
            temp,
            path,
        )

    finally:
        temp.unlink(
            missing_ok=True
        )


def _verify_frozen_history() -> None:
    if not _is_ancestor(
        FIRST_SCALING_CLOSURE_COMMIT
    ):
        raise RuntimeError(
            "first scaling closure commit "
            "is not an ancestor of HEAD"
        )

    historical_tag = _tag_target(
        HISTORICAL_SCALING_TAG
    )

    if (
        historical_tag
        != EXPECTED_HISTORICAL_SCALING_TAG_TARGET
    ):
        raise RuntimeError(
            "historical DR-0014 tag target changed"
        )

    if (
        _sha256_file(FIRST_SCALING_AUDIT)
        != EXPECTED_FIRST_SCALING_AUDIT_SHA256
    ):
        raise RuntimeError(
            "first scaling audit SHA mismatch"
        )

    if (
        _sha256_file(FIRST_SCALING_REPORT)
        != EXPECTED_FIRST_SCALING_REPORT_SHA256
    ):
        raise RuntimeError(
            "first scaling report SHA mismatch"
        )

    state_path = (
        PARENT.PREDECESSOR_STATE_FILE
    )

    if (
        not state_path.is_file()
        or
        _sha256_file(state_path)
        != EXPECTED_STATE_SHA256
    ):
        raise RuntimeError(
            "post-first-scaling predecessor-state SHA mismatch"
        )


def _load_plan():
    specs, proof = (
        PARENT._load_predecessor_specs()
    )

    state = PARENT._load_state()

    completed = []
    pending = []

    for spec in specs:
        if PARENT._spec_requires_network(
            state,
            spec,
        ):
            pending.append(spec)
        else:
            completed.append(spec)

    planned = pending[
        :PRODUCTION_MAX_NEW_REQUESTS
    ]

    return (
        specs,
        proof,
        state,
        completed,
        pending,
        planned,
    )


def _request_digest(
    specs: list,
) -> str:
    payload = (
        "\n".join(
            spec.request_id
            for spec in specs
        )
        + "\n"
    ).encode("ascii")

    return hashlib.sha256(
        payload
    ).hexdigest()


def _offline_tests(
    planned: list,
) -> dict:
    tests = {}

    if (
        _sha256_file(PARENT_PATH)
        != EXPECTED_PARENT_SHA256
    ):
        raise RuntimeError(
            "parent SHA regression"
        )

    tests[
        "frozen_parent_client_sha"
    ] = "PASS"

    if len(planned) != 100:
        raise RuntimeError(
            "production stage is not exactly 100 requests"
        )

    tests[
        "production_stage_cap_100"
    ] = "PASS"

    sequences = [
        spec.request_sequence
        for spec in planned
    ]

    if sequences != list(
        range(29, 129)
    ):
        raise RuntimeError(
            f"unexpected production sequences: {sequences}"
        )

    tests[
        "production_sequences_29_through_128"
    ] = "PASS"

    if (
        _evaluate_authorization(
            "synthetic-head",
            "synthetic-head",
            True,
        )
        != "ACTIVE"
    ):
        raise RuntimeError(
            "valid synthetic authorization failed"
        )

    tests[
        "valid_production_tag_state"
    ] = "PASS"

    for target, head, clean in (
        ("wrong", "head", True),
        ("head", "head", False),
    ):
        try:
            _evaluate_authorization(
                target,
                head,
                clean,
            )

            raise RuntimeError(
                "invalid authorization accepted"
            )

        except PARENT.AuthorizationError:
            pass

    tests[
        "wrong_tag_or_dirty_tree_rejected"
    ] = "PASS"

    calls = {
        "authorization": 0,
        "credentials": 0,
        "authentication": 0,
        "network": 0,
    }

    def denied():
        calls["authorization"] += 1

        raise PARENT.AuthorizationError(
            "synthetic denied"
        )

    def credentials():
        calls["credentials"] += 1
        return "x", "y"

    def authenticate(*_args):
        calls["authentication"] += 1
        raise AssertionError(
            "authentication must not run"
        )

    def stream(*_args):
        calls["network"] += 1
        raise AssertionError(
            "network must not run"
        )

    try:
        run_stage(
            authorization_fn=denied,
            credentials_fn=credentials,
            authenticate_fn=authenticate,
            stream_fn=stream,
        )

        raise RuntimeError(
            "authorization-first regression"
        )

    except PARENT.AuthorizationError:
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

    tests[
        "authorization_precedes_credentials_and_network"
    ] = "PASS"

    return tests


def offline_validate() -> dict:
    _verify_frozen_history()

    (
        specs,
        proof,
        state,
        completed,
        pending,
        planned,
    ) = _load_plan()

    if proof["request_count"] != 6347:
        raise RuntimeError(
            "request count changed"
        )

    if (
        proof["request_set_sha256"]
        != EXPECTED_REQUEST_SET_SHA256
    ):
        raise RuntimeError(
            "request-set SHA changed"
        )

    if len(completed) != 28:
        raise RuntimeError(
            f"expected 28 completed requests, "
            f"found {len(completed)}"
        )

    if len(pending) != 6319:
        raise RuntimeError(
            f"expected 6319 pending requests, "
            f"found {len(pending)}"
        )

    tests = _offline_tests(
        planned
    )

    payload = {
        "status": "PASS",
        "network_requests": 0,
        "protected_outcome_access": False,
        "production_acquisition_implemented":
            True,
        "production_acquisition_authorized":
            False,
        "production_max_new_requests":
            PRODUCTION_MAX_NEW_REQUESTS,
        "timeout_seconds":
            PARENT.TIMEOUT_SECONDS,
        "minimum_request_start_spacing_seconds":
            PARENT.MIN_REQUEST_START_SPACING_SECONDS,
        "request_spacing_clock":
            "monotonic",
        "automatic_retry":
            False,
        "request_count":
            proof["request_count"],
        "request_set_sha256":
            proof["request_set_sha256"],
        "completed_dedicated_requests":
            len(completed),
        "pending_dedicated_requests":
            len(pending),
        "planned_request_count":
            len(planned),
        "planned_request_sequences": [
            spec.request_sequence
            for spec in planned
        ],
        "planned_request_ids": [
            spec.request_id
            for spec in planned
        ],
        "planned_norad_ids": [
            spec.object_id
            for spec in planned
        ],
        "planned_request_digest":
            _request_digest(planned),
        "frozen_parent_client_sha256":
            EXPECTED_PARENT_SHA256,
        "first_scaling_closure_commit":
            FIRST_SCALING_CLOSURE_COMMIT,
        "first_scaling_audit_sha256":
            EXPECTED_FIRST_SCALING_AUDIT_SHA256,
        "first_scaling_report_sha256":
            EXPECTED_FIRST_SCALING_REPORT_SHA256,
        "pre_stage_state_sha256":
            EXPECTED_STATE_SHA256,
        "approval_tag_required":
            PRODUCTION_APPROVAL_TAG,
        "approval_tag_present":
            False,
        "network_authorization_state":
            "INACTIVE_TAG_ABSENT",
        "offline_tests":
            tests,
    }

    snapshot = (
        _authorization_snapshot()
    )

    if not snapshot[
        "approval_tag_present"
    ]:
        payload[
            "offline_validation_artifact_write"
        ] = "WRITTEN_PREAUTHORIZATION"

        _atomic_write_json(
            OFFLINE_VALIDATION,
            payload,
        )

        return payload

    runtime = dict(payload)
    runtime.update(snapshot)

    runtime[
        "production_acquisition_authorized"
    ] = True

    runtime[
        "offline_validation_artifact_write"
    ] = (
        "SKIPPED_TAGGED_RUNTIME_TO_"
        "PRESERVE_CLEAN_TRACKED_TREE"
    )

    return runtime


def _verify_frozen_stage_plan(
    planned: list,
) -> None:
    if not OFFLINE_VALIDATION.is_file():
        raise RuntimeError(
            "production offline validation artifact missing"
        )

    with OFFLINE_VALIDATION.open(
        "r",
        encoding="utf-8",
    ) as stream:
        frozen = json.load(stream)

    if frozen.get("status") != "PASS":
        raise RuntimeError(
            "frozen production validation is not PASS"
        )

    if (
        frozen.get("planned_request_count")
        != len(planned)
    ):
        raise RuntimeError(
            "planned request count changed"
        )

    if (
        frozen.get("planned_request_digest")
        != _request_digest(planned)
    ):
        raise RuntimeError(
            "production request digest changed"
        )

    ids = [
        spec.request_id
        for spec in planned
    ]

    if (
        frozen.get("planned_request_ids")
        != ids
    ):
        raise RuntimeError(
            "production request order changed"
        )


def run_stage(
    *,
    authorization_fn=
        _verify_authorization,
    credentials_fn=
        PARENT._get_credentials,
    authenticate_fn=
        PARENT.authenticate,
    stream_fn=
        PARENT.stream_predecessor_request_once,
) -> int:

    # Authorization must precede credentials/auth/network.
    authorization_fn()

    _verify_frozen_history()

    (
        specs,
        proof,
        state,
        completed,
        pending,
        planned,
    ) = _load_plan()

    if not planned:
        print(
            json.dumps(
                {
                    "status":
                        "NO_PENDING_PREDECESSOR_REQUESTS",
                    "new_requests": 0,
                },
                sort_keys=True,
            )
        )

        return 0

    _verify_frozen_stage_plan(
        planned
    )

    print(
        json.dumps(
            {
                "status":
                    "PRODUCTION_STAGE_PREFLIGHT_PASS",
                "planned_request_count":
                    len(planned),
                "production_max_new_requests":
                    PRODUCTION_MAX_NEW_REQUESTS,
                "planned_request_digest":
                    _request_digest(planned),
                "planned_sequences": [
                    spec.request_sequence
                    for spec in planned
                ],
                "planned_norad_ids": [
                    spec.object_id
                    for spec in planned
                ],
                "timeout_seconds":
                    PARENT.TIMEOUT_SECONDS,
                "minimum_request_start_spacing_seconds":
                    PARENT.MIN_REQUEST_START_SPACING_SECONDS,
                "automatic_retry":
                    False,
            },
            indent=2,
            sort_keys=True,
        )
    )

    username, password = (
        credentials_fn()
    )

    opener, cookie_jar = (
        authenticate_fn(
            username,
            password,
            PARENT.TIMEOUT_SECONDS,
        )
    )

    _ = cookie_jar

    previous_start = None
    new_requests = 0

    for spec in planned:
        previous_start = (
            PARENT._enforce_request_spacing(
                previous_start
            )
        )

        try:
            result = stream_fn(
                opener,
                spec,
                PARENT.TIMEOUT_SECONDS,
            )

        except Exception as exc:
            result = (
                PARENT._failure_result(
                    exc
                )
            )

            PARENT._record_state(
                state,
                spec,
                result,
            )

            new_requests += 1

            print(
                json.dumps(
                    {
                        "status":
                            "PRODUCTION_STAGE_STOPPED_ON_FAILURE",
                        "new_requests":
                            new_requests,
                        "request_sequence":
                            spec.request_sequence,
                        "request_id":
                            spec.request_id,
                        "NORAD_CAT_ID":
                            spec.object_id,
                        "failure_class":
                            result.get(
                                "failure_class"
                            ),
                        "detail":
                            result.get(
                                "detail"
                            ),
                    },
                    sort_keys=True,
                )
            )

            return 1

        PARENT._record_state(
            state,
            spec,
            result,
        )

        new_requests += 1

        print(
            json.dumps(
                {
                    "status":
                        result.get("status"),
                    "new_requests":
                        new_requests,
                    "request_sequence":
                        spec.request_sequence,
                    "request_id":
                        spec.request_id,
                    "NORAD_CAT_ID":
                        spec.object_id,
                    "row_count":
                        result.get("row_count"),
                    "sha256":
                        result.get("sha256"),
                },
                sort_keys=True,
            )
        )

        if (
            result.get("status")
            == "HOLD_MISSING_GP_ID_FALLBACK_REQUIRED"
        ):
            print(
                json.dumps(
                    {
                        "status":
                            "PRODUCTION_STAGE_STOPPED_ON_HOLD",
                        "new_requests":
                            new_requests,
                        "request_sequence":
                            spec.request_sequence,
                        "request_id":
                            spec.request_id,
                        "NORAD_CAT_ID":
                            spec.object_id,
                    },
                    sort_keys=True,
                )
            )

            return 2

    print(
        json.dumps(
            {
                "status":
                    "PRODUCTION_STAGE_COMPLETE",
                "new_requests":
                    new_requests,
                "state_file":
                    PARENT.PREDECESSOR_STATE_FILE
                    .relative_to(ROOT)
                    .as_posix(),
            },
            sort_keys=True,
        )
    )

    return 0


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
        "--acquire-stage",
        action="store_true",
    )

    args = parser.parse_args()

    if args.offline_validate:
        payload = offline_validate()

        print(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
            )
        )

        return 0

    return run_stage()


if __name__ == "__main__":
    raise SystemExit(main())
