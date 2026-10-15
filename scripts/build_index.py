"""Builds the search index: validates the evidence cards and embeds each one.

The embedding model and dimensions come from config/models.json. The output
index.json holds index_version (sha256 of the canonical cards, the model and
the dimensions), the model, the dimensions, the cards and their vectors, and
no date, so the same input gives the same file. Needs AWS credentials that
may invoke the model (fitcheck-ci-eval in CI).

Usage: python scripts/build_index.py [--output build/index.json]
"""

import argparse
import json
import sys
from pathlib import Path

from fitcheck_api.content import card_records, load_content
from fitcheck_api.embeddings import Embedder
from fitcheck_api.search import card_text, index_version

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build" / "index.json"
# Rounding keeps the file small; at this precision cosine scores move by less
# than 1e-5.
VECTOR_DECIMALS = 6


def embedding_config() -> tuple[str, int]:
    config = json.loads((ROOT / "config" / "models.json").read_text(encoding="utf-8"))["embedding"]
    return config["model_id"], int(config["dimensions"])


def write_index(path: Path, index: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    content = load_content(ROOT / "evidence")
    if content.errors:
        for error in content.errors:
            print(f"error: {error}", file=sys.stderr)
        sys.exit(1)

    model_id, dimensions = embedding_config()
    embedder = Embedder(model_id, dimensions, max_attempts=8)
    cards = card_records(content)
    vectors = [[round(value, VECTOR_DECIMALS) for value in embedder.embed(card_text(card))] for card in cards]
    index = {
        "index_version": index_version(cards, model_id, dimensions),
        "model": model_id,
        "dimensions": dimensions,
        "placeholder": False,
        "cards": cards,
        "vectors": vectors,
    }
    write_index(args.output, index)
    print(f"{args.output}: {len(cards)} cards, {args.output.stat().st_size} bytes, index_version {index['index_version']}")


if __name__ == "__main__":
    main()
