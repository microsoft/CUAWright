"""Compatibility imports for the separately installed Skill Factory addon."""

import importlib
import importlib.abc
import importlib.util
import sys


class _AddonLoader(importlib.abc.Loader):
    def __init__(self, target, spec):
        self.target = target
        self.target_spec = spec

    def create_module(self, spec):
        return importlib.import_module(self.target)

    def exec_module(self, module):
        module.__spec__ = self.target_spec

    def get_filename(self, fullname):
        return self.target_spec.origin

    def get_code(self, fullname):
        return self.target_spec.loader.get_code(self.target)


class _AddonFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not (
            fullname == "webwright.skill_factory"
            or fullname.startswith("webwright.skill_factory.")
            or fullname == "webwright.tools.skill_use"
        ):
            return None
        canonical = "cuawright." + fullname[len("webwright.") :]
        spec = importlib.util.find_spec(canonical)
        if spec is None:
            return None
        return importlib.util.spec_from_loader(
            fullname,
            _AddonLoader(canonical, spec),
            is_package=spec.submodule_search_locations is not None,
        )


sys.meta_path.insert(0, _AddonFinder())
