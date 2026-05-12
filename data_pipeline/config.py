# =============================================================================
# config.py — shim for data_pipeline/
# Dev mode : loads canonical config from Data_collector/ via importlib.
# Bundle   : all Python files are merged in sys._MEIPASS — import directly.
# =============================================================================
import sys

if getattr(sys, "frozen", False):
    # Inside PyInstaller bundle — config.py is already in sys.path
    from config import *          # noqa: F401, F403
else:
    import importlib.util, os
    _ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _REAL_CFG = os.path.join(_ROOT, "Data_collector", "config.py")
    _spec     = importlib.util.spec_from_file_location("_real_config", _REAL_CFG)
    _module   = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    for _name in dir(_module):
        if not _name.startswith("__"):
            globals()[_name] = getattr(_module, _name)
