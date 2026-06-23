# PyInstaller spec for the GUI app. Build from the repo root:
#   pyinstaller build/bsky-engagement-gui.spec
# Produces a single-file, windowed executable in dist/.
# NOTE: PyInstaller does not cross-compile — run this on each target OS
# (Windows builds the .exe, macOS builds the .app).

import sys

block_cipher = None

a = Analysis(
    ["../bsky_engagement/gui.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="bsky-engagement",
    debug=False,
    strip=False,
    upx=True,
    console=False,          # windowed GUI app
    disable_windowed_traceback=False,
    target_arch=None,
    icon=None,
)

# On macOS, also wrap into a .app bundle.
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="bsky-engagement.app",
        icon=None,
        bundle_identifier="social.bsky.engagement",
    )
