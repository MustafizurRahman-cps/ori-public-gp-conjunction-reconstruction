#!/usr/bin/env python3
"""G7 R6 A2 causal-history materializer.

Two modes are intentionally separated.

--offline-validate
    Verifies the frozen repository/input boundary, schemas, hashes, raw-file
    inventory, Git-ignore status, and deterministic helper logic. It does NOT
    open GP_HISTORY file contents, parse TLEs, propagate orbits, generate
    candidates, or write result artifacts.

--materialize --authorize-materialization
    Performs the single controlled A2 raw-history materialization pass.
    Every one of the 1,386 frozen main GP_HISTORY files is SHA-256 verified
    inline while it is read. The pass constructs a local-only SQLite database
    containing the publication-driven replay schedule, causal current-GP
    identities, 30-day-quality source records, and the minimal TLE payloads
    needed by the temporally supported orbital replays. TLE parse validity is
    checked after selection, but no SGP4 propagation is performed.

This implementation deliberately treats the previously frozen 107,740
temporal-support count as an upper bound until current-GP selection and parse
validity are re-established from the hash-verified history corpus.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import math
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_HEAD = "da5dfeb8f18d3d4ed9a96a33e3ba64010debe839"
ARCHITECTURE_TAG = "g7-r6-a2-execution-architecture-frozen"

EXPECTED_EVENTS = 6_927
EXPECTED_OBJECTS = 13_854
EXPECTED_MAIN_FILES = 1_386
EXPECTED_RAW_ROWS = 5_245_230
EXPECTED_LOCAL_MAIN_ROWS = 5_153_040
EXPECTED_CAUSAL_HISTORY_RECORDS = 5_165_579

EXPECTED_ELIGIBLE_REPLAYS = 2_020_081
EXPECTED_TEMPORAL_SUPPORT_REPLAYS = 107_740
EXPECTED_TOO_EARLY_REPLAYS = 1_912_341
EXPECTED_BOUNDARY_HITS = 0
EXPECTED_ZERO_SUPPORT_EVENTS = 1

REPORTING_DAYS = 90
QUALITY_WINDOW_DAYS = 30
PRIMARY_HORIZON_DAYS = 5
ASSOCIATION_TOLERANCE_MIN = 5

AUDIT = ROOT / "results/g6/G6_Final_Causal_History_Audit.csv"
BATCH_REGISTER = ROOT / "docs/research/G6_GPHistory_Production_Batch_Register.csv"

TEMPORAL_SUPPORT_EVENT = (
    ROOT / "results/g7/G7R6_A2_Temporal_Support_Per_Event.csv"
)
TEMPORAL_SUPPORT_SUMMARY = (
    ROOT / "results/g7/G7R6_A2_Temporal_Support_Summary.json"
)

ARCHITECTURE_SPEC = (
    ROOT / "docs/research/G7_R6_A2_Implementation_Execution_Architecture.md"
)
ARCHITECTURE_DECISION = (
    ROOT
    / "docs/research/DR-0057_G7_R6_A2_Implementation_Execution_Architecture_Freeze.md"
)

G4_NUMERICS = ROOT / "scripts/g4/numerics.py"
G5_CONTROLFLOW = ROOT / "scripts/g5/select_lstar_controlflow_amended.py"
G6_CANDIDATE = ROOT / "scripts/g6/generate_g6_preoutcome_candidates.py"
G7B_HISTORY = ROOT / "scripts/g7/run_g7b_single_pass_history_quality.py"

MATERIALIZED_DB = ROOT / "data/raw/g7/G7R6_A2_Materialized.sqlite3"
INCOMPLETE_DB = ROOT / "data/raw/g7/G7R6_A2_Materialized.incomplete.sqlite3"
SUMMARY_OUT = ROOT / "results/g7/G7R6_A2_Materialization_Summary.json"
AUDIT_OUT = ROOT / "results/g7/G7R6_A2_Materialization_Audit.txt"

EXPECTED_HASHES = {
    AUDIT:
        "41802759162ef4889b071aa6f15391d48a7a96cce7ededdd99c8e1d8a3ebc924",
    BATCH_REGISTER:
        "02883a71d797b661b83acf13d03f6756b79c44c02fdb30f9410c7d3313ea832e",
    TEMPORAL_SUPPORT_EVENT:
        "51f59e740a04225227b50f68809dd83edc6c1aecad427a5f4450f49b772da992",
    TEMPORAL_SUPPORT_SUMMARY:
        "71bc7f7671e7e056af1bcf178e577982795d6440c33a04f34e973cf40cbfbbda",
    ARCHITECTURE_DECISION:
        "d6d3d24f2c60e74f41a9ef8f08a0319d0e7abc1bb9d5b388757ce98b55aeedbf",
    ARCHITECTURE_SPEC:
        "b1f2d6fa29cace671727315652111d694ca385d6a9836dd062e3dca6bbb8da3f",
    G4_NUMERICS:
        "dc8de410b89e05ffd829563b4996b06703c31236e9a10fb56bd7f4e690048406",
    G5_CONTROLFLOW:
        "553578f4e90b044c6a783209a756ea3accc009ecbb7a09853e8cae46503f33c2",
    G6_CANDIDATE:
        "bce2a4b5e4dab8d0c4e461e2acb4dbac8afe22d3ac412922ba3670a832adcecb",
    G7B_HISTORY:
        "5e52461afb51a3fde4561231f845a073ede9f57a90db9549be82c5ab2fc58676",
}

EXPECTED_PACKAGES = {
    "numpy": "2.3.5",
    "scipy": "1.18.1",
    "sgp4": "2.27",
}

AVAILABLE_PREDECESSOR_CLASSES = {
    "PREDECESSOR_CAPTURED_IN_BATCH_UNION",
    "PREDECESSOR_ACQUIRED_BY_QUERY",
}

EXPECTED_PREDECESSOR_COUNTS = {
    "PREDECESSOR_CAPTURED_IN_BATCH_UNION": 7_507,
    "PREDECESSOR_ACQUIRED_BY_QUERY": 5_032,
    "LEFT_CENSORED_NO_PREDECESSOR_RETURNED": 1_315,
}

REQUIRED_RAW_FIELDS = {
    "NORAD_CAT_ID",
    "CREATION_DATE",
    "EPOCH",
    "GP_ID",
    "TLE_LINE1",
    "TLE_LINE2",
}

UTC0 = datetime(1970, 1, 1, tzinfo=UTC)


class MaterializationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ObjectSpec:
    object_id: str
    event_index: int
    role: str
    conj_id: str
    stratum: str
    reference_utc: datetime
    lower_utc: datetime
    report_start_utc: datetime
    quality_start_utc: datetime
    support_start_utc: datetime
    main_raw_path: Path
    main_raw_sha256: str
    main_window_rows: int
    predecessor_class: str
    predecessor_gp_id: str
    predecessor_creation_utc: datetime | None
    predecessor_epoch_utc: datetime | None
    predecessor_raw_path: Path | None
    predecessor_raw_sha256: str


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MaterializationError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def git_value(*args: str) -> str:
    return git(*args).stdout.strip()


def parse_utc(value: str, *, schema_declares_utc: bool = True) -> datetime:
    text = str(value).strip()
    if not text:
        raise MaterializationError("empty UTC timestamp")
    text = text.replace(" ", "T")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MaterializationError(
            f"malformed UTC timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        if not schema_declares_utc:
            raise MaterializationError(
                "naive timestamp without explicit UTC schema"
            )
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso_utc(value: datetime) -> str:
    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def to_us(value: datetime) -> int:
    delta = value.astimezone(UTC) - UTC0
    return (
        delta.days * 86400 * 1_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def from_us(value: int) -> datetime:
    return UTC0 + timedelta(microseconds=int(value))


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames is not None, f"missing CSV header: {path}")
        rows = [
            {
                (key or ""): (value if value is not None else "")
                for key, value in row.items()
            }
            for row in reader
        ]
        return list(reader.fieldnames), rows


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def normalize_repo_path(text: str) -> Path:
    path = Path(str(text).strip())
    if not path.is_absolute():
        path = ROOT / path
    return Path(os.path.abspath(os.path.normpath(str(path))))


def path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class HashingRawReader(io.RawIOBase):
    """Binary wrapper that hashes exactly the bytes consumed by TextIOWrapper."""

    def __init__(self, raw, digest: "hashlib._Hash"):
        super().__init__()
        self._raw = raw
        self._digest = digest

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self._raw.read(len(buffer))
        if not data:
            return 0
        self._digest.update(data)
        buffer[: len(data)] = data
        return len(data)

    def close(self) -> None:
        try:
            self._raw.close()
        finally:
            super().close()


def open_hashed_csv(path: Path):
    """Return (TextIOWrapper, digest) for one-pass CSV + whole-file SHA-256."""
    digest = hashlib.sha256()
    raw = path.open("rb")
    hashing = HashingRawReader(raw, digest)
    buffered = io.BufferedReader(hashing, buffer_size=1024 * 1024)
    text = io.TextIOWrapper(
        buffered,
        encoding="utf-8-sig",
        newline="",
    )
    return text, digest


def canonical_state_signature(
    row: dict[str, str],
    epoch_utc: datetime,
) -> tuple[str, ...]:
    """Exact G7B canonical state-signature semantics, source hash bound."""
    line1 = str(row.get("TLE_LINE1", "")).rstrip("\r\n")
    line2 = str(row.get("TLE_LINE2", "")).rstrip("\r\n")

    if (
        len(line1) < 63
        or len(line2) < 63
        or not line1.startswith("1 ")
        or not line2.startswith("2 ")
    ):
        raise MaterializationError(
            "malformed TLE in canonical state signature"
        )

    return (
        iso_utc(epoch_utc),
        line1[33:43].strip(),
        line1[44:52].strip(),
        line1[53:61].strip(),
        line1[62:63].strip(),
        line2[8:16].strip(),
        line2[17:25].strip(),
        line2[26:33].strip(),
        line2[34:42].strip(),
        line2[43:51].strip(),
        line2[52:63].strip(),
    )


def stable_tie_key(row: dict[str, str]) -> str:
    gp_id = str(row.get("GP_ID", "")).strip()
    if not gp_id:
        # The frozen G6 corpus previously passed a nonempty-GP_ID check for
        # every exact main-window row. The fallback canonical-record
        # serialization itself was not frozen in the supplied implementation,
        # so inventing one here would be scientifically unsafe.
        raise MaterializationError(
            "missing GP_ID encountered; canonical SHA fallback requires "
            "separate frozen serialization review"
        )
    return gp_id


def record_key(
    object_id: str,
    creation_us: int,
    epoch_us: int,
    tie_key: str,
) -> str:
    return "|".join(
        (
            str(object_id),
            str(int(creation_us)),
            str(int(epoch_us)),
            str(tie_key),
        )
    )


def record_order(record: dict) -> tuple[int, int, str]:
    return (
        int(record["creation_us"]),
        int(record["epoch_us"]),
        str(record["stable_tie_key"]),
    )


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(
        spec is not None and spec.loader is not None,
        f"cannot load module: {path}",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_repository_boundary() -> None:
    require(
        git_value("rev-parse", "HEAD") == EXPECTED_HEAD,
        "unexpected repository HEAD",
    )
    require(
        git_value("rev-parse", f"refs/tags/{ARCHITECTURE_TAG}^{{commit}}")
        == EXPECTED_HEAD,
        "A2 architecture tag target changed",
    )
    require(
        not git_value(
            "status",
            "--porcelain",
            "--untracked-files=no",
        ),
        "tracked working tree is not clean",
    )


def verify_small_hashes() -> dict[str, str]:
    observed: dict[str, str] = {}
    for path, expected in EXPECTED_HASHES.items():
        require(path.is_file(), f"frozen dependency missing: {path}")
        actual = sha256(path)
        require(
            actual == expected,
            "frozen dependency SHA mismatch\n"
            f"path={relative(path)}\nactual={actual}\nexpected={expected}",
        )
        observed[relative(path)] = actual
    return observed


def verify_environment() -> dict:
    packages = {
        package: importlib.metadata.version(package)
        for package in EXPECTED_PACKAGES
    }
    require(
        packages == EXPECTED_PACKAGES,
        f"runtime package versions changed: {packages}",
    )
    require(
        sys.version_info[:3] == (3, 12, 14),
        "Python runtime changed: " + platform.python_version(),
    )
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
    }


def load_control_inputs():
    audit_header, audit_rows = read_csv(AUDIT)
    batch_header, batch_rows = read_csv(BATCH_REGISTER)
    support_header, support_rows = read_csv(TEMPORAL_SUPPORT_EVENT)

    required_audit = {
        "final_g6_sample_index",
        "g6_temporal_stratum",
        "conj_id",
        "NORAD_CAT_ID",
        "object_role",
        "reference_tca_utc",
        "lower_bound_utc",
        "main_raw_path",
        "main_raw_SHA256",
        "main_window_rows",
        "final_predecessor_class",
        "predecessor_GP_ID",
        "predecessor_CREATION_DATE",
        "predecessor_EPOCH",
        "predecessor_raw_path",
        "predecessor_raw_SHA256",
        "causal_history_record_count",
        "assembly_status",
    }
    require(
        required_audit.issubset(set(audit_header)),
        "causal-history audit schema changed",
    )

    required_batch = {
        "batch_sequence",
        "batch_id",
        "batch_object_count",
        "norad_cat_id",
        "object_role",
        "conj_id",
        "final_g6_sample_index",
        "g6_temporal_stratum",
        "reference_tca_utc",
        "object_lower_bound_utc",
        "raw_relative_path",
    }
    require(
        required_batch.issubset(set(batch_header)),
        "production batch-register schema changed",
    )

    required_support = {
        "final_g6_sample_index",
        "conj_id",
        "reference_tca_utc",
        "temporal_support_boundary_utc",
        "effective_support_start_utc",
        "frozen_eligible_A2_replay_epoch_count",
        "temporal_support_epoch_count_inclusive",
        "temporal_support_boundary_exact_hit_count",
        "provably_too_early_epoch_count",
    }
    require(
        required_support.issubset(set(support_header)),
        "temporal-support event schema changed",
    )

    require(len(audit_rows) == EXPECTED_OBJECTS, "audit row count changed")
    require(len(batch_rows) == EXPECTED_OBJECTS, "batch-register row count changed")
    require(len(support_rows) == EXPECTED_EVENTS, "support event count changed")

    support_by_event = {}
    support_eligible_sum = 0
    support_sum = 0
    too_early_sum = 0
    boundary_hits = 0
    zero_support = 0

    for row in support_rows:
        index = int(row["final_g6_sample_index"])
        require(index not in support_by_event, f"duplicate support event {index}")
        support_by_event[index] = row
        support_eligible_sum += int(
            row["frozen_eligible_A2_replay_epoch_count"]
        )
        support_count = int(row["temporal_support_epoch_count_inclusive"])
        support_sum += support_count
        too_early_sum += int(row["provably_too_early_epoch_count"])
        boundary_hits += int(
            row["temporal_support_boundary_exact_hit_count"]
        )
        zero_support += int(support_count == 0)

    require(
        support_eligible_sum == EXPECTED_ELIGIBLE_REPLAYS,
        "frozen eligible replay total changed",
    )
    require(
        support_sum == EXPECTED_TEMPORAL_SUPPORT_REPLAYS,
        "frozen temporal-support total changed",
    )
    require(
        too_early_sum == EXPECTED_TOO_EARLY_REPLAYS,
        "frozen too-early total changed",
    )
    require(boundary_hits == EXPECTED_BOUNDARY_HITS, "boundary-hit count changed")
    require(
        zero_support == EXPECTED_ZERO_SUPPORT_EVENTS,
        "zero-support event count changed",
    )

    objects: dict[str, ObjectSpec] = {}
    events: dict[int, dict] = {}
    raw_hashes: dict[str, str] = {}
    objects_by_raw: dict[str, list[str]] = defaultdict(list)
    predecessor_counts: Counter[str] = Counter()
    main_row_sum = 0
    causal_row_sum = 0

    for row in audit_rows:
        require(row["assembly_status"].strip() == "PASS", "non-PASS audit row")

        event_index = int(row["final_g6_sample_index"])
        require(event_index in support_by_event, "audit event absent from support freeze")
        support = support_by_event[event_index]

        object_id = row["NORAD_CAT_ID"].strip()
        require(object_id and object_id.isdigit(), f"invalid NORAD ID: {object_id!r}")
        require(object_id not in objects, f"duplicate object: {object_id}")

        role = row["object_role"].strip()
        require(role in {"obj1", "obj2"}, f"invalid role: {role}")

        conj_id = row["conj_id"].strip()
        stratum = row["g6_temporal_stratum"].strip()
        reference = parse_utc(row["reference_tca_utc"])
        lower = parse_utc(row["lower_bound_utc"])

        require(
            support["conj_id"].strip() == conj_id,
            f"support/audit conjunction mismatch for event {event_index}",
        )
        require(
            parse_utc(support["reference_tca_utc"]) == reference,
            f"support/audit TCA mismatch for event {event_index}",
        )
        require(
            lower == reference - timedelta(days=180),
            f"180-day lower bound mismatch for object {object_id}",
        )

        report_start = reference - timedelta(days=REPORTING_DAYS)
        quality_start = report_start - timedelta(days=QUALITY_WINDOW_DAYS)
        support_start = parse_utc(support["effective_support_start_utc"])

        main_path = normalize_repo_path(row["main_raw_path"])
        main_sha = row["main_raw_SHA256"].strip().lower()
        require(len(main_sha) == 64, f"invalid main SHA for {object_id}")

        raw_key = path_key(main_path)
        prior_hash = raw_hashes.setdefault(raw_key, main_sha)
        require(
            prior_hash == main_sha,
            f"inconsistent expected SHA for raw file {main_path}",
        )

        predecessor_class = row["final_predecessor_class"].strip()
        require(
            predecessor_class in EXPECTED_PREDECESSOR_COUNTS,
            f"unexpected predecessor class: {predecessor_class}",
        )
        predecessor_counts[predecessor_class] += 1

        pred_creation = (
            parse_utc(row["predecessor_CREATION_DATE"])
            if row["predecessor_CREATION_DATE"].strip()
            else None
        )
        pred_epoch = (
            parse_utc(row["predecessor_EPOCH"])
            if row["predecessor_EPOCH"].strip()
            else None
        )
        pred_path = (
            normalize_repo_path(row["predecessor_raw_path"])
            if row["predecessor_raw_path"].strip()
            else None
        )
        pred_sha = row["predecessor_raw_SHA256"].strip().lower()

        if predecessor_class in AVAILABLE_PREDECESSOR_CLASSES:
            require(
                row["predecessor_GP_ID"].strip(),
                f"available predecessor lacks GP_ID: {object_id}",
            )
            require(
                pred_creation is not None and pred_epoch is not None,
                f"available predecessor lacks timestamps: {object_id}",
            )
            require(
                pred_path is not None and len(pred_sha) == 64,
                f"available predecessor lacks raw provenance: {object_id}",
            )
            require(
                pred_creation < lower,
                f"predecessor is not strictly before lower bound: {object_id}",
            )
        else:
            require(
                not row["predecessor_GP_ID"].strip(),
                f"left-censored object unexpectedly has predecessor GP_ID: {object_id}",
            )

        spec = ObjectSpec(
            object_id=object_id,
            event_index=event_index,
            role=role,
            conj_id=conj_id,
            stratum=stratum,
            reference_utc=reference,
            lower_utc=lower,
            report_start_utc=report_start,
            quality_start_utc=quality_start,
            support_start_utc=support_start,
            main_raw_path=main_path,
            main_raw_sha256=main_sha,
            main_window_rows=int(row["main_window_rows"]),
            predecessor_class=predecessor_class,
            predecessor_gp_id=row["predecessor_GP_ID"].strip(),
            predecessor_creation_utc=pred_creation,
            predecessor_epoch_utc=pred_epoch,
            predecessor_raw_path=pred_path,
            predecessor_raw_sha256=pred_sha,
        )
        objects[object_id] = spec
        objects_by_raw[raw_key].append(object_id)

        main_row_sum += int(row["main_window_rows"])
        causal_row_sum += int(row["causal_history_record_count"])

        event = events.setdefault(
            event_index,
            {
                "event_index": event_index,
                "conj_id": conj_id,
                "stratum": stratum,
                "reference_utc": reference,
                "report_start_utc": report_start,
                "quality_start_utc": quality_start,
                "support_boundary_utc": parse_utc(
                    support["temporal_support_boundary_utc"]
                ),
                "effective_support_start_utc": support_start,
                "expected_eligible": int(
                    support["frozen_eligible_A2_replay_epoch_count"]
                ),
                "expected_support": int(
                    support["temporal_support_epoch_count_inclusive"]
                ),
                "roles": {},
            },
        )

        require(event["conj_id"] == conj_id, "event conjunction disagreement")
        require(event["stratum"] == stratum, "event stratum disagreement")
        require(event["reference_utc"] == reference, "event TCA disagreement")
        require(role not in event["roles"], f"duplicate role in event {event_index}")
        event["roles"][role] = object_id

    require(len(objects) == EXPECTED_OBJECTS, "object census changed")
    require(len(events) == EXPECTED_EVENTS, "event census changed")
    require(len(objects_by_raw) == EXPECTED_MAIN_FILES, "main raw file census changed")
    require(main_row_sum == EXPECTED_LOCAL_MAIN_ROWS, "main-row audit sum changed")
    require(
        causal_row_sum == EXPECTED_CAUSAL_HISTORY_RECORDS,
        "causal-history record sum changed",
    )
    require(
        predecessor_counts == Counter(EXPECTED_PREDECESSOR_COUNTS),
        f"predecessor counts changed: {dict(predecessor_counts)}",
    )

    for event in events.values():
        require(
            set(event["roles"]) == {"obj1", "obj2"},
            f"event {event['event_index']} does not have obj1/obj2 exactly",
        )

    # Production register independently maps every frozen object to a batch path.
    batch_object_map: dict[str, str] = {}
    batch_sequences: set[int] = set()
    batch_paths: set[str] = set()

    for row in batch_rows:
        object_id = row["norad_cat_id"].strip()
        require(
            object_id in objects,
            f"unexpected production-register object {object_id}",
        )
        require(
            object_id not in batch_object_map,
            f"duplicate production-register object {object_id}",
        )
        raw_path = path_key(normalize_repo_path(row["raw_relative_path"]))
        batch_object_map[object_id] = raw_path
        batch_paths.add(raw_path)
        batch_sequences.add(int(row["batch_sequence"]))

        spec = objects[object_id]
        require(
            raw_path == path_key(spec.main_raw_path),
            f"audit/register raw-path mismatch for object {object_id}",
        )
        require(
            int(row["final_g6_sample_index"]) == spec.event_index,
            f"audit/register event mismatch for object {object_id}",
        )
        require(
            row["object_role"].strip() == spec.role,
            f"audit/register role mismatch for object {object_id}",
        )
        require(
            parse_utc(row["reference_tca_utc"]) == spec.reference_utc,
            f"audit/register TCA mismatch for object {object_id}",
        )

    require(len(batch_object_map) == EXPECTED_OBJECTS, "batch object census changed")
    require(len(batch_paths) == EXPECTED_MAIN_FILES, "batch raw-path census changed")
    require(
        batch_sequences == set(range(1, EXPECTED_MAIN_FILES + 1)),
        "batch sequences are not exactly 1..1386",
    )
    require(
        batch_paths == set(objects_by_raw),
        "audit/register raw-file path sets differ",
    )

    return {
        "audit_rows": audit_rows,
        "support_rows": support_rows,
        "objects": objects,
        "events": events,
        "objects_by_raw": objects_by_raw,
        "raw_hashes": raw_hashes,
    }


def verify_raw_inventory(control) -> None:
    missing = []
    for raw_key in sorted(control["objects_by_raw"]):
        ids = control["objects_by_raw"][raw_key]
        path = control["objects"][ids[0]].main_raw_path
        if not path.is_file():
            missing.append(relative(path))
    require(
        not missing,
        f"missing main GP_HISTORY files: {len(missing)}; examples={missing[:10]}",
    )


def verify_db_ignore() -> None:
    result = git(
        "check-ignore",
        "-q",
        relative(MATERIALIZED_DB),
        check=False,
    )
    require(
        result.returncode == 0,
        f"materialized DB is not Git-ignored: {relative(MATERIALIZED_DB)}",
    )
    incomplete_ignore = git(
        "check-ignore",
        "-q",
        relative(INCOMPLETE_DB),
        check=False,
    )
    require(
        incomplete_ignore.returncode == 0,
        f"incomplete materialized DB is not Git-ignored: {relative(INCOMPLETE_DB)}",
    )
    require(
        not git_value("ls-files", "--", relative(MATERIALIZED_DB)),
        "materialized DB is unexpectedly tracked",
    )


def helper_self_tests() -> None:
    # One-pass hashing wrapper.
    payload = b"a,b\r\n1,2\r\n"
    digest = hashlib.sha256()
    raw = io.BytesIO(payload)
    hashing = HashingRawReader(raw, digest)
    buffered = io.BufferedReader(hashing, buffer_size=8)
    text = io.TextIOWrapper(buffered, encoding="utf-8-sig", newline="")
    rows = list(csv.DictReader(text))
    text.close()
    require(rows == [{"a": "1", "b": "2"}], "hashed CSV self-test failed")
    require(
        digest.hexdigest() == hashlib.sha256(payload).hexdigest(),
        "inline SHA self-test failed",
    )

    # Ordering: CREATION_DATE, then EPOCH, then stable tie.
    records = [
        {"creation_us": 10, "epoch_us": 20, "stable_tie_key": "2"},
        {"creation_us": 10, "epoch_us": 21, "stable_tie_key": "1"},
        {"creation_us": 10, "epoch_us": 21, "stable_tie_key": "3"},
    ]
    require(
        max(records, key=record_order)["stable_tie_key"] == "3",
        "current-GP ordering self-test failed",
    )


def offline_validate() -> dict:
    verify_repository_boundary()
    hashes = verify_small_hashes()
    environment = verify_environment()
    control = load_control_inputs()
    verify_raw_inventory(control)
    verify_db_ignore()
    helper_self_tests()

    require(
        not MATERIALIZED_DB.exists(),
        "materialized DB already exists before first controlled pass",
    )
    require(
        not INCOMPLETE_DB.exists(),
        "incomplete materialized DB already exists before first controlled pass",
    )
    require(
        not SUMMARY_OUT.exists() and not AUDIT_OUT.exists(),
        "materialization final output already exists",
    )

    payload = {
        "status": "G7R6_A2_MATERIALIZER_OFFLINE_VALIDATION_PASS",
        "parent_head": EXPECTED_HEAD,
        "architecture_tag": ARCHITECTURE_TAG,
        "events_verified": len(control["events"]),
        "objects_verified": len(control["objects"]),
        "main_raw_files_verified_by_metadata_only": len(control["objects_by_raw"]),
        "raw_file_contents_opened": 0,
        "inline_raw_sha_performed": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_performed": False,
        "network_requests": 0,
        "materialized_db_gitignored": True,
        "frozen_input_hashes": hashes,
        "environment": environment,
        "next_safe_action": (
            "INDEPENDENT_REVIEW_THEN_EXPLICITLY_AUTHORIZE_SINGLE "
            "A2_MATERIALIZATION_PASS"
        ),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = DELETE;
        PRAGMA synchronous = FULL;
        PRAGMA foreign_keys = ON;

        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE events (
            event_index INTEGER PRIMARY KEY,
            conj_id TEXT NOT NULL,
            stratum TEXT NOT NULL,
            reference_us INTEGER NOT NULL,
            report_start_us INTEGER NOT NULL,
            quality_start_us INTEGER NOT NULL,
            support_boundary_us INTEGER NOT NULL,
            effective_support_start_us INTEGER NOT NULL,
            expected_eligible_replays INTEGER NOT NULL,
            expected_support_replays INTEGER NOT NULL,
            obj1 TEXT NOT NULL,
            obj2 TEXT NOT NULL
        );

        CREATE TABLE objects (
            object_id TEXT PRIMARY KEY,
            event_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            lower_us INTEGER NOT NULL,
            reference_us INTEGER NOT NULL,
            report_start_us INTEGER NOT NULL,
            quality_start_us INTEGER NOT NULL,
            support_start_us INTEGER NOT NULL,
            main_raw_path TEXT NOT NULL,
            main_raw_sha256 TEXT NOT NULL,
            expected_main_window_rows INTEGER NOT NULL,
            predecessor_class TEXT NOT NULL,
            predecessor_gp_id TEXT,
            predecessor_creation_us INTEGER,
            predecessor_epoch_us INTEGER,
            predecessor_raw_path TEXT,
            predecessor_raw_sha256 TEXT,
            main_baseline_record_key TEXT,
            support_baseline_record_key TEXT,
            FOREIGN KEY(event_index) REFERENCES events(event_index)
        );

        CREATE TABLE history_records (
            record_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_id TEXT NOT NULL,
            creation_us INTEGER NOT NULL,
            epoch_us INTEGER NOT NULL,
            gp_id TEXT NOT NULL,
            stable_tie_key TEXT NOT NULL,
            state_signature TEXT,
            state_signature_valid INTEGER NOT NULL
        );

        CREATE INDEX idx_history_object_creation
        ON history_records(object_id, creation_us, epoch_us, stable_tie_key);

        CREATE TABLE support_payloads (
            record_key TEXT PRIMARY KEY,
            object_id TEXT NOT NULL,
            creation_us INTEGER NOT NULL,
            epoch_us INTEGER NOT NULL,
            gp_id TEXT NOT NULL,
            stable_tie_key TEXT NOT NULL,
            tle_line1 TEXT NOT NULL,
            tle_line2 TEXT NOT NULL,
            source_class TEXT NOT NULL,
            parse_status TEXT NOT NULL DEFAULT 'NOT_EVALUATED',
            parse_failure_detail TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX idx_support_payload_object_order
        ON support_payloads(object_id, creation_us, epoch_us, stable_tie_key);

        CREATE TABLE invalid_quality_records (
            object_id TEXT NOT NULL,
            creation_us INTEGER NOT NULL,
            epoch_us INTEGER NOT NULL,
            gp_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            tle_line1 TEXT NOT NULL,
            tle_line2 TEXT NOT NULL
        );

        CREATE TABLE replay_updates (
            event_index INTEGER NOT NULL,
            replay_sequence INTEGER NOT NULL,
            replay_us INTEGER NOT NULL,
            temporal_support INTEGER NOT NULL,
            obj1_record_key TEXT NOT NULL,
            obj2_record_key TEXT NOT NULL,
            obj1_creation_us INTEGER NOT NULL,
            obj1_epoch_us INTEGER NOT NULL,
            obj1_gp_id TEXT NOT NULL,
            obj2_creation_us INTEGER NOT NULL,
            obj2_epoch_us INTEGER NOT NULL,
            obj2_gp_id TEXT NOT NULL,
            pair_parse_status TEXT NOT NULL DEFAULT 'NOT_EVALUATED',
            PRIMARY KEY(event_index, replay_sequence),
            UNIQUE(event_index, replay_us)
        );

        CREATE INDEX idx_replay_support
        ON replay_updates(temporal_support, event_index, replay_sequence);
        """
    )


def insert_control_metadata(connection, control, frozen_hashes) -> None:
    metadata = {
        "schema_version": "G7R6_A2_MATERIALIZED_V1",
        "parent_head": EXPECTED_HEAD,
        "architecture_tag": ARCHITECTURE_TAG,
        "events": str(EXPECTED_EVENTS),
        "objects": str(EXPECTED_OBJECTS),
        "expected_main_files": str(EXPECTED_MAIN_FILES),
        "expected_eligible_replays": str(EXPECTED_ELIGIBLE_REPLAYS),
        "expected_temporal_support_replays": str(
            EXPECTED_TEMPORAL_SUPPORT_REPLAYS
        ),
        "reporting_days": str(REPORTING_DAYS),
        "quality_window_days": str(QUALITY_WINDOW_DAYS),
        "primary_horizon_days": str(PRIMARY_HORIZON_DAYS),
        "association_tolerance_min": str(ASSOCIATION_TOLERANCE_MIN),
        "materializer_sha256": sha256(Path(__file__)),
    }
    for path_text, digest in sorted(frozen_hashes.items()):
        metadata[f"input_sha256::{path_text}"] = digest

    connection.executemany(
        "INSERT INTO metadata(key,value) VALUES (?,?)",
        sorted(metadata.items()),
    )

    for event_index in sorted(control["events"]):
        event = control["events"][event_index]
        connection.execute(
            """
            INSERT INTO events(
                event_index, conj_id, stratum,
                reference_us, report_start_us, quality_start_us,
                support_boundary_us, effective_support_start_us,
                expected_eligible_replays, expected_support_replays,
                obj1, obj2
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_index,
                event["conj_id"],
                event["stratum"],
                to_us(event["reference_utc"]),
                to_us(event["report_start_utc"]),
                to_us(event["quality_start_utc"]),
                to_us(event["support_boundary_utc"]),
                to_us(event["effective_support_start_utc"]),
                event["expected_eligible"],
                event["expected_support"],
                event["roles"]["obj1"],
                event["roles"]["obj2"],
            ),
        )

    for object_id in sorted(control["objects"], key=int):
        spec = control["objects"][object_id]
        connection.execute(
            """
            INSERT INTO objects(
                object_id, event_index, role,
                lower_us, reference_us, report_start_us, quality_start_us,
                support_start_us,
                main_raw_path, main_raw_sha256, expected_main_window_rows,
                predecessor_class, predecessor_gp_id,
                predecessor_creation_us, predecessor_epoch_us,
                predecessor_raw_path, predecessor_raw_sha256
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                object_id,
                spec.event_index,
                spec.role,
                to_us(spec.lower_utc),
                to_us(spec.reference_utc),
                to_us(spec.report_start_utc),
                to_us(spec.quality_start_utc),
                to_us(spec.support_start_utc),
                relative(spec.main_raw_path),
                spec.main_raw_sha256,
                spec.main_window_rows,
                spec.predecessor_class,
                spec.predecessor_gp_id or None,
                (
                    to_us(spec.predecessor_creation_utc)
                    if spec.predecessor_creation_utc is not None
                    else None
                ),
                (
                    to_us(spec.predecessor_epoch_utc)
                    if spec.predecessor_epoch_utc is not None
                    else None
                ),
                (
                    relative(spec.predecessor_raw_path)
                    if spec.predecessor_raw_path is not None
                    and spec.predecessor_raw_path.is_relative_to(ROOT)
                    else (
                        str(spec.predecessor_raw_path)
                        if spec.predecessor_raw_path is not None
                        else None
                    )
                ),
                spec.predecessor_raw_sha256 or None,
            ),
        )


def row_payload(raw: dict[str, str], object_id: str, source_class: str) -> dict:
    creation = parse_utc(raw["CREATION_DATE"])
    epoch = parse_utc(raw["EPOCH"])
    tie = stable_tie_key(raw)
    creation_us = to_us(creation)
    epoch_us = to_us(epoch)
    gp_id = raw["GP_ID"].strip()
    return {
        "record_key": record_key(object_id, creation_us, epoch_us, tie),
        "object_id": object_id,
        "creation_us": creation_us,
        "epoch_us": epoch_us,
        "gp_id": gp_id,
        "stable_tie_key": tie,
        "tle_line1": raw["TLE_LINE1"].rstrip("\r\n"),
        "tle_line2": raw["TLE_LINE2"].rstrip("\r\n"),
        "source_class": source_class,
    }


def insert_support_payload(connection, payload: dict) -> None:
    existing = connection.execute(
        """
        SELECT object_id, creation_us, epoch_us, gp_id, stable_tie_key,
               tle_line1, tle_line2
        FROM support_payloads
        WHERE record_key=?
        """,
        (payload["record_key"],),
    ).fetchone()

    candidate = (
        payload["object_id"],
        payload["creation_us"],
        payload["epoch_us"],
        payload["gp_id"],
        payload["stable_tie_key"],
        payload["tle_line1"],
        payload["tle_line2"],
    )

    if existing is not None:
        require(
            tuple(existing) == candidate,
            "conflicting payloads share one frozen record identity: "
            + payload["record_key"],
        )
        return

    connection.execute(
        """
        INSERT INTO support_payloads(
            record_key, object_id, creation_us, epoch_us,
            gp_id, stable_tie_key, tle_line1, tle_line2, source_class
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            payload["record_key"],
            payload["object_id"],
            payload["creation_us"],
            payload["epoch_us"],
            payload["gp_id"],
            payload["stable_tie_key"],
            payload["tle_line1"],
            payload["tle_line2"],
            payload["source_class"],
        ),
    )


def predecessor_metadata(spec: ObjectSpec) -> dict | None:
    if spec.predecessor_class not in AVAILABLE_PREDECESSOR_CLASSES:
        return None
    assert spec.predecessor_creation_utc is not None
    assert spec.predecessor_epoch_utc is not None
    return {
        "object_id": spec.object_id,
        "creation_us": to_us(spec.predecessor_creation_utc),
        "epoch_us": to_us(spec.predecessor_epoch_utc),
        "gp_id": spec.predecessor_gp_id,
        "stable_tie_key": spec.predecessor_gp_id,
        "record_key": record_key(
            spec.object_id,
            to_us(spec.predecessor_creation_utc),
            to_us(spec.predecessor_epoch_utc),
            spec.predecessor_gp_id,
        ),
        "source_class": spec.predecessor_class,
    }


def main_raw_pass(connection, control) -> dict:
    objects = control["objects"]
    objects_by_raw = control["objects_by_raw"]
    raw_hashes = control["raw_hashes"]

    main_baseline: dict[str, dict | None] = {
        object_id: None for object_id in objects
    }
    support_baseline: dict[str, dict | None] = {
        object_id: None for object_id in objects
    }
    captured_predecessor_payload: dict[str, dict] = {}

    local_counts: Counter[str] = Counter()
    raw_rows_scanned = 0
    local_main_rows = 0
    non_target_transport_rows = 0
    target_rows_outside_local_interval = 0
    quality_rows = 0
    invalid_quality_rows = 0
    support_payload_rows = 0
    main_files_opened = 0

    for file_number, raw_key in enumerate(sorted(objects_by_raw), start=1):
        target_ids = set(objects_by_raw[raw_key])
        path = objects[next(iter(target_ids))].main_raw_path
        expected_sha = raw_hashes[raw_key]

        stream, digest = open_hashed_csv(path)
        try:
            reader = csv.DictReader(stream)
            require(
                reader.fieldnames is not None,
                f"missing raw CSV header: {relative(path)}",
            )
            require(
                REQUIRED_RAW_FIELDS.issubset(set(reader.fieldnames)),
                f"required GP schema missing: {relative(path)}",
            )

            pending_history = []
            pending_payloads = []
            pending_invalid = []

            for raw in reader:
                raw_rows_scanned += 1
                object_id = str(raw.get("NORAD_CAT_ID", "")).strip()

                if object_id not in target_ids:
                    non_target_transport_rows += 1
                    continue

                spec = objects[object_id]
                creation = parse_utc(raw["CREATION_DATE"])
                epoch = parse_utc(raw["EPOCH"])
                creation_us = to_us(creation)
                epoch_us = to_us(epoch)

                gp_id = str(raw.get("GP_ID", "")).strip()
                tie = stable_tie_key(raw)

                # Capture a predecessor already present in the transported
                # batch union. This does not open any extra file.
                if (
                    spec.predecessor_class
                    == "PREDECESSOR_CAPTURED_IN_BATCH_UNION"
                    and gp_id == spec.predecessor_gp_id
                    and creation == spec.predecessor_creation_utc
                    and epoch == spec.predecessor_epoch_utc
                ):
                    captured_predecessor_payload[object_id] = row_payload(
                        raw,
                        object_id,
                        "PREDECESSOR_CAPTURED_IN_BATCH_UNION",
                    )

                # Only exact object-local [t_ref-180d,t_ref) rows enter
                # scientific main history.
                if not (spec.lower_utc <= creation < spec.reference_utc):
                    target_rows_outside_local_interval += 1
                    continue

                local_counts[object_id] += 1
                local_main_rows += 1

                meta = {
                    "record_key": record_key(
                        object_id, creation_us, epoch_us, tie
                    ),
                    "object_id": object_id,
                    "creation_us": creation_us,
                    "epoch_us": epoch_us,
                    "gp_id": gp_id,
                    "stable_tie_key": tie,
                }

                if creation < spec.report_start_utc:
                    prior = main_baseline[object_id]
                    if prior is None or record_order(meta) > record_order(prior):
                        main_baseline[object_id] = dict(meta)

                if creation < spec.support_start_utc:
                    prior = support_baseline[object_id]
                    if prior is None or record_order(meta) > record_order(prior):
                        full = row_payload(raw, object_id, "MAIN_WINDOW")
                        support_baseline[object_id] = full

                # Retain only records that can enter a 30-day window for
                # some reporting-period update.
                if creation > spec.quality_start_utc:
                    try:
                        signature = canonical_state_signature(raw, epoch)
                        signature_text = json.dumps(
                            signature,
                            separators=(",", ":"),
                            ensure_ascii=True,
                        )
                        signature_valid = 1
                    except MaterializationError as exc:
                        signature_text = None
                        signature_valid = 0
                        invalid_quality_rows += 1
                        pending_invalid.append(
                            (
                                object_id,
                                creation_us,
                                epoch_us,
                                gp_id,
                                str(exc),
                                raw["TLE_LINE1"].rstrip("\r\n"),
                                raw["TLE_LINE2"].rstrip("\r\n"),
                            )
                        )

                    pending_history.append(
                        (
                            object_id,
                            creation_us,
                            epoch_us,
                            gp_id,
                            tie,
                            signature_text,
                            signature_valid,
                        )
                    )
                    quality_rows += 1

                # Full TLE payload is required for publications at/after the
                # support start; the latest earlier payload is retained
                # separately as support_baseline.
                if creation >= spec.support_start_utc:
                    payload = row_payload(raw, object_id, "MAIN_WINDOW")
                    pending_payloads.append(
                        (
                            payload["record_key"],
                            payload["object_id"],
                            payload["creation_us"],
                            payload["epoch_us"],
                            payload["gp_id"],
                            payload["stable_tie_key"],
                            payload["tle_line1"],
                            payload["tle_line2"],
                            payload["source_class"],
                        )
                    )
                    support_payload_rows += 1

            if pending_history:
                connection.executemany(
                    """
                    INSERT INTO history_records(
                        object_id, creation_us, epoch_us, gp_id,
                        stable_tie_key, state_signature, state_signature_valid
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    pending_history,
                )

            if pending_payloads:
                for item in pending_payloads:
                    insert_support_payload(
                        connection,
                        {
                            "record_key": item[0],
                            "object_id": item[1],
                            "creation_us": item[2],
                            "epoch_us": item[3],
                            "gp_id": item[4],
                            "stable_tie_key": item[5],
                            "tle_line1": item[6],
                            "tle_line2": item[7],
                            "source_class": item[8],
                        },
                    )

            if pending_invalid:
                connection.executemany(
                    """
                    INSERT INTO invalid_quality_records(
                        object_id, creation_us, epoch_us, gp_id,
                        reason, tle_line1, tle_line2
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    pending_invalid,
                )

        finally:
            # Closing TextIOWrapper closes the underlying hashing reader.
            stream.close()

        actual_sha = digest.hexdigest()
        require(
            actual_sha == expected_sha,
            "inline main raw SHA mismatch\n"
            f"path={relative(path)}\nactual={actual_sha}\nexpected={expected_sha}",
        )

        main_files_opened += 1
        connection.commit()

        if file_number % 100 == 0 or file_number == EXPECTED_MAIN_FILES:
            print(
                f"A2_MATERIALIZATION_RAW_PROGRESS = "
                f"{file_number}/{EXPECTED_MAIN_FILES}",
                flush=True,
            )

    require(
        main_files_opened == EXPECTED_MAIN_FILES,
        f"main raw file count mismatch: {main_files_opened}",
    )
    require(
        raw_rows_scanned == EXPECTED_RAW_ROWS,
        f"transported raw row count changed: {raw_rows_scanned}",
    )
    require(
        local_main_rows == EXPECTED_LOCAL_MAIN_ROWS,
        f"exact local main row count changed: {local_main_rows}",
    )
    require(
        raw_rows_scanned
        == local_main_rows
        + target_rows_outside_local_interval
        + non_target_transport_rows,
        "transport/local row decomposition does not close",
    )

    for object_id, spec in objects.items():
        require(
            local_counts[object_id] == spec.main_window_rows,
            "object-local main row mismatch for "
            f"{object_id}: {local_counts[object_id]} != {spec.main_window_rows}",
        )

        if support_baseline[object_id] is not None:
            insert_support_payload(
                connection,
                support_baseline[object_id],
            )

        main_key = (
            main_baseline[object_id]["record_key"]
            if main_baseline[object_id] is not None
            else None
        )
        support_key = (
            support_baseline[object_id]["record_key"]
            if support_baseline[object_id] is not None
            else None
        )

        connection.execute(
            """
            UPDATE objects
            SET main_baseline_record_key=?,
                support_baseline_record_key=?
            WHERE object_id=?
            """,
            (main_key, support_key, object_id),
        )

    connection.commit()

    return {
        "main_baseline": main_baseline,
        "support_baseline": support_baseline,
        "captured_predecessor_payload": captured_predecessor_payload,
        "raw_rows_scanned": raw_rows_scanned,
        "local_main_rows": local_main_rows,
        "non_target_transport_rows": non_target_transport_rows,
        "target_rows_outside_local_interval":
            target_rows_outside_local_interval,
        "quality_rows": quality_rows,
        "invalid_quality_rows": invalid_quality_rows,
        "support_payload_rows_seen": support_payload_rows,
        "main_files_opened": main_files_opened,
    }


def load_object_publications(
    connection: sqlite3.Connection,
    object_id: str,
    report_start_us: int,
) -> list[dict]:
    rows = connection.execute(
        """
        SELECT creation_us, epoch_us, gp_id, stable_tie_key
        FROM history_records
        WHERE object_id=? AND creation_us>=?
        ORDER BY creation_us, epoch_us, stable_tie_key
        """,
        (object_id, report_start_us),
    ).fetchall()

    return [
        {
            "record_key": record_key(
                object_id,
                int(creation_us),
                int(epoch_us),
                str(tie),
            ),
            "object_id": object_id,
            "creation_us": int(creation_us),
            "epoch_us": int(epoch_us),
            "gp_id": str(gp_id),
            "stable_tie_key": str(tie),
        }
        for creation_us, epoch_us, gp_id, tie in rows
    ]


def initial_current(
    spec: ObjectSpec,
    main_baseline: dict | None,
) -> dict | None:
    candidates = []
    if main_baseline is not None:
        candidates.append(main_baseline)

    pred = predecessor_metadata(spec)
    if pred is not None:
        candidates.append(pred)

    if not candidates:
        return None

    return max(candidates, key=record_order)


def update_current(
    current: dict | None,
    publications: list[dict],
) -> dict:
    require(publications, "update_current received empty publication group")
    candidate = max(publications, key=record_order)
    if current is None or record_order(candidate) > record_order(current):
        return candidate
    return current


def schedule_events(connection, control, raw_state) -> dict:
    objects = control["objects"]
    events = control["events"]
    main_baseline = raw_state["main_baseline"]

    eligible_total = 0
    support_total = 0
    too_early_total = 0
    zero_support_events = 0
    per_event_support_counts = {}
    required_support_keys: set[str] = set()

    for event_number, event_index in enumerate(sorted(events), start=1):
        event = events[event_index]
        obj1 = event["roles"]["obj1"]
        obj2 = event["roles"]["obj2"]

        report_start_us = to_us(event["report_start_utc"])
        reference_us = to_us(event["reference_utc"])
        support_boundary_us = to_us(event["support_boundary_utc"])

        pubs1 = load_object_publications(
            connection, obj1, report_start_us
        )
        pubs2 = load_object_publications(
            connection, obj2, report_start_us
        )

        current1 = initial_current(objects[obj1], main_baseline[obj1])
        current2 = initial_current(objects[obj2], main_baseline[obj2])

        i = 0
        j = 0
        replay_sequence = 0
        event_eligible = 0
        event_support = 0

        pending_rows = []

        while i < len(pubs1) or j < len(pubs2):
            if i >= len(pubs1):
                replay_us = pubs2[j]["creation_us"]
            elif j >= len(pubs2):
                replay_us = pubs1[i]["creation_us"]
            else:
                replay_us = min(
                    pubs1[i]["creation_us"],
                    pubs2[j]["creation_us"],
                )

            group1 = []
            while i < len(pubs1) and pubs1[i]["creation_us"] == replay_us:
                group1.append(pubs1[i])
                i += 1

            group2 = []
            while j < len(pubs2) and pubs2[j]["creation_us"] == replay_us:
                group2.append(pubs2[j])
                j += 1

            # Same-time publications are all ingested before current selection.
            if group1:
                current1 = update_current(current1, group1)
            if group2:
                current2 = update_current(current2, group2)

            if current1 is None or current2 is None:
                continue

            require(
                report_start_us <= replay_us < reference_us,
                f"eligible replay outside reporting interval: event {event_index}",
            )

            replay_sequence += 1
            event_eligible += 1
            eligible_total += 1

            temporal_support = int(replay_us >= support_boundary_us)
            if temporal_support:
                event_support += 1
                support_total += 1
                required_support_keys.add(current1["record_key"])
                required_support_keys.add(current2["record_key"])
            else:
                too_early_total += 1

            pending_rows.append(
                (
                    event_index,
                    replay_sequence,
                    replay_us,
                    temporal_support,
                    current1["record_key"],
                    current2["record_key"],
                    current1["creation_us"],
                    current1["epoch_us"],
                    current1["gp_id"],
                    current2["creation_us"],
                    current2["epoch_us"],
                    current2["gp_id"],
                )
            )

        require(
            event_eligible == event["expected_eligible"],
            "eligible replay count disagrees with frozen workload for event "
            f"{event_index}: {event_eligible} != {event['expected_eligible']}",
        )
        require(
            event_support == event["expected_support"],
            "support replay count disagrees with frozen temporal-support result "
            f"for event {event_index}: {event_support} != "
            f"{event['expected_support']}",
        )

        if event_support == 0:
            zero_support_events += 1

        per_event_support_counts[event_index] = event_support

        if pending_rows:
            connection.executemany(
                """
                INSERT INTO replay_updates(
                    event_index, replay_sequence, replay_us,
                    temporal_support,
                    obj1_record_key, obj2_record_key,
                    obj1_creation_us, obj1_epoch_us, obj1_gp_id,
                    obj2_creation_us, obj2_epoch_us, obj2_gp_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                pending_rows,
            )

        if event_number % 500 == 0 or event_number == EXPECTED_EVENTS:
            connection.commit()
            print(
                f"A2_MATERIALIZATION_SCHEDULE_PROGRESS = "
                f"{event_number}/{EXPECTED_EVENTS}",
                flush=True,
            )

    connection.commit()

    require(
        eligible_total == EXPECTED_ELIGIBLE_REPLAYS,
        f"eligible replay total changed: {eligible_total}",
    )
    require(
        support_total == EXPECTED_TEMPORAL_SUPPORT_REPLAYS,
        f"temporal-support replay total changed: {support_total}",
    )
    require(
        too_early_total == EXPECTED_TOO_EARLY_REPLAYS,
        f"too-early replay total changed: {too_early_total}",
    )
    require(
        zero_support_events == EXPECTED_ZERO_SUPPORT_EVENTS,
        f"zero-support event count changed: {zero_support_events}",
    )

    return {
        "eligible_total": eligible_total,
        "support_total": support_total,
        "too_early_total": too_early_total,
        "zero_support_events": zero_support_events,
        "required_support_keys": required_support_keys,
        "per_event_support_counts": per_event_support_counts,
    }


def add_missing_predecessor_payloads(
    connection,
    control,
    raw_state,
    required_support_keys: set[str],
) -> dict:
    objects = control["objects"]
    captured = raw_state["captured_predecessor_payload"]

    existing = {
        row[0]
        for row in connection.execute(
            "SELECT record_key FROM support_payloads"
        )
    }

    missing = sorted(required_support_keys - existing)
    acquired_paths_opened = 0
    captured_inserted = 0

    for key in missing:
        object_id = key.split("|", 1)[0]
        require(object_id in objects, f"unknown selected support object: {object_id}")
        spec = objects[object_id]
        pred = predecessor_metadata(spec)

        require(
            pred is not None and pred["record_key"] == key,
            "selected support record lacks materialized payload and is not "
            f"the frozen predecessor: object={object_id}; key={key}",
        )

        if spec.predecessor_class == "PREDECESSOR_CAPTURED_IN_BATCH_UNION":
            require(
                object_id in captured,
                f"required captured predecessor payload not found: {object_id}",
            )
            payload = captured[object_id]
            require(payload["record_key"] == key, "captured predecessor key mismatch")
            insert_support_payload(connection, payload)
            captured_inserted += 1
            continue

        require(
            spec.predecessor_class == "PREDECESSOR_ACQUIRED_BY_QUERY",
            f"unexpected required predecessor class: {spec.predecessor_class}",
        )
        require(
            spec.predecessor_raw_path is not None,
            f"missing predecessor raw path: {object_id}",
        )
        path = spec.predecessor_raw_path
        require(path.is_file(), f"required predecessor raw missing: {relative(path)}")

        stream, digest = open_hashed_csv(path)
        try:
            reader = csv.DictReader(stream)
            require(
                reader.fieldnames is not None,
                f"required predecessor CSV header missing: {relative(path)}",
            )
            require(
                REQUIRED_RAW_FIELDS.issubset(set(reader.fieldnames)),
                f"required predecessor schema changed: {relative(path)}",
            )
            rows = list(reader)
        finally:
            stream.close()

        actual = digest.hexdigest()
        require(
            actual == spec.predecessor_raw_sha256,
            f"required predecessor raw SHA mismatch: {relative(path)}",
        )
        acquired_paths_opened += 1

        require(
            len(rows) == 1,
            f"acquired predecessor raw must have exactly one row: {relative(path)}",
        )
        raw = rows[0]
        require(
            raw["NORAD_CAT_ID"].strip() == object_id,
            f"acquired predecessor object mismatch: {object_id}",
        )
        payload = row_payload(
            raw,
            object_id,
            "PREDECESSOR_ACQUIRED_BY_QUERY",
        )
        require(
            payload["record_key"] == key,
            f"acquired predecessor identity mismatch: {object_id}",
        )
        insert_support_payload(connection, payload)

    connection.commit()

    remaining = required_support_keys - {
        row[0]
        for row in connection.execute(
            "SELECT record_key FROM support_payloads"
        )
    }
    require(
        not remaining,
        f"support payload coverage incomplete after predecessor resolution: "
        f"{len(remaining)}",
    )

    return {
        "support_payload_keys_required": len(required_support_keys),
        "captured_predecessor_payloads_inserted": captured_inserted,
        "acquired_predecessor_raw_files_opened": acquired_paths_opened,
    }


def parse_support_payloads(
    connection: sqlite3.Connection,
    required_support_keys: set[str],
) -> dict:
    # Importing the frozen G5 module is safe: execution is under __main__ guard.
    g5 = load_module("g7r6_a2_frozen_g5_controlflow", G5_CONTROLFLOW)

    parse_success = 0
    parse_failure = 0

    rows = connection.execute(
        """
        SELECT record_key, object_id, creation_us, epoch_us,
               gp_id, stable_tie_key, tle_line1, tle_line2
        FROM support_payloads
        ORDER BY object_id, creation_us, epoch_us, stable_tie_key
        """
    ).fetchall()

    required_rows_seen = 0

    for (
        key,
        object_id,
        creation_us,
        epoch_us,
        gp_id,
        tie,
        line1,
        line2,
    ) in rows:
        if key not in required_support_keys:
            continue

        required_rows_seen += 1
        record = g5.g4.GPRecord(
            object_id=str(object_id),
            epoch_utc=from_us(int(epoch_us)),
            creation_date_utc=from_us(int(creation_us)),
            stable_tie_key=str(tie),
            payload={
                "GP_ID": str(gp_id),
                "CREATION_DATE": iso_utc(from_us(int(creation_us))),
                "TLE_LINE1": str(line1),
                "TLE_LINE2": str(line2),
            },
        )

        try:
            g5.satrec_from_record(record)
        except (g5.g4.ParseError, ValueError, TypeError) as exc:
            status = "FAILED"
            detail = f"{type(exc).__name__}: {exc}"
            parse_failure += 1
        else:
            status = "SUCCESS"
            detail = ""
            parse_success += 1

        connection.execute(
            """
            UPDATE support_payloads
            SET parse_status=?, parse_failure_detail=?
            WHERE record_key=?
            """,
            (status, detail, key),
        )

    connection.commit()

    require(
        required_rows_seen == len(required_support_keys),
        "required support-payload parse coverage mismatch",
    )

    # Derive pair-level parse readiness without propagating.
    connection.execute(
        """
        UPDATE replay_updates
        SET pair_parse_status = CASE
            WHEN temporal_support = 0 THEN 'TEMPORAL_NON_SUPPORT'
            WHEN
                (SELECT parse_status FROM support_payloads
                 WHERE record_key=replay_updates.obj1_record_key)='SUCCESS'
                AND
                (SELECT parse_status FROM support_payloads
                 WHERE record_key=replay_updates.obj2_record_key)='SUCCESS'
            THEN 'PARSE_READY_BOTH'
            ELSE 'GP_PARSE_FAILURE'
        END
        """
    )
    connection.commit()

    pair_counts = dict(
        connection.execute(
            """
            SELECT pair_parse_status, COUNT(*)
            FROM replay_updates
            GROUP BY pair_parse_status
            """
        ).fetchall()
    )

    return {
        "unique_support_payload_parse_success": parse_success,
        "unique_support_payload_parse_failure": parse_failure,
        "pair_parse_status_counts": {
            str(key): int(value)
            for key, value in sorted(pair_counts.items())
        },
        "propagation_ready_support_replays": int(
            pair_counts.get("PARSE_READY_BOTH", 0)
        ),
        "support_parse_failure_replays": int(
            pair_counts.get("GP_PARSE_FAILURE", 0)
        ),
    }


def finalize_db_metadata(connection, payload: dict) -> None:
    rows = [
        (f"result::{key}", json.dumps(value, sort_keys=True))
        for key, value in sorted(payload.items())
    ]
    connection.executemany(
        "INSERT INTO metadata(key,value) VALUES (?,?)",
        rows,
    )
    connection.commit()


def materialize() -> dict:
    verify_repository_boundary()
    frozen_hashes = verify_small_hashes()
    environment = verify_environment()
    control = load_control_inputs()
    verify_raw_inventory(control)
    verify_db_ignore()
    helper_self_tests()

    require(
        not MATERIALIZED_DB.exists(),
        "materialized DB already exists; refusing to overwrite",
    )
    require(
        not SUMMARY_OUT.exists() and not AUDIT_OUT.exists(),
        "materialization final output exists; refusing to overwrite",
    )

    MATERIALIZED_DB.parent.mkdir(parents=True, exist_ok=True)
    temp_db = INCOMPLETE_DB
    require(
        not temp_db.exists(),
        f"incomplete materialization DB already exists: {relative(temp_db)}",
    )

    connection = sqlite3.connect(temp_db)
    try:
        create_schema(connection)
        insert_control_metadata(connection, control, frozen_hashes)
        connection.commit()

        raw_state = main_raw_pass(connection, control)
        schedule = schedule_events(connection, control, raw_state)
        predecessor_resolution = add_missing_predecessor_payloads(
            connection,
            control,
            raw_state,
            schedule["required_support_keys"],
        )
        parse_audit = parse_support_payloads(
            connection,
            schedule["required_support_keys"],
        )

        invalid_quality_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM invalid_quality_records"
            ).fetchone()[0]
        )

        database_counts = {
            "history_records": int(
                connection.execute(
                    "SELECT COUNT(*) FROM history_records"
                ).fetchone()[0]
            ),
            "support_payloads": int(
                connection.execute(
                    "SELECT COUNT(*) FROM support_payloads"
                ).fetchone()[0]
            ),
            "replay_updates": int(
                connection.execute(
                    "SELECT COUNT(*) FROM replay_updates"
                ).fetchone()[0]
            ),
            "temporal_support_updates": int(
                connection.execute(
                    "SELECT COUNT(*) FROM replay_updates "
                    "WHERE temporal_support=1"
                ).fetchone()[0]
            ),
        }

        require(
            database_counts["replay_updates"] == EXPECTED_ELIGIBLE_REPLAYS,
            "materialized replay-update row count changed",
        )
        require(
            database_counts["temporal_support_updates"]
            == EXPECTED_TEMPORAL_SUPPORT_REPLAYS,
            "materialized temporal-support row count changed",
        )

        final_status = (
            "PASS"
            if invalid_quality_count == 0
            else "HOLD_INVALID_30D_STATE_SIGNATURE"
        )

        result_metadata = {
            "status": final_status,
            "raw_rows_scanned": raw_state["raw_rows_scanned"],
            "exact_local_main_rows": raw_state["local_main_rows"],
            "main_files_opened": raw_state["main_files_opened"],
            "main_files_inline_sha_verified": raw_state["main_files_opened"],
            "quality_source_rows_materialized": raw_state["quality_rows"],
            "invalid_quality_state_signature_rows": invalid_quality_count,
            "eligible_replay_updates": schedule["eligible_total"],
            "temporal_support_replay_updates": schedule["support_total"],
            "provably_too_early_updates": schedule["too_early_total"],
            "zero_support_events": schedule["zero_support_events"],
            **predecessor_resolution,
            **parse_audit,
            **database_counts,
        }

        finalize_db_metadata(connection, result_metadata)
        connection.execute("PRAGMA integrity_check")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        require(integrity == "ok", f"SQLite integrity check failed: {integrity}")
        connection.commit()

    except Exception:
        connection.close()
        raise
    else:
        connection.close()

    os.replace(temp_db, MATERIALIZED_DB)

    db_sha = sha256(MATERIALIZED_DB)

    summary = {
        "status": result_metadata["status"],
        "analysis_role": "G7_R6_A2_CAUSAL_HISTORY_MATERIALIZATION",
        "parent_head": EXPECTED_HEAD,
        "architecture_tag": ARCHITECTURE_TAG,
        "materializer_sha256": sha256(Path(__file__)),
        "materialized_db": relative(MATERIALIZED_DB),
        "materialized_db_sha256": db_sha,
        "materialized_db_committable": False,
        "events": EXPECTED_EVENTS,
        "objects": EXPECTED_OBJECTS,
        "reporting_window_days": REPORTING_DAYS,
        "quality_window_days": QUALITY_WINDOW_DAYS,
        "primary_horizon_days": PRIMARY_HORIZON_DAYS,
        "association_tolerance_min": ASSOCIATION_TOLERANCE_MIN,
        "frozen_input_hashes": frozen_hashes,
        "environment": environment,
        "raw_history": {
            "main_files_opened": raw_state["main_files_opened"],
            "main_files_inline_sha_verified": raw_state["main_files_opened"],
            "transported_rows_scanned": raw_state["raw_rows_scanned"],
            "exact_local_main_rows": raw_state["local_main_rows"],
            "non_target_transport_rows_ignored":
                raw_state["non_target_transport_rows"],
            "target_rows_outside_exact_local_interval":
                raw_state["target_rows_outside_local_interval"],
            "expected_causal_history_records":
                EXPECTED_CAUSAL_HISTORY_RECORDS,
        },
        "replay_schedule": {
            "eligible_updates": schedule["eligible_total"],
            "temporal_support_updates": schedule["support_total"],
            "provably_too_early_updates": schedule["too_early_total"],
            "zero_support_events": schedule["zero_support_events"],
        },
        "quality_materialization": {
            "history_records": database_counts["history_records"],
            "invalid_state_signature_rows": invalid_quality_count,
            "window_semantics": "(t_r-30d,t_r]",
        },
        "support_payload_materialization": {
            **predecessor_resolution,
            **parse_audit,
            "support_payload_rows": database_counts["support_payloads"],
        },
        "operations": {
            "TLE_parse_validity_checked": True,
            "SGP4_orbital_propagation_performed": False,
            "candidate_generation_performed": False,
            "candidate_refinement_performed": False,
            "reference_association_performed": False,
            "S05_computed": False,
            "S06_computed": False,
            "S07_computed": False,
            "CAL0_refit_performed": False,
            "network_requests": 0,
        },
        "interpretation": (
            "Materialization/parse-readiness evidence only. "
            "PARSE_READY_BOTH does not imply propagation success, candidate "
            "existence, reference association, reconstruction accuracy, "
            "warning performance, or collision risk."
        ),
        "next_safe_action": (
            "INDEPENDENT_MATERIALIZATION_AUDIT"
            if result_metadata["status"] == "PASS"
            else "HOLD_AND_REVIEW_INVALID_30D_STATE_SIGNATURE_ROWS"
        ),
    }

    atomic_json(SUMMARY_OUT, summary)

    audit_lines = [
        "G7 R6 A2 MATERIALIZATION AUDIT",
        "================================",
        "",
        f"STATUS = {summary['status']}",
        f"PARENT_HEAD = {EXPECTED_HEAD}",
        f"ARCHITECTURE_TAG = {ARCHITECTURE_TAG}",
        "",
        f"MAIN_FILES_OPENED = {raw_state['main_files_opened']}",
        (
            "MAIN_FILES_INLINE_SHA_VERIFIED = "
            f"{raw_state['main_files_opened']}"
        ),
        f"RAW_ROWS_SCANNED = {raw_state['raw_rows_scanned']}",
        f"EXACT_LOCAL_MAIN_ROWS = {raw_state['local_main_rows']}",
        (
            "NON_TARGET_TRANSPORT_ROWS_IGNORED = "
            f"{raw_state['non_target_transport_rows']}"
        ),
        (
            "TARGET_ROWS_OUTSIDE_EXACT_LOCAL_INTERVAL = "
            f"{raw_state['target_rows_outside_local_interval']}"
        ),
        "",
        f"ELIGIBLE_REPLAY_UPDATES = {schedule['eligible_total']}",
        f"TEMPORAL_SUPPORT_UPDATES = {schedule['support_total']}",
        f"PROVABLY_TOO_EARLY_UPDATES = {schedule['too_early_total']}",
        f"ZERO_SUPPORT_EVENTS = {schedule['zero_support_events']}",
        "",
        (
            "SUPPORT_PAYLOAD_KEYS_REQUIRED = "
            f"{predecessor_resolution['support_payload_keys_required']}"
        ),
        (
            "CAPTURED_PREDECESSOR_PAYLOADS_INSERTED = "
            f"{predecessor_resolution['captured_predecessor_payloads_inserted']}"
        ),
        (
            "ACQUIRED_PREDECESSOR_RAW_FILES_OPENED = "
            f"{predecessor_resolution['acquired_predecessor_raw_files_opened']}"
        ),
        (
            "UNIQUE_SUPPORT_PAYLOAD_PARSE_SUCCESS = "
            f"{parse_audit['unique_support_payload_parse_success']}"
        ),
        (
            "UNIQUE_SUPPORT_PAYLOAD_PARSE_FAILURE = "
            f"{parse_audit['unique_support_payload_parse_failure']}"
        ),
        (
            "PROPAGATION_READY_SUPPORT_REPLAYS = "
            f"{parse_audit['propagation_ready_support_replays']}"
        ),
        (
            "SUPPORT_PARSE_FAILURE_REPLAYS = "
            f"{parse_audit['support_parse_failure_replays']}"
        ),
        "",
        (
            "QUALITY_SOURCE_ROWS_MATERIALIZED = "
            f"{database_counts['history_records']}"
        ),
        (
            "INVALID_30D_STATE_SIGNATURE_ROWS = "
            f"{invalid_quality_count}"
        ),
        "",
        f"MATERIALIZED_DB_SHA256 = {db_sha}",
        "",
        "SGP4_ORBITAL_PROPAGATION_PERFORMED = false",
        "CANDIDATE_GENERATION_PERFORMED = false",
        "REFERENCE_ASSOCIATION_PERFORMED = false",
        "S05_COMPUTED = false",
        "S06_COMPUTED = false",
        "S07_COMPUTED = false",
        "CAL0_REFIT_PERFORMED = false",
        "NETWORK_REQUESTS = 0",
        "",
        (
            "NEXT_ACTION = "
            + summary["next_safe_action"]
        ),
    ]
    atomic_text(AUDIT_OUT, "\n".join(audit_lines) + "\n")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--offline-validate", action="store_true")
    group.add_argument("--materialize", action="store_true")
    parser.add_argument(
        "--authorize-materialization",
        action="store_true",
        help="Required explicit guard for the one controlled raw-history pass.",
    )
    args = parser.parse_args()

    if args.offline_validate:
        require(
            not args.authorize_materialization,
            "--authorize-materialization is invalid with --offline-validate",
        )
        offline_validate()
        return 0

    require(
        args.authorize_materialization,
        "A2_MATERIALIZATION_NOT_AUTHORIZED: "
        "--materialize requires --authorize-materialization",
    )
    materialize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
