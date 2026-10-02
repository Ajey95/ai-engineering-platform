"""Deliver durable operational transitions to a configured HTTPS pager webhook."""

from __future__ import annotations

import argparse
import time

from platform_app.alert_delivery import deliver_next, validate_pager_destination
from platform_app.config import settings
from platform_app.db import SessionLocal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5)
    args = parser.parse_args()
    if not 1 <= args.poll_seconds <= 60:
        parser.error("Poll interval must be between 1 and 60 seconds")
    config = settings()
    try:
        validate_pager_destination(config.pager_webhook_url, config.pager_webhook_secret)
    except ValueError as error:
        parser.error(str(error))
    while True:
        with SessionLocal() as db:
            event_id = deliver_next(
                db, config.pager_webhook_url, config.pager_webhook_secret
            )
            db.commit()
        if event_id is None:
            if not args.serve:
                return 0
            time.sleep(args.poll_seconds)
        else:
            print(f"Processed alert transition {event_id}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
