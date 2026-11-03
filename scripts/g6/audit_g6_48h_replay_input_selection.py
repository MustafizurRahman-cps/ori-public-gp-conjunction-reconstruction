#!/usr/bin/env python3
"""Independent audit of frozen G6 48 h replay-input selection.

This audit independently re-derives the nominal-cutoff GP maximum from the
frozen raw Space-Track histories in ONE streaming pass.

It performs:
- no network requests;
- no TraCSS/reference-outcome reads;
- no propagation;
- no conjunction reconstruction;
- no numerical candidate search.

The local selected-TLE cache is checked against independently reconstructed
selected records but remains local-only.
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sgp4.api import Satrec, WGS72


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_HEAD = (
    "1f3414d7d0f1446be5cdfc53489fd1656af7dc98"
)

AUTH_TAG = (
    "g6-48h-replay-input-preparation-authorized"
)

EXPECTED_SELECTION_SHA = (
    "6d03f0b4c7104b7cbac76b649bedc87e9f497f297dccc19f2270e7fec5552095"
)

EXPECTED_SUMMARY_SHA = (
    "aa64f1353f61bb516e9ff0737f60b2d1b238fed42fa92ff20f55e2df613eae68"
)

EXPECTED_EXECUTION_AUDIT_SHA = (
    "f2565c4c14aca249bc9ef7dc5d254d860f0794f2c209f0298af579c2cbcfb07c"
)

EXPECTED_CACHE_SHA = (
    "b77cf2893cf92b2e577bf04f5f23d67dc6be31b9d1e8aeafe0e40efd6b08ed69"
)

EXPECTED_EVENTS = 6927
EXPECTED_OBJECTS = 13854
EXPECTED_MAIN_FILES = 1386
EXPECTED_RAW_ROWS = 5_245_230
EXPECTED_MAIN_WINDOW_ROWS = 5_153_040
EXPECTED_RAW_BYTES = 3_031_695_793

LSTAR_HOURS = 48

SAMPLE = (
    ROOT
    / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"
)

MANIFEST = (
    ROOT
    / "results/g6/G6_Final_Causal_History_Audit.csv"
)

MAIN_STATE = (
    ROOT
    / "data/raw/g6/G6_SpaceTrack_Acquisition_State.json"
)

SELECTION = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Selection.csv"
)

SUMMARY = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Summary.json"
)

EXECUTION_AUDIT = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Preparation_Execution_Audit.json"
)

CACHE = (
    ROOT
    / "data/raw/g6/G6_48H_Replay_Selected_GP_Cache.jsonl"
)

OUT = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Selection_Independent_Audit.json"
)


SELECTION_FIELDS = (
    "final_g6_sample_index",
    "reserve_event_index",
    "g6_temporal_stratum",
    "conj_id",
    "NORAD_CAT_ID",
    "object_role",
    "reference_tca_utc",
    "nominal_cutoff_utc",
    "replay_time_utc",
    "selection_status",
    "cutoff_GP_ID",
    "cutoff_stable_tie_key",
    "cutoff_CREATION_DATE",
    "cutoff_EPOCH",
    "replay_GP_ID",
    "replay_stable_tie_key",
    "replay_CREATION_DATE",
    "replay_EPOCH",
    "identity_reselection_status",
    "selected_source_class",
)


class AuditError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    object_id: str
    creation: datetime
    epoch: datetime
    stable_key: str
    gp_id: str
    tle1: str
    tle2: str
    source_class: str


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def git_value(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def parse_utc(value: str) -> datetime:
    text = str(value).strip()

    if not text:
        raise AuditError(
            "empty timestamp"
        )

    text = text.replace(
        " ",
        "T",
    )

    if text.endswith(
        ("Z", "z")
    ):
        text = (
            text[:-1]
            + "+00:00"
        )

    try:
        result = datetime.fromisoformat(
            text
        )
    except ValueError as exc:
        raise AuditError(
            f"malformed UTC timestamp: {value!r}"
        ) from exc

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=UTC
        )

    return result.astimezone(
        UTC
    )


def normalize_id(value) -> str:
    text = str(
        value
    ).strip()

    if (
        not text
        or not text.isdigit()
    ):
        raise AuditError(
            f"invalid NORAD_CAT_ID: {value!r}"
        )

    return str(
        int(text)
    )


def read_csv(path: Path):
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        reader = csv.DictReader(
            stream
        )

        fields = tuple(
            reader.fieldnames
            or ()
        )

        rows = list(
            reader
        )

    return fields, rows


def canonical_row_sha(
    row: dict[str, str],
    fields: tuple[str, ...],
) -> str:

    payload = json.dumps(
        {
            field:
                row.get(
                    field,
                    "",
                )
            for field
            in fields
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(
        payload
    ).hexdigest()


def make_candidate(
    row,
    fields,
    source_class,
):
    object_id = normalize_id(
        row[
            "NORAD_CAT_ID"
        ]
    )

    creation = parse_utc(
        row[
            "CREATION_DATE"
        ]
    )

    epoch = parse_utc(
        row[
            "EPOCH"
        ]
    )

    gp_id = (
        row.get(
            "GP_ID",
            "",
        )
        or ""
    ).strip()

    stable = (
        gp_id
        if gp_id
        else canonical_row_sha(
            row,
            fields,
        )
    )

    return Candidate(
        object_id=object_id,
        creation=creation,
        epoch=epoch,
        stable_key=stable,
        gp_id=gp_id,
        tle1=(
            row.get(
                "TLE_LINE1",
                "",
            )
            or ""
        ).strip(),
        tle2=(
            row.get(
                "TLE_LINE2",
                "",
            )
            or ""
        ).strip(),
        source_class=source_class,
    )


def key(candidate: Candidate):
    return (
        candidate.creation,
        candidate.epoch,
        candidate.stable_key,
    )


def identity(candidate: Candidate):
    return (
        candidate.creation,
        candidate.epoch,
        candidate.stable_key,
    )


def choose(
    current: Candidate | None,
    candidate: Candidate,
) -> Candidate:

    if current is None:
        return candidate

    if key(
        candidate
    ) > key(
        current
    ):
        return candidate

    if key(
        candidate
    ) == key(
        current
    ):
        # Equal frozen identity must not map to conflicting
        # TLE payloads.
        if (
            candidate.tle1
            != current.tle1
            or candidate.tle2
            != current.tle2
        ):
            raise AuditError(
                "AMBIGUOUS_EQUAL_MAXIMUM_KEY_WITH_DIFFERENT_TLE_PAYLOAD"
            )

    return current


# ------------------------------------------------------------
# Frozen boundary and generated artifact hashes
# ------------------------------------------------------------

if (
    git_value(
        "rev-parse",
        "HEAD",
    )
    != EXPECTED_HEAD
):
    raise AuditError(
        "unexpected HEAD"
    )

if (
    git_value(
        "rev-parse",
        f"refs/tags/{AUTH_TAG}^{{commit}}",
    )
    != EXPECTED_HEAD
):
    raise AuditError(
        "authorization tag target changed"
    )

if git_value(
    "status",
    "--porcelain",
    "--untracked-files=no",
):
    raise AuditError(
        "tracked working tree is not clean"
    )

for path, expected in {
    SELECTION:
        EXPECTED_SELECTION_SHA,

    SUMMARY:
        EXPECTED_SUMMARY_SHA,

    EXECUTION_AUDIT:
        EXPECTED_EXECUTION_AUDIT_SHA,

    CACHE:
        EXPECTED_CACHE_SHA,
}.items():

    if (
        not path.is_file()
        or sha256(path)
        != expected
    ):
        raise AuditError(
            "prepared artifact SHA mismatch: "
            + str(path)
        )


# ------------------------------------------------------------
# Load frozen sample, causal manifest, and selection
# ------------------------------------------------------------

_, sample_rows = read_csv(
    SAMPLE
)

_, manifest_rows = read_csv(
    MANIFEST
)

selection_fields, selection_rows = read_csv(
    SELECTION
)

if selection_fields != SELECTION_FIELDS:
    raise AuditError(
        "selection manifest schema changed"
    )

if (
    len(sample_rows)
    != EXPECTED_EVENTS
    or len(manifest_rows)
    != EXPECTED_OBJECTS
    or len(selection_rows)
    != EXPECTED_OBJECTS
):
    raise AuditError(
        "event/object/selection counts changed"
    )

manifest_by_object = {}

for row in manifest_rows:

    object_id = normalize_id(
        row[
            "NORAD_CAT_ID"
        ]
    )

    if object_id in manifest_by_object:
        raise AuditError(
            "duplicate object in causal manifest"
        )

    manifest_by_object[
        object_id
    ] = row


sample_by_event = {}
expected_object_roles = {}

for row in sample_rows:

    event_index = row[
        "final_g6_sample_index"
    ].strip()

    if event_index in sample_by_event:
        raise AuditError(
            "duplicate sample event index"
        )

    sample_by_event[
        event_index
    ] = row

    for role in (
        "obj1",
        "obj2",
    ):

        object_id = normalize_id(
            row[
                role
            ]
        )

        if object_id in expected_object_roles:
            raise AuditError(
                "sample is not object-disjoint"
            )

        expected_object_roles[
            object_id
        ] = (
            event_index,
            role,
        )


selection_by_object = {}
selection_by_event = defaultdict(
    list
)

source_counts = Counter()

for row in selection_rows:

    object_id = normalize_id(
        row[
            "NORAD_CAT_ID"
        ]
    )

    if object_id in selection_by_object:
        raise AuditError(
            "duplicate object in selection"
        )

    if object_id not in expected_object_roles:
        raise AuditError(
            "unexpected object in selection"
        )

    expected_event, expected_role = (
        expected_object_roles[
            object_id
        ]
    )

    if (
        row[
            "final_g6_sample_index"
        ].strip()
        != expected_event
        or row[
            "object_role"
        ].strip()
        != expected_role
    ):
        raise AuditError(
            "selection event/role mapping mismatch"
        )

    if (
        row[
            "selection_status"
        ].strip()
        != "CAUSAL_GP_SELECTED"
    ):
        raise AuditError(
            "unexpected non-selected object"
        )

    if (
        row[
            "identity_reselection_status"
        ].strip()
        != "UNCHANGED"
    ):
        raise AuditError(
            "reselection identity is not UNCHANGED"
        )

    gp_id = row[
        "cutoff_GP_ID"
    ].strip()

    stable_key = row[
        "cutoff_stable_tie_key"
    ].strip()

    if (
        not gp_id
        or stable_key
        != gp_id
    ):
        raise AuditError(
            "realized selected GP does not use GP_ID stable tie key"
        )

    cutoff_creation = parse_utc(
        row[
            "cutoff_CREATION_DATE"
        ]
    )

    cutoff_epoch = parse_utc(
        row[
            "cutoff_EPOCH"
        ]
    )

    replay_creation = parse_utc(
        row[
            "replay_CREATION_DATE"
        ]
    )

    replay_epoch = parse_utc(
        row[
            "replay_EPOCH"
        ]
    )

    if (
        gp_id
        != row[
            "replay_GP_ID"
        ].strip()
        or stable_key
        != row[
            "replay_stable_tie_key"
        ].strip()
        or cutoff_creation
        != replay_creation
        or cutoff_epoch
        != replay_epoch
    ):
        raise AuditError(
            "cutoff/replay identity differs in selection manifest"
        )

    reference = parse_utc(
        row[
            "reference_tca_utc"
        ]
    )

    nominal_cutoff = parse_utc(
        row[
            "nominal_cutoff_utc"
        ]
    )

    if (
        nominal_cutoff
        != reference
        - timedelta(
            hours=LSTAR_HOURS
        )
    ):
        raise AuditError(
            "nominal 48 h cutoff mismatch"
        )

    if cutoff_creation > nominal_cutoff:
        raise AuditError(
            "selected GP was not causally available at nominal cutoff"
        )

    source = row[
        "selected_source_class"
    ].strip()

    if source not in {
        "MAIN_WINDOW",
        "CAPTURED_PREDECESSOR",
        "QUERIED_PREDECESSOR",
    }:
        raise AuditError(
            "unexpected selected source class: "
            + source
        )

    source_counts[
        source
    ] += 1

    selection_by_object[
        object_id
    ] = row

    selection_by_event[
        expected_event
    ].append(
        row
    )


if (
    set(selection_by_object)
    != set(manifest_by_object)
    or set(selection_by_object)
    != set(expected_object_roles)
):
    raise AuditError(
        "selection/sample/manifest object sets differ"
    )


# ------------------------------------------------------------
# Event-level publication-driven replay rule
# ------------------------------------------------------------

for event_index, rows in (
    selection_by_event.items()
):

    if len(rows) != 2:
        raise AuditError(
            "event does not contain exactly two selected objects"
        )

    replay_times = {
        parse_utc(
            row[
                "replay_time_utc"
            ]
        )
        for row in rows
    }

    if len(replay_times) != 1:
        raise AuditError(
            "pair has inconsistent replay times"
        )

    replay = next(
        iter(
            replay_times
        )
    )

    selected_creations = [
        parse_utc(
            row[
                "cutoff_CREATION_DATE"
            ]
        )
        for row in rows
    ]

    if replay != max(
        selected_creations
    ):
        raise AuditError(
            "publication-driven replay time is incorrect"
        )

    nominal_cutoffs = {
        parse_utc(
            row[
                "nominal_cutoff_utc"
            ]
        )
        for row in rows
    }

    if len(
        nominal_cutoffs
    ) != 1:
        raise AuditError(
            "event objects have different nominal cutoffs"
        )

    if replay > next(
        iter(
            nominal_cutoffs
        )
    ):
        raise AuditError(
            "replay time exceeds nominal cutoff"
        )


if len(
    selection_by_event
) != EXPECTED_EVENTS:
    raise AuditError(
        "selection event coverage changed"
    )


# ------------------------------------------------------------
# Local selected-TLE cache
# ------------------------------------------------------------

cache_by_object = {}

with CACHE.open(
    "r",
    encoding="utf-8",
) as stream:

    for line in stream:

        if not line.strip():
            continue

        item = json.loads(
            line
        )

        object_id = normalize_id(
            item[
                "NORAD_CAT_ID"
            ]
        )

        if object_id in cache_by_object:
            raise AuditError(
                "duplicate object in selected-TLE cache"
            )

        cache_by_object[
            object_id
        ] = item


if (
    len(cache_by_object)
    != EXPECTED_OBJECTS
    or set(cache_by_object)
    != set(selection_by_object)
):
    raise AuditError(
        "selected-TLE cache coverage mismatch"
    )


tle_parse_verified = 0

for object_id, selected in (
    selection_by_object.items()
):

    cached = cache_by_object[
        object_id
    ]

    expected_event, expected_role = (
        expected_object_roles[
            object_id
        ]
    )

    if (
        cached[
            "final_g6_sample_index"
        ].strip()
        != expected_event
        or cached[
            "object_role"
        ].strip()
        != expected_role
        or cached[
            "conj_id"
        ].strip()
        != selected[
            "conj_id"
        ].strip()
    ):
        raise AuditError(
            "cache event identity mismatch"
        )

    if (
        cached[
            "GP_ID"
        ].strip()
        != selected[
            "replay_GP_ID"
        ].strip()
        or cached[
            "stable_tie_key"
        ].strip()
        != selected[
            "replay_stable_tie_key"
        ].strip()
        or parse_utc(
            cached[
                "CREATION_DATE"
            ]
        )
        != parse_utc(
            selected[
                "replay_CREATION_DATE"
            ]
        )
        or parse_utc(
            cached[
                "EPOCH"
            ]
        )
        != parse_utc(
            selected[
                "replay_EPOCH"
            ]
        )
        or parse_utc(
            cached[
                "replay_time_utc"
            ]
        )
        != parse_utc(
            selected[
                "replay_time_utc"
            ]
        )
    ):
        raise AuditError(
            "cache/selection metadata mismatch for "
            + object_id
        )

    tle1 = str(
        cached[
            "TLE_LINE1"
        ]
    )

    tle2 = str(
        cached[
            "TLE_LINE2"
        ]
    )

    if (
        not tle1.startswith(
            "1 "
        )
        or not tle2.startswith(
            "2 "
        )
    ):
        raise AuditError(
            "malformed cached TLE prefix for "
            + object_id
        )

    try:
        sat = Satrec.twoline2rv(
            tle1,
            tle2,
            WGS72,
        )
    except Exception as exc:
        raise AuditError(
            "cached TLE parse failure for "
            + object_id
        ) from exc

    if str(
        int(
            sat.satnum
        )
    ) != object_id:
        raise AuditError(
            "cached TLE satellite-number mismatch for "
            + object_id
        )

    tle_parse_verified += 1


# ------------------------------------------------------------
# Acquisition-state expectations
# ------------------------------------------------------------

state = json.loads(
    MAIN_STATE.read_text(
        encoding="utf-8"
    )
)

main_success = [
    item
    for item in state.get(
        "records",
        [],
    )
    if (
        item.get(
            "kind"
        )
        == "main"
        and item.get(
            "status"
        )
        == "SUCCESS"
    )
]

if len(
    main_success
) != EXPECTED_MAIN_FILES:
    raise AuditError(
        "successful main-batch count changed"
    )

state_by_path = {}

for item in main_success:

    relative = str(
        item[
            "raw_filename"
        ]
    )

    if relative in state_by_path:
        raise AuditError(
            "duplicate successful main raw path"
        )

    state_by_path[
        relative
    ] = item


objects_by_path = defaultdict(
    set
)

for object_id, manifest in (
    manifest_by_object.items()
):

    objects_by_path[
        manifest[
            "main_raw_path"
        ].strip()
    ].add(
        object_id
    )


if len(
    objects_by_path
) != EXPECTED_MAIN_FILES:
    raise AuditError(
        "causal manifest does not map to exactly 1386 main files"
    )


# ------------------------------------------------------------
# ONE independent raw-history pass
# ------------------------------------------------------------

best_main = {
    object_id:
        None
    for object_id
    in manifest_by_object
}

captured_predecessor = {}

raw_files_verified = 0
raw_rows_verified = 0
raw_bytes_verified = 0
main_window_rows_verified = 0

missing_gp_id_rows = 0

for relative_path in sorted(
    objects_by_path
):

    path = (
        ROOT
        / relative_path
    )

    expected_objects = (
        objects_by_path[
            relative_path
        ]
    )

    state_record = (
        state_by_path.get(
            relative_path
        )
    )

    if state_record is None:
        raise AuditError(
            "main raw file missing from state: "
            + relative_path
        )

    manifest_hashes = {
        manifest_by_object[
            object_id
        ][
            "main_raw_SHA256"
        ].strip()
        for object_id
        in expected_objects
    }

    if len(
        manifest_hashes
    ) != 1:
        raise AuditError(
            "main-file manifest SHA disagreement"
        )

    expected_sha = next(
        iter(
            manifest_hashes
        )
    )

    if (
        state_record[
            "sha256"
        ]
        != expected_sha
    ):
        raise AuditError(
            "main state/manifest SHA disagreement"
        )

    digest = hashlib.sha256()
    byte_count = 0

    def decoded_lines(
        binary_stream,
    ):
        nonlocal_byte = None
        # byte_count is updated through a mutable container
        # because generator-local nonlocal declarations are
        # undesirable in this top-level audit.
        for raw in binary_stream:
            digest.update(
                raw
            )
            byte_counter[
                0
            ] += len(raw)
            yield raw.decode(
                "utf-8-sig"
            )

    byte_counter = [
        0
    ]

    row_count = 0
    observed_objects = set()

    with path.open(
        "rb"
    ) as stream:

        reader = csv.DictReader(
            decoded_lines(
                stream
            )
        )

        fields = tuple(
            reader.fieldnames
            or ()
        )

        required = {
            "NORAD_CAT_ID",
            "CREATION_DATE",
            "EPOCH",
            "GP_ID",
            "TLE_LINE1",
            "TLE_LINE2",
        }

        if not required.issubset(
            fields
        ):
            raise AuditError(
                "main raw schema missing required fields"
            )

        for row in reader:

            row_count += 1

            object_id = normalize_id(
                row[
                    "NORAD_CAT_ID"
                ]
            )

            if object_id not in expected_objects:
                raise AuditError(
                    "unexpected object in main raw batch"
                )

            observed_objects.add(
                object_id
            )

            candidate = make_candidate(
                row,
                fields,
                "MAIN_WINDOW",
            )

            if not candidate.gp_id:
                missing_gp_id_rows += 1

            manifest = (
                manifest_by_object[
                    object_id
                ]
            )

            lower = parse_utc(
                manifest[
                    "lower_bound_utc"
                ]
            )

            upper = parse_utc(
                manifest[
                    "reference_tca_utc"
                ]
            )

            cutoff = (
                upper
                - timedelta(
                    hours=LSTAR_HOURS
                )
            )

            if (
                lower
                <= candidate.creation
                < upper
            ):
                main_window_rows_verified += 1

                if (
                    candidate.creation
                    <= cutoff
                ):
                    best_main[
                        object_id
                    ] = choose(
                        best_main[
                            object_id
                        ],
                        candidate,
                    )

            elif (
                candidate.creation
                < lower
                and manifest[
                    "final_predecessor_class"
                ].strip()
                == "PREDECESSOR_CAPTURED_IN_BATCH_UNION"
            ):

                if (
                    candidate.gp_id
                    == manifest[
                        "predecessor_GP_ID"
                    ].strip()
                    and candidate.creation
                    == parse_utc(
                        manifest[
                            "predecessor_CREATION_DATE"
                        ]
                    )
                    and candidate.epoch
                    == parse_utc(
                        manifest[
                            "predecessor_EPOCH"
                        ]
                    )
                ):
                    if (
                        object_id
                        in captured_predecessor
                    ):
                        raise AuditError(
                            "captured predecessor matched more than once"
                        )

                    captured_predecessor[
                        object_id
                    ] = Candidate(
                        object_id=
                            candidate.object_id,

                        creation=
                            candidate.creation,

                        epoch=
                            candidate.epoch,

                        stable_key=
                            candidate.stable_key,

                        gp_id=
                            candidate.gp_id,

                        tle1=
                            candidate.tle1,

                        tle2=
                            candidate.tle2,

                        source_class=
                            "CAPTURED_PREDECESSOR",
                    )

    byte_count = (
        byte_counter[
            0
        ]
    )

    actual_sha = (
        digest.hexdigest()
    )

    if (
        observed_objects
        != expected_objects
    ):
        raise AuditError(
            "main raw object coverage mismatch"
        )

    if actual_sha != expected_sha:
        raise AuditError(
            "independent main raw SHA failure: "
            + relative_path
        )

    if (
        row_count
        != int(
            state_record[
                "row_count"
            ]
        )
    ):
        raise AuditError(
            "independent main raw row-count failure"
        )

    if (
        byte_count
        != int(
            state_record[
                "byte_count"
            ]
        )
    ):
        raise AuditError(
            "independent main raw byte-count failure"
        )

    raw_files_verified += 1
    raw_rows_verified += row_count
    raw_bytes_verified += byte_count


if raw_files_verified != EXPECTED_MAIN_FILES:
    raise AuditError(
        "independent main-file count failure"
    )

if raw_rows_verified != EXPECTED_RAW_ROWS:
    raise AuditError(
        "independent raw-row total failure"
    )

if raw_bytes_verified != EXPECTED_RAW_BYTES:
    raise AuditError(
        "independent raw-byte total failure"
    )

if (
    main_window_rows_verified
    != EXPECTED_MAIN_WINDOW_ROWS
):
    raise AuditError(
        "independent main-window row total failure"
    )

if missing_gp_id_rows != 0:
    raise AuditError(
        "realized main raw corpus unexpectedly contains missing GP_ID"
    )


# ------------------------------------------------------------
# Independently reconstruct selected object at 48 h
# ------------------------------------------------------------

queried_predecessor_files_read = 0
selected_source_counts_independent = Counter()
selection_identity_matches = 0
selection_tle_matches = 0

for object_id, selected_row in (
    selection_by_object.items()
):

    expected = (
        best_main[
            object_id
        ]
    )

    manifest = (
        manifest_by_object[
            object_id
        ]
    )

    cutoff = parse_utc(
        selected_row[
            "nominal_cutoff_utc"
        ]
    )

    if expected is None:

        pred_class = (
            manifest[
                "final_predecessor_class"
            ].strip()
        )

        if (
            pred_class
            == "PREDECESSOR_CAPTURED_IN_BATCH_UNION"
        ):
            expected = (
                captured_predecessor.get(
                    object_id
                )
            )

            if expected is None:
                raise AuditError(
                    "required captured predecessor missing"
                )

        elif (
            pred_class
            == "PREDECESSOR_ACQUIRED_BY_QUERY"
        ):

            pred_path = (
                ROOT
                / manifest[
                    "predecessor_raw_path"
                ].strip()
            )

            if (
                not pred_path.is_file()
                or sha256(
                    pred_path
                )
                != manifest[
                    "predecessor_raw_SHA256"
                ].strip()
            ):
                raise AuditError(
                    "selected queried predecessor integrity failure"
                )

            fields, rows = read_csv(
                pred_path
            )

            if len(rows) != 1:
                raise AuditError(
                    "selected queried predecessor is not exactly one row"
                )

            expected = make_candidate(
                rows[0],
                fields,
                "QUERIED_PREDECESSOR",
            )

            queried_predecessor_files_read += 1

        elif (
            pred_class
            == "LEFT_CENSORED_NO_PREDECESSOR_RETURNED"
        ):
            expected = None

        else:
            raise AuditError(
                "unexpected predecessor class"
            )

    if (
        expected is None
        or expected.creation
        > cutoff
    ):
        raise AuditError(
            "independent derivation found no causal GP "
            "for object declared selected: "
            + object_id
        )

    selected_identity = (
        parse_utc(
            selected_row[
                "cutoff_CREATION_DATE"
            ]
        ),
        parse_utc(
            selected_row[
                "cutoff_EPOCH"
            ]
        ),
        selected_row[
            "cutoff_stable_tie_key"
        ].strip(),
    )

    if identity(
        expected
    ) != selected_identity:
        raise AuditError(
            "independent 48 h selected identity mismatch for "
            + object_id
        )

    selection_identity_matches += 1

    cached = cache_by_object[
        object_id
    ]

    if (
        expected.tle1
        != cached[
            "TLE_LINE1"
        ]
        or expected.tle2
        != cached[
            "TLE_LINE2"
        ]
    ):
        raise AuditError(
            "independent selected TLE mismatch for "
            + object_id
        )

    selection_tle_matches += 1

    selected_source_counts_independent[
        expected.source_class
    ] += 1


if (
    selection_identity_matches
    != EXPECTED_OBJECTS
    or selection_tle_matches
    != EXPECTED_OBJECTS
):
    raise AuditError(
        "independent selected-object coverage failure"
    )

if (
    selected_source_counts_independent
    != source_counts
):
    raise AuditError(
        "independent selected-source classification mismatch"
    )


# ------------------------------------------------------------
# Reselection theorem audit
#
# If s_i is maximal over records with creation <= cutoff,
# t_r=max(c_1,c_2), and each c_i <= t_r <= cutoff,
# then s_i belongs to the subset creation <= t_r and no
# member of that subset can outrank s_i. Therefore the
# cutoff-selected identity must also be the replay-time
# selected identity. No third full raw scan is needed.
# ------------------------------------------------------------

reselection_theorem_verified_events = 0

for event_index, rows in (
    selection_by_event.items()
):

    replay = parse_utc(
        rows[0][
            "replay_time_utc"
        ]
    )

    for row in rows:

        creation = parse_utc(
            row[
                "cutoff_CREATION_DATE"
            ]
        )

        cutoff = parse_utc(
            row[
                "nominal_cutoff_utc"
            ]
        )

        if not (
            creation
            <= replay
            <= cutoff
        ):
            raise AuditError(
                "reselection theorem premises fail"
            )

    reselection_theorem_verified_events += 1


# ------------------------------------------------------------
# Summary/execution-audit consistency
# ------------------------------------------------------------

summary = json.loads(
    SUMMARY.read_text(
        encoding="utf-8"
    )
)

execution = json.loads(
    EXECUTION_AUDIT.read_text(
        encoding="utf-8"
    )
)

if (
    summary.get(
        "status"
    )
    != "G6_48H_REPLAY_INPUT_PREPARATION_PASS"
):
    raise AuditError(
        "preparation summary is not PASS"
    )

if (
    execution.get(
        "status"
    )
    != "G6_48H_REPLAY_INPUT_PREPARATION_EXECUTION_PASS"
):
    raise AuditError(
        "execution audit is not PASS"
    )

for payload in (
    summary,
    execution,
):
    if (
        payload.get(
            "network_requests"
        )
        != 0
        or payload.get(
            "protected_outcome_access"
        )
        is not False
        or payload.get(
            "propagation_performed"
        )
        is not False
        or payload.get(
            "reconstruction_performed"
        )
        is not False
    ):
        raise AuditError(
            "pre-outcome control invariant failed"
        )


# ------------------------------------------------------------
# Cache governance
# ------------------------------------------------------------

ignore = subprocess.run(
    [
        "git",
        "check-ignore",
        "-v",
        CACHE.relative_to(
            ROOT
        ).as_posix(),
    ],
    cwd=ROOT,
    capture_output=True,
    text=True,
)

if ignore.returncode != 0:
    raise AuditError(
        "selected-TLE cache is not Git-ignored"
    )


# ------------------------------------------------------------
# Audit result
# ------------------------------------------------------------

result = {
    "status":
        "G6_48H_REPLAY_INPUT_SELECTION_INDEPENDENT_AUDIT_PASS",

    "audit_scope":
        "ONE_FULL_INDEPENDENT_RAW_PASS_PLUS_CACHE_AND_SELECTION_CROSSCHECK",

    "events_verified":
        EXPECTED_EVENTS,

    "objects_verified":
        EXPECTED_OBJECTS,

    "causal_gp_available_both_events":
        EXPECTED_EVENTS,

    "cutoff_unavailable_objects":
        0,

    "selected_identity_matches_independent_raw_derivation":
        selection_identity_matches,

    "selected_TLE_matches_independent_raw_derivation":
        selection_tle_matches,

    "selected_TLE_parse_and_satnum_verified":
        tle_parse_verified,

    "identity_reselection_verified_objects":
        EXPECTED_OBJECTS,

    "reselection_theorem_verified_events":
        reselection_theorem_verified_events,

    "independent_main_raw_files_rehashed":
        raw_files_verified,

    "independent_main_raw_rows_verified":
        raw_rows_verified,

    "independent_main_raw_bytes_verified":
        raw_bytes_verified,

    "independent_main_window_rows_verified":
        main_window_rows_verified,

    "independent_selected_queried_predecessor_files_read":
        queried_predecessor_files_read,

    "selected_source_class_counts":
        dict(
            sorted(
                source_counts.items()
            )
        ),

    "realized_main_raw_missing_gp_id_rows":
        missing_gp_id_rows,

    "stable_tie_fallback_exercised":
        False,

    "selection_manifest_sha256":
        EXPECTED_SELECTION_SHA,

    "preparation_summary_sha256":
        EXPECTED_SUMMARY_SHA,

    "execution_audit_sha256":
        EXPECTED_EXECUTION_AUDIT_SHA,

    "local_selected_TLE_cache_sha256":
        EXPECTED_CACHE_SHA,

    "local_selected_TLE_cache_gitignored":
        True,

    "network_requests":
        0,

    "TraCSS_reference_rows_opened":
        0,

    "protected_outcome_access":
        False,

    "propagation_performed":
        False,

    "reconstruction_performed":
        False,

    "G6_error_metrics_computed":
        False,

    "next_safe_action":
        "FREEZE_G6_48H_REPLAY_INPUT_SELECTION_BEFORE_PROPAGATION",
}

OUT.write_text(
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
        {
            **result,

            "audit_script_sha256":
                sha256(
                    Path(__file__)
                ),

            "audit_artifact_sha256":
                sha256(
                    OUT
                ),
        },
        indent=2,
        sort_keys=True,
    )
)
