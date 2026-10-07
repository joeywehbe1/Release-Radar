#!/usr/bin/env bash
# Release-window burst: for $1 minutes, a quick NEW DROP check about every minute,
# with a full check every 10 minutes. State is saved after every check.
set -u
minutes="$1"
end=$((SECONDS + minutes * 60))
last_full=-100000
while [ "$SECONDS" -lt "$end" ]; do
  if [ $((SECONDS - last_full)) -ge 600 ]; then
    timeout 9m python -m radar run --state state/state.json || echo "::warning::full check failed"
    last_full=$SECONDS
  else
    timeout 3m python -m radar run --fast --state state/state.json || echo "::warning::quick check failed"
  fi
  bash .github/save-state.sh || echo "::warning::state save failed"
  sleep 20
done
