"""A scripted stand-in for the bedrock-runtime client: Claude replies in order, Titan vectors by text."""

import io
import json
from collections.abc import Callable


def claude_payload(text: str | None, stop_reason: str = "end_turn", usage: dict | None = None, thinking: bool = False) -> dict:
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": "Working through it.", "signature": "sig"})
    if text is not None:
        content.append({"type": "text", "text": text})
    return {
        "content": content,
        "stop_reason": stop_reason,
        "usage": usage or {"input_tokens": 1000, "output_tokens": 500, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    }


class FakeBedrock:
    def __init__(self, replies: list[dict] | None = None, vectors: Callable[[str], list[float]] | None = None, dimensions: int = 3) -> None:
        self.replies = list(replies or [])
        self.vectors = vectors or (lambda text: [1.0, 0.0, 0.0][:dimensions])
        self.calls: list[dict] = []

    def invoke_model(self, modelId: str, body: str, contentType: str, accept: str) -> dict:
        request = json.loads(body)
        self.calls.append({"model": modelId, "body": request})
        if "inputText" in request:
            payload = {"embedding": self.vectors(request["inputText"]), "inputTextTokenCount": len(request["inputText"].split())}
        else:
            if not self.replies:
                raise AssertionError("no scripted Claude reply left")
            payload = self.replies.pop(0)
        return {
            "body": io.BytesIO(json.dumps(payload).encode("utf-8")),
            "ResponseMetadata": {"RequestId": f"req-{len(self.calls)}", "RetryAttempts": 0},
        }

    def claude_calls(self) -> list[dict]:
        return [call for call in self.calls if "inputText" not in call["body"]]
