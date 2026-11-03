#!/usr/bin/env python3
"""Build the frozen G6 causal-history object manifest.

This stage is deliberately pre-outcome and offline.

It combines:
- frozen G6 sample metadata;
- frozen main-history coverage/provenance;
- frozen completed predecessor state;
- frozen predecessor-acquisition closure.

It does NOT:
- contact Space-Track or any network service;
- open protected TraCSS outcome sources;
- propagate or reconstruct orbits;
- evaluate G6 outcomes;
- duplicate/materialize the ~5.15 million main-window GP rows;
- rescan/re-hash the ~3 GB main raw corpus.

Raw byte integrity was established by the frozen acquisition/coverage audits.
The subsequent replay implementation must reverify raw SHA-256 values inline
while reading the raw records needed for replay.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_HEAD = (
    "061759abbb32ffe755d037efd0799416a7310d94"
)

CLOSURE_TAG = (
    "g6-predecessor-acquisition-complete"
)

SAMPLE = (
    ROOT
    / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"
)

COVERAGE = (
    ROOT
    / "results/g6/G6_Main_History_Coverage_Audit.csv"
)

COVERAGE_SUMMARY = (
    ROOT
    / "results/g6/G6_Main_History_Coverage_Summary.json"
)

REQUEST_REGISTER = (
    ROOT
    / "docs/research/G6_Predecessor_Request_Register.csv"
)

MAIN_STATE = (
    ROOT
    / "data/raw/g6/G6_SpaceTrack_Acquisition_State.json"
)

PRED_STATE = (
    ROOT
    / "data/raw/g6/G6_SpaceTrack_Predecessor_Acquisition_State.json"
)

PRED_CLOSURE = (
    ROOT
    / "results/g6/G6_Predecessor_Acquisition_Closure_Audit.json"
)

OUT_AUDIT = (
    ROOT
    / "results/g6/G6_Final_Causal_History_Audit.csv"
)

OUT_SUMMARY = (
    ROOT
    / "results/g6/G6_Final_Causal_History_Summary.json"
)


EXPECTED_HASHES = {
    SAMPLE:
        "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef",

    COVERAGE:
        "8458dd3d783a29a298c129688ea67dd031ff2a4fc56a7fb37df66d2f755fa3fb",

    COVERAGE_SUMMARY:
        "9ca84a635740fb6c2ebe73b72a6265d9a6efb6b11a11e826831095009832b620",

    REQUEST_REGISTER:
        "fde612f6bea808babc5352002a2f91b86dc83e92f3a27a8e9210e2e296082a3b",

    MAIN_STATE:
        "e108db07530c58464fca223cdd2c5a54d850d23110edfd120e7b440f90004149",

    PRED_STATE:
        "0c56f69f0fd3d396c5bddcb2ca863af52e7c34694b1ba81185d17feffebf709e",

    PRED_CLOSURE:
        "72f4a733b5b22dd38eba80c57a7b05e722a2164d88b779b866a63a76319e77fb",
}


EXPECTED_EVENTS = 6_927
EXPECTED_OBJECTS = 13_854
EXPECTED_MAIN_BATCHES = 1_386
EXPECTED_PREDECESSOR_REQUESTS = 6_347

EXPECTED_MAIN_WINDOW_ROWS = 5_153_040

EXPECTED_CAPTURED = 7_507
EXPECTED_QUERY_SUCCESS = 5_032
EXPECTED_LEFT_CENSORED = 1_315

EXPECTED_AVAILABLE_PREDECESSORS = (
    EXPECTED_CAPTURED
    + EXPECTED_QUERY_SUCCESS
)

EXPECTED_CAUSAL_HISTORY_RECORDS = (
    EXPECTED_MAIN_WINDOW_ROWS
    + EXPECTED_AVAILABLE_PREDECESSORS
)


AUDIT_FIELDS = (
    "final_g6_sample_index",
    "reserve_event_index",
    "g6_temporal_stratum",
    "conj_id",
    "NORAD_CAT_ID",
    "object_role",
    "reference_tca_utc",
    "lower_bound_utc",

    "main_raw_path",
    "main_raw_SHA256",
    "main_window_rows",
    "earliest_main_CREATION_DATE",
    "latest_main_CREATION_DATE",

    "final_predecessor_class",
    "predecessor_GP_ID",
    "predecessor_CREATION_DATE",
    "predecessor_EPOCH",
    "predecessor_raw_path",
    "predecessor_raw_SHA256",

    "predecessor_evidence_path",
    "predecessor_evidence_SHA256",

    "causal_history_record_count",
    "lower_boundary_status",
    "assembly_status",
)


class AssemblyError(RuntimeError):
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


def parse_utc(value: str) -> datetime:
    text = value.strip()

    if not text:
        raise ValueError(
            "empty UTC timestamp"
        )

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    parsed = datetime.fromisoformat(
        text
    )

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=UTC
        )
    else:
        parsed = parsed.astimezone(
            UTC
        )

    return parsed


def iso_utc(value: datetime) -> str:
    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:
        return list(
            csv.DictReader(stream)
        )


def atomic_write_csv(
    path: Path,
    rows: list[dict],
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
            newline="",
        ) as stream:

            writer = csv.DictWriter(
                stream,
                fieldnames=AUDIT_FIELDS,
                lineterminator="\n",
            )

            writer.writeheader()
            writer.writerows(rows)

            stream.flush()
            os.fsync(stream.fileno())

        os.replace(
            tmp,
            path,
        )

    finally:
        tmp.unlink(
            missing_ok=True
        )


def atomic_write_json(
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
            os.fsync(stream.fileno())

        os.replace(
            tmp,
            path,
        )

    finally:
        tmp.unlink(
            missing_ok=True
        )


def verify_repository_boundary() -> None:

    head = git_value(
        "rev-parse",
        "HEAD",
    )

    if head != EXPECTED_HEAD:
        raise AssemblyError(
            "HEAD changed from predecessor-acquisition "
            f"closure: {head}"
        )

    tag_target = git_value(
        "rev-parse",
        f"refs/tags/{CLOSURE_TAG}^{{commit}}",
    )

    if tag_target != EXPECTED_HEAD:
        raise AssemblyError(
            "predecessor-acquisition closure tag changed"
        )

    tracked = git_value(
        "status",
        "--porcelain",
        "--untracked-files=no",
    )

    if tracked:
        raise AssemblyError(
            "tracked Git tree is not clean"
        )


def verify_frozen_hashes() -> None:

    for path, expected in EXPECTED_HASHES.items():

        if not path.is_file():
            raise AssemblyError(
                f"missing frozen input: {path}"
            )

        actual = sha_file(path)

        if actual != expected:
            raise AssemblyError(
                "frozen input SHA mismatch\n"
                f"path={path}\n"
                f"actual={actual}\n"
                f"expected={expected}"
            )


def load_sample() -> tuple[
    dict[str, dict[str, str]],
    list[dict[str, str]],
]:

    rows = read_csv(
        SAMPLE
    )

    if len(rows) != EXPECTED_EVENTS:
        raise AssemblyError(
            f"sample count={len(rows)}"
        )

    expected_indices = list(
        range(
            1,
            EXPECTED_EVENTS + 1,
        )
    )

    indices = [
        int(
            row["final_g6_sample_index"]
        )
        for row in rows
    ]

    if indices != expected_indices:
        raise AssemblyError(
            "sample indices are not contiguous 1..6927"
        )

    object_meta: dict[
        str,
        dict[str, str],
    ] = {}

    for row in rows:

        parse_utc(
            row["reference_tca_utc"]
        )

        for role in (
            "obj1",
            "obj2",
        ):

            object_id = (
                row[role].strip()
            )

            if object_id in object_meta:
                raise AssemblyError(
                    "G6 sample is not object-disjoint: "
                    + object_id
                )

            object_meta[object_id] = {
                "final_g6_sample_index":
                    row[
                        "final_g6_sample_index"
                    ].strip(),

                "reserve_event_index":
                    row[
                        "reserve_event_index"
                    ].strip(),

                "g6_temporal_stratum":
                    row[
                        "g6_temporal_stratum"
                    ].strip(),

                "conj_id":
                    row[
                        "conj_id"
                    ].strip(),

                "reference_tca_utc":
                    row[
                        "reference_tca_utc"
                    ].strip(),

                "object_role":
                    role,
            }

    if len(object_meta) != EXPECTED_OBJECTS:
        raise AssemblyError(
            "sample object count is not 13854"
        )

    return object_meta, rows


def load_coverage(
    object_meta: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:

    rows = read_csv(
        COVERAGE
    )

    if len(rows) != EXPECTED_OBJECTS:
        raise AssemblyError(
            "coverage audit object count is not 13854"
        )

    by_object: dict[
        str,
        dict[str, str],
    ] = {}

    for row in rows:

        object_id = (
            row[
                "NORAD_CAT_ID"
            ].strip()
        )

        if object_id in by_object:
            raise AssemblyError(
                "duplicate coverage object: "
                + object_id
            )

        if object_id not in object_meta:
            raise AssemblyError(
                "coverage contains non-sample object: "
                + object_id
            )

        sample_tca = parse_utc(
            object_meta[
                object_id
            ][
                "reference_tca_utc"
            ]
        )

        coverage_tca = parse_utc(
            row[
                "reference_tca_utc"
            ]
        )

        if coverage_tca != sample_tca:
            raise AssemblyError(
                "reference-TCA mismatch for "
                + object_id
            )

        lower = parse_utc(
            row[
                "lower_bound_utc"
            ]
        )

        if (
            sample_tca - lower
        ).total_seconds() != 180 * 86400:
            raise AssemblyError(
                "180-day lower boundary mismatch for "
                + object_id
            )

        if (
            row[
                "main_window_status"
            ].strip()
            != "MAIN_WINDOW_PRESENT"
        ):
            raise AssemblyError(
                "non-present main window for "
                + object_id
            )

        if (
            row[
                "hold_status"
            ].strip()
        ):
            raise AssemblyError(
                "coverage HOLD remains for "
                + object_id
            )

        if int(
            row[
                "main_window_rows"
            ]
        ) <= 0:
            raise AssemblyError(
                "empty main window for "
                + object_id
            )

        by_object[
            object_id
        ] = row

    if set(by_object) != set(object_meta):
        raise AssemblyError(
            "coverage/sample object sets differ"
        )

    return by_object


def load_main_state(
    object_meta: dict[str, dict[str, str]],
) -> tuple[
    dict[str, dict],
    dict[str, dict],
]:

    state = json.loads(
        MAIN_STATE.read_text(
            encoding="utf-8"
        )
    )

    records = state.get(
        "records",
        [],
    )

    if len(records) != 1390:
        raise AssemblyError(
            "main acquisition state is not 1390 records"
        )

    successes = [
        record
        for record in records
        if record.get("kind") == "main"
        and record.get("status") == "SUCCESS"
    ]

    if len(successes) != EXPECTED_MAIN_BATCHES:
        raise AssemblyError(
            "main state does not contain "
            "exactly 1386 successes"
        )

    success_by_batch: dict[
        str,
        dict,
    ] = {}

    object_source: dict[
        str,
        dict,
    ] = {}

    for record in successes:

        batch_id = str(
            record[
                "batch_id"
            ]
        )

        if batch_id in success_by_batch:
            raise AssemblyError(
                "duplicate successful batch: "
                + batch_id
            )

        raw_filename = str(
            record.get(
                "raw_filename",
                "",
            )
        )

        raw_sha = str(
            record.get(
                "sha256",
                "",
            )
        )

        if (
            not raw_filename
            or not raw_sha
        ):
            raise AssemblyError(
                "successful main state lacks provenance: "
                + batch_id
            )

        raw_path = (
            ROOT / raw_filename
        )

        if not raw_path.is_file():
            raise AssemblyError(
                "main raw file is absent: "
                + raw_filename
            )

        requested = [
            str(value)
            for value in (
                record.get(
                    "requested_object_ids"
                )
                or []
            )
        ]

        returned = {
            str(value)
            for value in (
                record.get(
                    "returned_ids"
                )
                or []
            )
        }

        if (
            not requested
            or set(requested) != returned
            or record.get(
                "requested_objects_with_zero_rows"
            )
            != []
        ):
            raise AssemblyError(
                "main object-return provenance failure: "
                + batch_id
            )

        for object_id in requested:

            if object_id not in object_meta:
                raise AssemblyError(
                    "main batch contains non-sample object: "
                    + object_id
                )

            if object_id in object_source:
                raise AssemblyError(
                    "object appears in multiple successful "
                    "main batches: "
                    + object_id
                )

            object_source[
                object_id
            ] = {
                "raw_path":
                    raw_filename,

                "raw_sha256":
                    raw_sha,

                "batch_id":
                    batch_id,
            }

        success_by_batch[
            batch_id
        ] = record

    if set(object_source) != set(object_meta):
        raise AssemblyError(
            "main-state object coverage differs "
            "from final G6 sample"
        )

    return (
        success_by_batch,
        object_source,
    )


def load_request_register() -> list[dict[str, str]]:

    rows = read_csv(
        REQUEST_REGISTER
    )

    if (
        len(rows)
        != EXPECTED_PREDECESSOR_REQUESTS
    ):
        raise AssemblyError(
            "predecessor request register "
            "is not 6347 rows"
        )

    for expected_sequence, row in enumerate(
        rows,
        start=1,
    ):

        object_id = (
            row[
                "NORAD_CAT_ID"
            ].strip()
        )

        if int(
            row[
                "request_sequence"
            ]
        ) != expected_sequence:
            raise AssemblyError(
                "request register sequence mismatch"
            )

        if (
            row[
                "request_identity"
            ].strip()
            != f"predecessor-{object_id}"
        ):
            raise AssemblyError(
                "request identity mismatch for "
                + object_id
            )

        if (
            row[
                "predecessor_status"
            ].strip()
            != "REQUIRES_DEDICATED_PREDECESSOR_QUERY"
        ):
            raise AssemblyError(
                "unexpected frozen request status for "
                + object_id
            )

    return rows


def load_predecessor_state(
    request_rows: list[dict[str, str]],
) -> dict[str, dict]:

    state = json.loads(
        PRED_STATE.read_text(
            encoding="utf-8"
        )
    )

    records = state.get(
        "records",
        [],
    )

    if (
        len(records)
        != EXPECTED_PREDECESSOR_REQUESTS
    ):
        raise AssemblyError(
            "predecessor state is not 6347 records"
        )

    by_object: dict[
        str,
        dict,
    ] = {}

    accepted = {
        "SUCCESS",
        "LEFT_CENSORED_NO_PREDECESSOR_RETURNED",
    }

    for expected_sequence, (
        frozen,
        record,
    ) in enumerate(
        zip(
            request_rows,
            records,
        ),
        start=1,
    ):

        object_id = (
            frozen[
                "NORAD_CAT_ID"
            ].strip()
        )

        if (
            int(
                record[
                    "request_sequence"
                ]
            )
            != expected_sequence
        ):
            raise AssemblyError(
                "predecessor state sequence mismatch"
            )

        if (
            str(
                record[
                    "NORAD_CAT_ID"
                ]
            )
            != object_id
        ):
            raise AssemblyError(
                "predecessor state object mismatch "
                f"at sequence {expected_sequence}"
            )

        if (
            record[
                "request_id"
            ]
            != f"predecessor-{object_id}"
        ):
            raise AssemblyError(
                "predecessor request-id mismatch for "
                + object_id
            )

        if (
            record.get(
                "automatic_retry"
            )
            is not False
        ):
            raise AssemblyError(
                "automatic-retry invariant failed for "
                + object_id
            )

        status = (
            record.get(
                "status"
            )
        )

        if status not in accepted:
            raise AssemblyError(
                "nonterminal predecessor state for "
                f"{object_id}: {status}"
            )

        lower = parse_utc(
            frozen[
                "lower_bound_utc"
            ]
        )

        exact = parse_utc(
            record[
                "exact_scientific_cutoff_utc"
            ]
        )

        if exact != lower:
            raise AssemblyError(
                "dedicated predecessor cutoff mismatch for "
                + object_id
            )

        evidence_path_text = str(
            record.get(
                "raw_filename"
            )
            or ""
        )

        evidence_sha = str(
            record.get(
                "sha256"
            )
            or ""
        )

        if (
            not evidence_path_text
            or not evidence_sha
        ):
            raise AssemblyError(
                "terminal predecessor record lacks "
                "retained evidence for "
                + object_id
            )

        evidence_path = (
            ROOT
            / evidence_path_text
        )

        if not evidence_path.is_file():
            raise AssemblyError(
                "predecessor evidence file absent: "
                + evidence_path_text
            )

        if status == "SUCCESS":

            if int(
                record.get(
                    "row_count"
                )
                or 0
            ) != 1:
                raise AssemblyError(
                    "SUCCESS predecessor row_count != 1 "
                    "for "
                    + object_id
                )

            gp_id = str(
                record.get(
                    "GP_ID"
                )
                or ""
            )

            creation_text = str(
                record.get(
                    "CREATION_DATE"
                )
                or ""
            )

            epoch_text = str(
                record.get(
                    "EPOCH"
                )
                or ""
            )

            if (
                not gp_id
                or not creation_text
                or not epoch_text
            ):
                raise AssemblyError(
                    "SUCCESS predecessor metadata incomplete "
                    "for "
                    + object_id
                )

            creation = parse_utc(
                creation_text
            )

            parse_utc(
                epoch_text
            )

            if not creation < lower:
                raise AssemblyError(
                    "dedicated predecessor violates strict "
                    "lower causal bound for "
                    + object_id
                )

        else:

            if int(
                record.get(
                    "row_count"
                )
                or 0
            ) != 0:
                raise AssemblyError(
                    "left-censored predecessor row_count "
                    "is not zero for "
                    + object_id
                )

            for field in (
                "GP_ID",
                "CREATION_DATE",
                "EPOCH",
            ):

                if record.get(
                    field
                ) not in (
                    None,
                    "",
                ):
                    raise AssemblyError(
                        "left-censored predecessor has "
                        f"unexpected {field} for "
                        + object_id
                    )

        if object_id in by_object:
            raise AssemblyError(
                "duplicate predecessor object: "
                + object_id
            )

        by_object[
            object_id
        ] = record

    return by_object


def verify_closure() -> dict:

    closure = json.loads(
        PRED_CLOSURE.read_text(
            encoding="utf-8"
        )
    )

    expected = {
        "status":
            "G6_PREDECESSOR_ACQUISITION_CLOSURE_PASS",

        "completed_dedicated_requests":
            6347,

        "remaining_dedicated_requests":
            0,

        "total_state_records":
            6347,

        "campaign_terminal_records":
            6219,

        "raw_verified_campaign_records":
            6219,

        "protected_outcome_access":
            False,

        "network_requests_during_closure":
            0,

        "further_predecessor_acquisition_authorized":
            False,
    }

    for key, value in expected.items():

        if closure.get(key) != value:
            raise AssemblyError(
                "predecessor closure invariant changed: "
                f"{key}={closure.get(key)!r}"
            )

    return closure


def build() -> dict:

    verify_repository_boundary()
    verify_frozen_hashes()

    if (
        OUT_AUDIT.exists()
        or OUT_SUMMARY.exists()
    ):
        raise AssemblyError(
            "refusing to overwrite existing "
            "causal-history outputs"
        )

    object_meta, sample_rows = (
        load_sample()
    )

    coverage = load_coverage(
        object_meta
    )

    _, main_source = (
        load_main_state(
            object_meta
        )
    )

    request_rows = (
        load_request_register()
    )

    predecessor_state = (
        load_predecessor_state(
            request_rows
        )
    )

    closure = verify_closure()

    coverage_status_counts = Counter(
        row[
            "predecessor_status"
        ].strip()
        for row in coverage.values()
    )

    if coverage_status_counts != Counter(
        {
            "CAPTURED_IN_BATCH_UNION":
                EXPECTED_CAPTURED,

            "REQUIRES_DEDICATED_PREDECESSOR_QUERY":
                EXPECTED_PREDECESSOR_REQUESTS,
        }
    ):
        raise AssemblyError(
            "coverage predecessor classification changed: "
            f"{dict(coverage_status_counts)}"
        )

    pred_status_counts = Counter(
        record[
            "status"
        ]
        for record in predecessor_state.values()
    )

    if pred_status_counts != Counter(
        {
            "SUCCESS":
                EXPECTED_QUERY_SUCCESS,

            "LEFT_CENSORED_NO_PREDECESSOR_RETURNED":
                EXPECTED_LEFT_CENSORED,
        }
    ):
        raise AssemblyError(
            "dedicated predecessor status counts changed: "
            f"{dict(pred_status_counts)}"
        )

    output_rows: list[
        dict[str, str | int]
    ] = []

    final_class_counts = Counter()

    total_main_rows = 0
    total_causal_records = 0

    queried_objects = set(
        predecessor_state
    )

    for object_id in sorted(
        object_meta,
        key=int,
    ):

        meta = object_meta[
            object_id
        ]

        cov = coverage[
            object_id
        ]

        source = main_source[
            object_id
        ]

        main_rows = int(
            cov[
                "main_window_rows"
            ]
        )

        total_main_rows += (
            main_rows
        )

        predecessor_gp_id = ""
        predecessor_creation = ""
        predecessor_epoch = ""
        predecessor_raw_path = ""
        predecessor_raw_sha = ""
        evidence_path = ""
        evidence_sha = ""

        frozen_class = (
            cov[
                "predecessor_status"
            ].strip()
        )

        if (
            frozen_class
            == "CAPTURED_IN_BATCH_UNION"
        ):

            if object_id in queried_objects:
                raise AssemblyError(
                    "captured object unexpectedly appears "
                    "in dedicated predecessor state: "
                    + object_id
                )

            final_class = (
                "PREDECESSOR_CAPTURED_IN_BATCH_UNION"
            )

            predecessor_gp_id = (
                cov[
                    "selected_predecessor_GP_ID"
                ].strip()
            )

            predecessor_creation = (
                cov[
                    "selected_predecessor_CREATION_DATE"
                ].strip()
            )

            predecessor_epoch = (
                cov[
                    "selected_predecessor_EPOCH"
                ].strip()
            )

            predecessor_raw_path = (
                cov[
                    "selected_predecessor_source_raw_path"
                ].strip()
            )

            predecessor_raw_sha = (
                cov[
                    "selected_predecessor_source_raw_sha256"
                ].strip()
            )

            if not all(
                (
                    predecessor_gp_id,
                    predecessor_creation,
                    predecessor_epoch,
                    predecessor_raw_path,
                    predecessor_raw_sha,
                )
            ):
                raise AssemblyError(
                    "captured predecessor provenance "
                    "incomplete for "
                    + object_id
                )

            if (
                predecessor_raw_path
                != source[
                    "raw_path"
                ]
                or predecessor_raw_sha
                != source[
                    "raw_sha256"
                ]
            ):
                raise AssemblyError(
                    "captured predecessor/main-state "
                    "raw provenance mismatch for "
                    + object_id
                )

            lower = parse_utc(
                cov[
                    "lower_bound_utc"
                ]
            )

            if not parse_utc(
                predecessor_creation
            ) < lower:
                raise AssemblyError(
                    "captured predecessor violates strict "
                    "lower bound for "
                    + object_id
                )

            parse_utc(
                predecessor_epoch
            )

            lower_boundary_status = (
                "PREDECESSOR_AVAILABLE"
            )

            causal_count = (
                main_rows + 1
            )

        elif (
            frozen_class
            == "REQUIRES_DEDICATED_PREDECESSOR_QUERY"
        ):

            record = predecessor_state.get(
                object_id
            )

            if record is None:
                raise AssemblyError(
                    "missing terminal predecessor state for "
                    + object_id
                )

            evidence_path = str(
                record[
                    "raw_filename"
                ]
            )

            evidence_sha = str(
                record[
                    "sha256"
                ]
            )

            if record[
                "status"
            ] == "SUCCESS":

                final_class = (
                    "PREDECESSOR_ACQUIRED_BY_QUERY"
                )

                predecessor_gp_id = str(
                    record[
                        "GP_ID"
                    ]
                )

                predecessor_creation = (
                    iso_utc(
                        parse_utc(
                            str(
                                record[
                                    "CREATION_DATE"
                                ]
                            )
                        )
                    )
                )

                predecessor_epoch = (
                    iso_utc(
                        parse_utc(
                            str(
                                record[
                                    "EPOCH"
                                ]
                            )
                        )
                    )
                )

                predecessor_raw_path = (
                    evidence_path
                )

                predecessor_raw_sha = (
                    evidence_sha
                )

                lower_boundary_status = (
                    "PREDECESSOR_AVAILABLE"
                )

                causal_count = (
                    main_rows + 1
                )

            else:

                final_class = (
                    "LEFT_CENSORED_NO_PREDECESSOR_RETURNED"
                )

                lower_boundary_status = (
                    "LEFT_CENSORED_NO_PREDECESSOR_RETURNED"
                )

                causal_count = (
                    main_rows
                )

        else:
            raise AssemblyError(
                "unexpected coverage predecessor class "
                f"for {object_id}: {frozen_class}"
            )

        final_class_counts[
            final_class
        ] += 1

        total_causal_records += (
            causal_count
        )

        output_rows.append(
            {
                "final_g6_sample_index":
                    meta[
                        "final_g6_sample_index"
                    ],

                "reserve_event_index":
                    meta[
                        "reserve_event_index"
                    ],

                "g6_temporal_stratum":
                    meta[
                        "g6_temporal_stratum"
                    ],

                "conj_id":
                    meta[
                        "conj_id"
                    ],

                "NORAD_CAT_ID":
                    object_id,

                "object_role":
                    meta[
                        "object_role"
                    ],

                "reference_tca_utc":
                    iso_utc(
                        parse_utc(
                            meta[
                                "reference_tca_utc"
                            ]
                        )
                    ),

                "lower_bound_utc":
                    iso_utc(
                        parse_utc(
                            cov[
                                "lower_bound_utc"
                            ]
                        )
                    ),

                "main_raw_path":
                    source[
                        "raw_path"
                    ],

                "main_raw_SHA256":
                    source[
                        "raw_sha256"
                    ],

                "main_window_rows":
                    main_rows,

                "earliest_main_CREATION_DATE":
                    cov[
                        "earliest_creation_date_in_main_window"
                    ].strip(),

                "latest_main_CREATION_DATE":
                    cov[
                        "latest_creation_date_in_main_window"
                    ].strip(),

                "final_predecessor_class":
                    final_class,

                "predecessor_GP_ID":
                    predecessor_gp_id,

                "predecessor_CREATION_DATE":
                    predecessor_creation,

                "predecessor_EPOCH":
                    predecessor_epoch,

                "predecessor_raw_path":
                    predecessor_raw_path,

                "predecessor_raw_SHA256":
                    predecessor_raw_sha,

                "predecessor_evidence_path":
                    evidence_path,

                "predecessor_evidence_SHA256":
                    evidence_sha,

                "causal_history_record_count":
                    causal_count,

                "lower_boundary_status":
                    lower_boundary_status,

                "assembly_status":
                    "PASS",
            }
        )

    expected_final_classes = Counter(
        {
            "PREDECESSOR_CAPTURED_IN_BATCH_UNION":
                EXPECTED_CAPTURED,

            "PREDECESSOR_ACQUIRED_BY_QUERY":
                EXPECTED_QUERY_SUCCESS,

            "LEFT_CENSORED_NO_PREDECESSOR_RETURNED":
                EXPECTED_LEFT_CENSORED,
        }
    )

    if final_class_counts != expected_final_classes:
        raise AssemblyError(
            "final predecessor-class distribution "
            f"mismatch: {dict(final_class_counts)}"
        )

    if total_main_rows != EXPECTED_MAIN_WINDOW_ROWS:
        raise AssemblyError(
            "main-window row total changed: "
            f"{total_main_rows}"
        )

    if (
        total_causal_records
        != EXPECTED_CAUSAL_HISTORY_RECORDS
    ):
        raise AssemblyError(
            "causal-history record total mismatch: "
            f"{total_causal_records}"
        )

    if len(output_rows) != EXPECTED_OBJECTS:
        raise AssemblyError(
            "final causal-history audit row count "
            "is not 13854"
        )

    atomic_write_csv(
        OUT_AUDIT,
        output_rows,
    )

    audit_sha = sha_file(
        OUT_AUDIT
    )

    summary = {
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

        "main_window_rule":
            "[reference_tca_utc - 180 days, reference_tca_utc)",

        "causal_availability_field":
            "CREATION_DATE",

        "predecessor_selection_rule":
            "(CREATION_DATE, EPOCH, stable_tie_key); "
            "stable_tie_key=GP_ID when present, "
            "else canonical-record SHA-256",

        "left_censored_policy":
            "NO_IMPUTATION_OR_SUBSTITUTION",

        "repeated_publications_policy":
            "PRESERVE_ALL_ROWS_NO_DEDUPLICATION",

        "main_window_rows":
            total_main_rows,

        "predecessor_available_objects":
            EXPECTED_AVAILABLE_PREDECESSORS,

        "left_censored_objects":
            EXPECTED_LEFT_CENSORED,

        "causal_history_record_count":
            total_causal_records,

        "final_predecessor_class_counts":
            dict(
                sorted(
                    final_class_counts.items()
                )
            ),

        "main_raw_files_expected":
            1386,

        "dedicated_predecessor_records":
            6347,

        "dedicated_predecessor_success":
            EXPECTED_QUERY_SUCCESS,

        "dedicated_predecessor_left_censored":
            EXPECTED_LEFT_CENSORED,

        "raw_content_rehash_performed_in_this_stage":
            False,

        "raw_reverification_requirement":
            "REVERIFY_FROZEN_RAW_SHA256_INLINE_DURING_REPLAY_READ",

        "final_causal_history_audit":
            OUT_AUDIT.relative_to(
                ROOT
            ).as_posix(),

        "final_causal_history_audit_sha256":
            audit_sha,

        "input_sha256": {
            path.relative_to(
                ROOT
            ).as_posix():
                digest

            for path, digest
            in EXPECTED_HASHES.items()
        },

        "predecessor_closure_campaign_terminal_records":
            closure[
                "campaign_terminal_records"
            ],

        "predecessor_closure_raw_verified_campaign_records":
            closure[
                "raw_verified_campaign_records"
            ],

        "next_safe_action":
            "INDEPENDENT_REVIEW_AND_FREEZE_CAUSAL_HISTORY_MANIFEST_BEFORE_REPLAY",
    }

    atomic_write_json(
        OUT_SUMMARY,
        summary,
    )

    print(
        json.dumps(
            {
                **summary,
                "final_causal_history_summary_sha256":
                    sha_file(
                        OUT_SUMMARY
                    ),
            },
            indent=2,
            sort_keys=True,
        )
    )

    return summary


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--build",
        action="store_true",
        required=True,
    )

    args = parser.parse_args()

    if args.build:
        build()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
