#!/bin/bash
cd "$(dirname "$0")" || exit 1
xattr -dr com.apple.quarantine . 2>/dev/null
if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
"$PY" launch.py "$@"
