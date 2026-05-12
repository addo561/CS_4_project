# =============================================================================
# SystemResourceOptimizer.spec
# PyInstaller build spec for the System Resource Optimizer
# Usage:  pyinstaller SystemResourceOptimizer.spec
# =============================================================================

import os, sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.dirname(os.path.abspath(SPEC))   # Final_year/

# ── Source files to add as data (non-Python assets) ──────────────────────────
added_files = [
    # Model + scaler
    (os.path.join(ROOT, "Data_collector", "models"), "models"),
    # App icon
    (os.path.join(ROOT, "assets", "icon.icns"),  "assets"),
    (os.path.join(ROOT, "assets", "icon_proper.png"), "assets"),
]

# ── Hidden imports PyInstaller misses ─────────────────────────────────────────
hidden = [
    # Our own modules
    "pipeline", "action_engine", "notifier", "config",
    "gru_model",
    # psutil
    "psutil", "psutil._psmacosx", "psutil._common",
    # sklearn
    "sklearn", "sklearn.utils._cython_blas",
    "sklearn.neighbors.typedefs",
    "sklearn.neighbors.quad_tree",
    "sklearn.tree._utils",
    # onnxruntime
    "onnxruntime",
    "onnxruntime.capi",
    "onnxruntime.capi.onnxruntime_pybind11_state",
    # torch (core only — avoids pulling in CUDA)
    "torch", "torch.nn", "torch.onnx",
    # plyer
    "plyer", "plyer.platforms.macosx.notification",
    # pyqtgraph
    "pyqtgraph",
    "pyqtgraph.graphicsItems",
]

a = Analysis(
    [os.path.join(ROOT, "files", "main.py")],
    pathex=[
        ROOT,
        os.path.join(ROOT, "files"),
        os.path.join(ROOT, "data_pipeline"),
        os.path.join(ROOT, "Data_collector"),
    ],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy torch bits we don't need
        "torch.distributed",
        "torch.cuda",
        "torchvision",
        "torchaudio",
        "tensorflow",
        "tensorboard",
        "matplotlib",
        "IPython",
        "notebook",
        "jupyter",
        "pytest",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SystemResourceOptimizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no terminal window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "icon.icns"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SystemResourceOptimizer",
)

app = BUNDLE(
    coll,
    name="System Resource Optimizer.app",
    icon=os.path.join(ROOT, "assets", "icon.icns"),
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
