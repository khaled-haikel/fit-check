"""Text embeddings through Bedrock InvokeModel (Titan Text Embeddings V2)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any


class Embedder:
    """Embeds text with the configured model, normalized, at a fixed dimension.

    Retries with exponential backoff come from botocore: "standard" mode for
    the Lambda's single calls, "adaptive" for CI's long runs, which also slows
    the client down when Bedrock throttles.
    """

    def __init__(
        self, model_id: str, dimensions: int, client: Any = None, max_attempts: int = 3, retry_mode: str = "standard"
    ) -> None:
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client("bedrock-runtime", config=Config(retries={"mode": retry_mode, "max_attempts": max_attempts}))
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

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        """One call per text, in order. Sequential on purpose: the account's Titan quota is low enough
        that parallel calls only trade time for throttling (ADR 0005)."""
        return [self.embed(text) for text in texts]
