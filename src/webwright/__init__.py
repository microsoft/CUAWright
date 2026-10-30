"""Compatibility namespace sharing CUAWright's browser module objects."""

import importlib
import importlib.abc
import importlib.util
import sys

from cuawright.webwright import *
from cuawright import webwright as _web

__path__ = _web.__path__
__version__ = _web.__version__


class _BrowserAliasLoader(importlib.abc.Loader):
    def __init__(self, target):
        self.target = target
        self.target_spec = importlib.util.find_spec(target)

    def create_module(self, spec):
        return importlib.import_module(self.target)

    def exec_module(self, module):
        # Keep the canonical spec intact when import machinery installs the alias.
        module.__spec__ = self.target_spec

    def get_filename(self, fullname):
        return self.target_spec.origin

    def get_code(self, fullname):
        # Support legacy `python -m webwright.<module>` invocations.
        return self.target_spec.loader.get_code(self.target)


class _BrowserAliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith("webwright."):
            return None
        canonical = "cuawright.webwright." + fullname[len("webwright.") :]
        loader = _BrowserAliasLoader(canonical)
        if loader.target_spec is None:
            return None
        return importlib.util.spec_from_loader(
            fullname,
            loader,
            is_package=loader.target_spec.submodule_search_locations is not None,
        )


sys.meta_path.insert(0, _BrowserAliasFinder())
