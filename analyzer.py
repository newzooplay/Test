
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlparse,
    urlunparse,
)

from playwright.async_api import async_playwright


# ==========================================
# CONFIGURATION
# ==========================================

TARGET_URL = "https://example.com"

NAVIGATION_TIMEOUT = 60_000
WAIT_AFTER_LOAD_MS = 5_000
MAX_NETWORK_RECORDS = 2000

SENSITIVE_QUERY_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "key",
    "signature",
    "sig",
    "auth",
    "authorization",
    "password",
    "session",
    "sessionid",
    "jwt",
}

VIDEO_EXTENSIONS = (
    ".m3u8",
    ".mpd",
    ".mp4",
    ".webm",
    ".m4v",
    ".mov",
    ".m4s",
    ".ts",
    ".ogg",
)

API_PATH_PATTERN = re.compile(
    r"/(?:api|graphql|rest|v[0-9]+)(?:/|$)",
    re.IGNORECASE,
)


# ==========================================
# HELPERS
# ==========================================

def redact_url(url):
    """Remove common sensitive query parameters from reports."""
    try:
        parsed = urlparse(url)

        query = [
            (key, "[REDACTED]" if key.lower() in
             SENSITIVE_QUERY_KEYS else value)
            for key, value in parse_qsl(
                parsed.query,
                keep_blank_values=True,
            )
        ]

        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query),
            "",  # Drop fragments
        ))
    except Exception:
        return "[URL REDACTED]"


def validate_url(url):
    parsed = urlparse(url)

    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(
            "TARGET_URL must be a valid HTTPS URL."
        )

    if parsed.username or parsed.password:
        raise ValueError(
            "URLs containing credentials are not allowed."
        )

    host = parsed.hostname.lower()

    if (
        host == "localhost"
        or host.endswith(".localhost")
        or host.endswith(".local")
        or host.endswith(".internal")
        or host.endswith(".test")
        or host.endswith(".invalid")
        or host.endswith(".example")
    ):
        raise ValueError(
            "Local or internal hostnames are not allowed."
        )


def is_video_url(url):
    path = urlparse(url).path.lower()
    return path.endswith(VIDEO_EXTENSIONS)


def is_api_url(url):
    parsed = urlparse(url)

    return bool(
        API_PATH_PATTERN.search(parsed.path)
        or "graphql" in parsed.path.lower()
    )


def save_report(report):
    with open(
        "report.json",
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            report,
            file,
            indent=2,
            ensure_ascii=False,
        )


# ==========================================
# MAIN BROWSER AUDIT
# ==========================================

async def analyze():
    validate_url(TARGET_URL)

    requests_seen = []
    responses_seen = []
    failures_seen = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
        )

        context = await browser.new_context(
            viewport={
                "width": 1365,
                "height": 900,
            },
            ignore_https_errors=False,
        )

        page = await context.new_page()

        page.set_default_timeout(20_000)

        # --------------------------------------
        # Capture browser network requests
        # --------------------------------------

        def on_request(request):
            if len(requests_seen) >= MAX_NETWORK_RECORDS:
                return

            requests_seen.append({
                "url": redact_url(request.url),
                "method": request.method,
                "resource_type": request.resource_type,
            })

        def on_response(response):
            if len(responses_seen) >= MAX_NETWORK_RECORDS:
                return

            try:
                headers = response.headers

                # Only record selected, useful response headers.
                safe_headers = {}

                for key in (
                    "content-type",
                    "content-length",
                    "cache-control",
                    "accept-ranges",
                    "content-range",
                    "access-control-allow-origin",
                ):
                    if key in headers:
                        safe_headers[key] = headers[key]

                responses_seen.append({
                    "url": redact_url(response.url),
                    "status": response.status,
                    "status_text": response.status_text,
                    "headers": safe_headers,
                    "request_type": response.request.resource_type,
                })

            except Exception:
                pass

        def on_request_failed(request):
            if len(failures_seen) >= 200:
                return

            failures_seen.append({
                "url": redact_url(request.url),
                "resource_type": request.resource_type,
                "failure": request.failure,
            })

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfailed", on_request_failed)

        report = {
            "target_url": redact_url(TARGET_URL),
            "started_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "browser": "Playwright Chromium",
        }

        try:
            # ----------------------------------
            # Navigate and execute JavaScript
            # ----------------------------------

            navigation_response = await page.goto(
                TARGET_URL,
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT,
            )

            # Allow some delayed JS/network activity.
            await page.wait_for_timeout(
                WAIT_AFTER_LOAD_MS
            )

            # ----------------------------------
            # Extract rendered DOM information
            # ----------------------------------

            page_data = await page.evaluate("""
                () => {
                    const meta = {};
                    for (const item of document.querySelectorAll('meta')) {
                        const key =
                            item.getAttribute('name') ||
                            item.getAttribute('property');

                        if (key) {
                            meta[key] = item.content || '';
                        }
                    }

                    const canonical =
                        document.querySelector('link[rel="canonical"]');

                    const headings = {};
                    for (let i = 1; i <= 6; i++) {
                        headings['h' + i] = [
                            ...document.querySelectorAll('h' + i)
                        ].map(el => el.innerText.trim());
                    }

                    const media = [
                        ...document.querySelectorAll(
                            'video, audio, source, track'
                        )
                    ].map(el => ({
                        tag: el.tagName.toLowerCase(),
                        src: el.currentSrc ||
                             el.src ||
                             el.getAttribute('src') ||
                             el.getAttribute('data-src') ||
                             null,
                        type: el.getAttribute('type'),
                    })).filter(el => el.src);

                    const resources = performance
                        .getEntriesByType('resource')
                        .map(entry => ({
                            name: entry.name,
                            initiator_type: entry.initiatorType,
                            duration_ms: Math.round(entry.duration),
                            transfer_size: entry.transferSize || 0,
                        }));

                    const scripts = [
                        ...document.querySelectorAll('script[src]')
                    ].map(el => el.src);

                    const stylesheets = [
                        ...document.querySelectorAll('link[rel="stylesheet"]')
                    ].map(el => el.href);

                    const images = [
                        ...document.querySelectorAll('img')
                    ].map(el => el.currentSrc || el.src)
                     .filter(Boolean);

                    const iframes = [
                        ...document.querySelectorAll('iframe')
                    ].map(el => el.src).filter(Boolean);

                    return {
                        title: document.title,
                        url: location.href,
                        html_lang: document.documentElement.lang || null,
                        meta,
                        canonical_url: canonical
                            ? canonical.href
                            : null,
                        headings,
                        scripts,
                        stylesheets,
                        images,
                        iframes,
                        media,
                        performance_resources: resources,
                    };
                }
            """)

            report["navigation"] = {
                "final_url": redact_url(page.url),
                "status": (
                    navigation_response.status
                    if navigation_response
                    else None
                ),
            }

            # Redact query parameters in extracted URLs too.
            for key in (
                "scripts",
                "stylesheets",
                "images",
                "iframes",
            ):
                page_data[key] = [
                    redact_url(url)
                    for url in page_data[key]
                ]

            page_data["canonical_url"] = (
                redact_url(page_data["canonical_url"])
                if page_data["canonical_url"]
                else None
            )

            for media in page_data["media"]:
                media["src"] = redact_url(media["src"])

            for resource in page_data["performance_resources"]:
                resource["name"] = redact_url(
                    resource["name"]
                )

            report["page"] = page_data

        except Exception as error:
            report["navigation_error"] = str(error)

        finally:
            report["network"] = {
                "requests": requests_seen,
                "responses": responses_seen,
                "failed_requests": failures_seen,
            }

            # Classify URLs from actual browser activity.
            all_urls = {
                item["url"]
                for item in requests_seen
            }

            video_urls = sorted(
                url for url in all_urls
                if is_video_url(url)
            )

            api_candidates = sorted(
                item["url"]
                for item in requests_seen
                if (
                    item["resource_type"] in ("xhr", "fetch")
                    or is_api_url(item["url"])
                )
            )

            report["discovery"] = {
                "api_candidates": sorted(set(api_candidates)),
                "video_url_candidates": video_urls,
                "stream_manifest_candidates": sorted(
                    url for url in video_urls
                    if urlparse(url).path.lower().endswith(
                        (".m3u8", ".mpd")
                    )
                ),
                "network_request_count": len(requests_seen),
                "network_response_count": len(responses_seen),
                "failed_request_count": len(failures_seen),
            }

            report["limitations"] = [
                "Captures only requests observed during this browser session.",
                "Delayed requests may require longer waiting or page interaction.",
                "API candidates are not proof that an endpoint is documented or public.",
                "Media URLs are observed references, not downloaded or decrypted streams.",
                "Sensitive query parameters are redacted in reported URLs.",
                "Only selected response headers are recorded; cookies and authorization headers are excluded.",
                "The target URL is hardcoded, but redirect and subresource destinations still need network-level SSRF protection in untrusted environments.",
            ]

            report["completed_at"] = datetime.now(
                timezone.utc
            ).isoformat()

            await context.close()
            await browser.close()

    save_report(report)

    print("Website audit completed.")
    print("Report saved: report.json")
    print(json.dumps(
        report.get("discovery", {}),
        indent=2,
        ensure_ascii=False,
    ))

    if report.get("navigation_error"):
        sys.exit(1)

    status = report.get("navigation", {}).get("status")

    if status is not None and status >= 400:
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(analyze())

    except KeyboardInterrupt:
        print("Audit interrupted.", file=sys.stderr)
        sys.exit(130)

    except Exception as error:
        print(
            f"Audit failed: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
