"""Measure local API request latency and simultaneous SSE delivery.

This deliberately accepts loopback targets only. It is a development capacity
probe, not evidence of hosted availability, authentication or recovery SLOs.
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


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    return sorted(values)[ceil(len(values) * 0.95) - 1]


async def measure_api(client: httpx.AsyncClient, path: str, rate: int, seconds: int) -> dict:
    started = perf_counter()

    async def one(index: int) -> tuple[int | str, float]:
        target = started + index / rate
        await asyncio.sleep(max(0, target - perf_counter()))
        before = perf_counter()
        try:
            response = await client.get(path)
            return response.status_code, (perf_counter() - before) * 1000
        except httpx.HTTPError as error:
            return type(error).__name__, (perf_counter() - before) * 1000

    rows = await asyncio.gather(*(one(index) for index in range(rate * seconds)))
    latencies = [latency for status, latency in rows if status == 200]
    failures = [status for status, _ in rows if status != 200]
    return {
        "offered_requests_per_second": rate,
        "duration_seconds": seconds,
        "requests": len(rows),
        "successful": len(latencies),
        "failed": len(failures),
        "failure_examples": [str(item) for item in failures[:5]],
        "latency_ms_p95": round(_p95(latencies), 1) if latencies else None,
        "elapsed_seconds": round(perf_counter() - started, 2),
    }


async def measure_sse(
    client: httpx.AsyncClient, run_id: str, viewers: int, hold_seconds: float,
) -> dict:
    path = f"/v1/runs/{run_id}/events"
    started = perf_counter()

    async def one() -> tuple[int | str, float | None]:
        before = perf_counter()
        try:
            async with client.stream("GET", path, headers={"Last-Event-ID": "0"}) as response:
                if response.status_code != 200:
                    return response.status_code, None
                async for line in response.aiter_lines():
                    if line.startswith("id: "):
                        latency = (perf_counter() - before) * 1000
                        await asyncio.sleep(hold_seconds)
                        return 200, latency
                return "stream_closed", None
        except httpx.HTTPError as error:
            return type(error).__name__, None

    rows = await asyncio.gather(*(one() for _ in range(viewers)))
    latencies = [latency for status, latency in rows if status == 200 and latency is not None]
    failures = [status for status, _ in rows if status != 200]
    return {
        "viewers": viewers,
        "successful": len(latencies),
        "failed": len(failures),
        "failure_examples": [str(item) for item in failures[:5]],
        "first_event_ms_p95": round(_p95(latencies), 1) if latencies else None,
        "hold_seconds": hold_seconds,
        "elapsed_seconds": round(perf_counter() - started, 2),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8098")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--request-rate", type=int, default=50)
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--viewers", type=int, default=100)
    parser.add_argument("--hold-seconds", type=float, default=2)
    args = parser.parse_args()
    target = urlsplit(args.base_url)
    if (
        target.scheme != "http" or target.hostname not in {"127.0.0.1", "localhost", "::1"}
        or target.path not in {"", "/"} or target.query or target.fragment
        or not 1 <= args.request_rate <= 100 or not 1 <= args.duration <= 60
        or not 1 <= args.viewers <= 200 or not 0 < args.hold_seconds <= 30
    ):
        parser.error("Use a loopback HTTP origin and bounded load parameters")
    headers = {}
    if token := os.environ.get("AIP_LOAD_BEARER"):
        headers["Authorization"] = f"Bearer {token}"
    if tenant_id := os.environ.get("AIP_LOAD_TENANT_ID"):
        headers["X-Tenant-ID"] = tenant_id
    limits = httpx.Limits(max_connections=max(150, args.viewers + 20))
    async with httpx.AsyncClient(
        base_url=args.base_url, headers=headers, limits=limits,
        timeout=httpx.Timeout(30.0), trust_env=False,
    ) as client:
        history = await client.get(f"/v1/runs/{args.run_id}/events/history")
        if history.status_code != 200 or not history.json():
            print(json.dumps({"error": "Run must have at least one authorized durable event"}))
            return 1
        api = await measure_api(client, "/v1/projects", args.request_rate, args.duration)
        sse = await measure_sse(client, args.run_id, args.viewers, args.hold_seconds)
    print(json.dumps({"scope": "loopback_development_only", "api": api, "sse": sse}, indent=2))
    return 0 if api["failed"] == 0 and sse["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
