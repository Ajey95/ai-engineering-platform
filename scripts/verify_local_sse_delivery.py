"""Measure delivery of one new event to already connected local SSE viewers.

The probe appends a synthetic event to an isolated local restore/test database.
It refuses the ordinary development database and any non-loopback endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from math import ceil
from time import perf_counter
from urllib.parse import urlsplit

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from platform_app.models import Run, RunEvent
from platform_app.service import append_event


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--viewers", type=int, default=100)
    args = parser.parse_args()
    endpoint = urlsplit(args.base_url)
    database_url = os.environ.get("AIP_TEST_POSTGRES_URL", "")
    database = urlsplit(database_url)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}
        or endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment
        or database.scheme not in {"postgresql", "postgresql+psycopg"}
        or database.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not database.path.lstrip("/").startswith(("aip_restore_", "aip_verify_"))
        or not 1 <= args.viewers <= 200
    ):
        parser.error("Use a loopback API and an isolated local restore/test PostgreSQL DB")
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with Session(engine) as db:
            run = db.get(Run, args.run_id)
            if run is None or run.tenant_id != "local-tenant":
                parser.error("Run must belong to the isolated local synthetic tenant")
            cursor = db.scalar(select(func.max(RunEvent.sequence)).where(
                RunEvent.run_id == run.id,
            )) or 0
        headers = {}
        if token := os.environ.get("AIP_LOAD_BEARER"):
            headers["Authorization"] = f"Bearer {token}"
        if tenant_id := os.environ.get("AIP_LOAD_TENANT_ID"):
            headers["X-Tenant-ID"] = tenant_id
        connected = 0
        all_connected = asyncio.Event()
        limits = httpx.Limits(max_connections=max(150, args.viewers + 20))
        async with httpx.AsyncClient(
            base_url=args.base_url, headers=headers, limits=limits,
            timeout=httpx.Timeout(30.0), trust_env=False,
        ) as client:
            async def viewer(index: int) -> tuple[int, int | str, float | None]:
                nonlocal connected
                try:
                    async with client.stream(
                        "GET", f"/v1/runs/{args.run_id}/events",
                        headers={"Last-Event-ID": str(cursor)},
                    ) as response:
                        if response.status_code != 200:
                            return index, response.status_code, None
                        connected += 1
                        if connected == args.viewers:
                            all_connected.set()
                        async for line in response.aiter_lines():
                            if line.startswith("id: ") and int(line[4:]) > cursor:
                                return index, 200, perf_counter()
                        return index, "stream_closed", None
                except httpx.HTTPError as error:
                    return index, type(error).__name__, None

            tasks = [asyncio.create_task(viewer(index)) for index in range(args.viewers)]
            try:
                await asyncio.wait_for(all_connected.wait(), timeout=20)
            except TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                print(json.dumps({"connected": connected, "error": "Viewer admission timed out"}))
                return 1
            with Session(engine) as db:
                run = db.scalar(select(Run).where(Run.id == args.run_id).with_for_update())
                append_event(db, run, "capacity.probe", {"scope": "synthetic_local_restore"})
                db.commit()
                committed_at = perf_counter()
            rows = await asyncio.gather(*tasks)
        latencies = sorted(
            (arrived_at - committed_at) * 1000
            for _, status, arrived_at in rows if status == 200 and arrived_at is not None
        )
        failures = [(index, status) for index, status, _ in rows if status != 200]
        print(json.dumps({
            "scope": "isolated_loopback_postgres_only",
            "connected": connected, "delivered": len(latencies),
            "failed": len(failures), "failure_examples": failures[:5],
            "delivery_ms_p95": round(latencies[ceil(len(latencies) * 0.95) - 1], 1)
            if latencies else None,
        }, indent=2))
        return 0 if not failures and len(latencies) == args.viewers else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
