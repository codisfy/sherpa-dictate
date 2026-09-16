#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python="$project_dir/.venv/bin/python"
export PYINSTALLER_CONFIG_DIR="$project_dir/build/pyinstaller-config"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

if ! "$python" -c "import PyInstaller" 2>/dev/null; then
    echo "PyInstaller is missing. Install release dependencies with:"
    echo "  $project_dir/.venv/bin/pip install -r $project_dir/requirements-dev.txt"
    exit 1
fi

cd "$project_dir"
exec "$python" -m PyInstaller --clean --noconfirm sherpa.spec
