"""Text embeddings through Bedrock InvokeModel (Titan Text Embeddings V2)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any


class Embedder:
    """Embeds text with the configured model, normalized, at a fixed dimension.

    Retries with exponential backoff come from botocore's standard retry mode.
    """

    def __init__(self, model_id: str, dimensions: int, client: Any = None, max_attempts: int = 3) -> None:
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client("bedrock-runtime", config=Config(retries={"mode": "standard", "max_attempts": max_attempts}))
        self.client = client
        self.model_id = model_id
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        response = self.client.invoke_model(
            modelId=self.model_id,
            body=json.dumps({"inputText": text, "dimensions": self.dimensions, "normalize": True}),
            contentType="application/json",
            accept="application/json",
        )
        vector = json.loads(response["body"].read())["embedding"]
        if len(vector) != self.dimensions:
            raise ValueError(f"{self.model_id} returned {len(vector)} dimensions, expected {self.dimensions}")
        return vector

    def embed_many(self, texts: Sequence[str], workers: int = 4) -> list[list[float]]:
        """Embeds in parallel (boto3 clients are thread-safe), keeping the order of texts."""
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(self.embed, texts))
