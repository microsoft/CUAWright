# Unified runtime and compatibility

New development belongs in `src/cuawright`. Browser and desktop code follow the
same `agents`, `config`, `environments`, `models`, `run`, `tools`, and `utils`
structure. `src/webwright` retains the previous browser implementation and YAML
configs; its commands and imports continue to use that code independently.

## Shared execution

`agents/default.py` owns `run_loop` and `CallBudget`. Both adapters implement the
same hooks: readiness, one turn, completion, maintenance, and final result.
The scheduler checks completion before maintenance and allows incomplete desktop
turns to skip compaction. Browser failures still persist partial trajectories.

The adapters preserve existing behavior:

| Behavior | Browser | Desktop |
| --- | --- | --- |
| Completion | Model completion with configured reflection gate | Standalone guest submit command |
| Budget | Successful action queries; zero/negative means unlimited | Every successful model response, including compaction, across all phases |
| Compaction | Best effort; summary calls do not consume the historical step limit | Counts against the shared phase budget; invalid summaries fail explicitly |
| Messages | Browser model abstraction and observation templates | Native Responses items and strict terminal result validation |
| Transport | Existing HTTP providers: OpenAI, Anthropic, OpenRouter | OpenAI SDK with sanitized retries and policy recovery |

`models/responses.py` shares stateless tool-session options and image encoding.
The transport adapters and message normalization retain their existing contracts.
They can be consolidated further after parity has been established; this change
does not claim that the full browser implementation disappears.

## Environments and benchmarks

Browser and workspace environments remain under `environments/`; the desktop
guest bridge and control clients live under `environments/desktop/`. Official
OSWorld source validation, compatibility patches, setup, phases, and evaluation
live under `run/benchmarks/osworld/`, outside the shared agent loop.

Desktop help imports neither OSWorld nor the OpenAI SDK. Desktop credentials,
custom endpoint URLs, and proxy configuration paths remain excluded from public
settings. Build provenance fingerprints the entire canonical core runtime;
installing the optional Skill Factory does not change that fingerprint.

## Commands and optional learning

| Command | Implementation |
| --- | --- |
| `cuawright web main ...` / `cuawright-web main ...` | `cuawright.run.browser` |
| `cuawright desktop ...` / `cuawright-desktop ...` | `cuawright.run.desktop` |
| `webwright main ...` / `python -m webwright.run.cli ...` | Retained `webwright.run.cli` |

`cuawright-skill-factory` is a separate optional wheel. Its canonical imports are
`cuawright.skill_factory` and `cuawright.tools.skill_use`; legacy
`webwright.skill_factory` and `webwright.tools.skill_use` imports resolve to the
same addon modules only when installed. The legacy core itself is retained code,
not an alias of the new runtime.

Backward compatibility increases the total packaged code because it retains a
browser copy. Physical line counts include blank lines and comments and are
reported separately for the canonical runtime, legacy runtime, and optional addon
in the README. A folder move alone does not reduce browser functionality or size.
