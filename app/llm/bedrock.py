"""Claude on Amazon Bedrock through the model-agnostic Converse API.

The endpoint is chosen explicitly rather than inherited: the local shell points every AWS SDK
at Floci (AWS_ENDPOINT_URL), whose Bedrock Runtime stub returns canned text with realistic
usage. That is right for tests and wrong for an evaluation run, which must reach real Bedrock
while S3 and SQS stay local. `endpoint_url=None` therefore ignores the ambient endpoint
configuration; pass Floci's URL to opt back in.

On `bedrock-runtime`, current Claude models need a geo or global inference profile id
(e.g. `us.anthropic.claude-sonnet-4-5-20250929-v1:0`).
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
        endpoint_url: str | None = None,
        client: Any = None,
    ) -> None:
        self._model = model
        options: dict[str, Any] = {
            "connect_timeout": 5,
            "read_timeout": timeout_seconds,
            "retries": {"max_attempts": 3, "mode": "adaptive"},
            # Newer than the shipped botocore type stubs, hence the untyped mapping.
            "ignore_configured_endpoint_urls": endpoint_url is None,
        }
        config = Config(**options)
        self.client = client or boto3.client(
            "bedrock-runtime", region_name=region, endpoint_url=endpoint_url, config=config
        )

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
