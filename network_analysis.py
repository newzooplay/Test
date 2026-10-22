
import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime, timezone

from playwright.async_api import async_playwright

TARGET = os.getenv(
    "TARGET_URL",
    "https://playerbee.top/charlie/pga-lpga-golf/3028",
)

OUT = Path("reports")
OUT.mkdir(exist_ok=True)

events = []
errors = []


def safe_url(url):
    """Remove query parameters and fragments from recorded URLs."""
    try:
        p = urlsplit(url)
        return urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    except Exception:
        return "[invalid URL]"


def record_request(request):
    url = request.url
    path = urlsplit(url).path.lower()

    events.append({
        "event": "request",
        "method": request.method,
        "resource_type": request.resource_type,
        "url": safe_url(url),
        "possible_manifest": path.endswith((".m3u8", ".mpd")),
    })


def record_response(response):
    url = response.url
    path = urlsplit(url).path.lower()

    events.append({
        "event": "response",
        "status": response.status,
        "resource_type": response.request.resource_type,
        "url": safe_url(url),
        "possible_manifest": path.endswith((".m3u8", ".mpd")),
        "content_type": response.headers.get("content-type", ""),
    })


async def main():
    summary = {
        "target": safe_url(TARGET),
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "title": "",
        "final_url": "",
        "main_status": None,
        "iframes": [],
        "media_elements": [],
        "browser_errors": [],
        "notes": [],
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            ignore_https_errors=False,
        )
        page = await context.new_page()

        page.on("request", record_request)
        page.on("response", record_response)
        page.on(
            "pageerror",
            lambda exc: errors.append(str(exc)[:300]),
        )

        try:
            response = await page.goto(
                TARGET,
                wait_until="domcontentloaded",
                timeout=30000,
            )

            if response:
                summary["main_status"] = response.status

            # Allow normal page scripts to run briefly.
            await page.wait_for_timeout(5000)

            summary["title"] = await page.title()
            summary["final_url"] = safe_url(page.url)

            summary["iframes"] = await page.locator("iframe").evaluate_all(
                """els => els.map(el => ({
                    src: el.getAttribute('src') || '',
                    title: el.getAttribute('title') || '',
                    width: el.getAttribute('width') || '',
                    height: el.getAttribute('height') || ''
                }))"""
            )

            summary["media_elements"] = await page.locator(
                "video, audio, source"
            ).evaluate_all(
                """els => els.map(el => ({
                    tag: el.tagName.toLowerCase(),
                    src: el.getAttribute('src') || '',
                    type: el.getAttribute('type') || ''
                }))"""
            )

            if summary["main_status"] == 403:
                summary["notes"].append(
                    "Access denied or challenge response; "
                    "the original player page may not have loaded."
                )

        except Exception as exc:
            summary["notes"].append(
                f"Navigation/inspection error: {type(exc).__name__}: "
                f"{str(exc)[:250]}"
            )

        finally:
            summary["browser_errors"] = errors[:50]
            summary["finished_utc"] = (
                datetime.now(timezone.utc).isoformat()
            )

            # Do not record request bodies, cookies, authorization headers,
            # or query parameters that may contain tokens or keys.
            (OUT / "summary.json").write_text(
                json.dumps(summary, indent=2),
                encoding="utf-8",
            )

            (OUT / "network.json").write_text(
                json.dumps(events, indent=2),
                encoding="utf-8",
            )

            report = [
                "# Chromium Network Analysis",
                "",
                f"- Target: {summary['target']}",
                f"- HTTP status: {summary['main_status']}",
                f"- Page title: {summary['title']}",
                f"- Final URL: {summary['final_url']}",
                f"- Requests/responses recorded: {len(events)}",
                f"- Iframes: {len(summary['iframes'])}",
                f"- Media elements: {len(summary['media_elements'])}",
                "",
                "## Notes",
            ]

            report.extend(
                f"- {note}" for note in summary["notes"]
            )
            report.extend([
                "",
                "## Iframes",
                "```json",
                json.dumps(summary["iframes"], indent=2),
                "```",
                "",
                "## Media elements",
                "```json",
                json.dumps(summary["media_elements"], indent=2),
                "```",
                "",
                "## Manifest request indicators",
            ])

            manifests = [
                e for e in events
                if e.get("possible_manifest")
            ]

            if manifests:
                for item in manifests:
                    report.append(
                        f"- {item['event']}: "
                        f"{item['url']} "
                        f"(status={item.get('status', 'pending')})"
                    )
            else:
                report.append(
                    "- No .m3u8/.mpd request detected in recorded events."
                )

            (OUT / "network-analysis.md").write_text(
                "\n".join(report) + "\n",
                encoding="utf-8",
            )

            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
