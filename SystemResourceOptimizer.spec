# =============================================================================
# SystemResourceOptimizer.spec
# PyInstaller build spec for the System Resource Optimizer
# Usage:  pyinstaller SystemResourceOptimizer.spec
# =============================================================================

import os
import sys
import platform
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.dirname(os.path.abspath(SPEC))   # Final_year/

# ── Source files to add as data (non-Python assets) ──────────────────────────
added_files = [
    # Model + scaler
    (os.path.join(ROOT, "src", "models"), "models"),
    # App icon
    (os.path.join(ROOT, "src", "assets", "icon.icns"),  "assets"),
    (os.path.join(ROOT, "src", "assets", "icon_proper.png"), "assets"),
]
added_files += collect_data_files("flet")
added_files += collect_data_files("flet-desktop")

# ── Hidden imports PyInstaller misses ─────────────────────────────────────────
hidden = [
    # Our own modules
    "core.pipeline", "core.action_engine", "core.notifier", "config",
    # psutil
    "psutil",
    # onnxruntime
    "onnxruntime",
    # sklearn
    "sklearn", "sklearn.utils._cython_blas", "sklearn.neighbors.typedefs", 
    "sklearn.neighbors.quad_tree", "sklearn.tree._utils",
    # pyqtgraph
    "pyqtgraph", "pyqtgraph.graphicsItems",
    # plyer
    "plyer", "plyer.platforms.win.notification", "plyer.platforms.macosx.notification",
]
hidden += collect_submodules("flet")
hidden += collect_submodules("flet-desktop")

# ── Analysis ─────────────────────────────────────────────────────────────────
a = Analysis(
    [os.path.join(ROOT, "src", "main.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy torch bits we don't need
        "torch",
        "torch.distributed",
        "torch.cuda",
        "torchvision",
        "torchaudio",
        "tensorflow",
        "tensorboard",
        "pandas",
        "matplotlib",
        "IPython",
        "notebook",
        "jupyter",
        "pytest",
        # Exclude heavy unused PyQt6 web and mobile frameworks
        "PyQt6.QtWebEngine",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtQml",
        "PyQt6.QtQuick",
        "PyQt6.QtQuickWidgets",
        "PyQt6.QtNetwork",
        "PyQt6.QtSql",
        "PyQt6.QtTest",
        "PyQt6.QtBluetooth",
        "PyQt6.QtMultimedia",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

_ICON = os.path.join(ROOT, "src", "assets", "icon.icns")
if platform.system() == "Windows":
    # On Windows, use the high-res PNG; PyInstaller/Pillow will convert it to ICO
    _ICON = os.path.join(ROOT, "src", "assets", "icon_proper.png")

is_win = platform.system() == "Windows"

exe = EXE(
    pyz,
    a.scripts,
    a.binaries if is_win else [],
    a.zipfiles if is_win else [],
    a.datas    if is_win else [],
    name="SystemResourceOptimizer_bin" if not is_win else "SystemResourceOptimizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
    exclude_binaries=not is_win,
)

if not is_win:
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="SystemResourceOptimizer",
    )

if platform.system() == 'Darwin':
    app = BUNDLE(
        coll,
        name="System Resource Optimizer.app",
        icon=os.path.join(ROOT, "src", "assets", "icon.icns"),
        bundle_identifier="com.knust.group4.systemresourceoptimizer",
        info_plist={
            "CFBundleName":             "System Resource Optimizer",
            "CFBundleDisplayName":      "System Resource Optimizer",
            "CFBundleVersion":          "1.0.0",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable":  True,
            "NSHumanReadableCopyright": "© 2026 KNUST Group 4",
            "LSMinimumSystemVersion":   "10.14",
            "NSRequiresAquaSystemAppearance": False,   # supports dark mode
        },
    )
