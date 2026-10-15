#!/usr/bin/env bash
# Smoke test shared by the deploy and rollback workflows. GET <api_url>/health
# must return ok=true and the expected commit. With "search", POST /search for
# a known query must also return one of the expected cards in its top 3.
# Retries each check for about a minute, because a new API, route or alias
# target can take a few seconds to serve.
#
# Usage: bash scripts/smoke_test.sh <api_url> <expected_commit> [search]
set -euo pipefail

usage="usage: smoke_test.sh <api_url> <expected_commit> [search]"
api_url="${1:?$usage}"
expected_commit="${2:?$usage}"
with_search="${3:-}"
if [[ -n "$with_search" && "$with_search" != "search" ]]; then
  echo "$usage" >&2
  exit 2
fi
attempts=12
delay_seconds=5

search_query="Terraform remote state and keyless GitHub OIDC deploys"
search_cards='["fitcheck-terraform-iac", "fitcheck-keyless-cicd"]'

# retry <description> <command...>: runs the command until it succeeds.
retry() {
  local description="$1"
  shift
  for attempt in $(seq 1 "$attempts"); do
    if "$@"; then
      return 0
    fi
    echo "Attempt $attempt/$attempts: $description did not pass"
    if ((attempt < attempts)); then
      sleep "$delay_seconds"
    fi
  done
  echo "Smoke test failed: $description" >&2
  return 1
}

check_health() {
  local body
  body="$(curl --silent --show-error --fail --max-time 10 "${api_url%/}/health")" || return 1
  if jq -e --arg commit "$expected_commit" '.ok == true and .commit == $commit' <<<"$body" >/dev/null 2>&1; then
    echo "GET /health -> $body"
    return 0
  fi
  echo "Unexpected response: $body"
  return 1
}

check_search() {
  local body request
  request="$(jq -cn --arg query "$search_query" '{query: $query, k: 3}')"
  body="$(curl --silent --show-error --fail --max-time 15 --request POST \
    --header 'content-type: application/json' --data "$request" "${api_url%/}/search")" || return 1
  if jq -e --argjson cards "$search_cards" '[.results[:3][].id] | any(.[]; IN($cards[]))' <<<"$body" >/dev/null 2>&1; then
    echo "POST /search \"$search_query\" -> top 3 $(jq -c '[.results[] | {id, score}]' <<<"$body"), index $(jq -r .index_version <<<"$body")"
    return 0
  fi
  echo "Unexpected response: $body"
  return 1
}

retry "GET /health with ok=true and commit $expected_commit" check_health
if [[ "$with_search" == "search" ]]; then
  retry "POST /search with $search_cards in the top 3" check_search
fi
echo "Smoke test passed."
