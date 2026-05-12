# =============================================================================
# notifier.py — shim for files/
# Dev mode : loads Notifier from data_pipeline/ via importlib.
# Bundle   : all modules merged in sys._MEIPASS — import directly.
# =============================================================================
import sys

if getattr(sys, "frozen", False):
    # Inside PyInstaller bundle — all modules are in sys._MEIPASS
    from notifier import Notifier  # noqa: F401
else:
    import importlib.util, os
    _ROOT          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _PIPELINE_DIR  = os.path.join(_ROOT, "data_pipeline")
    if _PIPELINE_DIR not in sys.path:
        sys.path.insert(0, _PIPELINE_DIR)

    _spec   = importlib.util.spec_from_file_location(
        "_real_notifier", os.path.join(_PIPELINE_DIR, "notifier.py")
    )
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)

    Notifier = _module.Notifier  # noqa: F401
