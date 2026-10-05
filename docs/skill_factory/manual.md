# Manual mode — manifests, gold gates, full control

[← back to the module README](../../extensions/skill-factory/src/cuawright/skill_factory/README.md)

## When to use this mode

There are two ways to grow the library, and they're one pipeline seen at two levels. **Quick mode**
(`init` / `build` / `learn`) is the convenience layer: it infers the template, extracts
parameters, and gates each solve for you, and it's the right default. **Manual mode** (`update`,
this doc) exposes the same machinery directly, so you write the template, declare the parameters,
and set each solve's verdict yourself. Reach for it in three cases: a benchmark with known answers
(pipe your evaluator's verdict in as `admit`, how this repo's WebArena numbers were made),
correctness beyond an exact string match (a judge, human review, partial credit), or a site behind
a login (the manifest carries credentials into replay; `learn` doesn't). The split is pragmatic
rather than fundamental, so these manual-only knobs could surface in `build` later.

| | quick mode (`build` / `learn`) | manual mode (`update`) |
|---|---|---|
| the template | an LLM infers it by grouping your runs | **you write the exact string** |
| the parameters | an LLM extracts them | **you declare them per run** |
| is this solve correct? | `--golds` (exact match) or `self_verify` | **you say so** (`admit`, required per run) |
| ADD or REFINE? | derived: refine if the template exists | **you say so** (`verdict`) |
| which runs | everything under one folder | **you list the directories** |
| credentials | left out, so a replay stays logged out | **`credentials` per run, carried into replay** |

The examples use a **WebArena GitLab** task. Because GitLab is both self-hosted and evaluated against a gold answer, it covers two of these cases at once. The examples are meant to represent [your own WebArena instance](https://github.com/web-arena-x/webarena): `http://gitlab.example.com` and the credentials are placeholders for your actual host and accounts.

The library grows offline from batches of solved tasks and is consumed at solve time by the agent.
Feed several instances of the same template (3+ with different values works well): `refine` aligns
them, and what differs between instances becomes the skill's parameters.

### 1. Solve a few instances of a template

Stock Webwright doesn't write the answer to a machine-readable file on its own. Append an output
instruction to the task so it does (the manifest in step 2 reads it):

```bash
ANSWER_SPEC='Additionally, write the final answer into $WORKSPACE_DIR/agent_response.json
as {"retrieved_data": <the answer, as a JSON list>}.'

python -m cuawright.run.browser main \
  -t "How many commits did kilian make to a11yproject on 3/1/2023? $ANSWER_SPEC" \
  --task-id t132_a --start-url http://gitlab.example.com -o outputs \
  -c base.yaml -c model_openai.yaml
```

Each run leaves a directory with `final_script.py` and `agent_response.json`. Repeat for 2–3 more
instances with different values (another user / repo / date).

### 2. Judge each solve, write the manifest

Score each run with your own evaluator and put the verdict in `admit`. The manifest is just the
list of runs:

```jsonc
// batch.json
{
  "template": "How many commits did {{user}} make to {{repo}} on {{date}}?",
  "runs": [
    {
      "dir": "outputs/t132_a_20260703_120000",   // run dir; final_script.py is read from it
      "admit": true,                             // your evaluator's verdict; false rows never enter
      "params": {"user": "kilian", "repo": "a11yproject", "date": "3/1/2023"},
      "verdict": "skip",
      "site": "gitlab",
      "output_schema": {"type": "number"}
    },
    { "dir": "outputs/t132_b_20260703_121500", "admit": false,
      "params": {"user": "gao", "repo": "2019", "date": "4/6/2023"},
      "verdict": "skip", "site": "gitlab", "output_schema": {"type": "number"} }
  ]
}
```

Assembling it programmatically from a gold set is shown end-to-end in step 6.

| field | required | meaning |
|---|---|---|
| `template` | yes | the template with `{{param}}` placeholders. **Skills are keyed by it**: a matching template refines the existing skill in place; a new one adds a skill. |
| `runs[].dir` | yes | a Webwright run directory; `final_script.py` is read from it |
| `runs[].admit` | yes | your verdict; `false` rows never enter the library |
| `runs[].params` | yes | this instance's values; `refine` exposes exactly what differs across runs as the skill's arguments |
| `runs[].verdict` | no (`skip`) | how the run used the library: `skip` = from scratch, `use` = reused as-is, `adapt` = reused + fixed the last step (**`adapt` triggers refining that fix back in**) |
| `runs[].site` | no | site tag stored in the skill's meta (helps retrieval) |
| `runs[].output_schema` | no | required shape of `retrieved_data`, e.g. `{"type": "number"}` |
| `runs[].answer` | no | read from the run dir's `agent_response.json` when omitted |
| `runs[].credentials` | no | login for the replay of a gated site (step 5); never written into the skill or `replays.json` |

### 3. Build / evolve the library

```bash
export OPENAI_API_KEY=...
python -m cuawright.skill_factory.update --manifest batch.json --library ./library --verify strict
```

`update` defaults to `--verify off` (a skill would land `unverified`), so pass `--verify strict`
or `shape` to have it graded. Prints a changelog:
`{"added": [...], "adapt_refined": [...], "use": [...], "dropped_wrong": n}`. Re-run with later
batches any time; batches may mix templates.

### 4. Reuse at solve time

> `build`/`learn` already resolve the library lookup out of the agent loop for you, so you can
> skip this. It's the manual wiring for when you drive Webwright runs yourself.

```python
from cuawright.skill_factory import with_skill_hint
prompt = with_skill_hint(prompt, task=task_text, library="/abs/path/to/library")
# then: python -m cuawright.run.browser main -t "$prompt" ...
```

`with_skill_hint` resolves the library lookup out of the agent loop (the `recommend` decision:
`{verdict, skill_id, source_path, how_to_reuse}`) and prepends the chosen skill to the prompt; the
agent reads that injected hint and reuses the source — it never queries the library itself. It also
resolves `./library` to an absolute path so the run finds it from its workspace; `--library` beats
the `SKILL_LIBRARY_ROOT` env var, so use one or the other.

### 5. Run a skill directly, and logged-in sites

Every skill is also a standalone script: it reads a `taskspec.json` and writes
`agent_response.json`:

```bash
cat > taskspec.json <<'EOF'
{"params": {"user": "byte", "repo": "empathy-prompts", "date": "4/2/2023"},
 "start_url": "http://gitlab.example.com",
 "credentials": {"username": "byte", "password": "hunter2"},
 "output_schema": {"type": "number"}}
EOF
python library/how_many_commits_did_user_make_to_repo_on_date/skill.py taskspec.json
```

`credentials` is the field `learn` can't fill, and it's why a logged-in site needs this path: the
skill logs in with what the taskspec (or the manifest, per run) hands it, so replay can run. They
never touch the skill or `replays.json`, so the library stays shareable.

### 6. The whole pipeline in one go

```bash
START_URL=http://gitlab.example.com
ANSWER_SPEC='Additionally, write the final answer into $WORKSPACE_DIR/agent_response.json
as {"retrieved_data": <the answer, as a JSON list>}.'

# 1) solve every instance (add xargs -P N or & to parallelize)
jq -c '.[]' tasks.json | while read -r row; do
  python -m cuawright.run.browser main -t "$(jq -r .task <<<"$row") $ANSWER_SPEC" \
    --task-id "$(jq -r .id <<<"$row")" --start-url "$START_URL" -o outputs \
    -c base.yaml -c model_openai.yaml
done

# 2) score each run with YOUR evaluator + assemble the manifest
python - <<'PY'
import json, glob
from your_harness import gold_eval          # your benchmark's own evaluator

TEMPLATE = "How many commits did {{user}} make to {{repo}} on {{date}}?"
SCHEMA = {"type": "number"}
runs = []
for t in json.load(open("tasks.json")):                     # {id, task, params, gold} per row
    d = sorted(glob.glob(f"outputs/{t['id']}_*"))[-1]
    answer = json.load(open(f"{d}/agent_response.json"))["retrieved_data"]
    runs.append({"dir": d, "admit": gold_eval(answer, t["gold"]), "params": t["params"],
                 "verdict": "skip", "site": "gitlab", "output_schema": SCHEMA})
json.dump({"template": TEMPLATE, "runs": runs}, open("batch.json", "w"), indent=2)
print(sum(r["admit"] for r in runs), "of", len(runs), "admitted")
PY

# 3) evolve the library (strict replay so skills land executable)
python -m cuawright.skill_factory.update --manifest batch.json --library ./library --verify strict

# 4) solve a NEW instance WITH the library
TASK="How many commits did byte make to empathy-prompts on 4/2/2023?"
PROMPT=$(python -c 'import sys; from cuawright.skill_factory import with_skill_hint
print(with_skill_hint(sys.argv[1], task=sys.argv[1], library="./library"))' "$TASK")
python -m cuawright.run.browser main -t "$PROMPT" \
  --task-id t132_new --start-url "$START_URL" -o outputs -c base.yaml -c model_openai.yaml
```

Repeat 1–3 as new solves land; the library evolves in place. This is exactly the loop our WebArena
evaluation runs (train → gate → update → held-out reuse).
