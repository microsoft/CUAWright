#!/usr/bin/env bash
# Solve a task WITH the library — prepends the skill hint and the answer-output
# instruction, then hands off to cuawright. Usage:
#   ./solve_with_library.sh "task text" START_URL /abs/path/to/library [webwright args...]
if [ $# -lt 3 ]; then
  echo "usage: $0 \"task text\" START_URL /abs/path/to/library [webwright args...]" >&2
  exit 1
fi
TASK="$1"; URL="$2"; LIB="$3"; shift 3
SPEC='Additionally, write the final answer into $WORKSPACE_DIR/agent_response.json as {"retrieved_data": <the answer, as a JSON list>}.'
PROMPT=$(python -c 'import sys; from cuawright.skill_factory import with_skill_hint
print(with_skill_hint(sys.argv[1] + " " + sys.argv[3], task=sys.argv[1], library=sys.argv[2]))' "$TASK" "$LIB" "$SPEC")
exec python -m cuawright.run.browser main -t "$PROMPT" --start-url "$URL" "$@"
