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

# Read version dynamically from version.txt if it exists
APP_VERSION = "3.2.1"
version_file = os.path.join(ROOT, "src", "assets", "version.txt")
if os.path.isfile(version_file):
    try:
        with open(version_file, "r") as f:
            v_val = f.read().strip()
            if v_val:
                if v_val.startswith("v") or v_val.startswith("V"):
                    APP_VERSION = v_val[1:]
                else:
                    APP_VERSION = v_val
    except Exception:
        pass

# ── Source files to add as data (non-Python assets) ──────────────────────────
added_files = [
    # Model + scaler
    (os.path.join(ROOT, "src", "models"), "models"),
    # App assets (including icons and version.txt)
    (os.path.join(ROOT, "src", "assets"), "assets"),
]
added_files += collect_data_files("flet")
added_files += collect_data_files("flet-desktop")

# ── Hidden imports PyInstaller misses ─────────────────────────────────────────
hidden = [
    # Our own modules
    "core.pipeline", "core.action_engine", "core.notifier", "core.process_names", "config",
    # psutil
    "psutil",
    # onnxruntime
    "onnxruntime",
    # sklearn
    "sklearn", "sklearn.utils._cython_blas", "sklearn.neighbors.typedefs", 
    "sklearn.neighbors.quad_tree", "sklearn.tree._utils",
    # winotify
    "winotify",
    # plyer
    "plyer", "plyer.platforms.macosx.notification",
]
hidden += collect_submodules("flet")
hidden += collect_submodules("flet-desktop")

# Excludes list
excludes_list = [
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
    # Exclude heavy unused PyQt6 web, mobile, 3D, and multimedia frameworks
    "PyQt6.QtWebEngine",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtQml",
    "PyQt6.QtQuick",
    "PyQt6.QtQuickWidgets",
    "PyQt6.QtQuick3D",
    "PyQt6.QtQuick3DEffects",
    "PyQt6.QtQuick3DHelpers",
    "PyQt6.QtQuick3DRuntimeRender",
    "PyQt6.Qt3D",
    "PyQt6.Qt3DAnimation",
    "PyQt6.Qt3DCore",
    "PyQt6.Qt3DExtras",
    "PyQt6.Qt3DInput",
    "PyQt6.Qt3DLogic",
    "PyQt6.Qt3DRender",
    "PyQt6.QtPdf",
    "PyQt6.QtPdfWidgets",
    "PyQt6.QtDesigner",
    "PyQt6.QtMultimedia",
    "PyQt6.QtMultimediaWidgets",
    "PyQt6.QtBluetooth",
    "PyQt6.QtSensors",
    "PyQt6.QtPositioning",
    "PyQt6.QtNfc",
    "PyQt6.QtSerialPort",
    "PyQt6.QtRemoteObjects",
    "PyQt6.QtSpatialAudio",
    "PyQt6.QtCharts",
    "PyQt6.QtNetwork",
    "PyQt6.QtSql",
    "PyQt6.QtTest",
]

# ── GUI Analysis ─────────────────────────────────────────────────────────────
a_gui = Analysis(
    [os.path.join(ROOT, "src", "dashboard.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes_list,
    noarchive=False,
    optimize=1,
)

pyz_gui = PYZ(a_gui.pure)

# ── Service Analysis ──────────────────────────────────────────────────────────
a_srv = Analysis(
    [os.path.join(ROOT, "src", "optimizer_service.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes_list,
    noarchive=False,
    optimize=1,
)

pyz_srv = PYZ(a_srv.pure)

_ICON = os.path.join(ROOT, "src", "assets", "icon.icns")
if platform.system() == "Windows":
    # Must be a real .ico file — PyInstaller embeds it in the EXE header
    # so Windows Explorer and the taskbar show the custom icon.
    _ICON = os.path.join(ROOT, "src", "assets", "icon.ico")

is_win = platform.system() == "Windows"

# ── EXE for GUI ───────────────────────────────────────────────────────────────
exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    a_gui.binaries if is_win else [],
    a_gui.zipfiles if is_win else [],
    a_gui.datas    if is_win else [],
    name="SystemResourceOptimizer",
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

# ── EXE for Service ───────────────────────────────────────────────────────────
exe_srv = EXE(
    pyz_srv,
    a_srv.scripts,
    a_srv.binaries if is_win else [],
    a_srv.zipfiles if is_win else [],
    a_srv.datas    if is_win else [],
    name="SystemResourceOptimizerService",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=not is_win,  # False on Windows (runs silently), True on macOS (avoids Dock icon)
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
        exe_gui,
        a_gui.binaries,
        a_gui.datas,
        exe_srv,
        a_srv.binaries,
        a_srv.datas,
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
            "CFBundleVersion":          APP_VERSION,
            "CFBundleShortVersionString": APP_VERSION,
            # Registers our bundle as the principal macOS GUI application.
            # Without this the Flet Flutter renderer subprocess "wins" the
            # Dock slot and shows the Flet logo instead of ours.
            "NSPrincipalClass":         "NSApplication",
            "CFBundleIconFile":         "icon.icns",
            "NSHighResolutionCapable":  True,
            "NSHumanReadableCopyright": "© 2026 KNUST Group 4",
            "LSMinimumSystemVersion":   "10.14",
            "NSRequiresAquaSystemAppearance": False,
            "LSBackgroundOnly":         False,
            # Do NOT set LSUIElement here — that would hide us from the
            # Dock and let the Flet subprocess take over the icon slot.
        },
    )
