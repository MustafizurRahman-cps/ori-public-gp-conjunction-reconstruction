#!/usr/bin/env python3
"""Independent audit of frozen G6 reference-TCA association outputs.

This audit:
- performs no orbital propagation;
- performs no candidate generation;
- reads no physical reference miss distance;
- computes no E_d, E_t distribution, or coverage;
- independently verifies the deterministic five-minute association decision.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

HEAD = "53bcf9c09183405384ebe3d4b925dc8d0f2ba76b"

SAMPLE = ROOT / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"

EVENT_STATUS = ROOT / "results/g6/G6_PreOutcome_Candidate_Event_Status.csv"

CANDIDATES = ROOT / "results/g6/G6_PreOutcome_Candidates.csv"

ASSOCIATION = ROOT / "results/g6/G6_Reference_TCA_Association.csv"

SUMMARY = ROOT / "results/g6/G6_Reference_TCA_Association_Summary.json"

EXECUTION = ROOT / "results/g6/G6_Reference_TCA_Association_Execution_Audit.json"

OUTPUT = ROOT / "results/g6/G6_Reference_TCA_Association_Output_Independent_Audit.json"


EXPECTED_HASHES = {
    SAMPLE:
        "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef",

    EVENT_STATUS:
        "844a4c3f90a1b5bb738f13911b5e9f99f22bfe72e01bdb43dd6242e292b7b308",

    CANDIDATES:
        "21b8a9f5c8aab4b3427fad5cd3f3c2823ffc0219bd2a36aeccafc6a0aaa80e36",

    ASSOCIATION:
        "31327a65448908791928aaa41a822f02086f97662c2c89ed4ea337fec2cfa622",

    SUMMARY:
        "909772847e2de5d6c304e5f26697cb08c7bca952bea5a4c81ef78b80645c4281",

    EXECUTION:
        "69d5664725906db8cb2bf443647774a421f96c985f8989c721c5c9376cbfa211",
}


EXPECTED_EVENTS = 6927
EXPECTED_READY = 6906
EXPECTED_CANDIDATES = 929821

TOLERANCE_S = 300.0

ASSOCIATION_FIELDS = (
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "input_preoutcome_status",
    "association_status",
    "final_status",
    "selected_candidate_index",
)


class AuditError(RuntimeError):
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


def parse_utc(value: str) -> datetime:

    text = value.strip()

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    parsed = datetime.fromisoformat(text)

    if parsed.tzinfo is None:
        raise AuditError(
            "naive timestamp encountered"
        )

    return parsed.astimezone(UTC)


def git_value(*args: str) -> str:

    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


# ============================================================
# Immutable input verification
# ============================================================

if git_value("rev-parse", "HEAD") != HEAD:
    raise AuditError("unexpected HEAD")

for path, expected in EXPECTED_HASHES.items():

    if not path.is_file():
        raise AuditError(
            f"missing audit input: {path}"
        )

    actual = sha256(path)

    if actual != expected:
        raise AuditError(
            f"SHA mismatch: {path}\n"
            f"actual={actual}\n"
            f"expected={expected}"
        )


# ============================================================
# Load frozen sample metadata
# ============================================================

sample = {}

with SAMPLE.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    forbidden = {
        "min_range",
        "mdistance",
        "Pc",
        "HBR",
        "covariance",
        "E_d",
        "E_t",
    }

    if forbidden.intersection(
        set(reader.fieldnames or ())
    ):
        raise AuditError(
            "physical/protected outcome entered sample register"
        )

    for expected_index, row in enumerate(
        reader,
        start=1,
    ):

        index = int(
            row["final_g6_sample_index"]
        )

        if index != expected_index:
            raise AuditError(
                "sample order/index mismatch"
            )

        parse_utc(
            row["reference_tca_utc"]
        )

        sample[index] = row


if len(sample) != EXPECTED_EVENTS:
    raise AuditError(
        "sample event count mismatch"
    )


# ============================================================
# Load frozen pre-association event statuses
# ============================================================

events = {}
input_counts = Counter()

with EVENT_STATUS.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    for expected_index, row in enumerate(
        reader,
        start=1,
    ):

        index = int(
            row["final_g6_sample_index"]
        )

        if index != expected_index:
            raise AuditError(
                "event-status order/index mismatch"
            )

        if (
            row["conj_id"].strip()
            != sample[index]["conj_id"].strip()
        ):
            raise AuditError(
                f"event/sample conjunction mismatch: {index}"
            )

        if (
            row["g6_temporal_stratum"].strip()
            != sample[index]["g6_temporal_stratum"].strip()
        ):
            raise AuditError(
                f"event/sample stratum mismatch: {index}"
            )

        parse_utc(
            row["replay_time_utc"]
        )

        status = (
            row["preoutcome_status"].strip()
        )

        input_counts[status] += 1
        events[index] = row


expected_input_counts = {
    "CANDIDATE_SET_READY": 6906,
    "PROPAGATION_FAILURE": 1,
    "UNRECOVERED_REFINEMENT_FAILURE": 20,
}

if dict(input_counts) != expected_input_counts:
    raise AuditError(
        f"preoutcome census changed: {dict(input_counts)}"
    )


# ============================================================
# Load and structurally verify generated association output
# ============================================================

association = {}
output_counts = Counter()
stratum_counts = defaultdict(Counter)

with ASSOCIATION.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    if tuple(
        reader.fieldnames or ()
    ) != ASSOCIATION_FIELDS:
        raise AuditError(
            "association output schema mismatch"
        )

    for expected_index, row in enumerate(
        reader,
        start=1,
    ):

        index = int(
            row["final_g6_sample_index"]
        )

        if index != expected_index:
            raise AuditError(
                "association output is not canonical contiguous order"
            )

        if (
            row["conj_id"].strip()
            != sample[index]["conj_id"].strip()
        ):
            raise AuditError(
                f"association/sample conjunction mismatch: {index}"
            )

        if (
            row["g6_temporal_stratum"].strip()
            != sample[index]["g6_temporal_stratum"].strip()
        ):
            raise AuditError(
                f"association/sample stratum mismatch: {index}"
            )

        input_status = (
            row["input_preoutcome_status"].strip()
        )

        if (
            input_status
            != events[index][
                "preoutcome_status"
            ].strip()
        ):
            raise AuditError(
                f"preoutcome status changed: {index}"
            )

        final_status = (
            row["final_status"].strip()
        )

        output_counts[
            final_status
        ] += 1

        stratum_counts[
            row["g6_temporal_stratum"].strip()
        ][
            final_status
        ] += 1

        association[index] = row


if len(association) != EXPECTED_EVENTS:
    raise AuditError(
        "association output row count mismatch"
    )


expected_final_counts = {
    "PROPAGATION_FAILURE": 1,
    "UNASSOCIATED_REFERENCE_EVENT": 67,
    "UNRECOVERED_REFINEMENT_FAILURE": 20,
    "VALID_ASSOCIATED": 6839,
}

if dict(output_counts) != expected_final_counts:
    raise AuditError(
        f"association final census mismatch: {dict(output_counts)}"
    )


# Verify 21 frozen terminal failures bypassed association.
for index, event in events.items():

    input_status = (
        event[
            "preoutcome_status"
        ].strip()
    )

    row = association[index]

    if input_status == "CANDIDATE_SET_READY":
        continue

    if (
        row["association_status"].strip()
        != "NOT_APPLICABLE_PREOUTCOME_FAILURE"
    ):
        raise AuditError(
            f"preoutcome failure entered association: {index}"
        )

    if (
        row["final_status"].strip()
        != input_status
    ):
        raise AuditError(
            f"preoutcome terminal status changed: {index}"
        )

    if row[
        "selected_candidate_index"
    ].strip():
        raise AuditError(
            f"preoutcome failure has selected candidate: {index}"
        )


# ============================================================
# Independent deterministic association reconstruction
# ============================================================

ready_seen = set()
candidate_rows = 0
association_matches = 0
selected_identity_matches = 0
unassociated_matches = 0


def verify_ready_event(
    event_index: int,
    rows: list[dict],
) -> None:

    global association_matches
    global selected_identity_matches
    global unassociated_matches

    if event_index in ready_seen:
        raise AuditError(
            f"duplicate candidate event group: {event_index}"
        )

    ready_seen.add(
        event_index
    )

    event = events[
        event_index
    ]

    if (
        event[
            "preoutcome_status"
        ].strip()
        != "CANDIDATE_SET_READY"
    ):
        raise AuditError(
            f"candidate rows belong to non-ready event: {event_index}"
        )

    expected_count = int(
        event["candidate_count"]
    )

    if len(rows) != expected_count:
        raise AuditError(
            f"candidate-count mismatch: {event_index}"
        )

    replay = parse_utc(
        event["replay_time_utc"]
    )

    reference_tca = parse_utc(
        sample[
            event_index
        ][
            "reference_tca_utc"
        ]
    )

    reference_time_s = (
        reference_tca
        - replay
    ).total_seconds()

    qualifying = []

    for expected_candidate_index, row in enumerate(
        rows,
        start=1,
    ):

        candidate_index = int(
            row["candidate_index"]
        )

        if candidate_index != expected_candidate_index:
            raise AuditError(
                f"candidate-index discontinuity: {event_index}"
            )

        time_s = float(
            row["time_from_replay_s"]
        )

        distance_km = float(
            row["distance_km"]
        )

        bracket_index = int(
            row["bracket_index"]
        )

        if not (
            math.isfinite(time_s)
            and math.isfinite(distance_km)
        ):
            raise AuditError(
                f"nonfinite candidate: {event_index}"
            )

        offset = abs(
            time_s
            - reference_time_s
        )

        if offset <= TOLERANCE_S:

            qualifying.append(
                (
                    offset,
                    time_s,
                    distance_km,
                    bracket_index,
                    candidate_index,
                )
            )

    output_row = association[
        event_index
    ]

    if not qualifying:

        expected_status = (
            "UNASSOCIATED_REFERENCE_EVENT"
        )

        expected_selected = ""

    else:

        selected = min(
            qualifying,
            key=lambda item: (
                item[0],
                item[1],
                item[2],
                item[3],
            ),
        )

        expected_status = (
            "VALID_ASSOCIATED"
        )

        expected_selected = str(
            selected[4]
        )

    if (
        output_row[
            "association_status"
        ].strip()
        != expected_status
    ):
        raise AuditError(
            f"independent association-status mismatch: {event_index}"
        )

    if (
        output_row[
            "final_status"
        ].strip()
        != expected_status
    ):
        raise AuditError(
            f"independent final-status mismatch: {event_index}"
        )

    if (
        output_row[
            "selected_candidate_index"
        ].strip()
        != expected_selected
    ):
        raise AuditError(
            f"selected-candidate mismatch: {event_index}"
        )

    association_matches += 1

    if expected_status == "VALID_ASSOCIATED":
        selected_identity_matches += 1
    else:
        unassociated_matches += 1


current_index = None
current_rows = []

with CANDIDATES.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    reader = csv.DictReader(stream)

    for row in reader:

        candidate_rows += 1

        event_index = int(
            row[
                "final_g6_sample_index"
            ]
        )

        if current_index is None:
            current_index = event_index

        if event_index != current_index:

            verify_ready_event(
                current_index,
                current_rows,
            )

            current_index = event_index
            current_rows = []

        current_rows.append(
            row
        )


if current_index is not None:

    verify_ready_event(
        current_index,
        current_rows,
    )


if candidate_rows != EXPECTED_CANDIDATES:
    raise AuditError(
        f"candidate-row count changed: {candidate_rows}"
    )


expected_ready_indices = {
    index
    for index, row
    in events.items()
    if (
        row[
            "preoutcome_status"
        ].strip()
        == "CANDIDATE_SET_READY"
    )
}

if ready_seen != expected_ready_indices:

    missing = sorted(
        expected_ready_indices
        - ready_seen
    )

    extra = sorted(
        ready_seen
        - expected_ready_indices
    )

    raise AuditError(
        "candidate-event membership mismatch; "
        f"missing={missing[:10]}, extra={extra[:10]}"
    )


if association_matches != EXPECTED_READY:
    raise AuditError(
        "not all association-eligible events independently matched"
    )

if selected_identity_matches != 6839:
    raise AuditError(
        "VALID_ASSOCIATED identity-match count mismatch"
    )

if unassociated_matches != 67:
    raise AuditError(
        "UNASSOCIATED independent-match count mismatch"
    )


# ============================================================
# Summary and execution-audit crosscheck
# ============================================================

summary = json.loads(
    SUMMARY.read_text(
        encoding="utf-8"
    )
)

execution = json.loads(
    EXECUTION.read_text(
        encoding="utf-8"
    )
)


if (
    summary.get("status")
    != "G6_REFERENCE_TCA_ASSOCIATION_PASS"
):
    raise AuditError(
        "association summary is not PASS"
    )

if (
    execution.get("status")
    != "G6_REFERENCE_TCA_ASSOCIATION_EXECUTION_PASS"
):
    raise AuditError(
        "execution audit is not PASS"
    )

for record in (
    summary,
    execution,
):

    if (
        record.get(
            "final_status_counts"
        )
        != expected_final_counts
    ):
        raise AuditError(
            "JSON final-status census mismatch"
        )


if (
    summary.get(
        "association_output_sha256"
    )
    != EXPECTED_HASHES[ASSOCIATION]
):
    raise AuditError(
        "summary association SHA mismatch"
    )

if (
    execution.get(
        "association_output_sha256"
    )
    != EXPECTED_HASHES[ASSOCIATION]
):
    raise AuditError(
        "execution association SHA mismatch"
    )

if (
    execution.get(
        "association_summary_sha256"
    )
    != EXPECTED_HASHES[SUMMARY]
):
    raise AuditError(
        "execution summary SHA mismatch"
    )


required_false_summary = (
    "HBR_access",
    "Pc_access",
    "absolute_TCA_error_E_t_computed",
    "candidate_generation_performed",
    "covariance_access",
    "coverage_computed",
    "method_retuning_performed",
    "miss_distance_error_E_d_computed",
    "orbital_propagation_performed",
    "reference_miss_distance_read",
    "selected_candidate_distance_exported",
    "selected_candidate_time_exported",
    "signed_TCA_residual_exported",
)

for key in required_false_summary:

    if summary.get(key) is not False:
        raise AuditError(
            f"summary safety invariant failed: {key}"
        )


for key in (
    "E_d_computed",
    "E_t_computed",
    "candidate_generation_performed",
    "coverage_computed",
    "method_retuning_performed",
    "orbital_propagation_performed",
    "reference_miss_distance_access",
):

    if execution.get(key) is not False:
        raise AuditError(
            f"execution safety invariant failed: {key}"
        )


if (
    summary.get(
        "TraCSS_physical_outcome_rows_opened"
    )
    != 0
    or execution.get(
        "TraCSS_physical_outcome_rows_opened"
    )
    != 0
):
    raise AuditError(
        "physical TraCSS outcome rows were opened"
    )


if (
    summary.get(
        "network_requests"
    )
    != 0
    or execution.get(
        "network_requests"
    )
    != 0
):
    raise AuditError(
        "network access reported"
    )


# ============================================================
# Audit result
# ============================================================

result = {
    "status":
        "G6_REFERENCE_TCA_ASSOCIATION_OUTPUT_INDEPENDENT_AUDIT_PASS",

    "audit_scope":
        "INDEPENDENT_ASSOCIATION_RECONSTRUCTION_NO_REPROPAGATION_NO_PHYSICAL_REFERENCE_OUTCOMES",

    "events_verified":
        EXPECTED_EVENTS,

    "association_eligible_events":
        EXPECTED_READY,

    "candidate_rows_rechecked":
        candidate_rows,

    "association_decisions_independently_matched":
        association_matches,

    "VALID_ASSOCIATED":
        6839,

    "UNASSOCIATED_REFERENCE_EVENT":
        67,

    "UNRECOVERED_REFINEMENT_FAILURE":
        20,

    "PROPAGATION_FAILURE":
        1,

    "selected_candidate_identity_matches":
        selected_identity_matches,

    "unassociated_decision_matches":
        unassociated_matches,

    "association_tolerance_seconds":
        TOLERANCE_S,

    "association_equality_band":
        None,

    "association_ordering":
        (
            "MIN_ABSOLUTE_REFERENCE_TCA_OFFSET__"
            "EARLIER_CANDIDATE_TIME__"
            "SMALLER_CANDIDATE_DISTANCE__"
            "LOWER_STABLE_CANDIDATE_INDEX"
        ),

    "association_output_sha256":
        EXPECTED_HASHES[
            ASSOCIATION
        ],

    "association_summary_sha256":
        EXPECTED_HASHES[
            SUMMARY
        ],

    "execution_audit_sha256":
        EXPECTED_HASHES[
            EXECUTION
        ],

    "candidate_csv_sha256":
        EXPECTED_HASHES[
            CANDIDATES
        ],

    "final_status_counts_by_temporal_stratum": {
        stratum:
            dict(
                sorted(
                    counter.items()
                )
            )

        for stratum, counter
        in sorted(
            stratum_counts.items()
        )
    },

    "orbital_propagation_performed":
        False,

    "candidate_generation_performed":
        False,

    "physical_reference_miss_distance_access":
        False,

    "TraCSS_physical_outcome_rows_opened":
        0,

    "E_t_distribution_computed":
        False,

    "E_d_computed":
        False,

    "coverage_computed":
        False,

    "method_retuning_performed":
        False,

    "network_requests":
        0,

    "next_safe_action":
        "FREEZE_REFERENCE_TCA_ASSOCIATION_OUTPUTS_BEFORE_ERROR_METRICS",
}


OUTPUT.write_text(
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
                    OUTPUT
                ),
        },
        indent=2,
        sort_keys=True,
    )
)
