# Albus' Archive Automaton

A simple macOS desktop app for downloading video, audio, or image media from URLs. It uses a Tkinter interface and can queue multiple downloads, optionally use browser cookies, and fall back to scanning a page for direct media links.

## Requirements

- Python 3.9+
- macOS (the app is designed for macOS and includes a .command launcher)
- ffmpeg available on your system or bundled via Python

## Install Python

If Python is not installed, install Python from the official installer at https://www.python.org/downloads/macos/.

## Install dependencies

Open Terminal and run these commands from this folder:

```bash
brew install ffmpeg
python3 -m pip install -r requirements.txt
```

These commands install the Homebrew packages and Python dependencies needed to run the app on macOS.

## Run the app

Open the launcher file in this folder:

```text
Open_Albus_Archive_Automaton.command
```

If macOS shows a warning and blocks it, right-click the file, choose Open, and confirm. This is normal for unsigned scripts. If needed, you can also clear the quarantine flag once in Terminal:

```bash
xattr -dr com.apple.quarantine Open_Albus_Archive_Automaton.command
```

## How to use it

1. Choose a save folder.
2. Paste a URL into the queue.
3. Optionally enter a custom file name without an extension.
4. Choose Video, Audio Only, or Images mode.
5. Click Add to Queue, then Start Queue.

## Notes

- The app uses yt-dlp for most downloads.
- If yt-dlp fails, it can attempt to scan the page for direct media URLs.
- Browser cookies are only used for yt-dlp-based downloads and are not saved by the app.
