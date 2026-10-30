# Optional CUAWright Skill Factory

This companion distribution adds browser-skill learning, verification, routing,
and reuse. The base CUAWright package excludes this code and its examples;
browser and desktop task execution work without it.

From the repository root:

```bash
pip install -e ".[skill-factory]" -e extensions/skill-factory
cuawright-skill-factory --help
```

Modules retain their existing `cuawright.webwright.skill_factory` and
`cuawright.webwright.tools.skill_use` names. Legacy `webwright` imports are
supported when the extension is installed. Installing it does not enable
automatic learning or reuse in normal runs; invoke its commands explicitly.

For development with both runtimes and the complete test suite:

```bash
pip install -e ".[desktop,skill-factory,test,build]" -e extensions/skill-factory
pytest -q
python -m build
python -m build extensions/skill-factory
```

Build and distribute the companion wheel alongside the core wheel; published
`cuawright[skill-factory]` resolves the matching `cuawright-skill-factory` release.
See the [full guide](src/cuawright/webwright/skill_factory/README.md).
