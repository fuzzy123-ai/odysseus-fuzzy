"""Local Temporal Light runtime boundary for durable Agent execution.

Exports are loaded lazily so ``python -m src.temporal_runtime.config`` does not
pre-import the module that ``runpy`` is about to execute.
"""

from importlib import import_module

__all__ = [
    "CLI_VERSION",
    "SDK_VERSION",
    "TemporalHealth",
    "TemporalLightConfig",
    "TemporalLightConfigError",
    "assert_installed_capabilities",
    "check_temporal_health",
    "load_temporal_light_config",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    return getattr(import_module(".config", __name__), name)
