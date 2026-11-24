#!/bin/sh
# Build "PV Writer.app", a small macOS launcher for running PV Writer from source.
#
# Started with `python main.py`, the app is shown as "python" in the menu bar, the Dock
# and Cmd+Tab, because macOS names a process after its application bundle. This script
# wraps the project in a real bundle so macOS shows "PV Writer" with its icon:
#
#   Contents/MacOS/PV Writer      copy of the Python interpreter (the process macOS names)
#   Contents/MacOS/pvwriter       entry point: runs main.py with that interpreter
#   Contents/pyvenv.cfg           makes the copy a virtual environment that reuses the
#   Contents/lib/…/site-packages  project's .venv packages (symbolic link)
#
# The bundle runs the code of this checkout: no rebuild is needed after a code change,
# only after recreating .venv or changing the Python version.
#
# Usage: scripts/make_macos_app.sh [destination folder]   (default: ~/Applications)

set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEST=${1:-"$HOME/Applications"}
APP="$DEST/PV Writer.app"
VENV_PY="$ROOT/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo "Environnement .venv introuvable : créez-le d'abord (voir README)." >&2
    exit 1
fi

REAL_PY=$("$VENV_PY" -c 'import os, sys; print(os.path.realpath(sys._base_executable))')
VERSION=$("$VENV_PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
SITE=$("$VENV_PY" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
FRAMEWORK=$("$VENV_PY" -c 'import sysconfig; print(sysconfig.get_config_var("PYTHONFRAMEWORK") or "")')
if [ -n "$FRAMEWORK" ]; then
    # A framework build (python.org, Homebrew) relaunches itself inside its own Python.app,
    # so the copy would still be named "Python".
    echo "Attention : ce Python est une build « framework » ; macOS risque d'afficher « Python »." >&2
    echo "Une build autonome (par exemple installée avec uv) donne le bon nom." >&2
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/lib/python$VERSION"

cp "$REAL_PY" "$APP/Contents/MacOS/PV Writer"
ln -s "$SITE" "$APP/Contents/lib/python$VERSION/site-packages"
cat > "$APP/Contents/pyvenv.cfg" <<EOF
home = $(dirname "$REAL_PY")
include-system-site-packages = false
version = $VERSION
EOF

cat > "$APP/Contents/MacOS/pvwriter" <<EOF
#!/bin/sh
exec "\$(dirname "\$0")/PV Writer" "$ROOT/main.py" "\$@"
EOF
chmod +x "$APP/Contents/MacOS/pvwriter"

cp "$ROOT/assets/icons/pv_writer.icns" "$APP/Contents/Resources/pv_writer.icns"
APP_VERSION=$(cd "$ROOT" && "$VENV_PY" -c 'from src import __version__; print(__version__)')
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>PV Writer</string>
    <key>CFBundleDisplayName</key><string>PV Writer</string>
    <key>CFBundleIdentifier</key><string>com.pvwriter.app</string>
    <key>CFBundleExecutable</key><string>pvwriter</string>
    <key>CFBundleIconFile</key><string>pv_writer</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$APP_VERSION</string>
    <key>CFBundleVersion</key><string>$APP_VERSION</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>PV Writer enregistre la réunion pour en rédiger le procès-verbal.</string>
</dict>
</plist>
EOF

# Ad-hoc signature, required on Apple Silicon once the binary has been copied
codesign --force --sign - "$APP/Contents/MacOS/PV Writer" >/dev/null 2>&1 || true
touch "$APP"

echo "Créé : $APP"
echo "Lancez PV Writer depuis ce dossier, Spotlight ou le Launchpad (glissez-le dans le Dock pour l'y garder)."
