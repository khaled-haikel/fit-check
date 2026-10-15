"""The prompts and configuration the analysis reads.

Laid out the same way in the repository (CI) and in the Lambda package
(fitcheck_api/data, copied by scripts/build_lambda.py): base/prompts/ and
base/config/.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .costs import ModelPrice, load_prices
from .normalize import normalize_job_description

EXAMPLES_PLACEHOLDER = "{{examples}}"


@dataclass(frozen=True)
class Assets:
    analysis: dict[str, Any]
    generation_model: str
    embedding_model: str
    prices: dict[str, ModelPrice]
    extract_system: str
    judge_system: str

    def price(self, model: str) -> ModelPrice:
        if model not in self.prices:
            raise KeyError(f"no price for {model} in config/prices.json")
        return self.prices[model]


def render_examples(examples: Mapping[str, Any]) -> str:
    """Each few-shot case as the extraction would see it, with the JSON it should answer."""
    rendered = []
    for example in examples["examples"]:
        lines = ",\n".join(json.dumps(requirement, ensure_ascii=False) for requirement in example["requirements"])
        rendered.append(
            "<example>\n"
            f"<job_description>\n{normalize_job_description(example['job_description'])}\n</job_description>\n"
            f'<output>\n{{"requirements": [\n{lines}\n]}}\n</output>\n'
            "</example>"
        )
    return "\n".join(rendered)


def load_assets(base: Path) -> Assets:
    config = base / "config"
    prompts = base / "prompts"
    models = json.loads((config / "models.json").read_text(encoding="utf-8"))
    template = (prompts / "extract.md").read_text(encoding="utf-8")
    if EXAMPLES_PLACEHOLDER not in template:
        raise ValueError(f"prompts/extract.md has no {EXAMPLES_PLACEHOLDER} placeholder")
    examples = json.loads((prompts / "extract_examples.json").read_text(encoding="utf-8"))
    return Assets(
        analysis=json.loads((config / "analysis.json").read_text(encoding="utf-8")),
        generation_model=models["generation"]["model_id"],
        embedding_model=models["embedding"]["model_id"],
        prices=load_prices(json.loads((config / "prices.json").read_text(encoding="utf-8"))),
        extract_system=template.replace(EXAMPLES_PLACEHOLDER, render_examples(examples)).strip(),
        judge_system=(prompts / "judge.md").read_text(encoding="utf-8").strip(),
    )
