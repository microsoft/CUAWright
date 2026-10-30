"""Record immutable runtime provenance in wheels, without modifying source."""

import json
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from cuawright.desktop.utils.artifacts import harness_provenance


class ProvenanceBuild(build_py):
    def run(self):
        provenance = harness_provenance()
        super().run()
        metadata = Path(self.build_lib) / "cuawright/desktop/build_provenance.json"
        metadata.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")


class ProvenanceSdist(sdist):
    def make_release_tree(self, base_dir, files):
        provenance = harness_provenance()
        super().make_release_tree(base_dir, files)
        metadata = Path(base_dir) / "src/cuawright/desktop/build_provenance.json"
        metadata.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")


setup(cmdclass={"build_py": ProvenanceBuild, "sdist": ProvenanceSdist})
