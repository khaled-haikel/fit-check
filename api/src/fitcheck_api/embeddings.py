"""Text embeddings through Bedrock InvokeModel (Titan Text Embeddings V2)."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Embedding:
    vector: list[float]
    input_tokens: int
    request_id: str
    retry_attempts: int
    latency_ms: float
    # Set by the evaluation's response cache; always False against Bedrock.
    cached: bool


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

    def request_body(self, text: str) -> str:
        return json.dumps({"inputText": text, "dimensions": self.dimensions, "normalize": True})

    def embed_detailed(self, text: str) -> Embedding:
        """The vector with the token count and call metadata the analysis logs and prices."""
        started = time.perf_counter()
        response = self.client.invoke_model(
            modelId=self.model_id,
            body=self.request_body(text),
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(response["body"].read())
        elapsed_ms = (time.perf_counter() - started) * 1000
        vector = payload["embedding"]
        if len(vector) != self.dimensions:
            raise ValueError(f"{self.model_id} returned {len(vector)} dimensions, expected {self.dimensions}")
        meta = response.get("ResponseMetadata", {})
        return Embedding(
            vector=vector,
            input_tokens=int(payload.get("inputTextTokenCount") or 0),
            request_id=str(meta.get("RequestId", "")),
            retry_attempts=int(meta.get("RetryAttempts", 0)),
            latency_ms=float(meta.get("FitcheckLatencyMs", elapsed_ms)),
            cached=bool(meta.get("FitcheckCached", False)),
        )

    def embed(self, text: str) -> list[float]:
        return self.embed_detailed(text).vector

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        """One call per text, in order. Sequential on purpose: the account's Titan quota is low enough
        that parallel calls only trade time for throttling (ADR 0005)."""
        return [self.embed(text) for text in texts]
