# Working in CUAWright

Use Python 3.12 for desktop work. Install with:

```bash
pip install -e ".[desktop,skill-factory,test,build]" -e extensions/skill-factory
playwright install chromium
```

Keep the browser and desktop runtimes separate:

- `src/cuawright/webwright/` owns browser messages, tools, compaction, and completion.
- `src/cuawright/desktop/` owns the OSWorld VM, guest bridge, explicit submit, and evaluation.
- `src/cuawright/core/` holds shared API helpers. Add code here only when both runtimes use it.

Do not introduce a universal agent/model/environment abstraction or duplicate the
browser runtime under another package. Preserve `webwright` compatibility aliases.
Keep provider-specific private integrations outside this repository. Never save
credentials or custom endpoint URLs in run artifacts, examples, or diagnostics.

Before changing desktop prompts, read `src/cuawright/desktop/config/prompts.py` in
full. Plain final text never completes a desktop task; the Actor must execute the
standalone command `python /opt/cuawright-tools/submit.py`. Official reproduction
must retain the pinned release, task setup, evaluator, provenance, and score.

Validate focused changes first, then run:

```bash
pytest -q
python -m build
python -m build extensions/skill-factory
python tests/packaging/check_wheels.py dist/cuawright-*.whl extensions/skill-factory/dist/*.whl
```

Keep net changes within 900 production Python lines and 600 test lines. No newly
added production file should exceed 300 lines.
