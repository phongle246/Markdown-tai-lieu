#!/usr/bin/env bash
# Build the Python engine into a single-file sidecar for the Tauri bundle.
#   scripts/build_engine.sh   → src-tauri/binaries/textbook2md-engine-<target-triple>
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install -e . pyinstaller
TRIPLE="$(rustc -vV | sed -n 's/host: //p')"
pyinstaller --onefile --name textbook2md-engine --paths engine \
  --collect-submodules textbook2md --collect-all pymupdf engine/textbook2md/__main__.py
mkdir -p src-tauri/binaries
cp "dist/textbook2md-engine" "src-tauri/binaries/textbook2md-engine-${TRIPLE}"
echo "sidecar → src-tauri/binaries/textbook2md-engine-${TRIPLE}"
