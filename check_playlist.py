
import os
import subprocess
import urllib.request
from urllib.parse import urlparse

SOURCE_URL = os.environ.get("PLAYLIST_URL", "").strip()
OUTPUT_FILE = "Ayna.m3u"
TIMEOUT = 12


def download_playlist(url):
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError("Playlist URL must use HTTP or HTTPS")

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read(20 * 1024 * 1024 + 1)

    if len(content) > 20 * 1024 * 1024:
        raise ValueError("Playlist exceeds 20 MB limit")

    text = content.decode("utf-8-sig", errors="replace")

    if not text.lstrip().startswith("#EXTM3U"):
        raise ValueError("Invalid M3U playlist")

    return text


def parse_playlist(text):
    lines = text.splitlines()
    header = lines[0].strip()
    entries = []
    pending = []

    for line in lines[1:]:
        line = line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):
            pending = [line]
        elif pending:
            pending.append(line)

            if not line.startswith("#"):
                entries.append((pending[:], line))
                pending = []

    return header, entries


def check_stream(url):
    if urlparse(url).scheme not in ("http", "https"):
        return False

    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-xerror",
        "-i", url,
        "-t", "3",
        "-f", "null",
        "-"
    ]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT,
            check=False,
        )

        return result.returncode == 0

    except (subprocess.TimeoutExpired, OSError):
        return False


def main():
    if not SOURCE_URL:
        raise SystemExit("Missing playlist URL")

    print("Downloading source playlist...")
    text = download_playlist(SOURCE_URL)
    header, entries = parse_playlist(text)

    if not entries:
        raise SystemExit("No playlist entries found")

    active = []
    seen = set()

    print(f"Checking {len(entries)} entries with FFmpeg...")

    for index, (metadata, url) in enumerate(entries, 1):
        if url in seen:
            continue

        seen.add(url)

        if check_stream(url):
            active.append((metadata, url))
            print(f"[ACTIVE] {index}: {url}")
        else:
            print(f"[DEAD/UNVERIFIED] {index}: {url}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        file.write(header + "\n")

        for metadata, url in active:
            file.write("\n".join(metadata) + "\n")
            file.write(url + "\n")

    print(f"Total: {len(entries)}")
    print(f"Passed FFmpeg check: {len(active)}")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
