#!/usr/bin/env bash
# Wait for the CI run of the current commit, show the result, and download its debug APK to build/ci/.
#   tools/fetch-ci-apk.sh            the commit at HEAD
#   tools/fetch-ci-apk.sh <sha>      another pushed commit
set -euo pipefail
cd "$(dirname "$0")/.."
SHA="${1:-$(git rev-parse HEAD)}"

echo "Waiting for CI on ${SHA:0:7}…"
RUN=""
for _ in $(seq 1 60); do
  RUN=$(gh run list --commit "$SHA" --workflow CI --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null || true)
  [ -n "$RUN" ] && break
  sleep 5
done
[ -n "$RUN" ] || { echo "No CI run found for $SHA. Did you push?"; exit 1; }

if ! gh run watch "$RUN" --exit-status --interval 15 >/dev/null; then
  echo "CI failed: $(gh run view "$RUN" --json url -q .url)"
  gh run view "$RUN" --log-failed | tail -80
  exit 1
fi

rm -rf build/ci && mkdir -p build/ci
gh run download "$RUN" -n canvas-debug-apk -D build/ci
echo "CI passed. APK: build/ci/app-debug.apk ($(wc -c < build/ci/app-debug.apk) bytes)"
