# Reproduce OSWorld-V2

Run your own desktop task with `cuawright desktop run`, or reproduce an official
benchmark task with `cuawright desktop reproduce osworld`. Both use the OSWorld
Ubuntu VM. Official tasks receive an evaluator score; custom tasks save the result
without a benchmark score.

## Install and prepare

Use Python 3.12+, Git, Docker, and preferably KVM. Run these commands from the
repository checkout:

```bash
pip install -e ".[desktop]"
```

Accept access to both gated datasets, then authenticate:

- <https://huggingface.co/datasets/xlangai/osworld_v2_tasks>
- <https://huggingface.co/datasets/xlangai/osworld_v2_assets_gated>

```bash
pip install huggingface_hub
hf auth login
```

Setup downloads the OSWorld source, tasks, and assets for the pinned 2026.08.08
release, installs the dependencies, and checks the task hashes.

For the VM, this guide uses the August 25 Zotero-fix image at immutable revision
`6e16459a2feb5a8f1ed65babcfe7a2a6205d049d`. This is the compatible newer image;
the original 0808 manifest names an older VM. The archive SHA-256 is
`14b08aa7ba6c023ecb91d46de8df5de32af4d1d6bd75ea925519caf9677fc8b3`.
Setup verifies that hash before extracting the image:

```bash
cuawright setup osworld --root ~/.cache/cuawright/osworld-v2-2026.08.08
cuawright verify osworld \
  --setup ~/.cache/cuawright/osworld-v2-2026.08.08/setup.json
```

The VM archive is about 14.9 GB. If you already have the files, point setup to them:

```bash
cuawright setup osworld \
  --root /path/to/cuawright-osworld \
  --source /path/to/clean/OSWorld-V2 \
  --tasks /path/to/task-classes \
  --assets /path/to/assets \
  --task-manifest /path/to/task_hashes.json \
  --vm /path/to/Ubuntu.qcow2 \
  --skip-install
```

`setup.json` records the hash of the VM you select. If you have repaired or expanded
a local VM, register it using `--vm` so the run records that version.

Save your Responses API key in a file outside the checkout, task, asset, and result
directories. Set its permissions to `0600` so only your user can read and write it.

## Smoke and custom task

Create the parent directory first, then use a new subdirectory for each run:

```bash
mkdir -p ~/cuawright-results
```

Replace `YOUR_MODEL`, the API URL, and the credentials path in the examples with
your provider's settings.

Check that the VM and model API work together with a smoke test. This makes paid
model requests:

```bash
cuawright smoke osworld \
  --setup /path/to/setup.json \
  --credentials /path/to/api-key \
  --results ~/cuawright-results/smoke \
  --model YOUR_MODEL \
  --responses-url https://api.example/v1/responses
```

Run any other desktop task:

```bash
cuawright desktop run \
  --setup /path/to/setup.json \
  --credentials /path/to/api-key \
  --results ~/cuawright-results/custom \
  --model YOUR_MODEL \
  --responses-url https://api.example/v1/responses \
  --instruction "Create /home/user/notes.txt containing a three-item checklist for planning a meeting. Reopen the file and verify its contents."
```

Custom tasks start from the VM's reset state. There are no CLI options to stage
input files or export generated files. The example saves `notes.txt` inside the VM;
the host results directory contains the run record, not a copy of that file.
The VM closes when the task ends. Arrange any file transfer during the task if you
need the generated artifact on the host.

## Choose an official task

After setup, list the downloaded task files:

```bash
ls ~/.cache/cuawright/osworld-v2-2026.08.08/tasks/task_*.py
```

Read the chosen file for its instruction and setup requirements. For example,
`task_001.py` uses `--task-id 001`. If you registered existing resources, use the
`tasks` path in your `setup.json` instead.

Tasks whose setup includes a `proxy` step need a working proxy supplied by you.
Save a JSON list in a private file outside the checkout, with your proxy's values:

```json
[
  {
    "host": "proxy.example.com",
    "port": 8080,
    "username": "YOUR_USERNAME",
    "password": "YOUR_PASSWORD",
    "protocol": "http"
  }
]
```

Username and password are optional for proxies without authentication. Pass the
absolute file path with `--proxy-config /absolute/path/to/proxies.json`.
A proxy service is not included in setup.

## Official scored reproduction

Verify the registered resources before each reproduction. The run records hashes
but does not automatically recheck the full setup lock, so keep task and asset
files unchanged after verification.

```bash
cuawright verify osworld --setup /path/to/setup.json
cuawright desktop reproduce osworld \
  --setup /path/to/setup.json \
  --credentials /path/to/api-key \
  --results ~/cuawright-results/task-001 \
  --task-id 001 \
  --model YOUR_MODEL \
  --responses-url https://api.example/v1/responses \
  --reasoning xhigh
```

`--reasoning` defaults to `xhigh`. To reproduce a reported score, pass the
effort used for that model:

| Model | `--reasoning` | OSWorld-V2 partial score |
| --- | --- | --- |
| GPT-5.6 Sol | `max` | 67.9 |
| GPT-5.5 | `xhigh` | 63.2 |

The result directory must not exist before launch, and its parent must exist.
The run saves:

- `manifest.json`: versions, hashes, and settings for the code, task, assets, VM,
  prompts, and tools.
- `trace.jsonl`: model requests and responses, commands, retries, and cleanup.
- `result.json`: the outcome or failure details, plus the evaluator score for an
  official task.

## Browser sample

Install Chromium:

```bash
playwright install chromium
```

Set `OPENAI_API_KEY` to the key for your Responses-compatible provider. This
sample keeps a browser page open and reports its title; it does not create a reusable
script. Run it in headless mode:

```bash
cuawright web main \
  -c base.yaml -c model_openai.yaml -c local_browser.yaml \
  -c environment.browser_mode=local_launch \
  -c environment.headless=true \
  -c model.responses_url=https://api.example/v1/responses \
  -c model.model_name=YOUR_MODEL \
  -t "Open the page and report its title." \
  --start-url https://example.com \
  --task-id sample
```

The existing `cuawright-web` and `webwright` commands remain compatible.
