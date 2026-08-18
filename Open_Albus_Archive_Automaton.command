#!/bin/bash
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python3" ]; then
  python3 -m venv .venv
  ./.venv/bin/python3 -m pip install --upgrade pip -q
fi

requirements_fingerprint="$(shasum -a 256 requirements.txt | awk '{print $1}')"
installed_fingerprint="$(sed -n '1p' .venv/.requirements.sha256 2>/dev/null)"

if [ "$requirements_fingerprint" != "$installed_fingerprint" ]; then
  if ! ./.venv/bin/python3 -m pip install -r requirements.txt -q; then
    echo "Could not install the app's Python dependencies."
    echo "Check your internet connection, then open this launcher again."
    exit 1
  fi
  echo "$requirements_fingerprint" > .venv/.requirements.sha256
fi

./.venv/bin/python3 "Albus_Archive_Automaton.py"
