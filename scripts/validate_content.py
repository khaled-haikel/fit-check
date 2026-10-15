"""Validates the evidence cards and the evaluation cases, without AWS.

Checks the schemas (api/src/fitcheck_api/evidence.py and cases.py), unique IDs,
file names equal to IDs, cited cards and derived_from cases that exist, https
URLs and empty fields. Prints every problem with its file and exits non-zero
if there is any.

Usage: python scripts/validate_content.py
"""

import sys
from pathlib import Path

from fitcheck_api.content import load_content

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    content = load_content(ROOT / "evidence", ROOT / "evals" / "cases")
    for error in content.errors:
        print(f"error: {error}")
    print(f"{len(content.cards)} cards and {len(content.cases)} cases checked, {len(content.errors)} problems.")
    sys.exit(1 if content.errors else 0)


if __name__ == "__main__":
    main()
