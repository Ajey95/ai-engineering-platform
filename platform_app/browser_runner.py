"""Record a controlled browser scenario and its observable outcome.

This runner is for fixtures inside an isolated execution environment. The
orchestrator must supply a trusted manifest; a model cannot change its origin
or process command. Browser output is evidence, never an agent verdict.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from playwright.async_api import async_playwright


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def same_origin(url: str, origin: str) -> bool:
    candidate = urlsplit(url)
    allowed = urlsplit(origin)
    return (
        candidate.scheme == allowed.scheme
        and candidate.hostname == allowed.hostname
        and candidate.port == allowed.port
        and candidate.username is None
        and candidate.password is None
    )


def safe_url(origin: str, path: str) -> str:
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        raise ValueError("Scenario navigation must use an absolute path on the allowed origin")
    url = urljoin(origin.rstrip("/") + "/", path.lstrip("/"))
    if not same_origin(url, origin):
        raise ValueError("Scenario navigation escaped the allowed origin")
    return url


async def wait_healthy(
    url: str, process: subprocess.Popen, deadline: float, instance_id: str,
    require_instance_header: bool = True,
) -> None:
    async with httpx.AsyncClient(follow_redirects=False, timeout=1.0) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"Fixture process exited before health check: {process.returncode}"
                )
            try:
                response = await client.get(url)
                if (
                    response.status_code == 200
                    and (
                        not require_instance_header
                        or response.headers.get("x-aip-fixture-instance") == instance_id
                    )
                ):
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
    raise TimeoutError("Fixture did not become healthy")


async def run_scenario(manifest: dict, workspace: Path, artifacts: Path) -> dict:
    origin = manifest["allowed_origin"]
    if urlsplit(origin).scheme != "http" or urlsplit(origin).hostname != "127.0.0.1":
        raise ValueError("Controlled fixture origin must be local loopback HTTP")
    if not same_origin(manifest["health_url"], origin):
        raise ValueError("Health check escaped the allowed origin")
    steps = manifest["scenario"]["steps"]
    if not isinstance(steps, list) or not steps or len(steps) > 100:
        raise ValueError("Scenario must have 1 to 100 steps")
    artifacts.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": "1.0",
        "case_id": manifest["case_id"],
        "started_at": timestamp(),
        "status": "ERROR",
        "steps": [],
        "responses": [],
        "console_errors": [],
        "page_errors": [],
        "request_failures": [],
        "recording": None,
        "recording_disabled_reason": None,
        "final_screenshot": None,
        "screenshot_sha256": None,
    }
    stdout_file = (artifacts / "fixture.log").open("w", encoding="utf-8")
    command = manifest["start_command"]
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise ValueError("Fixture start command must be an argument list")
    startup_timeout = manifest.get("startup_timeout_seconds", 20)
    if not isinstance(startup_timeout, int) or not 1 <= startup_timeout <= 300:
        raise ValueError("Startup timeout is outside policy")
    instance_id = secrets.token_hex(16)
    process = subprocess.Popen(
        command,
        cwd=workspace,
        env={**os.environ, "PYTHONUNBUFFERED": "1", "AIP_FIXTURE_INSTANCE_ID": instance_id},
        stdout=stdout_file,
        stderr=subprocess.STDOUT,
    )
    try:
        await wait_healthy(
            manifest["health_url"], process, time.monotonic() + startup_timeout, instance_id,
            require_instance_header=manifest.get("require_instance_header", True),
        )
        async with async_playwright() as playwright:
            executable = os.environ.get("AIP_BROWSER_EXECUTABLE")
            browser = await playwright.chromium.launch(
                headless=True, executable_path=executable
            )
            result["browser_version"] = browser.version
            masks = manifest["scenario"].get("mask_selectors", [])
            context_options = {"viewport": {"width": 1280, "height": 720}}
            if masks:
                # Screenshots can be masked before capture. Playwright videos
                # start at context creation, before page masks can be guaranteed.
                result["recording_disabled_reason"] = "sensitive_regions_present"
            else:
                context_options["record_video_dir"] = str(artifacts)
                context_options["record_video_size"] = {"width": 1280, "height": 720}
            context = await browser.new_context(**context_options)
            page = await context.new_page()
            video = page.video

            async def route_request(route):
                if not same_origin(route.request.url, origin):
                    await route.abort("blockedbyclient")
                else:
                    await route.continue_()

            await context.route("**/*", route_request)
            page.on(
                "response",
                lambda response: result["responses"].append(
                    {
                        "path": urlsplit(response.url).path,
                        "status": response.status,
                        "method": response.request.method,
                        "at": timestamp(),
                    }
                ),
            )
            page.on("console", lambda message: result["console_errors"].append(message.text[:500])
                    if message.type == "error" else None)
            page.on("pageerror", lambda error: result["page_errors"].append(str(error)[:500]))
            page.on(
                "requestfailed",
                lambda request: result["request_failures"].append(
                    {"path": urlsplit(request.url).path, "failure": request.failure}
                ),
            )
            try:
                for index, step in enumerate(steps):
                    entry = {"index": index, "action": step["action"], "at": timestamp()}
                    result["steps"].append(entry)
                    try:
                        action = step["action"]
                        if action == "goto":
                            await page.goto(
                                safe_url(origin, step["path"]), wait_until="networkidle"
                            )
                        elif action in {"fill", "click"}:
                            locator = page.get_by_role(step["role"], name=step["name"], exact=True)
                            if action == "fill":
                                await locator.fill(step["value"], timeout=5000)
                            else:
                                await locator.click(timeout=5000)
                        elif action == "expect_text":
                            await page.get_by_text(step["text"], exact=False).wait_for(timeout=5000)
                        else:
                            raise ValueError(f"Unsupported scenario action: {action}")
                        entry["status"] = "PASSED"
                        entry["page_path"] = urlsplit(page.url).path
                        headings = await page.get_by_role("heading").all_inner_texts()
                        statuses = await page.get_by_role("status").all_inner_texts()
                        entry["dom_summary"] = {
                            "title": (await page.title())[:100],
                            "headings": [text[:100] for text in headings[:5]],
                            "status": [text[:100] for text in statuses[:3]],
                        }
                    except Exception as error:
                        entry["status"] = "FAILED"
                        entry["error"] = f"{type(error).__name__}: {str(error)[:500]}"
                        result["status"] = "FAILED"
                        break
                else:
                    result["status"] = "PASSED"
                for selector in masks:
                    await page.locator(selector).evaluate_all(
                        "elements => elements.forEach(el => el.style.visibility = 'hidden')"
                    )
                screenshot = artifacts / "final.png"
                await page.screenshot(path=str(screenshot), full_page=True)
                result["final_screenshot"] = screenshot.name
                result["screenshot_sha256"] = hashlib.sha256(screenshot.read_bytes()).hexdigest()
            finally:
                await context.close()
                if video is not None:
                    result["recording"] = Path(await video.path()).name
                await browser.close()
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {str(error)[:500]}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        stdout_file.close()
        result["finished_at"] = timestamp()
        (artifacts / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = asyncio.run(run_scenario(manifest, args.workspace, args.artifacts))
    print(json.dumps({"case_id": result["case_id"], "status": result["status"]}))
    return 0 if result["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
