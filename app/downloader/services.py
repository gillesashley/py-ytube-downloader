RESOLUTIONS = (1080, 720)

# ponytail: mirrors main.py pick_video_format/pick_audio_format; keep in sync


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
    """Best audio-only format, highest bitrate."""
    audio = [
        f for f in formats if f.get("vcodec") == "none" and f.get("acodec") != "none"
    ]
    return max(audio, key=lambda f: f.get("abr") or 0) if audio else None
