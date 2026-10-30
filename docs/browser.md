# Browser workflow

The [browser quick start](../README.md#-browser-quick-start) covers installation
and script-based and live-browser runs.

## Images and verification in live-browser mode

The `best_default_judge_json_persistent_cli.yaml` config keeps a Browserbase cloud
session across shell commands. It requires `BROWSERBASE_API_KEY` and
`BROWSERBASE_PROJECT_ID`, alongside the configured model's credentials.

The agent attaches saved images to its model context with:

```bash
python -m cuawright.webwright.tools.image_read --path /absolute/workspace/image.png
```

It uses `self_reflection --scope trajectory` to judge the task screenshots before
finishing. The `image_qa` and `self_reflection` tools use the run's configured model.
When running a tool separately, pass `--model-config` with an absolute path to a
private YAML file containing your `model:` settings. Keep that file outside the
checkout and results directory.
