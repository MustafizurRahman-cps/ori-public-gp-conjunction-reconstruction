from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


PARENT_HEAD = "f6948dcfbf2b0b1fb95226c9c8f282cf200f8640"

PREOUTCOME_PATH = Path(
    "results/g6/G6_PreOutcome_Candidate_Event_Status.csv"
)

ASSOCIATION_PATH = Path(
    "results/g6/G6_Reference_TCA_Association.csv"
)

METRICS_PATH = Path(
    "results/g6/G6_Confirmatory_Event_Metrics.csv"
)

OUTPUT_CSV = Path(
    "results/g7/G7R6_A3_LockedLead_Status_Distribution.csv"
)

OUTPUT_JSON = Path(
    "results/g7/G7R6_A3_LockedLead_Status_Summary.json"
)

OUTPUT_AUDIT = Path(
    "results/g7/G7R6_A3_Derivation_Audit.txt"
)

EXPECTED_HASHES = {
    str(PREOUTCOME_PATH):
        "844a4c3f90a1b5bb738f13911b5e9f99f22bfe72e01bdb43dd6242e292b7b308",
    str(ASSOCIATION_PATH):
        "31327a65448908791928aaa41a822f02086f97662c2c89ed4ea337fec2cfa622",
    str(METRICS_PATH):
        "e92553698deba0643e5c11ad48ddccab33c2f9a0fd9ce4298a6ee167860c0a79",
}

# Frozen/native terminal-status vocabulary inherited from DR-0052.
STATUS_ORDER = [
    "NO_CAUSAL_GP_OBJECT_1",
    "NO_CAUSAL_GP_OBJECT_2",
    "GP_PARSE_FAILURE",
    "PROPAGATION_FAILURE",
    "UNRECOVERED_REFINEMENT_FAILURE",
    "NO_VALID_CANDIDATE",
    "UNASSOCIATED_REFERENCE_EVENT",
    "VALID_ASSOCIATED",
]

# These are already-frozen G6 headline values.
# They are used only as independent integrity checks, never for tuning.
EXPECTED_TOTAL = 6927
EXPECTED_EVALUABLE = 6906
EXPECTED_VALID_ASSOCIATED = 6839

EXPECTED_REALIZED_COUNTS = {
    "NO_CAUSAL_GP_OBJECT_1": 0,
    "NO_CAUSAL_GP_OBJECT_2": 0,
    "GP_PARSE_FAILURE": 0,
    "PROPAGATION_FAILURE": 1,
    "UNRECOVERED_REFINEMENT_FAILURE": 20,
    "NO_VALID_CANDIDATE": 0,
    "UNASSOCIATED_REFERENCE_EVENT": 67,
    "VALID_ASSOCIATED": 6839,
}


class A3DerivationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise A3DerivationError(
                f"CSV has no header: {path}"
            )

        rows = [
            {
                (k or ""): (v if v is not None else "")
                for k, v in row.items()
            }
            for row in reader
        ]

        return list(reader.fieldnames), rows


def parse_bool(value: str, field: str, key: tuple[str, str]) -> bool:
    normalized = value.strip().lower()

    if normalized in {"true", "1", "yes"}:
        return True

    if normalized in {"false", "0", "no"}:
        return False

    raise A3DerivationError(
        f"Unparseable boolean {field}={value!r} for key={key}"
    )


def event_key(row: dict[str, str]) -> tuple[str, str]:
    sample_index = row.get(
        "final_g6_sample_index",
        "",
    ).strip()

    conj_id = row.get(
        "conj_id",
        "",
    ).strip()

    if not sample_index or not conj_id:
        raise A3DerivationError(
            "Missing final_g6_sample_index or conj_id."
        )

    return sample_index, conj_id


def index_unique(
    rows: list[dict[str, str]],
    label: str,
) -> dict[tuple[str, str], dict[str, str]]:

    indexed: dict[
        tuple[str, str],
        dict[str, str],
    ] = {}

    for row in rows:
        key = event_key(row)

        if key in indexed:
            raise A3DerivationError(
                f"Duplicate event key in {label}: {key}"
            )

        indexed[key] = row

    return indexed


def require_columns(
    actual: list[str],
    required: set[str],
    label: str,
) -> None:

    missing = sorted(required - set(actual))

    if missing:
        raise A3DerivationError(
            f"{label} missing required columns: {missing}"
        )


def write_text_lf(path: Path, text: str) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(text)


def main() -> None:

    # ------------------------------------------------------------
    # Frozen input hashes
    # ------------------------------------------------------------

    observed_hashes: dict[str, str] = {}

    for path_text, expected in EXPECTED_HASHES.items():
        path = Path(path_text)

        if not path.is_file():
            raise A3DerivationError(
                f"Frozen input missing: {path}"
            )

        observed = sha256(path)
        observed_hashes[path_text] = observed

        if observed != expected:
            raise A3DerivationError(
                f"Frozen input hash mismatch for {path}: "
                f"expected {expected}, observed {observed}"
            )

    # ------------------------------------------------------------
    # Load only frozen G6 outputs
    # ------------------------------------------------------------

    pre_header, pre_rows = read_csv(
        PREOUTCOME_PATH
    )

    assoc_header, assoc_rows = read_csv(
        ASSOCIATION_PATH
    )

    metrics_header, metrics_rows = read_csv(
        METRICS_PATH
    )

    require_columns(
        pre_header,
        {
            "final_g6_sample_index",
            "conj_id",
            "preoutcome_status",
        },
        "preoutcome",
    )

    require_columns(
        assoc_header,
        {
            "final_g6_sample_index",
            "conj_id",
            "input_preoutcome_status",
            "association_status",
            "final_status",
            "selected_candidate_index",
        },
        "association",
    )

    require_columns(
        metrics_header,
        {
            "final_g6_sample_index",
            "conj_id",
            "final_status",
            "evaluable",
            "valid_associated",
            "selected_candidate_index",
        },
        "metrics",
    )

    pre = index_unique(
        pre_rows,
        "preoutcome",
    )

    assoc = index_unique(
        assoc_rows,
        "association",
    )

    metrics = index_unique(
        metrics_rows,
        "metrics",
    )

    # ------------------------------------------------------------
    # Full-membership and one-to-one checks
    # ------------------------------------------------------------

    pre_keys = set(pre)
    assoc_keys = set(assoc)
    metric_keys = set(metrics)

    if not (
        pre_keys == assoc_keys == metric_keys
    ):
        raise A3DerivationError(
            "Frozen G6 event memberships are not identical "
            "across the three A3 inputs."
        )

    if len(pre_keys) != EXPECTED_TOTAL:
        raise A3DerivationError(
            f"Expected {EXPECTED_TOTAL} G6 events, "
            f"observed {len(pre_keys)}."
        )

    # ------------------------------------------------------------
    # Row-level consistency checks
    # ------------------------------------------------------------

    status_counter: Counter[str] = Counter()
    evaluable_counter: Counter[str] = Counter()
    valid_counter: Counter[str] = Counter()

    total_evaluable = 0
    total_valid = 0

    for key in sorted(
        pre_keys,
        key=lambda x: (int(x[0]), x[1]),
    ):

        pre_row = pre[key]
        assoc_row = assoc[key]
        metric_row = metrics[key]

        pre_status = pre_row[
            "preoutcome_status"
        ].strip()

        input_pre_status = assoc_row[
            "input_preoutcome_status"
        ].strip()

        assoc_final = assoc_row[
            "final_status"
        ].strip()

        metric_final = metric_row[
            "final_status"
        ].strip()

        if pre_status != input_pre_status:
            raise A3DerivationError(
                f"Preoutcome status disagreement for {key}: "
                f"{pre_status!r} != {input_pre_status!r}"
            )

        if assoc_final != metric_final:
            raise A3DerivationError(
                f"Final-status disagreement for {key}: "
                f"{assoc_final!r} != {metric_final!r}"
            )

        if assoc_final not in STATUS_ORDER:
            raise A3DerivationError(
                f"Unexpected terminal status for {key}: "
                f"{assoc_final!r}"
            )

        assoc_selected = assoc_row[
            "selected_candidate_index"
        ].strip()

        metric_selected = metric_row[
            "selected_candidate_index"
        ].strip()

        if assoc_selected != metric_selected:
            raise A3DerivationError(
                f"Selected-candidate disagreement for {key}: "
                f"{assoc_selected!r} != {metric_selected!r}"
            )

        evaluable = parse_bool(
            metric_row["evaluable"],
            "evaluable",
            key,
        )

        valid_associated = parse_bool(
            metric_row["valid_associated"],
            "valid_associated",
            key,
        )

        expected_valid = (
            assoc_final == "VALID_ASSOCIATED"
        )

        if valid_associated != expected_valid:
            raise A3DerivationError(
                f"valid_associated inconsistent with final_status "
                f"for {key}."
            )

        expected_evaluable = (
            pre_status == "CANDIDATE_SET_READY"
        )

        if evaluable != expected_evaluable:
            raise A3DerivationError(
                f"evaluable inconsistent with frozen preoutcome "
                f"status for {key}: "
                f"preoutcome={pre_status!r}, "
                f"evaluable={evaluable}"
            )

        if expected_valid and not evaluable:
            raise A3DerivationError(
                f"VALID_ASSOCIATED event is not evaluable: {key}"
            )

        if (
            assoc_final == "VALID_ASSOCIATED"
            and not assoc_selected
        ):
            raise A3DerivationError(
                f"VALID_ASSOCIATED event lacks selected candidate: "
                f"{key}"
            )

        if (
            assoc_final != "VALID_ASSOCIATED"
            and assoc_selected
        ):
            raise A3DerivationError(
                f"Non-valid terminal status unexpectedly has "
                f"selected candidate: {key}"
            )

        status_counter[assoc_final] += 1

        if evaluable:
            evaluable_counter[assoc_final] += 1
            total_evaluable += 1

        if valid_associated:
            valid_counter[assoc_final] += 1
            total_valid += 1

    # ------------------------------------------------------------
    # Frozen headline integrity checks
    # ------------------------------------------------------------

    if total_evaluable != EXPECTED_EVALUABLE:
        raise A3DerivationError(
            f"Expected evaluable={EXPECTED_EVALUABLE}, "
            f"observed {total_evaluable}."
        )

    if total_valid != EXPECTED_VALID_ASSOCIATED:
        raise A3DerivationError(
            f"Expected valid_associated="
            f"{EXPECTED_VALID_ASSOCIATED}, "
            f"observed {total_valid}."
        )

    realized_counts = {
        status: int(status_counter[status])
        for status in STATUS_ORDER
    }

    if realized_counts != EXPECTED_REALIZED_COUNTS:
        raise A3DerivationError(
            "Derived native terminal-status counts do not match "
            "the frozen G6 result."
        )

    if sum(realized_counts.values()) != EXPECTED_TOTAL:
        raise A3DerivationError(
            "Terminal-status counts do not sum to full G6 "
            "membership."
        )

    # ------------------------------------------------------------
    # Output status distribution
    # ------------------------------------------------------------

    distribution_rows: list[dict[str, str]] = []

    for index, status in enumerate(
        STATUS_ORDER,
        start=1,
    ):

        count = realized_counts[status]

        distribution_rows.append(
            {
                "status_order":
                    str(index),
                "native_g6_terminal_status":
                    status,
                "count":
                    str(count),
                "full_g6_denominator":
                    str(EXPECTED_TOTAL),
                "proportion_full_g6":
                    f"{count / EXPECTED_TOTAL:.12f}",
                "evaluable_count":
                    str(
                        int(
                            evaluable_counter[
                                status
                            ]
                        )
                    ),
                "valid_associated_count":
                    str(
                        int(
                            valid_counter[
                                status
                            ]
                        )
                    ),
            }
        )

    OUTPUT_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_CSV.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:

        fieldnames = [
            "status_order",
            "native_g6_terminal_status",
            "count",
            "full_g6_denominator",
            "proportion_full_g6",
            "evaluable_count",
            "valid_associated_count",
        ]

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            lineterminator="\n",
        )

        writer.writeheader()
        writer.writerows(
            distribution_rows
        )

    # ------------------------------------------------------------
    # Deterministic summary
    # ------------------------------------------------------------

    summary = {
        "artifact_id":
            "G7R6_A3_LOCKED_LEAD_STATUS",
        "analysis_role":
            "post-confirmatory zero-propagation A3 status derivation",
        "parent_head":
            PARENT_HEAD,
        "population": {
            "full_g6_events":
                EXPECTED_TOTAL,
            "evaluable_events":
                total_evaluable,
            "non_evaluable_events":
                EXPECTED_TOTAL - total_evaluable,
            "valid_associated_events":
                total_valid,
            "evaluable_unassociated_events":
                realized_counts[
                    "UNASSOCIATED_REFERENCE_EVENT"
                ],
        },
        "native_terminal_status_counts":
            realized_counts,
        "native_terminal_status_proportions_full_g6": {
            status:
                realized_counts[status]
                / EXPECTED_TOTAL
            for status in STATUS_ORDER
        },
        "input_sha256":
            observed_hashes,
        "constraints": {
            "raw_gp_history_opened":
                False,
            "protected_reference_source_opened":
                False,
            "orbital_propagation_performed":
                False,
            "candidate_generation_performed":
                False,
            "candidate_refinement_performed":
                False,
            "reference_reassociation_performed":
                False,
            "cal0_refit_performed":
                False,
            "threshold_retuning_performed":
                False,
            "network_access_performed":
                False,
            "g6_membership_changed":
                False,
            "failed_events_removed":
                False,
        },
        "interpretation": {
            "purpose":
                "explicit retention and reporting of frozen "
                "information/failure/association status",
            "conditional_accuracy_changed":
                False,
            "no_pre_tca_reconstruction_inferred":
                False,
            "note":
                "NO_PRE-TCA_RECONSTRUCTION is reserved for "
                "the frozen sequential A2/S07 analysis and is "
                "not fabricated from the locked-lead G6 table.",
        },
    }

    write_text_lf(
        OUTPUT_JSON,
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
    )

    # ------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------

    csv_hash = sha256(
        OUTPUT_CSV
    )

    json_hash = sha256(
        OUTPUT_JSON
    )

    audit_lines = [
        "G7 R6 A3 ZERO-PROPAGATION DERIVATION AUDIT",
        "==========================================",
        "",
        f"PARENT_HEAD = {PARENT_HEAD}",
        "",
        "INPUT_HASH_VERIFICATION = PASS",
        f"PREOUTCOME_SHA256 = "
        f"{observed_hashes[str(PREOUTCOME_PATH)]}",
        f"ASSOCIATION_SHA256 = "
        f"{observed_hashes[str(ASSOCIATION_PATH)]}",
        f"METRICS_SHA256 = "
        f"{observed_hashes[str(METRICS_PATH)]}",
        "",
        "FULL_MEMBERSHIP_IDENTITY = PASS",
        f"FULL_G6_EVENT_COUNT = {EXPECTED_TOTAL}",
        "DUPLICATE_EVENT_KEYS = 0",
        "PREOUTCOME_STATUS_ALIGNMENT = PASS",
        "FINAL_STATUS_ALIGNMENT = PASS",
        "SELECTED_CANDIDATE_ALIGNMENT = PASS",
        "EVALUABLE_FLAG_ALIGNMENT = PASS",
        "VALID_ASSOCIATED_FLAG_ALIGNMENT = PASS",
        "",
        f"EVALUABLE = "
        f"{total_evaluable}/{EXPECTED_TOTAL}",
        f"VALID_ASSOCIATED = "
        f"{total_valid}/{EXPECTED_TOTAL}",
        f"NON_EVALUABLE = "
        f"{EXPECTED_TOTAL - total_evaluable}",
        f"EVALUABLE_UNASSOCIATED = "
        f"{realized_counts['UNASSOCIATED_REFERENCE_EVENT']}",
        "",
        "NATIVE_TERMINAL_STATUS_COUNTS:",
    ]

    for status in STATUS_ORDER:
        audit_lines.append(
            f"{status} = "
            f"{realized_counts[status]}"
        )

    audit_lines.extend(
        [
            "",
            "EXPECTED_FROZEN_G6_COUNTS_MATCH = PASS",
            "",
            f"STATUS_DISTRIBUTION_SHA256 = {csv_hash}",
            f"STATUS_SUMMARY_SHA256 = {json_hash}",
            "",
            "RAW_GP_HISTORY_OPENED = false",
            "PROTECTED_REFERENCE_SOURCE_OPENED = false",
            "ORBITAL_PROPAGATION_PERFORMED = false",
            "CANDIDATE_GENERATION_PERFORMED = false",
            "CANDIDATE_REFINEMENT_PERFORMED = false",
            "REASSOCIATION_PERFORMED = false",
            "CAL0_REFIT_PERFORMED = false",
            "THRESHOLD_RETUNING_PERFORMED = false",
            "NETWORK_ACCESS_PERFORMED = false",
            "G6_MEMBERSHIP_CHANGED = false",
            "FAILED_EVENTS_REMOVED = false",
            "",
            "NO_PRE_TCA_RECONSTRUCTION_INFERRED = false",
            "",
            "A3_DERIVATION = PASS",
        ]
    )

    write_text_lf(
        OUTPUT_AUDIT,
        "\n".join(audit_lines)
        + "\n",
    )

    print(
        "A3_DERIVATION = PASS"
    )
    print(
        f"FULL_G6_EVENT_COUNT = "
        f"{EXPECTED_TOTAL}"
    )
    print(
        f"EVALUABLE = "
        f"{total_evaluable}"
    )
    print(
        f"VALID_ASSOCIATED = "
        f"{total_valid}"
    )
    print(
        f"NON_EVALUABLE = "
        f"{EXPECTED_TOTAL - total_evaluable}"
    )
    print(
        f"EVALUABLE_UNASSOCIATED = "
        f"{realized_counts['UNASSOCIATED_REFERENCE_EVENT']}"
    )

    for status in STATUS_ORDER:
        print(
            f"{status} = "
            f"{realized_counts[status]}"
        )

    print(
        f"STATUS_DISTRIBUTION_SHA256 = "
        f"{sha256(OUTPUT_CSV)}"
    )
    print(
        f"STATUS_SUMMARY_SHA256 = "
        f"{sha256(OUTPUT_JSON)}"
    )
    print(
        f"DERIVATION_AUDIT_SHA256 = "
        f"{sha256(OUTPUT_AUDIT)}"
    )


if __name__ == "__main__":
    main()
