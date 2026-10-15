#!/usr/bin/env bash
# Smoke test shared by the deploy and rollback workflows. GET <api_url>/health
# must return ok=true and the expected commit. Optional checks:
# - search: POST /search for a known query returns one of the expected cards
#   in its top 3.
# - checks: POST /checks with {} is a 400, GET of an unknown ID is a 404, and
#   the canary job description (scripts/smoke_check_jd.txt) reaches done
#   within 240 s with at least 3 requirements, all judged, and at least one
#   meets with a citation. A 429 (daily_limit, monthly_budget or busy) only
#   warns. The canary costs Bedrock calls only when analysis_version changes;
#   otherwise its result comes back from the cache.
# Retries the first checks for about a minute, because a new API, route or
# alias target can take a few seconds to serve.
#
# Usage: bash scripts/smoke_test.sh <api_url> <expected_commit> [search] [checks]
set -euo pipefail

usage="usage: smoke_test.sh <api_url> <expected_commit> [search] [checks]"
api_url="${1:?$usage}"
api_url="${api_url%/}"
expected_commit="${2:?$usage}"
shift 2
for check in "$@"; do
  if [[ "$check" != "search" && "$check" != "checks" ]]; then
    echo "$usage" >&2
    exit 2
  fi
done
attempts=12
delay_seconds=5
canary_timeout_seconds=240
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
response_file="$(mktemp)"
trap 'rm -f "$response_file"' EXIT

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

# request <method> <path> [json body]: prints the HTTP status, leaves the body in $response_file.
request() {
  local method="$1" path="$2" data="${3:-}"
  local arguments=(--silent --show-error --max-time 15 --output "$response_file" --write-out '%{http_code}' --request "$method")
  if [[ -n "$data" ]]; then
    arguments+=(--header 'content-type: application/json' --data "$data")
  fi
  curl "${arguments[@]}" "$api_url$path" || echo "000"
}

check_health() {
  local body
  body="$(curl --silent --show-error --fail --max-time 10 "$api_url/health")" || return 1
  if jq -e --arg commit "$expected_commit" '.ok == true and .commit == $commit' <<<"$body" >/dev/null 2>&1; then
    echo "GET /health -> $body"
    return 0
  fi
  echo "Unexpected response: $body"
  return 1
}

check_search() {
  local body payload
  payload="$(jq -cn --arg query "$search_query" '{query: $query, k: 3}')"
  body="$(curl --silent --show-error --fail --max-time 15 --request POST \
    --header 'content-type: application/json' --data "$payload" "$api_url/search")" || return 1
  if jq -e --argjson cards "$search_cards" '[.results[:3][].id] | any(.[]; IN($cards[]))' <<<"$body" >/dev/null 2>&1; then
    echo "POST /search \"$search_query\" -> top 3 $(jq -c '[.results[] | {id, score}]' <<<"$body"), index $(jq -r .index_version <<<"$body")"
    return 0
  fi
  echo "Unexpected response: $body"
  return 1
}

check_checks_validation() {
  local status
  status="$(request POST /checks '{}')"
  if [[ "$status" != "400" ]]; then
    echo "POST /checks {} returned $status, expected 400"
    return 1
  fi
  status="$(request GET /checks/aaaaaaaaaaaaaaaaaaaaaaaaaa)"
  if [[ "$status" != "404" ]]; then
    echo "GET /checks/<unknown id> returned $status, expected 404"
    return 1
  fi
  echo "POST /checks {} -> 400; GET /checks/<unknown id> -> 404"
}

# Sets check_id, or returns 2 for a 429 (a warning, not a failure).
start_canary() {
  local status payload
  payload="$(jq -Rs '{job_description: .}' "$script_dir/smoke_check_jd.txt")"
  status="$(request POST /checks "$payload")"
  case "$status" in
    200 | 202)
      check_id="$(jq -r .check_id "$response_file")"
      echo "POST /checks (canary) -> $status $(jq -c . "$response_file")"
      ;;
    429)
      echo "warning: POST /checks (canary) -> 429 $(jq -r .error "$response_file"); the canary is skipped"
      return 2
      ;;
    *)
      echo "POST /checks (canary) returned $status: $(cat "$response_file")"
      return 1
      ;;
  esac
}

check_canary() {
  local started status body
  check_id=""
  local outcome=0
  start_canary || outcome=$?
  if ((outcome == 2)); then
    return 0
  fi
  if ((outcome != 0)); then
    retry "POST /checks (canary)" start_canary
  fi
  started="$(date +%s)"
  while (($(date +%s) - started < canary_timeout_seconds)); do
    status="$(request GET "/checks/$check_id")"
    if [[ "$status" != "200" ]]; then
      echo "GET /checks/$check_id returned $status"
      sleep "$delay_seconds"
      continue
    fi
    body="$(cat "$response_file")"
    case "$(jq -r .status <<<"$body")" in
      done)
        echo "Canary $check_id: done; verdicts $(jq -c '[.requirements[] | "\(.id) \(.verdict)"]' <<<"$body"); cost $(jq -r .usage.cost_usd <<<"$body") USD; latency $(jq -r .usage.latency_ms <<<"$body") ms"
        if jq -e '(.requirements | length) >= 3 and all(.requirements[]; .verdict != null) and any(.requirements[]; .verdict == "meets" and (.citations | length) > 0)' <<<"$body" >/dev/null; then
          return 0
        fi
        echo "Smoke test failed: the canary needs at least 3 requirements, all judged, and a meets with a citation" >&2
        return 1
        ;;
      failed)
        echo "Smoke test failed: the canary failed with $(jq -r .error <<<"$body")" >&2
        return 1
        ;;
      *)
        echo "Canary $check_id: $(jq -r .status <<<"$body")"
        sleep "$delay_seconds"
        ;;
    esac
  done
  echo "Smoke test failed: the canary did not finish within $canary_timeout_seconds s" >&2
  return 1
}

retry "GET /health with ok=true and commit $expected_commit" check_health
for check in "$@"; do
  case "$check" in
    search) retry "POST /search with $search_cards in the top 3" check_search ;;
    checks)
      retry "POST /checks validation and GET /checks 404" check_checks_validation
      check_canary
      ;;
  esac
done
echo "Smoke test passed."
