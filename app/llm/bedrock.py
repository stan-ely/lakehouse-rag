"""Claude on Amazon Bedrock through the model-agnostic Converse API.

boto3 honours AWS_ENDPOINT_URL, so locally this talks to Floci's Bedrock Runtime stub, which
returns canned text with realistic usage: enough to exercise the provider, error handling and
cost accounting without an AWS account. On `bedrock-runtime`, current Claude models need a geo
or global inference profile id (e.g. `us.anthropic.claude-haiku-4-5-20251001-v1:0`).
"""

import time
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError

from app.llm.base import Completion, LLMError, LLMTimeout, Usage


class BedrockProvider:
    name = "bedrock"

    def __init__(
        self,
        model: str,
        *,
        region: str = "us-east-1",
        timeout_seconds: float = 30,
        client: Any = None,
    ) -> None:
        self._model = model
        config = Config(
            connect_timeout=5,
            read_timeout=timeout_seconds,
            retries={"max_attempts": 3, "mode": "adaptive"},
        )
        self.client = client or boto3.client("bedrock-runtime", region_name=region, config=config)

    @property
    def model(self) -> str:
        return self._model

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> Completion:
        start = time.perf_counter()
        try:
            response = self.client.converse(
                modelId=self._model,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": max_tokens},
            )
        except (ReadTimeoutError, ConnectTimeoutError) as exc:
            raise LLMTimeout(str(exc)) from exc
        except (ClientError, BotoCoreError) as exc:
            raise LLMError(str(exc)) from exc

        content = response.get("output", {}).get("message", {}).get("content", [])
        usage = response.get("usage", {})
        return Completion(
            text="".join(block.get("text", "") for block in content),
            model=self._model,
            provider=self.name,
            usage=Usage(
                input_tokens=int(usage.get("inputTokens", 0)),
                output_tokens=int(usage.get("outputTokens", 0)),
                cache_read_tokens=int(usage.get("cacheReadInputTokens", 0)),
                cache_write_tokens=int(usage.get("cacheWriteInputTokens", 0)),
            ),
            stop_reason=response.get("stopReason"),
            latency_ms=(time.perf_counter() - start) * 1000,
        )
