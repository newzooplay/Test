
import os
import re
import urllib.request
import urllib.error
from urllib.parse import urlparse

SOURCE_URL = os.environ.get("PLAYLIST_URL", "").strip()
OUTPUT_FILE = "active_playlist.m3u"
TIMEOUT = 12
MAX_READ = 65536


def fetch(url, limit=MAX_READ):
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only HTTP/HTTPS URLs are supported")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 M3U-Checker/1.0",
            "Range": f"bytes=0-{limit - 1}",
        },
    )

    with urllib.request.urlopen(
        request, timeout=TIMEOUT
    ) as response:
        status = response.status
        content_type = response.headers.get(
            "Content-Type", ""
        ).lower()
        data = response.read(limit)

    return status, content_type, data


def parse_playlist(text):
    lines = text.lstrip("\ufeff").splitlines()
    header = "#EXTM3U"
    entries = []
    pending = []

    for line in lines:
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("#EXTM3U"):
            header = stripped
            continue

        if stripped.startswith("#EXTINF"):
            if pending:
                pending = []
            pending.append(line)
            continue

        if pending:
            pending.append(line)

            if not stripped.startswith("#"):
                entries.append((pending[:], stripped))
                pending = []

    return header, entries


def check_stream(url):
    try:
        status, content_type, data = fetch(url)

        if status not in (200, 206):
            return False, f"HTTP {status}"

        if not data:
            return False, "Empty response"

        sample = data.lstrip().lower()

        # Avoid treating HTML error/login pages as video streams.
        if (
            "text/html" in content_type
            or sample.startswith(b"<!doctype html")
            or sample.startswith(b"<html")
        ):
            return False, "HTML/login/error page"

        # HLS playlists must contain the HLS signature.
        is_hls = (
            ".m3u8" in url.lower()
            or "mpegurl" in content_type
        )

        if is_hls and not data.lstrip().startswith(
            b"#EXTM3U"
        ):
            return False, "Invalid HLS response"

        return True, f"HTTP {status}"

    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, str(exc)

    except Exception as exc:
        return False, str(exc)


def main():
    if not SOURCE_URL:
        raise SystemExit("Missing PLAYLIST_URL input")

    print("Downloading source playlist...")

    status, content_type, raw = fetch(
        SOURCE_URL, limit=10 * 1024 * 1024
    )

    if status != 200:
        raise SystemExit(f"Source returned HTTP {status}")

    text = raw.decode("utf-8-sig", errors="replace")

    if "#EXTM3U" not in text:
        raise SystemExit("Source is not a valid M3U playlist")

    header, entries = parse_playlist(text)

    if not entries:
        raise SystemExit("No channel entries found")

    active_entries = []
    seen = set()

    print(f"Checking {len(entries)} entries...")

    for index, (block, url) in enumerate(entries, 1):
        if url in seen:
            continue
        seen.add(url)

        ok, reason = check_stream(url)

        # Only retain entries that pass the HTTP checks.
        if ok:
            active_entries.append(block)
            print(f"[ACTIVE] {index}: {url}")
        else:
            print(f"[SKIPPED] {index}: {reason}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as output:
        output.write(header + "\n")

        for block in active_entries:
            output.write("\n".join(block) + "\n")

    print(f"Total entries: {len(entries)}")
    print(f"Passed checks: {len(active_entries)}")
    print(f"Saved: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
