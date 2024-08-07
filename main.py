import sys
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


def get_quality_choice(formats):
    """Let user choose video quality."""
    print("\nAvailable video qualities:")
    qualities = {}
    i = 1
    for format in formats:
        if format.get('vcodec') != 'none':  # Only video formats
            resolution = format.get('resolution', 'Unknown')
            format_note = format.get('format_note', '')
            fps = format.get('fps', '')
            quality_str = f"{resolution} {format_note} {fps}fps".strip()
            qualities[i] = format
            print(f"{i}. {quality_str}")
            i += 1

    while True:
        try:
            choice = int(input("\nEnter the number of your preferred quality: "))
            if choice in qualities:
                return qualities[choice]
            else:
                print(f"Invalid choice. Please enter a number between 1 and {len(qualities)}.")
        except ValueError:
            print("Please enter a valid number.")


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

    if best_audio:
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
    if len(sys.argv) != 2:
        print("Usage: python script.py <YouTube URL>")
        sys.exit(1)

    url = sys.argv[1]
    download_video(url)