#!/usr/bin/env python3
"""Independent offline review of A2 primary-metric execution authorization.

No real metric derivation is performed.
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

PARENT_COMMIT = "0e7cdecfa22b0c8130d1409f82306d816a3f651f"
PARENT_TAG = "g7-r6-a2-metric-implementation-v2-frozen"
AUTH_TAG = "g7-r6-a2-primary-metric-execution-authorized"

WRAPPER = ROOT / "scripts/g7/run_g7r6_a2_metric_execution_authorization.py"
EXECUTOR = ROOT / "scripts/g7/run_g7r6_a2_metric_analysis.py"
OFFLINE = ROOT / "results/g7/G7R6_A2_Metric_Execution_Authorization_Offline_Validation.json"
OUT = ROOT / "results/g7/G7R6_A2_Metric_Execution_Authorization_Independent_Review.json"

EXPECTED_WRAPPER_SHA256 = "80666b0b915a5a003f3eefc1a02cd0433abf329bd2e78d1bd2fb2ef84551b4bc"
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


def tag_target(tag: str) -> str | None:
    result = git("rev-list", "-n", "1", tag, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def load_wrapper():
    require(WRAPPER.is_file(), "authorization wrapper missing")
    require(
        sha256(WRAPPER) == EXPECTED_WRAPPER_SHA256,
        "authorization wrapper SHA mismatch",
    )
    spec = importlib.util.spec_from_file_location(
        "g7r6_a2_metric_authorization_under_review",
        WRAPPER,
    )
    require(spec is not None and spec.loader is not None, "cannot load authorization wrapper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def source_structure_audit() -> dict:
    source = WRAPPER.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_roots = set()
    authorization_true_calls = 0
    executor_calls = 0
    network_imports = {"requests", "urllib", "http", "socket", "aiohttp"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "execute_metrics":
                executor_calls += 1
                for kw in node.keywords:
                    if (
                        kw.arg == "authorization"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        authorization_true_calls += 1

    require(
        not (imported_roots & network_imports),
        f"authorization wrapper imports network dependency: {sorted(imported_roots & network_imports)}",
    )
    require(
        authorization_true_calls == 1,
        "authorization wrapper must contain exactly one authorization=True executor call",
    )
    require(
        executor_calls >= 2,
        "authorization wrapper must exercise blocked and authorized executor paths",
    )

    required_markers = [
        "A2_PRIMARY_METRIC_EXECUTION_AUTHORIZATION_TAG_MISSING",
        "large_input_execution_time_rehash_required",
        "secondary_14d_60d_execution_authorized",
        "INDEPENDENT_POST_EXECUTION_A2_METRIC_AUDIT_BEFORE_INTERPRETATION",
    ]
    for marker in required_markers:
        require(marker in source, f"authorization marker missing: {marker}")

    return {
        "imported_roots": sorted(imported_roots),
        "network_import_hits": [],
        "execute_metrics_call_count": executor_calls,
        "authorization_true_call_count": authorization_true_calls,
        "required_markers_verified": required_markers,
    }


def main() -> int:
    require(
        git_value("rev-parse", "HEAD") == PARENT_COMMIT,
        "authorization review must run at DR-0066 commit",
    )
    require(
        tag_target(PARENT_TAG) == PARENT_COMMIT,
        "metric-implementation freeze tag target changed",
    )
    require(
        tag_target(AUTH_TAG) is None,
        "authorization tag already exists during pre-freeze review",
    )
    require(not OUT.exists(), "authorization independent-review output already exists")
    require(OFFLINE.is_file(), "authorization offline-validation output missing")
    require(
        sha256(EXECUTOR) == EXPECTED_EXECUTOR_SHA256,
        "frozen executor SHA changed",
    )

    with OFFLINE.open("r", encoding="utf-8") as stream:
        offline = json.load(stream)

    require(
        offline["status"]
        == "G7R6_A2_METRIC_EXECUTION_AUTHORIZATION_OFFLINE_VALIDATION_PASS",
        "authorization offline validation did not pass",
    )
    require(
        offline["wrapper_sha256"] == EXPECTED_WRAPPER_SHA256,
        "offline validation/wrapper SHA mismatch",
    )
    require(
        offline["authorization_tag_present"] is False,
        "offline validation unexpectedly reports authorization tag present",
    )
    require(
        offline["metric_execution_guard_verified"] is True,
        "offline validation lost execution guard",
    )
    require(
        offline["large_input_execution_time_rehash_required"] is True,
        "execution-time large-input rehash requirement changed",
    )
    require(
        offline["secondary_14d_60d_execution_authorized"] is False,
        "offline authorization unexpectedly permits secondary windows",
    )
    require(
        int(offline["expected_q_object_update_rows"]) == 4_040_162,
        "expected Q object-update cardinality changed",
    )

    wrapper = load_wrapper()
    structure = source_structure_audit()

    # Missing authorization tag must stop before any executor authorization=True call.
    blocked = False
    try:
        wrapper.verify_execution_authorization()
    except wrapper.AuthorizationError as exc:
        blocked = "AUTHORIZATION_TAG_MISSING" in str(exc)
    require(blocked, "missing authorization tag was not independently enforced")

    result = {
        "status": "G7R6_A2_METRIC_EXECUTION_AUTHORIZATION_INDEPENDENT_REVIEW_PASS",
        "parent_commit": PARENT_COMMIT,
        "parent_tag": PARENT_TAG,
        "authorization_tag": AUTH_TAG,
        "wrapper_sha256": EXPECTED_WRAPPER_SHA256,
        "executor_sha256": EXPECTED_EXECUTOR_SHA256,
        "offline_validation_sha256": sha256(OFFLINE),
        "source_structure_audit": structure,
        "missing_authorization_tag_guard_independently_verified": blocked,
        "execution_time_materialized_db_rehash_required": True,
        "execution_time_production_csv_rehash_required": True,
        "secondary_14d_60d_execution_authorized": False,
        "real_metric_execution_performed": False,
        "orbital_propagation_performed": False,
        "candidate_generation_performed": False,
        "reference_association_recomputed": False,
        "raw_GP_HISTORY_opened": False,
        "network_requests": 0,
        "A0_execution_authorized": False,
        "next_safe_action":
            "FREEZE_PRIMARY_A2_METRIC_EXECUTION_AUTHORIZATION_BEFORE_EXECUTION",
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")

    print(json.dumps(
        {
            **result,
            "independent_review_sha256": sha256(OUT),
        },
        indent=2,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
