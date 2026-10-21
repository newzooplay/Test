
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

TARGET_URL = os.getenv(
    "TARGET_URL",
    "https://v4jxp5.movielinkbd.at/movie/mKs_ojqfqk3njpyykzcnckj3ykxtli"
)

OUTPUT_FILE = Path("reports/report.json")
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)


def analyze_website(url):
    result = {
        "target_url": url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "http": {},
        "page": {},
        "javascript_errors": [],
        "diagnostics": [],
    }

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        result["diagnostics"].append("Invalid HTTP/HTTPS URL")
        return result

    # HTTP request and response headers
    try:
        response = requests.get(
            url,
            timeout=20,
            allow_redirects=True,
            headers={"User-Agent": "WebsiteDiagnostics/1.0"},
        )

        result["http"] = {
            "status_code": response.status_code,
            "final_url": response.url,
            "content_type": response.headers.get("Content-Type"),
            "content_length": response.headers.get("Content-Length"),
            "server": response.headers.get("Server"),
            "headers": dict(response.headers),
            "redirect_count": len(response.history),
        }

    except requests.RequestException as exc:
        result["diagnostics"].append(
            f"HTTP request failed: {type(exc).__name__}: {exc}"
        )

    # Browser page diagnostics
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.on(
                "pageerror",
                lambda error: result["javascript_errors"].append(str(error)),
            )

            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except PlaywrightTimeoutError:
                result["diagnostics"].append(
                    "Page navigation timed out; partial diagnostics may be available."
                )

            # Allow basic page scripts to initialize.
            try:
                page.wait_for_timeout(1500)

                result["page"] = page.evaluate("""() => ({
                    title: document.title || null,
                    description:
                        document.querySelector('meta[name="description"]')
                        ?.getAttribute('content') || null,
                    canonical:
                        document.querySelector('link[rel="canonical"]')
                        ?.href || null,
                    language: document.documentElement.lang || null,
                    document_ready_state: document.readyState,
                    page_hostname: location.hostname,
                    headings: Array.from(
                        document.querySelectorAll('h1, h2')
                    ).slice(0, 30).map(el => ({
                        tag: el.tagName.toLowerCase(),
                        text: (el.innerText || '').trim().slice(0, 200)
                    })),
                    image_count: document.images.length,
                    link_count: document.links.length
                })""")

            except Exception as exc:
                result["diagnostics"].append(
                    f"Page inspection failed: {type(exc).__name__}: {exc}"
                )

            browser.close()

    except Exception as exc:
        result["diagnostics"].append(
            f"Browser setup failed: {type(exc).__name__}: {exc}"
        )

    return result


def main():
    report = analyze_website(TARGET_URL)

    OUTPUT_FILE.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Report saved to {OUTPUT_FILE}")
    print(json.dumps(report, indent=2, ensure_ascii=False))

    # Non-zero status indicates a failed HTTP request or server error.
    status = report.get("http", {}).get("status_code")
    if status is None or status >= 500:
        sys.exit(1)


if __name__ == "__main__":
    main()
