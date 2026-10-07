#!/usr/bin/env bash
# Saves state/state.json as the single commit of the bot-state branch (force-pushed),
# so main's history stays clean and songs that were posted are never posted twice.
set -euo pipefail
[ -s state/state.json ] || { echo "No state to save"; exit 0; }
tmp=$(mktemp -d)
cp state/state.json "$tmp/"
cd "$tmp"
git init -q -b bot-state
git add state.json
git -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit -q -m "Radar state $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q --force "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" bot-state
rm -rf "$tmp"
