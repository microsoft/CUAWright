# CUAWright desktop

Desktop execution uses the pinned OSWorld-V2 Ubuntu Docker VM and a persistent
terminal Actor. Browser execution remains under `cuawright.webwright`; the two
runtimes do not share their loops, messages, compaction, or completion semantics.

## Workflows

`cuawright desktop run` accepts an arbitrary instruction. It provisions the same
VM, guest bridge, screenshot controls, Actor prompt, call budget, compaction, and
standalone submit control as benchmark execution. It does not load a task class,
run an evaluator, or emit a score.

`cuawright desktop reproduce osworld` loads an official gated task class, applies
its exact setup, preserves proxy and multi-phase behavior, runs its evaluator after
submission, and emits a score in `[0, 1]`.

Plain model text never completes either workflow. The Actor must execute:

```bash
python /opt/cuawright-tools/submit.py
```

## Setup

See [REPRODUCE.md](../REPRODUCE.md) for pinned setup, existing-resource
registration, VM smoke, browser sample, and official reproduction commands.
`cuawright verify osworld` checks the clean source commit, all 108 task classes
against the official hash manifest, nonempty assets, and the registered VM hash.

The setup lock pins:

- OSWorld-V2 `v2026.08.08` at
  `d578d2d4e0dc82b43e270fdaa7fa89d9708cd154`
- tasks and assets at `v2026.08.08`
- website release `v2026.08.08`
- Docker VM artifact `v2026.06.24`, reused by the 0808 release

The task and asset repositories are gated. Setup fails with upstream access
instructions if Hugging Face authorization has not been accepted.

## API and artifacts

`--responses-url` is always the full POST URL ending in `/responses`. CUAWright
derives the OpenAI SDK API root by removing exactly that final segment, so browser,
desktop Actor, evaluator, and user simulator target the same gateway without
double-appending `/responses`.

Credentials must be a user-owned mode-`0600` file. Credentials, proxy paths, and
endpoint identities are excluded from public settings and browser snapshots.

Every run writes `manifest.json`, `trace.jsonl`, and `result.json`. Failures record
an explicit stage without exception text or hidden evaluator state. Official runs
also record task/assets/VM provenance and the evaluator score.
