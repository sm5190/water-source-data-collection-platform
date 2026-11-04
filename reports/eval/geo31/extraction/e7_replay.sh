#!/usr/bin/env bash
# E7 controlled saved-document replay.
#   baseline = ff2c559 (immediately before E1), improved = 8699898 (E6); the diff between them
#   is exactly the six E1-E6 commits. Each arm is a frozen `git archive` and runs ITS OWN
#   scripts/replay_run.py with PYTHONPATH=<arm>/src; before every replay a preflight asserts
#   that water_source_research and REPO_ROOT (config, field definitions) resolve inside the arm.
#   Same .venv, same .env (read in place, never copied), persistent response cache OFF, one
#   fresh process per replay (no in-memory cache carried over), CITY_IDENTITY_ALLOW_DRAFT unset.
#   2 repeats per arm per city, arm order alternated per repeat; one replay at a time.
set -u
REPO="C:/Users/shuto/OneDrive/Documents/Raws/water source_cities/water-source-data-collection-platform/water-source-data-collection-platform"
SP="C:/Users/shuto/AppData/Local/Temp/claude/51f4b2d44d49f256/cb431ec8-887c-4adc-b8e1-5eb1e3675a8b/scratchpad"
OUT="$REPO/reports/eval/geo31/extraction/replays.jsonl"
cd "$REPO"
declare -A REV=([baseline]=ff2c559 [improved]=8699898)
for name in baseline improved; do
  if [ ! -f "$SP/e7_$name/FROZEN_AT" ]; then
    rm -rf "$SP/e7_$name" && mkdir -p "$SP/e7_$name"
    git archive "${REV[$name]}" src config scripts | tar -x -C "$SP/e7_$name"
    git rev-parse "${REV[$name]}" > "$SP/e7_$name/FROZEN_AT"
  fi
done
export LLM_PROVIDER=vt LLM__PERSISTENT_CACHE=off
unset CITY_IDENTITY_ALLOW_DRAFT
PARENTS="Houston:b13e0d1d-aa03-471b-be3b-b63c93ceba9d Santiago:b844390c-4e71-4635-8433-dc6122779573 Vienna:6f48e458-0700-489f-ab50-d50aa1f2a81f Nairobi:21b46e42-5dc5-41fd-ad97-6f0db36a40cb"
for rep in 1 2; do
  for pc in $PARENTS; do
    city=${pc%%:*}; parent=${pc##*:}
    if [ $rep = 1 ]; then arms="baseline improved"; else arms="improved baseline"; fi
    for name in $arms; do
      if grep -q "\"condition\": \"$name\", \"repeat\": $rep, \"city\": \"$city\".*\"exit\": 0" "$OUT" 2>/dev/null; then
        continue  # done (resumable; failed attempts are retried)
      fi
      ARM="$SP/e7_$name"
      log="$SP/e7_${name}_${city}_${rep}.log"
      PYTHONPATH="$ARM/src" uv run python -c "
import sys, water_source_research as w, water_source_research.core.paths as p
arm = sys.argv[1].replace('\\\\', '/').lower()
src = w.__file__.replace('\\\\', '/').lower(); root = str(p.REPO_ROOT).replace('\\\\', '/').lower()
assert src.startswith(arm), (src, arm); assert root.startswith(arm), (root, arm)
print('preflight ok', src, root)" "$ARM" > "$log" 2>&1 || { echo "PREFLIGHT FAILED $name $city"; cat "$log"; exit 3; }
      start=$(date +%s)
      PYTHONPATH="$ARM/src" uv run python "$ARM/scripts/replay_run.py" "$parent" >> "$log" 2>&1
      code=$?
      secs=$(( $(date +%s) - start ))
      run=$(grep -o "replay [0-9a-f-]\{36\}" "$log" | tail -1 | cut -d' ' -f2)
      echo "{\"condition\": \"$name\", \"repeat\": $rep, \"city\": \"$city\", \"parent\": \"$parent\", \"run_id\": \"$run\", \"exit\": $code, \"seconds\": $secs, \"commit\": \"$(cat $ARM/FROZEN_AT)\"}" >> "$OUT"
      echo "$name $rep $city -> $run exit $code ${secs}s"
    done
  done
done
echo "E7 REPLAYS DONE"
