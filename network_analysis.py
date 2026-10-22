
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import async_playwright

TARGET = os.getenv(
    "TARGET_URL",
    "https://playerbee.top/charlie/pga-lpga-golf/3028",
)

OUT = Path("reports")
OUT.mkdir(exist_ok=True)

events = []
pages_seen = set()
errors = []
MAX_EVENTS = 5000


def safe_url(url):
    """Remove query strings, fragments and user-info from report URLs."""
    try:
        p = urlsplit(url)

        if p.scheme not in ("http", "https"):
            return f"{p.scheme}:"

        host = p.hostname or ""
        if p.port:
            host = f"{host}:{p.port}"

        return urlunsplit((p.scheme, host, p.path, "", ""))
    except Exception:
        return "[invalid URL]"


def add_event(event):
    if len(events) < MAX_EVENTS:
        event["time_utc"] = datetime.now(timezone.utc).isoformat()
        events.append(event)


def possible_manifest(url):
    path = urlsplit(url).path.lower()
    return path.endswith((".m3u8", ".mpd"))


def classify_url(url):
    host = (urlsplit(url).hostname or "").lower()

    if "playerbee.top" in host:
        return "target-domain"

    if "cloudflare.com" in host or host.endswith(".challenges.cloudflare.com"):
        return "security-challenge"

    return "external-domain"


def record_request(request):
    url = request.url
    previous = request.redirected_from

    add_event({
        "event": "request",
        "method": request.method,
        "resource_type": request.resource_type,
        "url": safe_url(url),
        "domain_class": classify_url(url),
        "possible_manifest": possible_manifest(url),
        "redirected_from": (
            safe_url(previous.url) if previous else None
        ),
    })


def record_response(response):
    request = response.request
    url = response.url

    add_event({
        "event": "response",
        "status": response.status,
        "resource_type": request.resource_type,
        "url": safe_url(url),
        "domain_class": classify_url(url),
        "possible_manifest": possible_manifest(url),
        "content_type": response.headers.get("content-type", ""),
        "redirect_location": safe_url(
            response.headers.get("location", "")
        ) if response.headers.get("location") else None,
    })


def record_failed_request(request):
    add_event({
        "event": "request_failed",
        "method": request.method,
        "resource_type": request.resource_type,
        "url": safe_url(request.url),
        "domain_class": classify_url(request.url),
        "failure": (request.failure or "unknown")[:200],
    })


def record_navigation(frame):
    if frame == frame.page.main_frame:
        kind = "main_navigation"
    else:
        kind = "iframe_navigation"

    url = frame.url

    add_event({
        "event": kind,
        "url": safe_url(url),
        "domain_class": classify_url(url),
    })


async def inspect_page(page, kind):
    page_id = id(page)

    if page_id in pages_seen:
        return

    pages_seen.add(page_id)

    page.on("request", record_request)
    page.on("response", record_response)
    page.on("requestfailed", record_failed_request)
    page.on("framenavigated", record_navigation)

    page.on(
        "pageerror",
        lambda exc: errors.append(str(exc)[:200]),
    )

    add_event({
        "event": kind,
        "url": safe_url(page.url),
        "domain_class": classify_url(page.url),
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
        "notes": [],
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        context = await browser.new_context(
            ignore_https_errors=False,
        )

        # Detect new tabs and popup windows.
        context.on(
            "page",
            lambda page: asyncio.create_task(
                inspect_page(page, "new_tab_or_popup")
            ),
        )

        page = await context.new_page()
        await inspect_page(page, "main_page")

        try:
            response = await page.goto(
                TARGET,
                wait_until="domcontentloaded",
                timeout=30000,
            )

            if response:
                summary["main_status"] = response.status

            # Give ordinary page scripts time to run.
            await page.wait_for_timeout(5000)

            summary["title"] = await page.title()
            summary["final_url"] = safe_url(page.url)

            summary["iframes"] = await page.locator(
                "iframe"
            ).evaluate_all(
                """els => els.map(el => ({
                    src: el.getAttribute('src') || '',
                    title: el.getAttribute('title') || '',
                    width: el.getAttribute('width') || '',
                    height: el.getAttribute('height') || ''
                }))"""
            )

            # Sanitize iframe URLs before saving.
            for iframe in summary["iframes"]:
                iframe["src"] = safe_url(iframe["src"])

            summary["media_elements"] = await page.locator(
                "video, audio, source"
            ).evaluate_all(
                """els => els.map(el => ({
                    tag: el.tagName.toLowerCase(),
                    src: el.getAttribute('src') || '',
                    type: el.getAttribute('type') || ''
                }))"""
            )

            for media in summary["media_elements"]:
                media["src"] = safe_url(media["src"])

            if summary["main_status"] == 403:
                summary["notes"].append(
                    "HTTP 403: the page may be blocked or showing "
                    "a security challenge."
                )

            if summary["main_status"] == 307:
                summary["notes"].append(
                    "HTTP 307 was observed. Check navigation events "
                    "for subsequent redirects."
                )

        except Exception as exc:
            summary["notes"].append(
                f"{type(exc).__name__}: {str(exc)[:250]}"
            )

        finally:
            summary["browser_errors"] = errors[:50]
            summary["finished_utc"] = (
                datetime.now(timezone.utc).isoformat()
            )

            # Save structured reports.
            (OUT / "summary.json").write_text(
                json.dumps(summary, indent=2),
                encoding="utf-8",
            )

            (OUT / "network.json").write_text(
                json.dumps(events, indent=2),
                encoding="utf-8",
            )

            # Generate a readable report.
            report = [
                "# Chromium Network Analysis",
                "",
                f"- Target: {summary['target']}",
                f"- HTTP status: {summary['main_status']}",
                f"- Title: {summary['title']}",
                f"- Final URL: {summary['final_url']}",
                f"- Recorded events: {len(events)}",
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
                "## Navigation and redirects",
                "",
            ])

            for event in events:
                if event["event"] in (
                    "main_navigation",
                    "iframe_navigation",
                    "new_tab_or_popup",
                ):
                    report.append(
                        f"- {event['event']}: {event['url']}"
                    )

            report.extend([
                "",
                "## External requests",
                "",
            ])

            for event in events:
                if (
                    event.get("event") == "request"
                    and event.get("domain_class") == "external-domain"
                ):
                    report.append(
                        f"- {event['resource_type']}: {event['url']}"
                    )

            report.extend([
                "",
                "## Manifest request indicators",
                "",
            ])

            manifests = [
                event for event in events
                if event.get("possible_manifest")
            ]

            if manifests:
                for event in manifests:
                    report.append(
                        f"- {event['event']}: {event['url']} "
                        f"(status={event.get('status', 'unknown')})"
                    )
            else:
                report.append(
                    "- No .m3u8 or .mpd request detected."
                )

            (OUT / "network-analysis.md").write_text(
                "\n".join(report) + "\n",
                encoding="utf-8",
            )

            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
