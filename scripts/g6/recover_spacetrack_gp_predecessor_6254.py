#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
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

PARENT_SHA256 = (
    "e4251b33d4f264fa3e2e6c413682209e9d55bf36c998ac72d55f3ed2a778187a"
)

CAMPAIGN_PATH = (
    ROOT
    / "scripts/g6/acquire_spacetrack_gp_predecessors_campaign.py"
)

CAMPAIGN_SHA256 = (
    "91bd7278400c34904d92bb3e50341de4ddbe270764f58a17c93dcaccf2c270e2"
)

CAMPAIGN_APPROVAL_TAG = (
    "g6-predecessor-campaign-continuation-approved"
)

CAMPAIGN_APPROVAL_COMMIT = (
    "9f3a1f64b5a0737c9a0864c65d3ba08b22de9c67"
)

RECOVERY_APPROVAL_TAG = (
    "g6-predecessor-6254-recovery-approved"
)

TARGET_SEQUENCE = 6254
TARGET_REQUEST_ID = "predecessor-62195"
TARGET_NORAD = "62195"

EXPECTED_FAILED_STATE_SHA256 = (
    "15225c8c7f68ff3898e4b96ed7804173e8f81201bf0f0e6aa809d69c48738870"
)

EXPECTED_EXACT_CUTOFF = (
    "2024-07-09T13:17:14.475775Z"
)

EXPECTED_TRANSPORT_CUTOFF = (
    "2024-07-09T13:17:14.000000Z"
)

EXPECTED_FAILURE_CLASS = (
    "TRANSPORT_FAILURE"
)

EXPECTED_FAILURE_DETAIL = (
    "Space-Track data request returned HTTP 503; no retry was attempted"
)

OFFLINE_VALIDATION = (
    ROOT
    / "results/g6/G6_Predecessor_6254_Recovery_Offline_Validation.json"
)

RECOVERY_DIR = (
    ROOT
    / "data/raw/g6/recovery"
)

ATTEMPT_EVIDENCE = (
    RECOVERY_DIR
    / "G6_Predecessor_Seq6254_Retry01_Evidence.json"
)

APPLIED_MARKER = (
    RECOVERY_DIR
    / "G6_Predecessor_Seq6254_Retry01_Applied.json"
)

STATE_PATH = (
    ROOT
    / "data/raw/g6/G6_SpaceTrack_Predecessor_Acquisition_State.json"
)

SUCCESS = "SUCCESS"
LEFT_CENSORED = (
    "LEFT_CENSORED_NO_PREDECESSOR_RETURNED"
)
HOLD = (
    "HOLD_MISSING_GP_ID_FALLBACK_REQUIRED"
)
FAILED = "FAILED"

ALLOWED_RETRY_RESULTS = {
    SUCCESS,
    LEFT_CENSORED,
    HOLD,
    FAILED,
}


def sha_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def canonical_sha(value) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


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


def tracked_tree_clean() -> bool:
    return not git(
        "status",
        "--porcelain",
        "--untracked-files=no",
    ).stdout.strip()


def atomic_json(
    path: Path,
    payload,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if path.exists():
        raise RuntimeError(
            f"refusing to overwrite recovery evidence: {path}"
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


def atomic_replace_state(
    payload: dict,
) -> None:
    fd, name = tempfile.mkstemp(
        prefix=f".{STATE_PATH.name}.",
        suffix=".tmp",
        dir=STATE_PATH.parent,
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
            STATE_PATH,
        )

    finally:
        temp.unlink(
            missing_ok=True
        )


def load_module(
    path: Path,
    name: str,
):
    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"cannot import {path}"
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    return module


def verify_static_boundary() -> None:
    if (
        sha_file(PARENT_PATH)
        != PARENT_SHA256
    ):
        raise RuntimeError(
            "frozen parent-client SHA mismatch"
        )

    if (
        sha_file(CAMPAIGN_PATH)
        != CAMPAIGN_SHA256
    ):
        raise RuntimeError(
            "frozen campaign-wrapper SHA mismatch"
        )

    if (
        tag_target(
            CAMPAIGN_APPROVAL_TAG
        )
        != CAMPAIGN_APPROVAL_COMMIT
    ):
        raise RuntimeError(
            "historical DR-0016 campaign tag changed"
        )

    ancestor = git(
        "merge-base",
        "--is-ancestor",
        CAMPAIGN_APPROVAL_COMMIT,
        "HEAD",
        check=False,
    )

    if ancestor.returncode != 0:
        raise RuntimeError(
            "DR-0016 approval commit is not an ancestor"
        )


PARENT = load_module(
    PARENT_PATH,
    "g6_predecessor_recovery_parent",
)


def load_target():
    if not STATE_PATH.is_file():
        raise RuntimeError(
            "predecessor state file missing"
        )

    raw = STATE_PATH.read_bytes()
    state_sha = hashlib.sha256(
        raw
    ).hexdigest()

    state = json.loads(
        raw.decode("utf-8")
    )

    records = state.get("records")

    if (
        state.get("schema_version") != 1
        or not isinstance(records, list)
    ):
        raise RuntimeError(
            "invalid predecessor state schema"
        )

    if len(records) != TARGET_SEQUENCE:
        raise RuntimeError(
            f"expected {TARGET_SEQUENCE} state records, "
            f"found {len(records)}"
        )

    sequences = [
        r.get("request_sequence")
        for r in records
    ]

    if sequences != list(
        range(1, TARGET_SEQUENCE + 1)
    ):
        raise RuntimeError(
            "predecessor state is not contiguous 1-6254"
        )

    last = records[-1]

    required = {
        "request_sequence":
            TARGET_SEQUENCE,
        "request_id":
            TARGET_REQUEST_ID,
        "NORAD_CAT_ID":
            TARGET_NORAD,
        "status":
            FAILED,
        "failure_class":
            EXPECTED_FAILURE_CLASS,
        "detail":
            EXPECTED_FAILURE_DETAIL,
        "exact_scientific_cutoff_utc":
            EXPECTED_EXACT_CUTOFF,
        "transport_cutoff_utc":
            EXPECTED_TRANSPORT_CUTOFF,
        "automatic_retry":
            False,
        "raw_filename":
            None,
        "sha256":
            None,
        "row_count":
            None,
    }

    for key, expected in required.items():
        if last.get(key) != expected:
            raise RuntimeError(
                f"sequence-6254 blocker mismatch: "
                f"{key}={last.get(key)!r}, "
                f"expected={expected!r}"
            )

    specs, proof = (
        PARENT._load_predecessor_specs()
    )

    if proof["request_count"] != 6347:
        raise RuntimeError(
            "frozen predecessor request count changed"
        )

    spec = specs[
        TARGET_SEQUENCE - 1
    ]

    if (
        spec.request_sequence
        != TARGET_SEQUENCE
        or spec.request_id
        != TARGET_REQUEST_ID
        or str(spec.object_id)
        != TARGET_NORAD
    ):
        raise RuntimeError(
            "frozen sequence-6254 request identity changed"
        )

    return {
        "state":
            state,
        "records":
            records,
        "state_sha256":
            state_sha,
        "failure_record":
            last,
        "failure_record_sha256":
            canonical_sha(last),
        "spec":
            spec,
    }


def response_paths(spec) -> list[Path]:
    return [
        spec.final_path,
        PARENT._text_no_result_path(spec),
        PARENT._invalid_response_path(spec),
    ]


def verify_pre_retry_boundary():
    verify_static_boundary()

    target = load_target()

    if (
        target["state_sha256"]
        != EXPECTED_FAILED_STATE_SHA256
    ):
        raise RuntimeError(
            "blocked predecessor-state SHA changed"
        )

    existing = [
        str(path)
        for path in response_paths(
            target["spec"]
        )
        if path.exists()
    ]

    if existing:
        raise RuntimeError(
            "sequence-6254 response evidence unexpectedly "
            f"already exists: {existing[0]}"
        )

    if ATTEMPT_EVIDENCE.exists():
        raise RuntimeError(
            "recovery attempt evidence already exists; "
            "a second retry is prohibited"
        )

    if APPLIED_MARKER.exists():
        raise RuntimeError(
            "recovery applied marker unexpectedly exists"
        )

    return target


def authorization_snapshot() -> dict:
    head = git(
        "rev-parse",
        "HEAD",
    ).stdout.strip()

    target = tag_target(
        RECOVERY_APPROVAL_TAG
    )

    clean = tracked_tree_clean()

    if target is None:
        state = "INACTIVE_TAG_ABSENT"

    elif target != head:
        raise RuntimeError(
            f"{RECOVERY_APPROVAL_TAG} resolves to {target}; "
            f"current HEAD is {head}"
        )

    elif not clean:
        raise RuntimeError(
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


def verify_authorization() -> None:
    snap = authorization_snapshot()

    if (
        snap["network_authorization_state"]
        != "ACTIVE"
    ):
        raise RuntimeError(
            "sequence-6254 recovery network request "
            "is not authorized"
        )


def build_parent_record(
    spec,
    result: dict,
) -> dict:
    # Exact field semantics copied from the frozen parent's
    # append-only _record_state(), but returned rather than appended.
    return {
        "request_sequence":
            spec.request_sequence,
        "request_id":
            spec.request_id,
        "kind":
            "predecessor",
        "NORAD_CAT_ID":
            spec.object_id,
        "status":
            result.get("status"),
        "retrieval_utc":
            result.get(
                "retrieval_utc",
                PARENT._utc_now(),
            ),
        "exact_scientific_cutoff_utc":
            PARENT._iso(
                spec.exact_scientific_cutoff_utc
            ),
        "transport_cutoff_utc":
            PARENT._iso(
                spec.transport_cutoff_utc
            ),
        "query":
            PARENT.non_secret_predicate(spec),
        "server_orderby":
            "CREATION_DATE desc,EPOCH desc,GP_ID desc",
        "limit":
            1,
        "timeout_seconds":
            PARENT.TIMEOUT_SECONDS,
        "automatic_retry":
            False,
        "row_count":
            result.get("row_count"),
        "byte_count":
            result.get("byte_count"),
        "sha256":
            result.get("sha256"),
        "raw_filename":
            result.get("raw_path"),
        "GP_ID":
            result.get("GP_ID"),
        "CREATION_DATE":
            result.get("CREATION_DATE"),
        "EPOCH":
            result.get("EPOCH"),
        "failure_class":
            result.get("failure_class"),
        "detail":
            result.get("detail"),
    }


def offline_validate() -> dict:
    target = verify_pre_retry_boundary()

    snap = authorization_snapshot()

    payload = {
        "status":
            "PASS",
        "target_request_sequence":
            TARGET_SEQUENCE,
        "target_request_id":
            TARGET_REQUEST_ID,
        "target_norad_cat_id":
            TARGET_NORAD,
        "pre_recovery_state_sha256":
            target["state_sha256"],
        "original_failure_record_sha256":
            target["failure_record_sha256"],
        "original_failure_class":
            target["failure_record"][
                "failure_class"
            ],
        "original_failure_detail":
            target["failure_record"]["detail"],
        "exact_scientific_cutoff_utc":
            target["failure_record"][
                "exact_scientific_cutoff_utc"
            ],
        "transport_cutoff_utc":
            target["failure_record"][
                "transport_cutoff_utc"
            ],
        "response_evidence_paths_clear":
            True,
        "recovery_attempt_evidence_absent":
            True,
        "maximum_authorized_data_requests":
            1,
        "automatic_retry":
            False,
        "state_update_semantics":
            "REPLACE_SEQUENCE_6254_NOT_APPEND",
        "original_failure_preservation":
            (
                "IMMUTABLE_SEPARATE_RECOVERY_EVIDENCE"
            ),
        "parent_client_sha256":
            PARENT_SHA256,
        "campaign_wrapper_sha256":
            CAMPAIGN_SHA256,
        "historical_campaign_tag_target":
            CAMPAIGN_APPROVAL_COMMIT,
        "approval_tag_required":
            RECOVERY_APPROVAL_TAG,
        "network_requests":
            0,
        "protected_outcome_access":
            False,
        **snap,
    }

    if not snap["approval_tag_present"]:
        if OFFLINE_VALIDATION.exists():
            raise RuntimeError(
                "preauthorization validation artifact "
                "already exists"
            )

        atomic_json(
            OFFLINE_VALIDATION,
            payload,
        )

        payload[
            "offline_validation_artifact_write"
        ] = "WRITTEN_PREAUTHORIZATION"

    else:
        payload[
            "offline_validation_artifact_write"
        ] = (
            "SKIPPED_TAGGED_RUNTIME_TO_"
            "PRESERVE_CLEAN_TRACKED_TREE"
        )

    return payload


def retry_once() -> int:
    # Authorization is checked before credentials,
    # authentication, or the data request.
    verify_authorization()

    target = verify_pre_retry_boundary()

    spec = target["spec"]

    username, password = (
        PARENT._get_credentials()
    )

    opener, cookie_jar = (
        PARENT.authenticate(
            username,
            password,
            PARENT.TIMEOUT_SECONDS,
        )
    )

    _ = cookie_jar

    # Exactly one data request is permitted.
    try:
        result = (
            PARENT.stream_predecessor_request_once(
                opener,
                spec,
                PARENT.TIMEOUT_SECONDS,
            )
        )

    except Exception as exc:
        result = (
            PARENT._failure_result(exc)
        )

    status = result.get(
        "status"
    )

    replacement = build_parent_record(
        spec,
        result,
    )

    evidence = {
        "schema_version":
            1,
        "recovery_id":
            "g6-predecessor-seq6254-retry01",
        "request_sequence":
            TARGET_SEQUENCE,
        "request_id":
            TARGET_REQUEST_ID,
        "NORAD_CAT_ID":
            TARGET_NORAD,
        "pre_recovery_state_sha256":
            target["state_sha256"],
        "original_failure_record":
            target["failure_record"],
        "original_failure_record_sha256":
            target["failure_record_sha256"],
        "retry_result_record":
            replacement,
        "retry_result_record_sha256":
            canonical_sha(replacement),
        "retry_status":
            status,
        "data_requests_issued":
            1,
        "automatic_retry":
            False,
        "network_retry_authorized_count":
            1,
        "protected_outcome_access":
            False,
        "state_update_semantics":
            "REPLACE_SEQUENCE_6254_NOT_APPEND",
    }

    # Persist the attempt history before touching operative state.
    atomic_json(
        ATTEMPT_EVIDENCE,
        evidence,
    )

    if status not in ALLOWED_RETRY_RESULTS:
        print(
            json.dumps(
                {
                    "status":
                        "RECOVERY_STOPPED_UNKNOWN_STATUS",
                    "returned_status":
                        status,
                    "data_requests_issued":
                        1,
                    "state_replaced":
                        False,
                },
                sort_keys=True,
            )
        )

        return 4

    new_state = copy.deepcopy(
        target["state"]
    )

    # Replacement, never append:
    # len(records) remains exactly 6254.
    new_state["records"][-1] = (
        replacement
    )

    atomic_replace_state(
        new_state
    )

    post_state_sha = (
        sha_file(STATE_PATH)
    )

    marker = {
        "schema_version":
            1,
        "recovery_id":
            "g6-predecessor-seq6254-retry01",
        "attempt_evidence_sha256":
            sha_file(ATTEMPT_EVIDENCE),
        "post_recovery_state_sha256":
            post_state_sha,
        "replacement_record_sha256":
            canonical_sha(replacement),
        "replacement_status":
            status,
        "total_state_records":
            len(new_state["records"]),
        "state_replaced_not_appended":
            True,
        "data_requests_issued":
            1,
        "automatic_retry":
            False,
        "protected_outcome_access":
            False,
    }

    atomic_json(
        APPLIED_MARKER,
        marker,
    )

    if status in {
        SUCCESS,
        LEFT_CENSORED,
    }:
        final_status = (
            "RECOVERY_RESOLVED"
        )

        rc = 0

    elif status == HOLD:
        final_status = (
            "RECOVERY_UNRESOLVED_HOLD"
        )

        rc = 2

    else:
        final_status = (
            "RECOVERY_UNRESOLVED_FAILURE"
        )

        rc = 1

    print(
        json.dumps(
            {
                "status":
                    final_status,
                "request_sequence":
                    TARGET_SEQUENCE,
                "request_id":
                    TARGET_REQUEST_ID,
                "NORAD_CAT_ID":
                    TARGET_NORAD,
                "retry_result_status":
                    status,
                "data_requests_issued":
                    1,
                "automatic_retry":
                    False,
                "state_replaced_not_appended":
                    True,
                "total_state_records":
                    len(new_state["records"]),
                "post_recovery_state_sha256":
                    post_state_sha,
                "attempt_evidence":
                    ATTEMPT_EVIDENCE
                    .relative_to(ROOT)
                    .as_posix(),
                "attempt_evidence_sha256":
                    sha_file(
                        ATTEMPT_EVIDENCE
                    ),
                "applied_marker_sha256":
                    sha_file(
                        APPLIED_MARKER
                    ),
            },
            sort_keys=True,
        )
    )

    return rc


def audit_recovery() -> int:
    verify_static_boundary()

    if not ATTEMPT_EVIDENCE.is_file():
        raise RuntimeError(
            "recovery attempt evidence does not exist"
        )

    if not APPLIED_MARKER.is_file():
        raise RuntimeError(
            "recovery applied marker does not exist"
        )

    with ATTEMPT_EVIDENCE.open(
        "r",
        encoding="utf-8",
    ) as f:
        evidence = json.load(f)

    with APPLIED_MARKER.open(
        "r",
        encoding="utf-8",
    ) as f:
        marker = json.load(f)

    raw = STATE_PATH.read_bytes()

    state_sha = hashlib.sha256(
        raw
    ).hexdigest()

    state = json.loads(
        raw.decode("utf-8")
    )

    records = state["records"]

    if len(records) != TARGET_SEQUENCE:
        raise RuntimeError(
            "post-recovery state record count is not 6254"
        )

    last = records[-1]

    if (
        last.get("request_sequence")
        != TARGET_SEQUENCE
        or last.get("request_id")
        != TARGET_REQUEST_ID
        or str(last.get("NORAD_CAT_ID"))
        != TARGET_NORAD
    ):
        raise RuntimeError(
            "post-recovery sequence-6254 identity mismatch"
        )

    if (
        canonical_sha(last)
        != evidence[
            "retry_result_record_sha256"
        ]
    ):
        raise RuntimeError(
            "operative state does not match "
            "the preserved retry-result record"
        )

    if (
        marker["post_recovery_state_sha256"]
        != state_sha
    ):
        raise RuntimeError(
            "post-recovery state SHA mismatch"
        )

    status = last.get(
        "status"
    )

    raw_verified = False
    causal_verified = False

    if status in {
        SUCCESS,
        LEFT_CENSORED,
        HOLD,
    }:
        raw_name = last.get(
            "raw_filename"
        )

        if not raw_name:
            raise RuntimeError(
                "retry result requires retained raw evidence"
            )

        raw_path = ROOT / raw_name

        if not raw_path.is_file():
            raise RuntimeError(
                "retry raw evidence missing"
            )

        if (
            sha_file(raw_path)
            != last.get("sha256")
        ):
            raise RuntimeError(
                "retry raw SHA mismatch"
            )

        if (
            raw_path.stat().st_size
            != last.get("byte_count")
        ):
            raise RuntimeError(
                "retry raw byte-count mismatch"
            )

        raw_verified = True

    if status == SUCCESS:
        if (
            not last.get("CREATION_DATE")
            or not last.get("GP_ID")
            or last.get("row_count") != 1
        ):
            raise RuntimeError(
                "resolved SUCCESS record is incomplete"
            )

        creation = (
            PARENT._parse_utc(
                last["CREATION_DATE"]
            )
        )

        cutoff = (
            PARENT._parse_utc(
                last[
                    "exact_scientific_cutoff_utc"
                ]
            )
        )

        if not creation < cutoff:
            raise RuntimeError(
                "resolved SUCCESS violates causal cutoff"
            )

        causal_verified = True

    resolved = status in {
        SUCCESS,
        LEFT_CENSORED,
    }

    audit_status = (
        "RECOVERY_AUDIT_PASS_RESOLVED"
        if resolved
        else
        "RECOVERY_AUDIT_PASS_UNRESOLVED"
    )

    print(
        json.dumps(
            {
                "status":
                    audit_status,
                "request_sequence":
                    TARGET_SEQUENCE,
                "NORAD_CAT_ID":
                    TARGET_NORAD,
                "operative_status":
                    status,
                "resolved":
                    resolved,
                "total_state_records":
                    len(records),
                "state_replaced_not_appended":
                    True,
                "post_recovery_state_sha256":
                    state_sha,
                "original_failure_preserved":
                    True,
                "original_failure_record_sha256":
                    evidence[
                        "original_failure_record_sha256"
                    ],
                "retry_result_record_sha256":
                    evidence[
                        "retry_result_record_sha256"
                    ],
                "attempt_evidence_sha256":
                    sha_file(
                        ATTEMPT_EVIDENCE
                    ),
                "applied_marker_sha256":
                    sha_file(
                        APPLIED_MARKER
                    ),
                "raw_evidence_verified":
                    raw_verified,
                "causal_success_verified":
                    causal_verified,
                "data_requests_issued":
                    evidence[
                        "data_requests_issued"
                    ],
                "automatic_retry":
                    False,
                "network_requests_during_audit":
                    0,
                "protected_outcome_access":
                    False,
            },
            indent=2,
            sort_keys=True,
        )
    )

    return (
        0
        if resolved
        else 2
    )


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
        "--retry-once",
        action="store_true",
    )

    modes.add_argument(
        "--audit-recovery",
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

    if args.retry_once:
        return retry_once()

    return audit_recovery()


if __name__ == "__main__":
    raise SystemExit(main())
