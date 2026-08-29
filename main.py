import sys
from pathlib import Path

import yt_dlp
from yt_dlp.utils import DownloadError

RESOLUTIONS = (1080, 720)
FFMPEG_DIR = Path(__file__).parent / "ffmpeg" / "bin"


def pick_video_format(formats):
    """Best video format, preferring 1080p then 720p."""
    for height in RESOLUTIONS:
        candidates = [
            f
            for f in formats
            if f.get("height") == height and f.get("vcodec") != "none"
        ]
        if candidates:
            return max(candidates, key=lambda f: f.get("fps") or 0)
    return None


def pick_audio_format(formats):
    audio = [
        f for f in formats if f.get("vcodec") == "none" and f.get("acodec") != "none"
    ]
    return max(audio, key=lambda f: f.get("abr") or 0) if audio else None


def download(url):
    try:
        with yt_dlp.YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "ffmpeg_location": str(FFMPEG_DIR),
                "outtmpl": "%(title)s.%(ext)s",
            }
        ) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = info.get("formats", [])
        video = pick_video_format(formats)
        if not video:
            print(
                f"Error: '{info.get('title', url)}' has no 720p "
                "or 1080p format. Skipping."
            )
            return

        audio = pick_audio_format(formats)
        if audio and video.get("acodec") == "none":
            format_sel = f"{video['format_id']}+{audio['format_id']}"
        else:
            format_sel = f"{video['format_id']}/best"

        print(f"Downloading: {info.get('title', url)} ({video['height']}p)")
        with yt_dlp.YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "ffmpeg_location": str(FFMPEG_DIR),
                "outtmpl": "%(title)s.%(ext)s",
                "format": format_sel,
            }
        ) as ydl:
            ydl.download([url])
    except DownloadError as e:
        print(f"Error downloading '{url}': {e}. Skipping.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: python main.py <YouTube URL> [more URLs...]")
        sys.exit(1)
    # Rejoin URLs the shell split on '&' (common on Windows cmd)
    if len(args) > 1 and not all(a.startswith("http") for a in args):
        args = ["&".join(args)]
    for url in args:
        download(url)
