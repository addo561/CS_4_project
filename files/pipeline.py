# =============================================================================
# pipeline.py — shim for files/
# Dev mode : loads Pipeline from data_pipeline/ via importlib.
# Bundle   : all modules merged in sys._MEIPASS — import directly.
# =============================================================================
import sys

if getattr(sys, "frozen", False):
    from pipeline import Pipeline, PipelineResult   # noqa: F401
else:
    import importlib.util, os
    _ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _COLLECTOR    = os.path.join(_ROOT, "Data_collector")
    _PIPELINE_DIR = os.path.join(_ROOT, "data_pipeline")
    for _p in (_PIPELINE_DIR, _COLLECTOR):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    _spec   = importlib.util.spec_from_file_location(
        "_real_pipeline", os.path.join(_PIPELINE_DIR, "pipeline.py")
    )
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    Pipeline       = _module.Pipeline        # noqa: F401
    PipelineResult = _module.PipelineResult  # noqa: F401
