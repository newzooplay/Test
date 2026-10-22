
import os
import sys
import subprocess
import urllib.request
from urllib.parse import urlparse

MAX_PLAYLIST_SIZE = 20 * 1024 * 1024
FFMPEG_TIMEOUT = 15


def download_playlist(url):
    """Download and validate an M3U playlist."""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Playlist URL must be a valid HTTP/HTTPS URL.")

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    print("Downloading source playlist...")

    with urllib.request.urlopen(request, timeout=30) as response:
        print("HTTP status:", response.status)
        print("Content-Type:", response.headers.get("Content-Type", "unknown"))
        raw = response.read(MAX_PLAYLIST_SIZE + 1)

    if len(raw) > MAX_PLAYLIST_SIZE:
        raise ValueError("Playlist exceeds the 20 MB limit.")

    text = raw.decode("utf-8-sig", errors="replace").strip()

    # Ignore leading blank lines or whitespace.
    text = text.lstrip()

    if not text.startswith("#EXTM3U"):
        preview = "\n".join(text.splitlines()[:5])
        print("Downloaded content preview:")
        print(preview[:1000] or "(empty response)")
        raise ValueError(
            "Invalid M3U playlist: the response does not start with #EXTM3U. "
            "Check whether the URL points directly to the raw playlist."
        )

    print("Playlist downloaded successfully.")
    return text


def parse_playlist(text):
    """Split the playlist into its header and individual entries."""
    lines = text.splitlines()
    header = []
    entries = []
    current_entry = None
    found_entry = False

    for line in lines:
        line = line.strip()

        if not line:
            if current_entry is not None:
                current_entry.append(line)
            elif not found_entry:
                header.append(line)
            continue

        if line.startswith("#EXTINF"):
            found_entry = True
            current_entry = [line]
            continue

        if current_entry is None:
            if not found_entry:
                header.append(line)
            continue

        if line.startswith("#"):
            current_entry.append(line)
            continue

        # A non-comment line is treated as the stream URL.
        if line.startswith(("http://", "https://")):
            current_entry.append(line)
            entries.append(current_entry)
            current_entry = None
        else:
            print("Skipping unsupported stream URL:", line[:150])
            current_entry = None

    if not entries:
        raise ValueError(
            "No valid HTTP/HTTPS stream entries found in the playlist."
        )

    return header, entries


def check_stream(url):
    """Use FFmpeg to test whether a stream can be read."""
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-rw_timeout", "8000000",
        "-i", url,
        "-t", "2",
        "-f", "null",
        "-"
    ]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=FFMPEG_TIMEOUT
        )

        if result.returncode == 0:
            return True

        error = (result.stderr or "").strip().splitlines()
        if error:
            print("FFmpeg:", error[-1][:250])

        return False

    except subprocess.TimeoutExpired:
        print("FFmpeg timed out.")
        return False

    except FileNotFoundError:
        raise RuntimeError(
            "FFmpeg was not found. Install FFmpeg in the GitHub workflow."
        )


def main():
    source_url = os.environ.get("PLAYLIST_URL", "").strip()

    if not source_url:
        print("Error: PLAYLIST_URL input is empty.")
        sys.exit(1)

    text = download_playlist(source_url)
    header, entries = parse_playlist(text)

    active_entries = []
    total = len(entries)

    print(f"Found {total} stream entries.")
    print("Checking streams with FFmpeg...")

    for index, entry in enumerate(entries, start=1):
        stream_url = entry[-1]

        # The final entry line is the stream URL.
        if not stream_url.startswith(("http://", "https://")):
            print(f"[{index}/{total}] SKIP: invalid URL")
            continue

        print(f"[{index}/{total}] Checking {stream_url[:180]}")

        try:
            active = check_stream(stream_url)
        except RuntimeError as exc:
            print(exc)
            sys.exit(1)

        if active:
            print(f"[{index}/{total}] ACTIVE")
            active_entries.append(entry)
        else:
            print(f"[{index}/{total}] DEAD / UNVERIFIED")

    output_lines = header[:]

    if not output_lines or not any(
        line.strip().startswith("#EXTM3U") for line in output_lines
    ):
        output_lines.insert(0, "#EXTM3U")

    for entry in active_entries:
        output_lines.extend(entry)

    output_file = "active_playlist.m3u"

    with open(output_file, "w", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(output_lines).rstrip() + "\n")

    print()
    print("Playlist check completed.")
    print(f"Total entries: {total}")
    print(f"Active entries: {len(active_entries)}")
    print(f"Output file: {output_file}")

    if not active_entries:
        print(
            "Warning: No streams passed the FFmpeg check. "
            "The output playlist contains the header only."
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
