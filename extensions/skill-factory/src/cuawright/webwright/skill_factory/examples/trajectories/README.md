# Three solved runs, so you can try `learn` without solving first

`learn` is the day-to-day path — you've been using Webwright anyway, so hand it the runs you
already have. But that's hard to *try* if you've never run Webwright: you'd have to spend 30
minutes solving before you had anything to distil. These are three real solves of the flights
template, so you don't have to:

```bash
cd extensions/skill-factory/src/cuawright/webwright/skill_factory/examples
python -m cuawright.webwright.skill_factory learn trajectories --library ./library --verify off
```

Three runs → one template → five lifted parameters → one skill. ~100 s and an API key (the
grouping and distillation are model calls). Nothing else is needed: **`--verify off` never opens
a browser**, so this works today and in a year, and it can't be rejected.

What lands says so on the label: `grade: unverified`. That is its own state, distinct from
`reference` — `reference` means the replay ran and the skill *failed* it, which is a claim we
haven't earned here because nothing ran. You're seeing the distillation half (gate → group →
lift parameters → skill), not the gate that proves it runs.

**To see verification too**, drop the flag — but read the expiry note first, because the replay
drives the live site:

```bash
python -m cuawright.webwright.skill_factory learn trajectories --library ./library   # --verify strict
```

That takes ~5-13 min, opens a browser per instance, and **may reject on the first attempt** —
that's the gate working, not a misconfiguration; run it again. See
[What to expect](../../README.md#what-to-expect). The proof that verification works doesn't rest
on this demo anyway: the checked-in skill in `../learned_library/` carries
`verified: true, grade: executable`, and the checked-in skill runs it directly
(`python ../learned_library/what_is_the_earliest_nonstop_flight_from_2c8dab1/skill.py --flags`).

## What's here, and what isn't

`learn` reads exactly three files per run, so that's all we ship:

| file | what it's for |
|---|---|
| `task.json` | the task text and start_url — the template is inferred from these |
| `final_script.py` | the working script the agent wrote — the raw material distillation aligns |
| `agent_response.json` | the answer it got — the baseline replay-verify has to reproduce |

The full run directories were ~12 MB each, almost all screenshots; none of that is read. The
absolute library path the solving machine had in its prompt has been scrubbed.

## ⚠️ These expire

The runs are pinned to **2026-08-15**, and their answers were true on 2026-07-15:

| route | answer |
|---|---|
| SEA→JFK | `["AS26", "Alaska", "7:00 AM"]` |
| SFO→BOS | `["B6 434", "JetBlue", "6:00 AM"]` |
| LAX→ORD | `["UA 729", "United", "12:10 AM"]` |

This only matters if you drop `--verify off`. `--verify strict` (the default) replays the skill
against the **live** site and demands these answers back. So:

- **if an airline reschedules**, the replay returns a different flight and the skill is rejected;
- **once 2026-08-15 is in the past**, Google Flights can't show that date at all, the skill
  crashes rather than answers, and it is rejected.

Neither is a bug in the pipeline — it's a fixture with a shelf life, and this is what the shelf
life looks like. `--verify shape` does **not** rescue the second case: a crashed skill writes no
answer, and shape still requires a non-empty one.

`--verify off` is immune to both, which is why it's the command at the top: it never opens the
page, so there is nothing to be stale about. To get the *whole* loop back on live data, re-solve
with a future date — move the `date` in `../flights.skill.yaml` and `build` it.
