# Benchmark contract

`platform_app/benchmark_contract.py` validates a versioned 40-case suite:
ten form/frontend bugs, ten API contract bugs, ten CSV/timestamp bugs and ten
non-bug or underspecified reports. At least 20 cases must be held out from
development, including release cases. Each case pins a Git commit, fixture,
hidden oracle digest, dependency lock digest, image digest, seed, expected
reproduction and accepted outcome classes.
The 40 cases must use distinct fixture directories and hidden oracle paths.

Validate a complete suite with:

```powershell
uv run python -m scripts.validate_benchmark --manifest PATH_TO_SUITE_JSON
```

The validator checks the pinned Git objects and rejects an incomplete suite.
An optional results JSON must contain exactly one distinct run per case for
one model. Scoring includes failed attempts in the denominator and flags a
claimed repair without a passing hidden check, including repairs on non-bug
cases. Supplied result booleans are not independently attested by this tool;
its output is labelled `UNVERIFIED_RESULTS` and `metric_targets_met` is not a
release verdict.

Only the `form-submit-001` development fixture currently exists. The other
39 reviewed cases, held-out oracle execution, provider-run evidence binding,
and repeated-trial qualification are still required before this benchmark
can report a platform result.
