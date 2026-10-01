"""Run trusted fixture checks inside a development container.

The hidden oracle is mounted only for the oracle invocation. Browser and named
test containers cannot read it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from verifier import run_hidden_oracle, run_named_test


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["named", "oracle"], required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--name", default="baseline")
    parser.add_argument("--oracle", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.mode == "named":
        receipt = run_named_test(manifest, args.name, args.workspace, args.artifacts)
    else:
        if args.oracle is None:
            parser.error("--oracle is required for oracle mode")
        receipt = run_hidden_oracle(manifest, args.workspace, args.oracle, args.artifacts)
    print(json.dumps({"case_id": receipt["case_id"], "status": receipt["status"]}))
    return 0 if receipt["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
