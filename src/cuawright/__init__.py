"""CUAWright: shared browser and desktop runtime."""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
__version__ = "0.2.0"


def __getattr__(name):
    if name in {
        "Agent",
        "Environment",
        "Model",
        "package_dir",
        "global_config_dir",
        "global_config_file",
    }:
        from . import contracts

        return getattr(contracts, name)
    raise AttributeError(name)
