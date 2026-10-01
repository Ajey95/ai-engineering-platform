"""Check local synthetic evidence playback in the real frontend."""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    output = Path("artifacts/evaluation-v2/ui-check")
    output.mkdir(parents=True, exist_ok=True)
    responses = []
    errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("response", lambda response: responses.append({
            "path": response.url.split("?", 1)[0].split("127.0.0.1:5173")[-1],
            "status": response.status,
        }) if "/v1/dev/evaluation" in response.url else None)
        page.on("pageerror", lambda error: errors.append(str(error)[:300]))
        await page.goto("http://127.0.0.1:5173/", wait_until="networkidle")
        await page.get_by_role("button", name="Evaluations").click()
        await page.get_by_role("heading", name="form-submit-001").wait_for(timeout=15000)
        await page.locator("video").first.wait_for(timeout=15000)
        assert await page.locator("video").count() == 2
        for video in await page.locator("video").all():
            await video.evaluate("element => { element.muted = true; return element.play() }")
        await page.wait_for_function(
            "Array.from(document.querySelectorAll('video')).every(v => v.readyState >= 2)",
            timeout=20000,
        )
        await page.screenshot(path=str(output / "evaluation.png"), full_page=True)
        video_state = await page.locator("video").evaluate_all(
            "elements => elements.map(v => ({readyState: v.readyState, "
            "duration: v.duration, currentTime: v.currentTime}))"
        )
        mobile = await browser.new_page(viewport={"width": 390, "height": 844})
        await mobile.goto("http://127.0.0.1:5173/", wait_until="networkidle")
        await mobile.get_by_role("button", name="Open navigation").click()
        await mobile.get_by_role("button", name="Evaluations").click()
        await mobile.get_by_role("heading", name="form-submit-001").wait_for(timeout=15000)
        await mobile.locator("video").first.wait_for(timeout=15000)
        await mobile.screenshot(path=str(output / "evaluation-mobile.png"), full_page=True)
        mobile_overflow = await mobile.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth"
        )
        await browser.close()
    manifest_count = sum(item["path"].endswith("master.m3u8") and item["status"] in {200, 206}
                         for item in responses)
    segment_count = sum(item["path"].endswith(".m4s") and item["status"] in {200, 206}
                        for item in responses)
    result = {
        "videos": video_state, "manifest_200_count": manifest_count,
        "segment_200_count": segment_count, "page_errors": errors,
        "mobile_horizontal_overflow": mobile_overflow,
        "responses": responses,
        "screenshot": str(output / "evaluation.png"),
    }
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "responses"}))
    assert manifest_count == 2 and segment_count >= 2 and not errors and not mobile_overflow


if __name__ == "__main__":
    asyncio.run(main())
