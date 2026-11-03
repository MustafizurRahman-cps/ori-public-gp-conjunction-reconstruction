#!/usr/bin/env python3
"""Independent offline review of the G7 R6 A2 metric implementation.

No real A2 metric execution is performed.  The review verifies frozen input
bindings, source structure, output schema intent, authorization blocking, and
synthetic semantics for S05/S06/S07 and Q01-Q06.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PARENT_COMMIT = "68e1a188db9ecc18d80c65655e75e05c4cb123b1"
PARENT_TAG = "g7-r6-a2-metric-semantics-frozen"

EXECUTOR = ROOT / "scripts/g7/run_g7r6_a2_metric_analysis.py"
OFFLINE = ROOT / "results/g7/G7R6_A2_Metric_Implementation_Offline_Validation.json"
OUT = ROOT / "results/g7/G7R6_A2_Metric_Implementation_Independent_Review.json"

EXPECTED_EXECUTOR_SHA256 = "6fc04230f230c79053bc1e9e5a9f2887bd9842fa07f234667122a1f051b2cc51"


class ReviewError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReviewError(message)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
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


def tag_target(tag: str) -> str | None:
    result = subprocess.run(
        ["git", "rev-list", "-n", "1", tag],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def load_executor():
    require(EXECUTOR.is_file(), "metric executor missing")
    require(
        sha256(EXECUTOR) == EXPECTED_EXECUTOR_SHA256,
        "metric executor SHA mismatch",
    )
    spec = importlib.util.spec_from_file_location(
        "g7r6_a2_metric_executor_under_review",
        EXECUTOR,
    )
    require(spec is not None and spec.loader is not None, "cannot import executor")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def source_structure_audit() -> dict:
    source = EXECUTOR.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    prohibited_imports = {
        "sgp4",
        "requests",
        "urllib",
        "http",
        "socket",
        "aiohttp",
    }
    require(
        not (imported_roots & prohibited_imports),
        f"prohibited dependency imported: {sorted(imported_roots & prohibited_imports)}",
    )

    calls_true = 0
    calls_false = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name) and target.id == "execute_metrics":
            for kw in node.keywords:
                if kw.arg == "authorization":
                    if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        calls_true += 1
                    if isinstance(kw.value, ast.Constant) and kw.value.value is False:
                        calls_false += 1

    require(calls_true == 0, "executor source self-authorizes real metric execution")
    require(calls_false >= 2, "authorization=False guards are not exercised")

    execute_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "execute_metrics"
    ]
    require(len(execute_nodes) == 1, "execute_metrics definition count changed")

    execute_source = ast.get_source_segment(source, execute_nodes[0]) or ""
    require(
        "A2MetricExecutionNotAuthorized" in execute_source
        and "authorization is not True" in execute_source,
        "execute_metrics authorization guard is missing",
    )

    required_semantic_markers = [
        "FILTERED_VALID_ASSOCIATED_CHRONOLOGICAL",
        "EARLIEST_PRE_TCA_VALID_ASSOCIATED",
        "(t_r-W,t_r]",
        "INSUFFICIENT_SUCCESSIVE_VALID_ASSOCIATED_RECONSTRUCTIONS",
        "NO PRE-TCA RECONSTRUCTION",
        "q06_requires_valid_associated",
    ]
    for marker in required_semantic_markers:
        require(marker in source, f"semantic marker missing: {marker}")

    require(
        "sgp4" not in source.lower(),
        "executor source unexpectedly references SGP4",
    )
    require(
        "requests." not in source.lower()
        and "http://" not in source.lower()
        and "https://" not in source.lower(),
        "executor source unexpectedly contains network operation marker",
    )

    return {
        "imported_roots": sorted(imported_roots),
        "authorization_true_call_count": calls_true,
        "authorization_false_call_count": calls_false,
        "execute_metrics_guard_verified": True,
        "prohibited_import_hits": [],
        "semantic_markers_verified": required_semantic_markers,
    }


def main() -> int:
    require(
        git_value("rev-parse", "HEAD") == PARENT_COMMIT,
        "implementation review must run at DR-0065 commit",
    )
    require(
        tag_target(PARENT_TAG) == PARENT_COMMIT,
        "metric-semantics tag target changed",
    )
    require(not OUT.exists(), "independent metric implementation review already exists")
    require(OFFLINE.is_file(), "offline validation artifact missing")

    with OFFLINE.open("r", encoding="utf-8") as stream:
        offline = json.load(stream)

    require(
        offline.get("status")
        == "G7R6_A2_METRIC_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",
        "offline validation did not pass",
    )
    require(
        offline.get("executor_sha256") == EXPECTED_EXECUTOR_SHA256,
        "offline validation/executor hash mismatch",
    )
    require(
        offline["operations"]["real_metric_execution_performed"] is False,
        "offline validation reports real metric execution",
    )
    require(
        offline["metric_execution_blocked_without_authorization"] is True,
        "offline validation did not verify execution guard",
    )

    executor = load_executor()
    structure = source_structure_audit()
    synthetic = executor.synthetic_validation()

    blocked = False
    try:
        executor.execute_metrics(authorization=False)
    except executor.A2MetricExecutionNotAuthorized:
        blocked = True
    require(blocked, "independent review could not verify execution guard")

    output_plan = offline["output_plan"]
    require(
        int(output_plan["q_object_update_rows_expected"]) == 4_040_162,
        "Q object-update planned cardinality changed",
    )
    require(
        output_plan["event_transactions_atomic"] is True,
        "event-atomic checkpointing not frozen",
    )
    require(
        output_plan["resume_supported"] is True,
        "resume support not frozen",
    )

    semantics = offline["semantics"]
    require(
        semantics["s05_s06_sequence"]
        == "FILTERED_VALID_ASSOCIATED_CHRONOLOGICAL",
        "S05/S06 sequence semantics changed",
    )
    require(
        semantics["intervening_failure_resets_sequence"] is False,
        "intervening-failure semantics changed",
    )
    require(
        semantics["s07"] == "EARLIEST_PRE_TCA_VALID_ASSOCIATED",
        "S07 semantics changed",
    )
    require(
        int(semantics["q01_q05_schedule_rows"]) == 2_020_081,
        "Q01-Q05 schedule changed",
    )
    require(
        int(semantics["q03_q05_primary_window_days"]) == 30,
        "primary quality window changed",
    )
    require(
        semantics["q03_q05_secondary_windows_days_frozen"] == [14, 60],
        "frozen secondary quality windows changed",
    )
    require(
        semantics["q03_q05_secondary_execution"]
        == "DEFERRED_NOT_PART_OF_PRIMARY_IMPLEMENTATION",
        "secondary quality-window execution boundary changed",
    )
    require(
        semantics["materialized_quality_start"] == "t_ref-120d"
        and semantics["report_start"] == "t_ref-90d"
        and int(semantics["primary_quality_guard_days"]) == 30,
        "materialized primary-quality boundary semantics changed",
    )
    require(
        semantics["q06_requires_valid_associated"] is True,
        "Q06 applicability changed",
    )

    result = {
        "status": "G7R6_A2_METRIC_IMPLEMENTATION_INDEPENDENT_REVIEW_PASS",
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "executor_sha256": EXPECTED_EXECUTOR_SHA256,
        "offline_validation_sha256": sha256(OFFLINE),
        "source_structure_audit": structure,
        "synthetic_validation_repeated": synthetic,
        "execution_guard_independently_verified": blocked,
        "real_metric_execution_performed": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_recomputed": False,
        "raw_GP_HISTORY_opened": False,
        "secondary_14d_60d_metric_execution_performed": False,
        "network_requests": 0,
        "A0_execution_authorized": False,
        "next_safe_action":
            "FREEZE_A2_METRIC_IMPLEMENTATION_BEFORE_EXECUTION_AUTHORIZATION",
    }

    with OUT.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")

    print(json.dumps({
        **result,
        "independent_review_sha256": sha256(OUT),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
