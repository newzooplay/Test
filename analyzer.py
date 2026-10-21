
import json
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ==========================================
# CONFIGURATION: HARDCODE YOUR TARGET URL
# ==========================================

TARGET_URL = "https://v4jxp5.movielinkbd.at/movie/mKs_ojqfqk3njpyykzcnckj3ykxtli"

TIMEOUT = 20
MAX_HTML_BYTES = 5_000_000
USER_AGENT = "WebsitePageAnalyzer/1.0"

# ==========================================
# PATTERNS
# ==========================================

API_PATTERN = re.compile(
    r"/(?:api|graphql|rest|v[0-9]+)(?:/|$)",
    re.IGNORECASE,
)

VIDEO_EXTENSIONS = (
    ".mp4",
    ".webm",
    ".ogg",
    ".m3u8",
    ".mpd",
    ".m4v",
    ".mov",
    ".m4s",
    ".ts",
)

API_KEYWORDS = (
    "api",
    "graphql",
    "endpoint",
    "fetch(",
    "axios",
    "xmlhttprequest",
)


# ==========================================
# URL VALIDATION
# ==========================================

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

    blocked_hosts = (
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
    )

    if (
        host in blocked_hosts
        or host.endswith(".localhost")
        or host.endswith(".local")
        or host.endswith(".internal")
    ):
        raise ValueError(
            "Local or internal hosts are not allowed."
        )

    return parsed


# ==========================================
# HELPER FUNCTIONS
# ==========================================

def absolute_url(base_url, value):
    if not value:
        return None

    value = value.strip()

    if not value or value.startswith(
        ("#", "javascript:", "data:", "blob:", "mailto:", "tel:")
    ):
        return None

    result = urljoin(base_url, value)
    parsed = urlparse(result)

    if parsed.scheme not in ("http", "https"):
        return None

    return result


def unique_sorted(values):
    return sorted(set(v for v in values if v))


def get_meta_content(soup, name=None, property_name=None):
    attrs = {}

    if name:
        attrs["name"] = name

    if property_name:
        attrs["property"] = property_name

    tag = soup.find("meta", attrs=attrs)

    if tag:
        return tag.get("content")

    return None


def is_video_url(url):
    if not url:
        return False

    path = urlparse(url).path.lower()

    return path.endswith(VIDEO_EXTENSIONS)


def is_api_candidate(url):
    parsed = urlparse(url)

    if API_PATTERN.search(parsed.path):
        return True

    return any(
        word in parsed.query.lower()
        for word in ("graphql", "api=")
    )


# ==========================================
# FETCH WEBSITE
# ==========================================

def fetch_page(url):
    validate_url(url)

    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
    })

    response = None

    try:
        # Follow redirects, subject to the target's
        # normal public website configuration.
        response = session.get(
            url,
            timeout=TIMEOUT,
            allow_redirects=True,
            stream=True,
        )

        redirect_chain = []

        for item in response.history:
            redirect_chain.append({
                "status_code": item.status_code,
                "url": item.url,
                "location": item.headers.get("Location"),
            })

        final_url = response.url
        final_parsed = urlparse(final_url)

        if (
            final_parsed.scheme != "https"
            or not final_parsed.hostname
            or final_parsed.username
            or final_parsed.password
        ):
            raise ValueError(
                "Redirect ended at an invalid or non-HTTPS URL."
            )

        content_type = response.headers.get(
            "Content-Type", ""
        )

        result = {
            "requested_url": url,
            "final_url": final_url,
            "status_code": response.status_code,
            "reason": response.reason,
            "http_headers": dict(response.headers),
            "redirect_chain": redirect_chain,
            "content_type": content_type,
            "analyzed_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

        if "html" not in content_type.lower():
            result["note"] = (
                "The response Content-Type does not indicate HTML."
            )
            return result

        chunks = []
        total_bytes = 0

        for chunk in response.iter_content(
            chunk_size=65536
        ):
            if not chunk:
                continue

            total_bytes += len(chunk)

            if total_bytes > MAX_HTML_BYTES:
                raise ValueError(
                    "HTML response exceeds the 5 MB limit."
                )

            chunks.append(chunk)

        raw_html = b"".join(chunks)

        encoding = response.encoding or "utf-8"

        html = raw_html.decode(
            encoding,
            errors="replace",
        )

        result["html_bytes_inspected"] = len(raw_html)

        return result, html

    finally:
        if response is not None:
            response.close()

        session.close()


# ==========================================
# ANALYZE HTML
# ==========================================

def analyze_html(base_url, html):
    soup = BeautifulSoup(html, "html.parser")

    resources = set()
    scripts = set()
    stylesheets = set()
    images = set()
    iframe_urls = set()
    api_urls = set()
    video_urls = set()
    stream_urls = set()

    # --------------------------
    # Scripts
    # --------------------------

    for tag in soup.find_all("script"):
        src = absolute_url(
            base_url,
            tag.get("src"),
        )

        if src:
            scripts.add(src)
            resources.add(src)

    # --------------------------
    # Stylesheets and links
    # --------------------------

    for tag in soup.find_all("link"):
        href = absolute_url(
            base_url,
            tag.get("href"),
        )

        if not href:
            continue

        resources.add(href)

        rel = tag.get("rel", [])
        if isinstance(rel, str):
            rel = rel.split()

        rel = [item.lower() for item in rel]

        if "stylesheet" in rel:
            stylesheets.add(href)

    # --------------------------
    # Images
    # --------------------------

    for tag in soup.find_all("img"):
        for attr in ("src", "data-src", "data-original"):
            image_url = absolute_url(
                base_url,
                tag.get(attr),
            )

            if image_url:
                images.add(image_url)
                resources.add(image_url)

        srcset = tag.get("srcset")

        if srcset:
            for item in srcset.split(","):
                candidate = item.strip().split()

                if candidate:
                    image_url = absolute_url(
                        base_url,
                        candidate[0],
                    )

                    if image_url:
                        images.add(image_url)
                        resources.add(image_url)

    # --------------------------
    # Iframes
    # --------------------------

    for tag in soup.find_all("iframe"):
        iframe_url = absolute_url(
            base_url,
            tag.get("src"),
        )

        if iframe_url:
            iframe_urls.add(iframe_url)
            resources.add(iframe_url)

    # --------------------------
    # Video, audio and source tags
    # --------------------------

    for tag in soup.find_all(
        ["video", "audio", "source", "track"]
    ):
        for attr in (
            "src",
            "data-src",
            "data-video",
            "data-hls",
            "data-dash",
        ):
            media_url = absolute_url(
                base_url,
                tag.get(attr),
            )

            if not media_url:
                continue

            resources.add(media_url)

            if is_video_url(media_url):
                video_urls.add(media_url)

                path = urlparse(
                    media_url
                ).path.lower()

                if path.endswith((".m3u8", ".mpd", ".m4s", ".ts")):
                    stream_urls.add(media_url)

    # --------------------------
    # Metadata: Open Graph video
    # --------------------------

    for property_name in (
        "og:video",
        "og:video:url",
        "og:video:secure_url",
    ):
        value = get_meta_content(
            soup,
            property_name=property_name,
        )

        media_url = absolute_url(base_url, value)

        if media_url:
            resources.add(media_url)
            video_urls.add(media_url)

            if is_video_url(media_url):
                stream_urls.add(media_url)

    # --------------------------
    # All href/src attributes
    # --------------------------

    for tag in soup.find_all(True):
        for attr in (
            "href",
            "src",
            "action",
            "data-url",
            "data-api",
            "data-endpoint",
            "data-stream",
        ):
            value = tag.get(attr)

            candidate = absolute_url(
                base_url,
                value,
            )

            if not candidate:
                continue

            resources.add(candidate)

            if is_api_candidate(candidate):
                api_urls.add(candidate)

            if is_video_url(candidate):
                video_urls.add(candidate)

                if urlparse(
                    candidate
                ).path.lower().endswith(
                    (".m3u8", ".mpd", ".m4s", ".ts")
                ):
                    stream_urls.add(candidate)

    # --------------------------
    # Inline HTML URL candidates
    # --------------------------

    # This searches HTML text for URLs explicitly
    # present in the document. It does not execute JS.

    inline_url_pattern = re.compile(
        r"""["']((?:https?:)?//[^"' \t\r\n<>]+|"""
        r"""/(?:api|graphql|rest|streams?)/[^"' \t\r\n<>]+)"""
        r"""["']""",
        re.IGNORECASE,
    )

    for match in inline_url_pattern.findall(html):
        candidate = absolute_url(base_url, match)

        if not candidate:
            continue

        resources.add(candidate)

        if is_api_candidate(candidate):
            api_urls.add(candidate)

        if is_video_url(candidate):
            video_urls.add(candidate)

            if urlparse(
                candidate
            ).path.lower().endswith(
                (".m3u8", ".mpd", ".m4s", ".ts")
            ):
                stream_urls.add(candidate)

    # --------------------------
    # Page metadata
    # --------------------------

    title = (
        soup.title.get_text(" ", strip=True)
        if soup.title
        else None
    )

    canonical_tag = soup.find(
        "link",
        rel=lambda value: value and (
            "canonical" in (
                value if isinstance(value, list)
                else value.split()
            )
        ),
    )

    canonical_url = (
        absolute_url(
            base_url,
            canonical_tag.get("href"),
        )
        if canonical_tag
        else None
    )

    headings = {}

    for level in range(1, 7):
        headings[f"h{level}"] = [
            tag.get_text(" ", strip=True)
            for tag in soup.find_all(f"h{level}")
        ]

    meta = {
        "description": get_meta_content(
            soup,
            name="description",
        ),
        "keywords": get_meta_content(
            soup,
            name="keywords",
        ),
        "robots": get_meta_content(
            soup,
            name="robots",
        ),
        "author": get_meta_content(
            soup,
            name="author",
        ),
        "og_title": get_meta_content(
            soup,
            property_name="og:title",
        ),
        "og_description": get_meta_content(
            soup,
            property_name="og:description",
        ),
        "og_image": get_meta_content(
            soup,
            property_name="og:image",
        ),
        "og_url": get_meta_content(
            soup,
            property_name="og:url",
        ),
    }

    for key, value in meta.items():
        if key in ("og_image", "og_url") and value:
            meta[key] = absolute_url(base_url, value)

    # --------------------------
    # Final report
    # --------------------------

    return {
        "page_title": title,
        "canonical_url": canonical_url,
        "meta_tags": meta,
        "headings": headings,
        "counts": {
            "resources": len(resources),
            "scripts": len(scripts),
            "stylesheets": len(stylesheets),
            "images": len(images),
            "iframes": len(iframe_urls),
            "api_candidates": len(api_urls),
            "video_urls": len(video_urls),
            "stream_candidates": len(stream_urls),
        },
        "resources": unique_sorted(resources),
        "scripts": unique_sorted(scripts),
        "stylesheets": unique_sorted(stylesheets),
        "images": unique_sorted(images),
        "iframes": unique_sorted(iframe_urls),
        "api_url_candidates": unique_sorted(api_urls),
        "video_urls": unique_sorted(video_urls),
        "stream_url_candidates": unique_sorted(stream_urls),
        "limitations": [
            "Only resources declared in the returned HTML are inspected.",
            "JavaScript is not executed and browser network requests are not captured.",
            "API candidates are heuristic matches, not verified API endpoints.",
            "Protected streams, DRM, and authenticated resources are not extracted.",
        ],
    }


# ==========================================
# MAIN
# ==========================================

def main():
    print("=" * 60)
    print("WEBSITE PAGE ANALYZER")
    print("=" * 60)
    print(f"Target: {TARGET_URL}")

    validate_url(TARGET_URL)

    fetched = fetch_page(TARGET_URL)

    # A non-HTML response returns a single dictionary.
    if isinstance(fetched, dict):
        report = fetched
    else:
        response_info, html = fetched

        report = response_info
        report["analysis"] = analyze_html(
            response_info["final_url"],
            html,
        )

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

    print("\nAnalysis complete.")
    print("Report saved to report.json")
    print(json.dumps(
        report,
        indent=2,
        ensure_ascii=False,
    ))

    status = report.get("status_code", 0)

    if status >= 400:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print(
            "\nAnalysis interrupted.",
            file=sys.stderr,
        )
        sys.exit(130)

    except Exception as error:
        print(
            f"\nAnalysis failed: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
