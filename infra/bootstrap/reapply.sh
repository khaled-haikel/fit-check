#!/usr/bin/env bash
# Re-applies infra/bootstrap from a branch, in AWS CloudShell, with one command:
# plan, print a filtered summary, ask, apply the saved plan only on "y", and
# always return to main afterwards.
#
# Usage: bash ~/fit-check/infra/bootstrap/reapply.sh <branch>
#
# Needs Terraform in ~/bin (README.md, step 3) and the git-ignored
# backend.hcl and terraform.tfvars in this directory.
set -euo pipefail

PLAN_FILE=""
LOG_FILE=""

back_to_main() {
  rm -f "$PLAN_FILE" "$LOG_FILE"
  echo "Returning to main."
  git switch --quiet main && git pull --quiet --ff-only
}

fail_with_log() {
  cat "$LOG_FILE" >&2
  exit 1
}

# Everything runs inside main, so bash has read the whole script before
# git switch can replace this file on disk.
main() {
  local branch="${1:?usage: reapply.sh <branch>}"
  local dir status answer

  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "$dir"

  export PATH="$HOME/bin:$PATH"
  export TF_PLUGIN_CACHE_DIR=/tmp/terraform-plugin-cache
  mkdir -p "$TF_PLUGIN_CACHE_DIR"

  if ! command -v terraform >/dev/null; then
    echo "terraform is not in ~/bin: install it first (README.md, step 3)." >&2
    exit 1
  fi
  if [[ -e backend_override.tf ]]; then
    echo "backend_override.tf exists: it would point Terraform at a local state. Remove it first." >&2
    exit 1
  fi
  for file in backend.hcl terraform.tfvars; do
    if [[ ! -f "$file" ]]; then
      echo "$file is missing: recreate it (README.md, Later runs)." >&2
      exit 1
    fi
  done

  PLAN_FILE="$dir/bootstrap.tfplan"
  LOG_FILE="$(mktemp)"
  trap back_to_main EXIT

  git fetch --quiet --prune origin
  git switch --quiet "$branch"
  git pull --quiet --ff-only
  echo "Planning infra/bootstrap at $(git log --oneline -1)"

  terraform init -input=false -backend-config=backend.hcl >"$LOG_FILE" 2>&1 || fail_with_log

  status=0
  terraform plan -input=false -detailed-exitcode -out="$PLAN_FILE" >"$LOG_FILE" 2>&1 || status=$?
  case "$status" in
    0)
      echo "No changes."
      return 0
      ;;
    2) ;;
    *) fail_with_log ;;
  esac

  # Changed resources, their +/-/~ lines and the Plan: line.
  terraform show -no-color "$PLAN_FILE" |
    grep -E '^[[:space:]]*(# .* (will|must) be |(-/\+|[-+~]) |Plan: )' || true

  read -r -p "Apply? (y/n) " answer
  if [[ "$answer" == "y" ]]; then
    terraform apply -input=false "$PLAN_FILE"
  else
    echo "Not applied."
  fi
}

main "$@"
