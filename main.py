import sys
import shutil
import yt_dlp


def get_video_info(url):
    """Fetch video information and available formats."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'youtube_include_dash_manifest': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return info
        except yt_dlp.DownloadError as e:
            print(f"Error fetching video info: {str(e)}")
            sys.exit(1)


def get_best_audio_format(formats):
    """Get the best audio format."""
    audio_formats = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') != 'none']
    if not audio_formats:
        return None
    return max(audio_formats, key=lambda x: x.get('abr', 0) or 0)


def get_quality_choice(formats, only_progressive=False):
    """Let user choose video quality.

    If `only_progressive` is True, show only formats that include audio.
    """
    print("\nAvailable video qualities:")
    qualities = {}
    i = 1
    for fmt in formats:
        if fmt.get('vcodec') != 'none':  # Only video formats
            if only_progressive and fmt.get('acodec') == 'none':
                continue
            resolution = fmt.get('resolution', 'Unknown')
            format_note = fmt.get('format_note', '')
            fps = fmt.get('fps', '')
            quality_str = f"{resolution} {format_note} {fps}fps".strip()
            qualities[i] = fmt
            print(f"{i}. {quality_str}")
            i += 1

    if not qualities:
        print("No matching qualities available.")
        return None

    while True:
        try:
            choice = int(input("\nEnter the number of your preferred quality: "))
            if choice in qualities:
                return qualities[choice]
            else:
                print(f"Invalid choice. Please enter a number between 1 and {len(qualities)}.")
        except ValueError:
            print("Please enter a valid number.")


def ffmpeg_available():
    """Return True if ffmpeg is available on PATH."""
    return shutil.which('ffmpeg') is not None


def download_video(url):
    """Main function to handle video download."""
    info = get_video_info(url)
    if not info:
        return

    formats = info['formats']
    video_formats = [f for f in formats if f.get('vcodec') != 'none']
    video_formats.sort(key=lambda x: (x.get('height', 0), x.get('fps', 0)), reverse=True)

    chosen_format = get_quality_choice(video_formats)
    best_audio = get_best_audio_format(formats)

    if not chosen_format:
        print("No format chosen, aborting.")
        return

    # If merging (video-only + audio) is needed but ffmpeg is missing,
    # offer fallbacks to the user.
    needs_merge = best_audio and chosen_format.get('acodec') == 'none'
    if needs_merge and not ffmpeg_available():
        print("\nffmpeg is not installed or not on PATH.")
        print("Merging video and audio requires ffmpeg. Options:")
        print("  1) Download video-only (no audio)")
        print("  2) Choose a different quality that includes audio")
        print("  3) Abort and install ffmpeg (recommended)")
        while True:
            choice = input("Enter 1, 2 or 3: ").strip()
            if choice == '1':
                format_str = f"{chosen_format['format_id']}"
                break
            elif choice == '2':
                # Ask user to pick from progressive formats only
                progressive = get_quality_choice(video_formats, only_progressive=True)
                if progressive:
                    chosen_format = progressive
                    best_audio = get_best_audio_format(formats)
                    # progressive includes audio
                    format_str = f"{chosen_format['format_id']}/best"
                    break
                else:
                    print("No progressive formats available. Please choose another option.")
            elif choice == '3':
                print("Please install ffmpeg and re-run the script. See https://ffmpeg.org/download.html")
                return
            else:
                print("Invalid choice.")
    else:
        if best_audio and chosen_format.get('acodec') == 'none':
            format_str = f"{chosen_format['format_id']}+{best_audio['format_id']}/bestaudio"
        else:
            format_str = f"{chosen_format['format_id']}/best"

    ydl_opts = {
        'format': format_str,
        'outtmpl': '%(title)s.%(ext)s',
    }

    print(f"\nDownloading: {info['title']} ({chosen_format.get('resolution', 'Unknown')})")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            ydl.download([url])
            print(f"\nDownload complete: {info['title']}")
        except yt_dlp.DownloadError as e:
            print(f"Download failed: {str(e)}")
            sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python script.py <YouTube URL>")
        print("On Windows, quote the URL to avoid shell splitting, e.g.:")
        print("  python main.py " + '"https://www.youtube.com/watch?v=...&pp=..."')
        sys.exit(1)

    # Reconstruct the URL in case the shell split it on '&' (common on Windows cmd)
    if len(sys.argv) == 2:
        url = sys.argv[1]
    else:
        url = '&'.join(sys.argv[1:])
    download_video(url)
