#!/bin/bash
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python3" ]; then
  python3 -m venv .venv
  ./.venv/bin/python3 -m pip install --upgrade pip -q
fi

requirements_fingerprint="$(shasum -a 256 requirements.txt | awk '{print $1}')"

echo "Checking for Albus dependency updates..."
if ./.venv/bin/python3 -m pip install --upgrade -r requirements.txt -q --disable-pip-version-check; then
  echo "$requirements_fingerprint" > .venv/.requirements.sha256
elif ! ./.venv/bin/python3 -c "import gallery_dl, imageio_ffmpeg, yt_dlp; from yt_dlp.version import __version__; assert tuple(map(int, __version__.split('.'))) >= (2026, 8, 19)" 2>/dev/null; then
  echo "Could not install the app's required Python dependencies."
  echo "Check your internet connection, then open this launcher again."
  exit 1
else
  echo "Could not check for updates. Starting with the installed dependencies."
fi

./.venv/bin/python3 "Albus_Archive_Automaton.py"
