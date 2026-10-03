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

The catalog now includes `form-submit-001` and 39 additional, distinct
synthetic fixture trees: nine form cases, ten API status cases, ten CSV or
timestamp cases and ten non-bug cases. Hidden oracles and reference versions
live outside the repair source trees. The generated catalog is checked by
`python -m scripts.verify_benchmark_cases`: bug baselines must fail the hidden
checks, non-bug baselines must pass, and every reference must pass. Browser
reproduction is checked with `python -m scripts.verify_benchmark_browser`.

After committing fixture, oracle and lock assets, run
`python -m scripts.build_benchmark_manifest --image-digest sha256:<built-image-id>`
to pin their exact commit and the locally built sandbox image in
`benchmarks/suite-v1.json`; then use `scripts.validate_benchmark` above. This
local image ID is not a pushed registry digest. Provider-run evidence binding,
held-out model evaluation, repeated-trial scores and hosted qualification
remain separate gates. The synthetic cases exercise a narrow shared app
shape; their oracle results do not establish general repair success.
