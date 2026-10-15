"""Builds the search index: validates the evidence cards and embeds their chunks.

The embedding model and dimensions come from config/models.json, the retrieval
settings the Lambda serves from config/retrieval.json. Each card gives one
chunk for the whole card, one for title and summary, and one per claim
(fitcheck_api.search.card_chunks). The index keeps every chunk's text (BM25
reads the claims) but embeds only the kinds the settings rank with; the
evaluation embeds the rest for its other configurations. The output
index.json holds the cards, the chunks, their vectors and index_version, a
sha256 of everything but the vectors, and no date, so the same input gives the
same file. Needs AWS credentials that may invoke the model (fitcheck-ci-eval
in CI).

Usage: python scripts/build_index.py [--output build/index.json]
"""

import argparse
import json
import sys
from pathlib import Path

from fitcheck_api.content import card_records, load_content
from fitcheck_api.embeddings import Embedder
from fitcheck_api.search import RetrievalConfig, index_document, index_version, stored_vector

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build" / "index.json"


def embedding_config() -> tuple[str, int]:
    config = json.loads((ROOT / "config" / "models.json").read_text(encoding="utf-8"))["embedding"]
    return config["model_id"], int(config["dimensions"])


def retrieval_config() -> RetrievalConfig:
    path = ROOT / "config" / "retrieval.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    config = RetrievalConfig.from_dict(data)
    if config.to_dict() != data:
        sys.exit(f"{path} must list every setting: {json.dumps(config.to_dict())}")
    return config


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
    document = index_document(card_records(content), model_id, dimensions, retrieval_config())
    embedder = Embedder(model_id, dimensions, max_attempts=10, retry_mode="adaptive")
    embedded = [chunk for chunk in document["chunks"] if chunk["kind"] in document["embedded"]]
    vectors = embedder.embed_many([chunk["text"] for chunk in embedded])
    index = {
        "index_version": index_version(document),
        "placeholder": False,
        **document,
        "vectors": [stored_vector(vector) for vector in vectors],
    }
    write_index(args.output, index)
    print(
        f"{args.output}: {len(document['cards'])} cards, {len(document['chunks'])} chunks, {len(vectors)} vectors "
        f"({', '.join(document['embedded'])}), {args.output.stat().st_size} bytes, index_version {index['index_version']}"
    )


if __name__ == "__main__":
    main()
