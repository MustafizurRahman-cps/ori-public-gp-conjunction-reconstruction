#!/usr/bin/env python3
"""Independent offline review of the frozen G6 causal-history manifest."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_HEAD = (
    "061759abbb32ffe755d037efd0799416a7310d94"
)

CLOSURE_TAG = (
    "g6-predecessor-acquisition-complete"
)

BUILDER = (
    ROOT / "scripts/g6/build_causal_history_manifest.py"
)

AUDIT = (
    ROOT / "results/g6/G6_Final_Causal_History_Audit.csv"
)

SUMMARY = (
    ROOT / "results/g6/G6_Final_Causal_History_Summary.json"
)

SAMPLE = (
    ROOT / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"
)

MAIN_STATE = (
    ROOT / "data/raw/g6/G6_SpaceTrack_Acquisition_State.json"
)

PRED_STATE = (
    ROOT
    / "data/raw/g6/G6_SpaceTrack_Predecessor_Acquisition_State.json"
)

COVERAGE = (
    ROOT / "results/g6/G6_Main_History_Coverage_Audit.csv"
)

OUT = (
    ROOT / "results/g6/G6_Causal_History_Manifest_Review.json"
)

EXPECTED_BUILDER_SHA = (
    "02cfd20585ea4df72116488d9d0add99231156af9a53e62e1bedb1476df90c8e"
)

EXPECTED_AUDIT_SHA = (
    "41802759162ef4889b071aa6f15391d48a7a96cce7ededdd99c8e1d8a3ebc924"
)

EXPECTED_SUMMARY_SHA = (
    "49665cc82e109d0d65e85140c172f3245c2cca4c3c7f2c1ed5c6c21dcb436767"
)

EXPECTED_MAIN_STATE_SHA = (
    "e108db07530c58464fca223cdd2c5a54d850d23110edfd120e7b440f90004149"
)

EXPECTED_PRED_STATE_SHA = (
    "0c56f69f0fd3d396c5bddcb2ca863af52e7c34694b1ba81185d17feffebf709e"
)

EXPECTED_EVENTS = 6927
EXPECTED_OBJECTS = 13854
EXPECTED_MAIN_ROWS = 5153040
EXPECTED_CAPTURED = 7507
EXPECTED_QUERY_SUCCESS = 5032
EXPECTED_LEFT = 1315
EXPECTED_CAUSAL_ROWS = 5165579
EXPECTED_MAIN_FILES = 1386
EXPECTED_PRED_RECORDS = 6347

PROTECTED_FIELDS = {
    "min_range",
    "mdistance",
    "reconstructed_miss_distance",
    "e_d",
    "e_t",
    "reference_state",
    "relative_speed",
    "pc",
    "covariance",
    "hbr",
    "risk",
}


class ReviewError(RuntimeError):
    pass


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def parse_utc(value: str) -> datetime:
    text = value.strip()

    if not text:
        raise ValueError("empty UTC timestamp")

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    parsed = datetime.fromisoformat(text)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)

    return parsed


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:
        return list(csv.DictReader(stream))


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

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
            os.fsync(stream.fileno())

        os.replace(tmp, path)

    finally:
        tmp.unlink(missing_ok=True)


# ------------------------------------------------------------
# REPOSITORY + FROZEN MANIFEST HASHES
# ------------------------------------------------------------

if git("rev-parse", "HEAD") != EXPECTED_HEAD:
    raise ReviewError(
        "HEAD changed from causal-history build boundary"
    )

if (
    git(
        "rev-parse",
        f"refs/tags/{CLOSURE_TAG}^{{commit}}",
    )
    != EXPECTED_HEAD
):
    raise ReviewError(
        "predecessor-acquisition closure tag changed"
    )

if git(
    "status",
    "--porcelain",
    "--untracked-files=no",
):
    raise ReviewError(
        "tracked tree is not clean"
    )

for path, expected in (
    (BUILDER, EXPECTED_BUILDER_SHA),
    (AUDIT, EXPECTED_AUDIT_SHA),
    (SUMMARY, EXPECTED_SUMMARY_SHA),
    (MAIN_STATE, EXPECTED_MAIN_STATE_SHA),
    (PRED_STATE, EXPECTED_PRED_STATE_SHA),
):
    if not path.is_file():
        raise ReviewError(
            f"missing input: {path}"
        )

    actual = sha_file(path)

    if actual != expected:
        raise ReviewError(
            f"SHA mismatch: {path}\n"
            f"actual={actual}\n"
            f"expected={expected}"
        )


# ------------------------------------------------------------
# SAMPLE / EVENT STRUCTURE
# ------------------------------------------------------------

sample_rows = read_csv(SAMPLE)

if len(sample_rows) != EXPECTED_EVENTS:
    raise ReviewError(
        "sample does not contain 6927 events"
    )

sample_by_object: dict[str, dict] = {}
sample_by_event: dict[str, dict] = {}

for row in sample_rows:
    event_index = row["final_g6_sample_index"].strip()
    conj_id = row["conj_id"].strip()

    if event_index in sample_by_event:
        raise ReviewError(
            "duplicate final sample index"
        )

    sample_by_event[event_index] = row

    for role in ("obj1", "obj2"):
        object_id = row[role].strip()

        if object_id in sample_by_object:
            raise ReviewError(
                "sample object-disjointness failure: "
                + object_id
            )

        sample_by_object[object_id] = {
            "event_index": event_index,
            "conj_id": conj_id,
            "role": role,
            "tca": row["reference_tca_utc"].strip(),
            "stratum": row["g6_temporal_stratum"].strip(),
        }

if len(sample_by_object) != EXPECTED_OBJECTS:
    raise ReviewError(
        "sample does not contain 13854 distinct objects"
    )


# ------------------------------------------------------------
# MANIFEST STRUCTURAL REVIEW
# ------------------------------------------------------------

audit_rows = read_csv(AUDIT)

if len(audit_rows) != EXPECTED_OBJECTS:
    raise ReviewError(
        "manifest audit is not 13854 rows"
    )

with AUDIT.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:
    header = next(csv.reader(stream))

header_lower = {
    value.strip().lower()
    for value in header
}

protected_present = sorted(
    header_lower & PROTECTED_FIELDS
)

if protected_present:
    raise ReviewError(
        "protected fields present in causal-history manifest: "
        + repr(protected_present)
    )

by_object: dict[str, dict[str, str]] = {}
event_manifest = defaultdict(list)

class_counts = Counter()
lower_status_counts = Counter()

main_rows_total = 0
causal_rows_total = 0
available_count = 0

for row in audit_rows:
    object_id = row["NORAD_CAT_ID"].strip()

    if object_id in by_object:
        raise ReviewError(
            "duplicate object in manifest: "
            + object_id
        )

    frozen = sample_by_object.get(object_id)

    if frozen is None:
        raise ReviewError(
            "manifest contains non-sample object: "
            + object_id
        )

    if (
        row["final_g6_sample_index"].strip()
        != frozen["event_index"]
        or row["conj_id"].strip()
        != frozen["conj_id"]
        or row["object_role"].strip()
        != frozen["role"]
        or row["g6_temporal_stratum"].strip()
        != frozen["stratum"]
    ):
        raise ReviewError(
            "sample/manifest identity mismatch for "
            + object_id
        )

    tca = parse_utc(
        row["reference_tca_utc"]
    )

    if tca != parse_utc(
        frozen["tca"]
    ):
        raise ReviewError(
            "manifest/sample TCA mismatch for "
            + object_id
        )

    lower = parse_utc(
        row["lower_bound_utc"]
    )

    if (
        tca - lower
    ).total_seconds() != 180 * 86400:
        raise ReviewError(
            "180-day interval mismatch for "
            + object_id
        )

    earliest = parse_utc(
        row["earliest_main_CREATION_DATE"]
    )

    latest = parse_utc(
        row["latest_main_CREATION_DATE"]
    )

    if not (
        lower <= earliest
        <= latest
        < tca
    ):
        raise ReviewError(
            "main-window causal bounds fail for "
            + object_id
        )

    main_rows = int(
        row["main_window_rows"]
    )

    if main_rows <= 0:
        raise ReviewError(
            "empty main history for "
            + object_id
        )

    final_class = (
        row["final_predecessor_class"].strip()
    )

    class_counts[final_class] += 1

    predecessor_available = final_class in {
        "PREDECESSOR_CAPTURED_IN_BATCH_UNION",
        "PREDECESSOR_ACQUIRED_BY_QUERY",
    }

    expected_causal = (
        main_rows
        + (1 if predecessor_available else 0)
    )

    actual_causal = int(
        row["causal_history_record_count"]
    )

    if actual_causal != expected_causal:
        raise ReviewError(
            "causal-record accounting mismatch for "
            + object_id
        )

    if predecessor_available:
        available_count += 1

        gp_id = row["predecessor_GP_ID"].strip()
        creation_text = (
            row["predecessor_CREATION_DATE"].strip()
        )
        epoch_text = (
            row["predecessor_EPOCH"].strip()
        )

        if not (
            gp_id
            and creation_text
            and epoch_text
        ):
            raise ReviewError(
                "available predecessor metadata incomplete for "
                + object_id
            )

        if not parse_utc(
            creation_text
        ) < lower:
            raise ReviewError(
                "predecessor violates strict lower bound for "
                + object_id
            )

        parse_utc(epoch_text)

        if (
            row["lower_boundary_status"].strip()
            != "PREDECESSOR_AVAILABLE"
        ):
            raise ReviewError(
                "available predecessor has wrong boundary status"
            )

    elif final_class == (
        "LEFT_CENSORED_NO_PREDECESSOR_RETURNED"
    ):
        if any(
            row[field].strip()
            for field in (
                "predecessor_GP_ID",
                "predecessor_CREATION_DATE",
                "predecessor_EPOCH",
                "predecessor_raw_path",
                "predecessor_raw_SHA256",
            )
        ):
            raise ReviewError(
                "left-censored object has imputed predecessor metadata: "
                + object_id
            )

        if (
            row["lower_boundary_status"].strip()
            != "LEFT_CENSORED_NO_PREDECESSOR_RETURNED"
        ):
            raise ReviewError(
                "left-censored boundary status mismatch"
            )

    else:
        raise ReviewError(
            "unexpected final predecessor class: "
            + final_class
        )

    if row["assembly_status"].strip() != "PASS":
        raise ReviewError(
            "manifest assembly status is not PASS"
        )

    main_rows_total += main_rows
    causal_rows_total += actual_causal

    by_object[object_id] = row
    event_manifest[
        row["final_g6_sample_index"].strip()
    ].append(row)


expected_classes = Counter({
    "PREDECESSOR_CAPTURED_IN_BATCH_UNION":
        EXPECTED_CAPTURED,

    "PREDECESSOR_ACQUIRED_BY_QUERY":
        EXPECTED_QUERY_SUCCESS,

    "LEFT_CENSORED_NO_PREDECESSOR_RETURNED":
        EXPECTED_LEFT,
})

if class_counts != expected_classes:
    raise ReviewError(
        "manifest predecessor-class counts differ: "
        + repr(dict(class_counts))
    )

if main_rows_total != EXPECTED_MAIN_ROWS:
    raise ReviewError(
        "main-window row total mismatch"
    )

if causal_rows_total != EXPECTED_CAUSAL_ROWS:
    raise ReviewError(
        "causal-history record total mismatch"
    )

if available_count != (
    EXPECTED_CAPTURED
    + EXPECTED_QUERY_SUCCESS
):
    raise ReviewError(
        "available predecessor count mismatch"
    )

if set(by_object) != set(sample_by_object):
    raise ReviewError(
        "manifest and sample object sets differ"
    )

if len(event_manifest) != EXPECTED_EVENTS:
    raise ReviewError(
        "manifest event count is not 6927"
    )

for index, rows in event_manifest.items():
    if len(rows) != 2:
        raise ReviewError(
            f"event {index} does not have exactly two objects"
        )

    roles = {
        row["object_role"].strip()
        for row in rows
    }

    if roles != {"obj1", "obj2"}:
        raise ReviewError(
            f"event {index} object roles are invalid"
        )


# ------------------------------------------------------------
# CROSS-CHECK AGAINST COVERAGE
# ------------------------------------------------------------

coverage_rows = read_csv(COVERAGE)

if len(coverage_rows) != EXPECTED_OBJECTS:
    raise ReviewError(
        "coverage audit is not 13854 rows"
    )

coverage_by_object = {
    row["NORAD_CAT_ID"].strip(): row
    for row in coverage_rows
}

if set(coverage_by_object) != set(by_object):
    raise ReviewError(
        "coverage/manifest object sets differ"
    )

for object_id, manifest in by_object.items():
    coverage = coverage_by_object[object_id]

    if (
        int(coverage["main_window_rows"])
        != int(manifest["main_window_rows"])
    ):
        raise ReviewError(
            "coverage/main-window count mismatch for "
            + object_id
        )

    frozen_class = coverage[
        "predecessor_status"
    ].strip()

    final_class = manifest[
        "final_predecessor_class"
    ].strip()

    if frozen_class == (
        "CAPTURED_IN_BATCH_UNION"
    ):
        if final_class != (
            "PREDECESSOR_CAPTURED_IN_BATCH_UNION"
        ):
            raise ReviewError(
                "captured predecessor classification mismatch"
            )

        checks = (
            (
                "selected_predecessor_GP_ID",
                "predecessor_GP_ID",
            ),
            (
                "selected_predecessor_CREATION_DATE",
                "predecessor_CREATION_DATE",
            ),
            (
                "selected_predecessor_EPOCH",
                "predecessor_EPOCH",
            ),
            (
                "selected_predecessor_source_raw_path",
                "predecessor_raw_path",
            ),
            (
                "selected_predecessor_source_raw_sha256",
                "predecessor_raw_SHA256",
            ),
        )

        for left, right in checks:
            if (
                coverage[left].strip()
                != manifest[right].strip()
            ):
                raise ReviewError(
                    "captured predecessor provenance mismatch "
                    f"for {object_id}: {left}"
                )

    elif frozen_class == (
        "REQUIRES_DEDICATED_PREDECESSOR_QUERY"
    ):
        if final_class not in {
            "PREDECESSOR_ACQUIRED_BY_QUERY",
            "LEFT_CENSORED_NO_PREDECESSOR_RETURNED",
        }:
            raise ReviewError(
                "query-required object has invalid final class"
            )

    else:
        raise ReviewError(
            "unexpected coverage predecessor classification"
        )


# ------------------------------------------------------------
# MAIN RAW PROVENANCE WITHOUT REHASHING ~3 GB
# ------------------------------------------------------------

main_state = json.loads(
    MAIN_STATE.read_text(
        encoding="utf-8"
    )
)

main_success = [
    record
    for record in main_state["records"]
    if record.get("kind") == "main"
    and record.get("status") == "SUCCESS"
]

if len(main_success) != EXPECTED_MAIN_FILES:
    raise ReviewError(
        "main-success count is not 1386"
    )

main_by_path = {}

for record in main_success:
    path = str(record["raw_filename"])
    sha = str(record["sha256"])

    if path in main_by_path:
        raise ReviewError(
            "duplicate main raw path in state"
        )

    if not re.fullmatch(
        r"[0-9a-f]{64}",
        sha,
    ):
        raise ReviewError(
            "invalid main SHA format"
        )

    if not (
        ROOT / path
    ).is_file():
        raise ReviewError(
            "main raw file missing: "
            + path
        )

    main_by_path[path] = sha

if len(main_by_path) != EXPECTED_MAIN_FILES:
    raise ReviewError(
        "main raw path count is not 1386"
    )

for object_id, row in by_object.items():
    path = row[
        "main_raw_path"
    ].strip()

    sha = row[
        "main_raw_SHA256"
    ].strip()

    if main_by_path.get(path) != sha:
        raise ReviewError(
            "manifest/main-state provenance mismatch for "
            + object_id
        )


# ------------------------------------------------------------
# FULL DEDICATED-PREDECESSOR EVIDENCE REHASH
# ------------------------------------------------------------

pred_state = json.loads(
    PRED_STATE.read_text(
        encoding="utf-8"
    )
)

pred_records = pred_state.get(
    "records",
    [],
)

if len(pred_records) != EXPECTED_PRED_RECORDS:
    raise ReviewError(
        "predecessor state is not 6347 records"
    )

pred_by_object = {}

evidence_hash_verified = 0
evidence_bytes_verified = 0
success_csv_rows_verified = 0

for sequence, record in enumerate(
    pred_records,
    start=1,
):
    if int(record["request_sequence"]) != sequence:
        raise ReviewError(
            "predecessor state sequence discontinuity"
        )

    object_id = str(
        record["NORAD_CAT_ID"]
    )

    if object_id in pred_by_object:
        raise ReviewError(
            "duplicate dedicated predecessor object"
        )

    status = record["status"]

    if status not in {
        "SUCCESS",
        "LEFT_CENSORED_NO_PREDECESSOR_RETURNED",
    }:
        raise ReviewError(
            "nonterminal dedicated predecessor status"
        )

    evidence_rel = str(
        record.get("raw_filename") or ""
    )

    evidence_sha = str(
        record.get("sha256") or ""
    )

    if (
        not evidence_rel
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            evidence_sha,
        )
    ):
        raise ReviewError(
            "predecessor evidence provenance incomplete for "
            + object_id
        )

    evidence = ROOT / evidence_rel

    if not evidence.is_file():
        raise ReviewError(
            "predecessor evidence file missing: "
            + evidence_rel
        )

    actual_sha = sha_file(
        evidence
    )

    if actual_sha != evidence_sha:
        raise ReviewError(
            "predecessor evidence SHA mismatch: "
            + evidence_rel
        )

    evidence_hash_verified += 1
    evidence_bytes_verified += (
        evidence.stat().st_size
    )

    manifest = by_object.get(
        object_id
    )

    if manifest is None:
        raise ReviewError(
            "dedicated predecessor object not found in manifest"
        )

    if (
        manifest[
            "predecessor_evidence_path"
        ].strip()
        != evidence_rel
        or manifest[
            "predecessor_evidence_SHA256"
        ].strip()
        != evidence_sha
    ):
        raise ReviewError(
            "manifest/state predecessor evidence mismatch for "
            + object_id
        )

    lower = parse_utc(
        manifest["lower_bound_utc"]
    )

    if status == "SUCCESS":
        if (
            manifest[
                "final_predecessor_class"
            ].strip()
            != "PREDECESSOR_ACQUIRED_BY_QUERY"
        ):
            raise ReviewError(
                "SUCCESS predecessor class mismatch"
            )

        rows = read_csv(
            evidence
        )

        if len(rows) != 1:
            raise ReviewError(
                "SUCCESS predecessor evidence is not one row"
            )

        raw = rows[0]

        if (
            raw["NORAD_CAT_ID"].strip()
            != object_id
        ):
            raise ReviewError(
                "SUCCESS predecessor raw object mismatch"
            )

        if (
            raw["GP_ID"].strip()
            != str(record["GP_ID"])
        ):
            raise ReviewError(
                "SUCCESS predecessor GP_ID mismatch"
            )

        raw_creation = parse_utc(
            raw["CREATION_DATE"]
        )

        raw_epoch = parse_utc(
            raw["EPOCH"]
        )

        if (
            raw_creation
            != parse_utc(
                str(
                    record[
                        "CREATION_DATE"
                    ]
                )
            )
            or raw_epoch
            != parse_utc(
                str(
                    record[
                        "EPOCH"
                    ]
                )
            )
        ):
            raise ReviewError(
                "SUCCESS predecessor timestamp mismatch"
            )

        if not raw_creation < lower:
            raise ReviewError(
                "SUCCESS predecessor violates causal lower bound"
            )

        success_csv_rows_verified += 1

    else:
        if (
            manifest[
                "final_predecessor_class"
            ].strip()
            != "LEFT_CENSORED_NO_PREDECESSOR_RETURNED"
        ):
            raise ReviewError(
                "left-censored predecessor class mismatch"
            )

        if int(
            record.get("row_count") or 0
        ) != 0:
            raise ReviewError(
                "left-censored predecessor row_count != 0"
            )

    pred_by_object[
        object_id
    ] = record

if evidence_hash_verified != EXPECTED_PRED_RECORDS:
    raise ReviewError(
        "not all 6347 predecessor evidence files were verified"
    )

if success_csv_rows_verified != EXPECTED_QUERY_SUCCESS:
    raise ReviewError(
        "not all 5032 SUCCESS predecessor CSVs were verified"
    )


# ------------------------------------------------------------
# SUMMARY CROSS-CHECK
# ------------------------------------------------------------

summary = json.loads(
    SUMMARY.read_text(
        encoding="utf-8"
    )
)

expected_summary = {
    "status":
        "G6_CAUSAL_HISTORY_ASSEMBLY_PASS",

    "network_requests":
        0,

    "protected_outcome_access":
        False,

    "propagation_performed":
        False,

    "reconstruction_performed":
        False,

    "reference_outcome_source_opened":
        False,

    "events":
        EXPECTED_EVENTS,

    "objects":
        EXPECTED_OBJECTS,

    "main_window_rows":
        EXPECTED_MAIN_ROWS,

    "predecessor_available_objects":
        EXPECTED_CAPTURED
        + EXPECTED_QUERY_SUCCESS,

    "left_censored_objects":
        EXPECTED_LEFT,

    "causal_history_record_count":
        EXPECTED_CAUSAL_ROWS,

    "raw_content_rehash_performed_in_this_stage":
        False,
}

for key, expected in expected_summary.items():
    if summary.get(key) != expected:
        raise ReviewError(
            "summary invariant mismatch: "
            f"{key}={summary.get(key)!r}"
        )

if (
    summary[
        "final_causal_history_audit_sha256"
    ]
    != EXPECTED_AUDIT_SHA
):
    raise ReviewError(
        "summary does not bind expected audit SHA"
    )


# ------------------------------------------------------------
# REVIEW OUTPUT
# ------------------------------------------------------------

result = {
    "status":
        "G6_CAUSAL_HISTORY_MANIFEST_REVIEW_PASS",

    "network_requests":
        0,

    "protected_outcome_access":
        False,

    "propagation_performed":
        False,

    "reconstruction_performed":
        False,

    "reference_outcome_source_opened":
        False,

    "events_verified":
        EXPECTED_EVENTS,

    "objects_verified":
        EXPECTED_OBJECTS,

    "object_disjointness_verified":
        True,

    "events_with_exactly_two_objects":
        EXPECTED_EVENTS,

    "main_window_rows_verified":
        main_rows_total,

    "causal_history_record_count_verified":
        causal_rows_total,

    "predecessor_available_objects":
        available_count,

    "predecessor_class_counts":
        dict(
            sorted(
                class_counts.items()
            )
        ),

    "main_raw_files_present":
        len(main_by_path),

    "main_raw_full_rehash_performed":
        False,

    "main_raw_integrity_basis":
        "FROZEN_STREAMING_COVERAGE_AUDIT_AND_MAIN_STATE_SHA",

    "dedicated_predecessor_evidence_files_rehashed":
        evidence_hash_verified,

    "dedicated_predecessor_evidence_bytes_rehashed":
        evidence_bytes_verified,

    "dedicated_predecessor_success_csv_rows_verified":
        success_csv_rows_verified,

    "left_censored_no_imputation_verified":
        EXPECTED_LEFT,

    "strict_predecessor_lower_bound_verified":
        True,

    "main_window_bounds_verified_from_frozen_audit":
        True,

    "builder_sha256":
        EXPECTED_BUILDER_SHA,

    "manifest_audit_sha256":
        EXPECTED_AUDIT_SHA,

    "manifest_summary_sha256":
        EXPECTED_SUMMARY_SHA,

    "next_safe_action":
        "FREEZE_CAUSAL_HISTORY_MANIFEST_BEFORE_48H_REPLAY",
}

atomic_json(
    OUT,
    result,
)

print(
    json.dumps(
        {
            **result,
            "review_artifact_sha256":
                sha_file(OUT),
        },
        indent=2,
        sort_keys=True,
    )
)
