"""Operator-only model lifecycle transition with a durable reason."""

from __future__ import annotations

import argparse
import json

from platform_app.db import SessionLocal
from platform_app.model_qualification import QualificationError, change_model_state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("enable", "deprecate", "disable"))
    parser.add_argument("model_entry_id")
    parser.add_argument("--operator", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    try:
        result = change_model_state(
            SessionLocal, args.model_entry_id, args.action, args.operator, args.reason,
        )
    except QualificationError as error:
        parser.error(f"{error.code}: {error}")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
