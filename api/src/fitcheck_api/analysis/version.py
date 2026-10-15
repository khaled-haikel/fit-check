"""analysis_version: everything an analysis result depends on, as one sha256.

It covers the prompts, config/analysis.json, the generation model, the search
index and the code of this package. scripts/build_lambda.py stamps it into the
Lambda package and evals/analysis.py computes it the same way. Standard
library only, so the build can import it without the API's dependencies.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

PROMPT_FILES = ("extract.md", "extract_examples.json", "judge.md")
CODE_DIR = Path(__file__).resolve().parent


def _digest(data: bytes) -> str:
    # Line endings are unified, so a Windows checkout and the Linux runner agree.
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def code_files(directory: Path = CODE_DIR) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.py"))}


def analysis_version(
    prompts: Mapping[str, bytes],
    analysis_config: bytes,
    generation_model: str,
    index_version: str,
    code: Mapping[str, bytes],
) -> str:
    manifest = {
        "prompts": {name: _digest(data) for name, data in sorted(prompts.items())},
        "analysis_config": _digest(analysis_config),
        "generation_model": generation_model,
        "index_version": index_version,
        "code": {name: _digest(data) for name, data in sorted(code.items())},
    }
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def analysis_version_from(base: Path, generation_model: str, index_version: str, code_dir: Path = CODE_DIR) -> str:
    """The version from files laid out as in the repository: base/prompts/* and base/config/analysis.json."""
    prompts = {name: (base / "prompts" / name).read_bytes() for name in PROMPT_FILES}
    config = (base / "config" / "analysis.json").read_bytes()
    return analysis_version(prompts, config, generation_model, index_version, code_files(code_dir))
