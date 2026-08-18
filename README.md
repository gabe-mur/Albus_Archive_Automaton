# Albus' Archive Automaton

A simple macOS desktop app for downloading video, audio, or image media from URLs. It uses a Tkinter interface and can queue multiple downloads, optionally use browser cookies, and fall back to scanning a page for direct media links.

## Requirements

- Python 3.9+
- macOS (the app is designed for macOS and includes a .command launcher)
- ffmpeg available on your system or bundled via Python

## Install Python

If Python is not installed, install Python from the official installer at https://www.python.org/downloads/macos/.

## Install dependencies

Open Terminal and run this command from this folder:

```bash
brew install ffmpeg
```

Python dependencies are installed automatically into a local virtual environment (`.venv`) the first time you run the app launcher — see below.

## Run the app

Open the launcher file in this folder:

```text
Open_Albus_Archive_Automaton.command
```

The first launch creates a `.venv` folder in this directory and installs the packages from `requirements.txt` into it. This keeps the app's dependencies isolated from your system Python, so a `brew upgrade` won't break the app. If you upgrade Homebrew's Python to a new major version, delete the `.venv` folder and relaunch to rebuild it.

If the app fails to launch with a `_tkinter` error, install the Tk support package for your Python version, e.g.:

```bash
brew install python-tk@3.14
```

(replace `3.14` with your `python3 --version`'s major.minor).

If macOS shows a warning and blocks it, right-click the file, choose Open, and confirm. This is normal for unsigned scripts. If needed, you can also clear the quarantine flag once in Terminal:

```bash
xattr -dr com.apple.quarantine Open_Albus_Archive_Automaton.command
```

## How to use it

1. Paste a media URL into the Link field.
2. Optionally enter a custom file name without an extension.
3. Choose Video, Audio Only, or Images mode.
4. Choose a save folder.
5. Click Add to Queue.
6. Add any other URLs you want to download.
7. Click Start Queue.
8. When an item finishes, right-click it for file actions or double-click it to open the saved file.

## Notes

- The app uses yt-dlp for most downloads.
- YouTube downloads require a supported JavaScript runtime. The app automatically uses Deno, Node.js, or QuickJS when one is installed; Node.js is available from `brew install node` if needed.
- If yt-dlp fails, it can attempt to scan the page for direct media URLs.
- Browser cookies are only used for yt-dlp-based downloads and are not saved by the app.
