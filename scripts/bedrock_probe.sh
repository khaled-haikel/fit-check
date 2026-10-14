#!/usr/bin/env bash
# Bedrock probe, run by the administrator in AWS CloudShell (stage 2).
# Creates a throwaway venv under /tmp with pinned SDKs and runs
# scripts/bedrock_probe.py. Makes no writes to the AWS account.
#
# Usage: bash scripts/bedrock_probe.sh
set -euo pipefail

BOTO3_VERSION="1.43.113"
ANTHROPIC_VERSION="1.14.0"
VENV=/tmp/fitcheck-bedrock-probe

main() {
  local here python candidate
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  # Both SDKs need Python 3.10 or newer.
  python=""
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null &&
      "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
      python="$candidate"
      break
    fi
  done
  if [[ -z "$python" ]]; then
    # Installs into the session's system directories, which CloudShell
    # discards when the session ends; nothing is written to the account.
    echo "No Python 3.10+ found; installing python3.12 for this CloudShell session."
    sudo dnf install -y -q python3.12 python3.12-pip
    python=python3.12
  fi
  echo "Using $("$python" --version)"

  rm -rf "$VENV"
  "$python" -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --disable-pip-version-check \
    "boto3==$BOTO3_VERSION" "anthropic[bedrock]==$ANTHROPIC_VERSION"

  "$VENV/bin/python" "$here/bedrock_probe.py"
}

main "$@"
