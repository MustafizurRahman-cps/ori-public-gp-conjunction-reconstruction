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
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PARENT_PATH = (
    ROOT
    / "scripts/g6/acquire_spacetrack_gp_predecessors.py"
)

EXPECTED_PARENT_SHA256 = (
    "e4251b33d4f264fa3e2e6c413682209e9d55bf36c998ac72d55f3ed2a778187a"
)

CAMPAIGN_START_COMMIT = (
    "31effd00b42e01a72a8549b13c38e0cf17313025"
)

HISTORICAL_PRODUCTION_TAG = (
    "g6-predecessor-production-approved"
)

EXPECTED_HISTORICAL_PRODUCTION_TAG_TARGET = (
    "4b8e77f511006d7dd1ec7ea52a94a11d3a4cc552"
)

CAMPAIGN_APPROVAL_TAG = (
    "g6-predecessor-campaign-approved"
)

CAMPAIGN_START_SEQUENCE = 129
CAMPAIGN_END_SEQUENCE = 6347
MAX_NEW_REQUESTS_PER_INVOCATION = 500

EXPECTED_INITIAL_STATE_SHA256 = (
    "fd6edd44c0e9868e9ce2dc5309945944c7fc8d10ec5a187ee5a30e6dd82d6e48"
)

EXPECTED_REQUEST_SET_SHA256 = (
    "976628343077b14cb7017493a08757ce93def34b5966f4b87688d6b106e46bee"
)

FIRST_PRODUCTION_AUDIT = (
    ROOT
    / "results/g6/G6_Predecessor_First_Production_Audit.json"
)

EXPECTED_FIRST_PRODUCTION_AUDIT_SHA256 = (
    "d7fd90815b97c0bd3bca241b03adb54b80f1fff51b98baeb6006c282ee275da2"
)

FIRST_PRODUCTION_REPORT = (
    ROOT
    / "docs/research/G6_Predecessor_First_Production_Report.md"
)

EXPECTED_FIRST_PRODUCTION_REPORT_SHA256 = (
    "94e16f00fe253c395570ed47a86abb417bb4b9f4f9ca211c1a64e05d634e6d7a"
)

OFFLINE_VALIDATION = (
    ROOT
    / "results/g6/G6_Predecessor_Campaign_Offline_Validation.json"
)

SUCCESS = "SUCCESS"
LEFT_CENSORED = (
    "LEFT_CENSORED_NO_PREDECESSOR_RETURNED"
)
HOLD = (
    "HOLD_MISSING_GP_ID_FALLBACK_REQUIRED"
)
FAILED = "FAILED"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def _canonical_digest(value) -> str:
    data = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(data).hexdigest()


def _request_digest(specs: list) -> str:
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


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def _load_parent():
    if (
        _sha256_file(PARENT_PATH)
        != EXPECTED_PARENT_SHA256
    ):
        raise RuntimeError(
            "frozen predecessor parent-client SHA mismatch"
        )

    name = "g6_predecessor_campaign_parent"

    spec = importlib.util.spec_from_file_location(
        name,
        PARENT_PATH,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            "cannot import frozen predecessor parent"
        )

    module = importlib.util.module_from_spec(spec)
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


def _tag_target(tag: str) -> str | None:
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


def _is_ancestor(commit: str) -> bool:
    result = _git(
        "merge-base",
        "--is-ancestor",
        commit,
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
            f"{CAMPAIGN_APPROVAL_TAG} resolves to "
            f"{tag_target}; current HEAD is {head}"
        )

    if not tracked_clean:
        raise PARENT.AuthorizationError(
            "tracked working tree is not clean"
        )

    return "ACTIVE"


def _authorization_snapshot() -> dict:
    head = _git_head()
    target = _tag_target(
        CAMPAIGN_APPROVAL_TAG
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


def _verify_authorization() -> None:
    snap = _authorization_snapshot()

    if (
        snap["network_authorization_state"]
        != "ACTIVE"
    ):
        raise PARENT.AuthorizationError(
            f"{CAMPAIGN_APPROVAL_TAG} is absent; "
            "campaign network acquisition is unauthorized"
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

        os.replace(temp, path)

    finally:
        temp.unlink(
            missing_ok=True
        )


def _verify_static_history() -> None:
    if not _is_ancestor(
        CAMPAIGN_START_COMMIT
    ):
        raise RuntimeError(
            "campaign start commit is not an ancestor"
        )

    old_target = _tag_target(
        HISTORICAL_PRODUCTION_TAG
    )

    if (
        old_target
        != EXPECTED_HISTORICAL_PRODUCTION_TAG_TARGET
    ):
        raise RuntimeError(
            "historical DR-0015 tag changed"
        )

    if (
        _sha256_file(FIRST_PRODUCTION_AUDIT)
        != EXPECTED_FIRST_PRODUCTION_AUDIT_SHA256
    ):
        raise RuntimeError(
            "first production audit SHA mismatch"
        )

    if (
        _sha256_file(FIRST_PRODUCTION_REPORT)
        != EXPECTED_FIRST_PRODUCTION_REPORT_SHA256
    ):
        raise RuntimeError(
            "first production report SHA mismatch"
        )


def _load_specs_and_state():
    specs, proof = (
        PARENT._load_predecessor_specs()
    )

    if proof["request_count"] != 6347:
        raise RuntimeError(
            "frozen predecessor request count changed"
        )

    if (
        proof["request_set_sha256"]
        != EXPECTED_REQUEST_SET_SHA256
    ):
        raise RuntimeError(
            "frozen predecessor request-set SHA changed"
        )

    state = PARENT._load_state()
    records = state.get("records", [])

    if state.get("schema_version") != 1:
        raise RuntimeError(
            "unexpected predecessor state schema"
        )

    return specs, proof, state, records


def _load_frozen_campaign_validation() -> dict:
    if not OFFLINE_VALIDATION.is_file():
        raise RuntimeError(
            "campaign offline validation artifact missing"
        )

    with OFFLINE_VALIDATION.open(
        "r",
        encoding="utf-8",
    ) as f:
        frozen = json.load(f)

    if frozen.get("status") != "PASS":
        raise RuntimeError(
            "frozen campaign validation is not PASS"
        )

    return frozen


def _validate_progress(
    *,
    require_frozen_validation: bool,
    verify_raw: bool,
) -> dict:
    _verify_static_history()

    (
        specs,
        proof,
        state,
        records,
    ) = _load_specs_and_state()

    if len(records) < 128:
        raise RuntimeError(
            "closed 1-128 predecessor prefix is missing"
        )

    if len(records) > CAMPAIGN_END_SEQUENCE:
        raise RuntimeError(
            "state contains records beyond sequence 6347"
        )

    sequences = [
        r.get("request_sequence")
        for r in records
    ]

    expected_sequences = list(
        range(1, len(records) + 1)
    )

    if sequences != expected_sequences:
        raise RuntimeError(
            "state is not a contiguous one-record-per-sequence prefix"
        )

    for index, record in enumerate(records):
        spec = specs[index]

        if (
            record.get("request_sequence")
            != spec.request_sequence
        ):
            raise RuntimeError(
                f"sequence identity mismatch at index {index}"
            )

        if (
            record.get("request_id")
            != spec.request_id
        ):
            raise RuntimeError(
                f"request_id mismatch at sequence "
                f"{spec.request_sequence}"
            )

        if (
            str(record.get("NORAD_CAT_ID"))
            != str(spec.object_id)
        ):
            raise RuntimeError(
                f"NORAD identity mismatch at sequence "
                f"{spec.request_sequence}"
            )

    closed_prefix = records[:128]
    prefix_digest = _canonical_digest(
        closed_prefix
    )

    if require_frozen_validation:
        frozen = (
            _load_frozen_campaign_validation()
        )

        if (
            frozen.get(
                "closed_prefix_records_sha256"
            )
            != prefix_digest
        ):
            raise RuntimeError(
                "closed sequences 1-128 prefix changed"
            )

        universe = specs[
            CAMPAIGN_START_SEQUENCE - 1:
            CAMPAIGN_END_SEQUENCE
        ]

        if (
            frozen.get(
                "campaign_universe_request_digest"
            )
            != _request_digest(universe)
        ):
            raise RuntimeError(
                "campaign universe digest changed"
            )

    for record in closed_prefix:
        if record.get("status") != SUCCESS:
            raise RuntimeError(
                "previously closed 1-128 prefix changed"
            )

    blocker = None
    terminal_count = 0
    raw_verified = 0
    causal_verified = 0

    campaign_records = records[128:]

    for campaign_index, record in enumerate(
        campaign_records
    ):
        seq = record["request_sequence"]
        status = record.get("status")

        if status in {FAILED, HOLD}:
            blocker = {
                "request_sequence":
                    seq,
                "request_id":
                    record.get("request_id"),
                "NORAD_CAT_ID":
                    record.get("NORAD_CAT_ID"),
                "status":
                    status,
                "failure_class":
                    record.get("failure_class"),
            }

            # A blocker must be the final recorded campaign
            # request. Records after a FAILED/HOLD request
            # would violate the frozen stop-before-continuation
            # semantics.
            if (
                campaign_index
                != len(campaign_records) - 1
            ):
                raise RuntimeError(
                    "state contains records after a "
                    "FAILED/HOLD campaign blocker"
                )

            break

        if status not in {
            SUCCESS,
            LEFT_CENSORED,
        }:
            raise RuntimeError(
                f"unknown campaign status at sequence "
                f"{seq}: {status}"
            )

        if (
            record.get("automatic_retry")
            is not False
        ):
            raise RuntimeError(
                f"automatic_retry changed at sequence {seq}"
            )

        raw_name = record.get("raw_filename")
        recorded_sha = record.get("sha256")
        recorded_bytes = record.get("byte_count")

        if (
            not raw_name
            or not recorded_sha
            or recorded_bytes is None
        ):
            raise RuntimeError(
                f"incomplete raw provenance at sequence {seq}"
            )

        if verify_raw:
            raw = ROOT / raw_name

            if not raw.is_file():
                raise RuntimeError(
                    f"missing raw evidence at sequence {seq}"
                )

            if (
                _sha256_file(raw)
                != recorded_sha
            ):
                raise RuntimeError(
                    f"raw SHA mismatch at sequence {seq}"
                )

            if (
                raw.stat().st_size
                != recorded_bytes
            ):
                raise RuntimeError(
                    f"raw byte-count mismatch at sequence {seq}"
                )

            raw_verified += 1

        if status == SUCCESS:
            if record.get("row_count") != 1:
                raise RuntimeError(
                    f"SUCCESS row_count != 1 at sequence {seq}"
                )

            if not record.get("GP_ID"):
                raise RuntimeError(
                    f"SUCCESS missing GP_ID at sequence {seq}"
                )

            creation = _parse_utc(
                record["CREATION_DATE"]
            )

            cutoff = _parse_utc(
                record[
                    "exact_scientific_cutoff_utc"
                ]
            )

            if not creation < cutoff:
                raise RuntimeError(
                    f"causal-cutoff failure at sequence {seq}"
                )

            causal_verified += 1

        elif status == LEFT_CENSORED:
            if record.get("row_count") != 0:
                raise RuntimeError(
                    f"left-censor row_count != 0 at sequence {seq}"
                )

        terminal_count += 1

    if blocker is not None:
        next_specs = []

    else:
        next_index = len(records)

        next_specs = specs[
            next_index:
            min(
                next_index
                + MAX_NEW_REQUESTS_PER_INVOCATION,
                CAMPAIGN_END_SEQUENCE,
            )
        ]

    campaign_completed = (
        blocker is None
        and len(records)
        == CAMPAIGN_END_SEQUENCE
    )

    # FAILED/HOLD is a recorded request attempt but is not a
    # scientifically completed dedicated predecessor request.
    completed_dedicated_requests = (
        128 + terminal_count
    )

    remaining_dedicated_requests = (
        CAMPAIGN_END_SEQUENCE
        - completed_dedicated_requests
    )

    return {
        "state":
            state,
        "records":
            records,
        "state_sha256":
            _sha256_file(
                PARENT.PREDECESSOR_STATE_FILE
            ),
        "closed_prefix_records_sha256":
            prefix_digest,
        "total_state_records":
            len(records),
        "completed_dedicated_requests":
            completed_dedicated_requests,
        "campaign_terminal_records":
            terminal_count,
        "remaining_dedicated_requests":
            remaining_dedicated_requests,
        "raw_verified_campaign_records":
            raw_verified,
        "causal_verified_success_records":
            causal_verified,
        "blocker":
            blocker,
        "campaign_completed":
            campaign_completed,
        "next_specs":
            next_specs,
    }


def _offline_tests() -> dict:
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

    if MAX_NEW_REQUESTS_PER_INVOCATION != 500:
        raise RuntimeError(
            "campaign invocation cap changed"
        )

    tests[
        "max_500_new_requests_per_invocation"
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
            "valid synthetic campaign authorization failed"
        )

    tests[
        "valid_campaign_tag_state"
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
            "synthetic authorization denied"
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
        run_next(
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
    snap = _authorization_snapshot()

    if not snap["approval_tag_present"]:
        progress = _validate_progress(
            require_frozen_validation=False,
            verify_raw=False,
        )

        if progress[
            "total_state_records"
        ] != 128:
            raise RuntimeError(
                "preauthorization campaign state must "
                "contain exactly 128 closed records"
            )

        if (
            progress["state_sha256"]
            != EXPECTED_INITIAL_STATE_SHA256
        ):
            raise RuntimeError(
                "initial campaign state SHA mismatch"
            )

        if progress["blocker"] is not None:
            raise RuntimeError(
                "campaign cannot begin with blocker"
            )

        specs, proof, state, records = (
            _load_specs_and_state()
        )

        universe = specs[
            CAMPAIGN_START_SEQUENCE - 1:
            CAMPAIGN_END_SEQUENCE
        ]

        initial = universe[
            :MAX_NEW_REQUESTS_PER_INVOCATION
        ]

        if len(universe) != 6219:
            raise RuntimeError(
                "campaign universe is not 6219 requests"
            )

        if len(initial) != 500:
            raise RuntimeError(
                "initial campaign invocation is not 500 requests"
            )

        if [
            s.request_sequence
            for s in initial
        ] != list(range(129, 629)):
            raise RuntimeError(
                "initial campaign sequences are not 129-628"
            )

        tests = _offline_tests()

        payload = {
            "status":
                "PASS",
            "network_requests":
                0,
            "protected_outcome_access":
                False,
            "campaign_acquisition_implemented":
                True,
            "campaign_acquisition_authorized":
                False,
            "campaign_start_sequence":
                CAMPAIGN_START_SEQUENCE,
            "campaign_end_sequence":
                CAMPAIGN_END_SEQUENCE,
            "campaign_request_count":
                len(universe),
            "campaign_universe_request_digest":
                _request_digest(universe),
            "max_new_requests_per_invocation":
                MAX_NEW_REQUESTS_PER_INVOCATION,
            "initial_invocation_request_count":
                len(initial),
            "initial_invocation_sequences":
                [
                    initial[0].request_sequence,
                    initial[-1].request_sequence,
                ],
            "initial_invocation_request_digest":
                _request_digest(initial),
            "initial_invocation_request_ids":
                [
                    s.request_id
                    for s in initial
                ],
            "completed_dedicated_requests":
                128,
            "pending_dedicated_requests":
                6219,
            "initial_state_sha256":
                EXPECTED_INITIAL_STATE_SHA256,
            "closed_prefix_records_sha256":
                progress[
                    "closed_prefix_records_sha256"
                ],
            "request_count":
                proof["request_count"],
            "request_set_sha256":
                proof["request_set_sha256"],
            "timeout_seconds":
                PARENT.TIMEOUT_SECONDS,
            "minimum_request_start_spacing_seconds":
                PARENT.MIN_REQUEST_START_SPACING_SECONDS,
            "request_spacing_clock":
                "monotonic",
            "automatic_retry":
                False,
            "failure_or_hold_blocks_future_invocations":
                True,
            "approval_tag_required":
                CAMPAIGN_APPROVAL_TAG,
            "approval_tag_present":
                False,
            "network_authorization_state":
                "INACTIVE_TAG_ABSENT",
            "offline_tests":
                tests,
            "offline_validation_artifact_write":
                "WRITTEN_PREAUTHORIZATION",
        }

        _atomic_write_json(
            OFFLINE_VALIDATION,
            payload,
        )

        return payload

    frozen = _load_frozen_campaign_validation()

    progress = _validate_progress(
        require_frozen_validation=True,
        verify_raw=True,
    )

    runtime = {
        **frozen,
        **snap,
        "campaign_acquisition_authorized":
            (
                snap[
                    "network_authorization_state"
                ]
                == "ACTIVE"
            ),
        "current_state_sha256":
            progress["state_sha256"],
        "current_completed_dedicated_requests":
            progress[
                "completed_dedicated_requests"
            ],
        "current_remaining_dedicated_requests":
            progress[
                "remaining_dedicated_requests"
            ],
        "current_campaign_terminal_records":
            progress[
                "campaign_terminal_records"
            ],
        "current_blocker":
            progress["blocker"],
        "campaign_completed":
            progress[
                "campaign_completed"
            ],
        "next_invocation_request_count":
            len(progress["next_specs"]),
        "next_invocation_sequences":
            (
                [
                    progress[
                        "next_specs"
                    ][0].request_sequence,
                    progress[
                        "next_specs"
                    ][-1].request_sequence,
                ]
                if progress["next_specs"]
                else []
            ),
        "next_invocation_request_digest":
            (
                _request_digest(
                    progress["next_specs"]
                )
                if progress["next_specs"]
                else None
            ),
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

    return runtime


def audit_progress() -> int:
    progress = _validate_progress(
        require_frozen_validation=True,
        verify_raw=True,
    )

    if progress["blocker"] is not None:
        status = (
            "CAMPAIGN_BLOCKED_REVIEW_REQUIRED"
        )

    elif progress["campaign_completed"]:
        status = "CAMPAIGN_COMPLETE"

    else:
        status = "CAMPAIGN_PROGRESS_PASS"

    next_specs = progress[
        "next_specs"
    ]

    payload = {
        "status":
            status,
        "state_sha256":
            progress["state_sha256"],
        "total_state_records":
            progress["total_state_records"],
        "completed_dedicated_requests":
            progress[
                "completed_dedicated_requests"
            ],
        "campaign_terminal_records":
            progress[
                "campaign_terminal_records"
            ],
        "remaining_dedicated_requests":
            progress[
                "remaining_dedicated_requests"
            ],
        "raw_verified_campaign_records":
            progress[
                "raw_verified_campaign_records"
            ],
        "causal_verified_success_records":
            progress[
                "causal_verified_success_records"
            ],
        "blocker":
            progress["blocker"],
        "next_invocation_request_count":
            len(next_specs),
        "next_invocation_sequences":
            (
                [
                    next_specs[0].request_sequence,
                    next_specs[-1].request_sequence,
                ]
                if next_specs
                else []
            ),
        "next_invocation_request_digest":
            (
                _request_digest(next_specs)
                if next_specs
                else None
            ),
        "network_requests_during_audit":
            0,
        "protected_outcome_access":
            False,
    }

    print(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
    )

    return (
        2
        if progress["blocker"]
        else 0
    )


def run_next(
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

    # Authorization must be checked before credentials/auth/network.
    authorization_fn()

    progress = _validate_progress(
        require_frozen_validation=True,
        verify_raw=True,
    )

    if progress["blocker"] is not None:
        print(
            json.dumps(
                {
                    "status":
                        "CAMPAIGN_BLOCKED_REVIEW_REQUIRED",
                    "blocker":
                        progress["blocker"],
                    "network_requests":
                        0,
                },
                sort_keys=True,
            )
        )

        return 3

    if progress["campaign_completed"]:
        print(
            json.dumps(
                {
                    "status":
                        "CAMPAIGN_COMPLETE",
                    "new_requests":
                        0,
                    "completed_dedicated_requests":
                        CAMPAIGN_END_SEQUENCE,
                    "remaining_dedicated_requests":
                        0,
                },
                sort_keys=True,
            )
        )

        return 0

    planned = progress[
        "next_specs"
    ]

    if not planned:
        raise RuntimeError(
            "no blocker, not complete, but no planned requests"
        )

    invocation_digest = (
        _request_digest(planned)
    )

    print(
        json.dumps(
            {
                "status":
                    "CAMPAIGN_INVOCATION_PREFLIGHT_PASS",
                "completed_before_invocation":
                    progress[
                        "completed_dedicated_requests"
                    ],
                "remaining_before_invocation":
                    progress[
                        "remaining_dedicated_requests"
                    ],
                "planned_request_count":
                    len(planned),
                "planned_sequence_start":
                    planned[0].request_sequence,
                "planned_sequence_end":
                    planned[-1].request_sequence,
                "planned_request_digest":
                    invocation_digest,
                "max_new_requests_per_invocation":
                    MAX_NEW_REQUESTS_PER_INVOCATION,
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

    state = progress["state"]
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
                PARENT._failure_result(exc)
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
                            "CAMPAIGN_STOPPED_ON_FAILURE",
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

        status = result.get(
            "status"
        )

        print(
            json.dumps(
                {
                    "status":
                        status,
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

        if status == FAILED:
            print(
                json.dumps(
                    {
                        "status":
                            "CAMPAIGN_STOPPED_ON_FAILURE",
                        "new_requests":
                            new_requests,
                        "request_sequence":
                            spec.request_sequence,
                    },
                    sort_keys=True,
                )
            )

            return 1

        if status == HOLD:
            print(
                json.dumps(
                    {
                        "status":
                            "CAMPAIGN_STOPPED_ON_HOLD",
                        "new_requests":
                            new_requests,
                        "request_sequence":
                            spec.request_sequence,
                    },
                    sort_keys=True,
                )
            )

            return 2

        if status not in {
            SUCCESS,
            LEFT_CENSORED,
        }:
            print(
                json.dumps(
                    {
                        "status":
                            "CAMPAIGN_STOPPED_ON_UNKNOWN_STATUS",
                        "new_requests":
                            new_requests,
                        "request_sequence":
                            spec.request_sequence,
                        "returned_status":
                            status,
                    },
                    sort_keys=True,
                )
            )

            return 4

    completed_after = (
        progress[
            "completed_dedicated_requests"
        ]
        + new_requests
    )

    remaining_after = (
        CAMPAIGN_END_SEQUENCE
        - completed_after
    )

    print(
        json.dumps(
            {
                "status":
                    "CAMPAIGN_INVOCATION_COMPLETE",
                "new_requests":
                    new_requests,
                "completed_dedicated_requests":
                    completed_after,
                "remaining_dedicated_requests":
                    remaining_after,
                "invocation_request_digest":
                    invocation_digest,
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

    return run_next()


if __name__ == "__main__":
    raise SystemExit(main())
