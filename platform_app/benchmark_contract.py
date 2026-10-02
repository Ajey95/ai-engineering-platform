"""Versioned 30+10 benchmark contract and conservative release scoring."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REVISION = re.compile(r"^[a-zA-Z0-9._-]{1,100}$")
EXPECTED_COUNTS = {
    "form_frontend": 10,
    "api_contract": 10,
    "csv_timestamp": 10,
    "non_bug": 10,
}


class BenchmarkError(Exception):
    pass


def _relative_asset(value: str, prefix: str) -> str:
    path = Path(value)
    if (
        path.is_absolute()
        or not value.startswith(prefix)
        or ".." in path.parts
        or "\\" in value
        or not value
    ):
        raise ValueError("Benchmark asset path is outside its reviewed scope")
    return value


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: str
    category: Literal["form_frontend", "api_contract", "csv_timestamp", "non_bug"]
    split: Literal["development", "regression", "release"]
    base_commit: str
    fixture_path: str
    hidden_oracle_path: str
    hidden_oracle_sha256: str
    dependency_lock_path: str
    dependency_lock_sha256: str
    environment_image_sha256: str
    run_seed: int = Field(ge=0, le=2**32 - 1)
    expected_reproduction: Literal["BUG_REPRODUCED", "NO_SUPPORTED_DEFECT"]
    accepted_outcomes: list[Literal["VERIFIED_REPAIR", "INCONCLUSIVE", "NO_DEFECT"]] = Field(
        min_length=1
    )

    @model_validator(mode="after")
    def validate_case(self):
        if not REVISION.fullmatch(self.case_id) or not HEX40.fullmatch(self.base_commit):
            raise ValueError("Case identity or pinned commit is invalid")
        if any(not HEX64.fullmatch(value) for value in (
            self.hidden_oracle_sha256,
            self.dependency_lock_sha256,
            self.environment_image_sha256,
        )):
            raise ValueError("Case digest is invalid")
        _relative_asset(self.fixture_path, "benchmarks/fixtures/")
        _relative_asset(self.hidden_oracle_path, "benchmarks/oracles/")
        _relative_asset(self.dependency_lock_path, "benchmarks/locks/")
        if len(set(self.accepted_outcomes)) != len(self.accepted_outcomes):
            raise ValueError("Accepted outcomes are duplicated")
        if self.category == "non_bug":
            if self.expected_reproduction != "NO_SUPPORTED_DEFECT":
                raise ValueError("Non-bug cases must expect no supported defect")
            if "VERIFIED_REPAIR" in self.accepted_outcomes:
                raise ValueError("Non-bug cases cannot accept a repair")
        elif self.expected_reproduction != "BUG_REPRODUCED":
            raise ValueError("Bug cases must define a reproducible defect")
        return self


class BenchmarkSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"]
    benchmark_revision: str
    oracle_revision: str
    cases: list[BenchmarkCase]

    @model_validator(mode="after")
    def validate_suite(self):
        if not REVISION.fullmatch(self.benchmark_revision) or not REVISION.fullmatch(
            self.oracle_revision
        ):
            raise ValueError("Benchmark revision is invalid")
        counts = Counter(case.category for case in self.cases)
        if counts != EXPECTED_COUNTS:
            raise ValueError("Benchmark requires exactly 10 cases in each of four categories")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Benchmark case IDs must be unique")
        fixtures = [case.fixture_path for case in self.cases]
        oracles = [case.hidden_oracle_path for case in self.cases]
        if len(set(fixtures)) != len(fixtures) or len(set(oracles)) != len(oracles):
            raise ValueError("Every benchmark case needs a distinct fixture and hidden oracle")
        held_out = sum(case.split != "development" for case in self.cases)
        if held_out < 20 or not any(case.split == "release" for case in self.cases):
            raise ValueError("At least 20 cases must be held out, including release cases")
        return self


def verify_suite_assets(suite: BenchmarkSuite, repository: Path) -> None:
    root = repository.resolve()
    checked_commits: set[str] = set()
    checked_assets: set[tuple[str, str, str]] = set()
    checked_fixtures: set[tuple[str, str]] = set()
    for case in suite.cases:
        if case.base_commit not in checked_commits:
            valid = subprocess.run(
                ["git", "cat-file", "-e", f"{case.base_commit}^{{commit}}"],
                cwd=root, capture_output=True, check=False, timeout=10,
            )
            if valid.returncode != 0:
                raise BenchmarkError(f"Pinned commit is missing for {case.case_id}")
            checked_commits.add(case.base_commit)
        for relative, expected in (
            (case.hidden_oracle_path, case.hidden_oracle_sha256),
            (case.dependency_lock_path, case.dependency_lock_sha256),
        ):
            if (case.base_commit, relative, expected) in checked_assets:
                continue
            candidate = root / relative
            path = candidate.resolve()
            if (
                not path.is_relative_to(root)
                or candidate.is_symlink()
                or any(parent.is_symlink() for parent in candidate.parents if parent != root)
                or not path.is_file()
            ):
                raise BenchmarkError(f"Missing or untrusted asset for {case.case_id}")
            pinned = subprocess.run(
                ["git", "show", f"{case.base_commit}:{relative}"],
                cwd=root, capture_output=True, check=False, timeout=10,
            )
            if pinned.returncode != 0 or hashlib.sha256(pinned.stdout).hexdigest() != expected:
                raise BenchmarkError(f"Pinned asset digest changed for {case.case_id}")
            checked_assets.add((case.base_commit, relative, expected))
        if (case.base_commit, case.fixture_path) in checked_fixtures:
            continue
        candidate_fixture = root / case.fixture_path
        fixture = candidate_fixture.resolve()
        if (
            not fixture.is_relative_to(root)
            or not fixture.is_dir()
            or candidate_fixture.is_symlink()
            or any(parent.is_symlink() for parent in candidate_fixture.parents if parent != root)
            or any(path.is_symlink() for path in fixture.rglob("*"))
        ):
            raise BenchmarkError(f"Fixture is missing for {case.case_id}")
        pinned_fixture = subprocess.run(
            ["git", "cat-file", "-e", f"{case.base_commit}:{case.fixture_path}"],
            cwd=root, capture_output=True, check=False, timeout=10,
        )
        if pinned_fixture.returncode != 0:
            raise BenchmarkError(f"Pinned fixture is missing for {case.case_id}")
        checked_fixtures.add((case.base_commit, case.fixture_path))


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: str
    benchmark_revision: str
    model_entry_id: str
    run_id: str
    outcome: Literal["VERIFIED_REPAIR", "INCONCLUSIVE", "NO_DEFECT", "FAILED"]
    reproduced: bool
    hidden_check_executed: bool
    hidden_check_passed: bool
    actual_cost_usd: Decimal = Field(ge=0)
    duration_ms: int = Field(ge=0)

    @field_validator("actual_cost_usd", mode="before")
    @classmethod
    def parse_cost(cls, value):
        if isinstance(value, bool):
            raise ValueError("Cost must be a finite decimal")
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as error:
            raise ValueError("Cost must be a finite decimal") from error
        if not parsed.is_finite() or parsed < 0:
            raise ValueError("Cost must be a nonnegative finite decimal")
        return parsed


def score_complete_suite(suite: BenchmarkSuite, results: list[CaseResult]) -> dict:
    """Missing or duplicate attempts fail qualification; no case leaves the denominator."""
    by_id = {result.case_id: result for result in results}
    if len(results) != len(suite.cases) or len(by_id) != len(results):
        raise BenchmarkError("Every benchmark case needs exactly one result")
    expected_ids = {case.case_id for case in suite.cases}
    if set(by_id) != expected_ids or any(
        result.benchmark_revision != suite.benchmark_revision for result in results
    ):
        raise BenchmarkError("Result case IDs or benchmark revision differ")
    if (
        len({result.model_entry_id for result in results}) != 1
        or len({result.run_id for result in results}) != len(results)
    ):
        raise BenchmarkError("Results must pin one model and distinct run IDs")
    bugs = [case for case in suite.cases if case.category != "non_bug"]
    nonbugs = [case for case in suite.cases if case.category == "non_bug"]
    false_success = [
        case.case_id for case in bugs
        if by_id[case.case_id].outcome == "VERIFIED_REPAIR"
        and not (
            by_id[case.case_id].hidden_check_executed
            and by_id[case.case_id].hidden_check_passed
        )
    ] + [
        case.case_id for case in nonbugs
        if by_id[case.case_id].outcome == "VERIFIED_REPAIR"
    ]
    repaired = sum(
        by_id[case.case_id].outcome == "VERIFIED_REPAIR"
        and by_id[case.case_id].hidden_check_executed
        and by_id[case.case_id].hidden_check_passed
        for case in bugs
    )
    no_defect = sum(
        by_id[case.case_id].outcome in case.accepted_outcomes for case in nonbugs
    )
    reproduced = sum(by_id[case.case_id].reproduced for case in bugs)
    total_cost = sum((result.actual_cost_usd for result in results), Decimal(0))
    return {
        "benchmark_revision": suite.benchmark_revision,
        "cases": len(results),
        "bug_cases": len(bugs),
        "non_bug_cases": len(nonbugs),
        "reproduction_rate": str(Decimal(reproduced) / Decimal(len(bugs))),
        "verified_repair_rate": str(Decimal(repaired) / Decimal(len(bugs))),
        "all_submitted_verified_repair_rate": str(Decimal(repaired) / Decimal(len(results))),
        "non_bug_correct_rate": str(Decimal(no_defect) / Decimal(len(nonbugs))),
        "false_success_case_ids": false_success,
        "actual_model_cost_usd": str(total_cost),
        "metric_targets_met": (
            reproduced >= 27 and repaired >= 21 and no_defect >= 9 and not false_success
        ),
        "qualification_status": "UNVERIFIED_RESULTS",
    }


def suite_digest(suite: BenchmarkSuite) -> str:
    return hashlib.sha256(
        json.dumps(suite.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
