# Examples

Everything the examples run lives here, and nothing is hand-written — the library is
verbatim `learn` output.

```
examples/
├── flights.skill.yaml     # the spec the checked-in library came from — build it to remake it
├── trajectories/          # those solves' runs — try `learn` without solving first
├── solve_with_library.sh  # the solve wrapper: skill hint + answer-output instruction
├── learned_library/       # the checked-in artifact (skill.py + meta.json + replays.json)
│   └── what_is_the_earliest_nonstop_flight…/
├── tasks.example.json     # manual mode: a filled task list   (module README, step 6)
└── batch.example.json     # manual mode: a filled manifest    (module README, step 2)
```

## The checked-in skill

Three from-scratch solves of "earliest nonstop flight" on Google Flights (SEA→JFK,
SFO→BOS, LAX→ORD; 59, 25 and 40 agent steps) were grouped by
`python -m cuawright.webwright.skill_factory learn --verify strict` into one template with **five**
lifted parameters, and the distilled skill reproduced all three training answers standalone
before it landed (`meta.json`: `verified: true, grade: executable`):

```json
{
  "template": "What is the earliest nonstop flight from {{origin_city}} ({{origin_code}}) to {{destination_city}} ({{destination_code}}) on {{date}} (one-way)? Return the answer as a list: [flight_number, airline, departure_time], ...",
  "signature": { "params": ["origin_city", "origin_code", "destination_city", "destination_code", "date"],
                 "call": "python skill.py taskspec.json" },
  "n_solves": 3, "verified": true, "grade": "executable"
}
```

Why this task: a flight *schedule* is a stable, client-independent fact the page states
plainly — so the answer is the same today, tomorrow, and on your machine, which is exactly
what lets `--verify strict` and standalone reuse mean something. On the unseen route SEA→DEN the skill
runs standalone in **10 steps / ~40 s / no model at all**, and an independent model-free probe of the
page agrees. With the agent in the loop, reusing the library is both cheaper and steadier than solving
from scratch — see the measured numbers in the module README.

## Run it

Standalone — no API key, ~40 s. The skill is a plain CLI (its five params as `--flags`, or a
positional `taskspec.json`):

```bash
python learned_library/what_is_the_earliest_nonstop_flight_from_2c8dab1/skill.py \
    --origin-city Seattle --origin-code SEA --destination-city Denver --destination-code DEN --date 2026-08-26
```

With the agent in the loop — `route` decides `run` / `adapt` / `skip` and then acts (needs a key).
The task below asks for the *shortest-duration* nonstop, which this skill (it finds the *earliest*)
can't run as-is, so `route` adapts it with the agent. Drop `--start-url` to just print the decision:

```bash
python -m cuawright.webwright.skill_factory route \
    --task "What is the nonstop flight with the shortest flight duration from Seattle (SEA) to Denver (DEN) on 2026-08-26 (one-way)? Return the answer as a list: [flight_number, airline, duration]." \
    --library ./learned_library \
    --start-url https://www.google.com/flights -c <your_model.yaml>
```

Manual mode (explicit manifests, gold gates): see the module README; the two
`*.example.json` files here are filled-in versions of the inputs it asks you to write.

## Remaking it

`flights.skill.yaml` is the spec those three solves came from — it's how `learned_library/` was
produced, and running it reproduces the whole loop:

```bash
python -m cuawright.webwright.skill_factory build flights.skill.yaml --library ./library --jobs 3
```

Its `date` is pinned so the run is reproducible, which also means it goes stale — move the date
forward and it works again. (Measured: Google Flights still lists nonstops ~11 months out, so a
far date buys most of a year.)
