"""Register a provider model as disabled until live qualification passes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from platform_app.db import SessionLocal
from platform_app.model_qualification import QualificationError, register_model_entry
from platform_app.schemas import ModelRegister


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", required=True, type=Path)
    parser.add_argument("--operator", required=True)
    args = parser.parse_args()
    try:
        body = ModelRegister.model_validate(json.loads(args.definition.read_text(encoding="utf-8")))
        with SessionLocal() as db:
            model = register_model_entry(db, body, args.operator)
            db.commit()
        print(json.dumps({"model_entry_id": model.id, "state": model.state}))
        return 0
    except (OSError, ValueError, ValidationError):
        print(json.dumps({"status": "failed", "code": "INVALID_DEFINITION"}))
        return 2
    except QualificationError as error:
        print(json.dumps({"status": "failed", "code": error.code, "message": str(error)}))
        return 2
    except SQLAlchemyError:
        print(json.dumps({"status": "failed", "code": "REGISTRY_UNAVAILABLE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
