#!/usr/bin/env bash
# Smoke test shared by the deploy and rollback workflows: GET <api_url>/health
# must return ok=true and the expected commit. Retries for about a minute,
# because a new API, route or alias target can take a few seconds to serve.
#
# Usage: bash scripts/smoke_test.sh <api_url> <expected_commit>
set -euo pipefail

api_url="${1:?usage: smoke_test.sh <api_url> <expected_commit>}"
expected_commit="${2:?usage: smoke_test.sh <api_url> <expected_commit>}"
health_url="${api_url%/}/health"
attempts=12
delay_seconds=5

for attempt in $(seq 1 "$attempts"); do
  if body="$(curl --silent --show-error --fail --max-time 10 "$health_url")"; then
    if jq -e --arg commit "$expected_commit" '.ok == true and .commit == $commit' <<<"$body" >/dev/null 2>&1; then
      echo "Smoke test passed: GET $health_url -> $body"
      exit 0
    fi
    echo "Attempt $attempt/$attempts: unexpected response: $body"
  else
    echo "Attempt $attempt/$attempts: request failed"
  fi
  if (( attempt < attempts )); then
    sleep "$delay_seconds"
  fi
done

echo "Smoke test failed: GET $health_url never returned ok=true with commit $expected_commit" >&2
exit 1
