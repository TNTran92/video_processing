"""Thin LLM client: one chat() for both models, over Ollama's
OpenAI-compatible endpoint.

No business logic here (no schema validation, no re-prompting — that's
pipeline.py). Just: build request, call HTTP, typed retries on
transient failures, surface the string content.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from config import LLMConfig, ModelsConfig


class LLMError(Exception):
    """Non-retryable failure (4xx, bad response shape)."""

    def __init__(self, message: str, *, code: str = "llm_error", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LLMTimeout(LLMError):
    def __init__(self, message: str = "LLM request timed out"):
        super().__init__(message, code="llm_timeout", retryable=True)


class LLMClient:
    def __init__(self, cfg: LLMConfig, models: ModelsConfig):
        self._cfg = cfg
        self._models = models
        # pseudocode: httpx.AsyncClient(base_url=models.base_url + "/v1",
        #   timeout=cfg.timeout_seconds, headers={"Authorization": ... if api_key})
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle ---------------------------------------------------------
    async def open(self) -> None:
        # pseudocode: create the AsyncClient
        ...

    async def close(self) -> None:
        # pseudocode: await self._client.aclose()
        ...

    # -- API ---------------------------------------------------------------
    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """One chat completion against {base_url}/v1/chat/completions.

        model:      models.video_model | models.summary_model (caller's choice)
        messages:   [{"role": "system"|"user", "content": "..."}]
        Returns the assistant message content as a string (NOT parsed).

        Retry policy (spec §6): up to cfg.max_retries on retryable
        failures (timeout, connection reset, 5xx), exponential backoff
        base cfg.retry_backoff_seconds. 4xx => immediate LLMError,
        never retried.
        """
        # pseudocode:
        payload = {"model": model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens}
        for attempt in range(cfg.max_retries + 1):
            try:
                resp = await self._client.post("/chat/completions", json=payload)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if attempt == cfg.max_retries:
                    raise LLMTimeout() from exc
                await asyncio.sleep(cfg.retry_backoff_seconds * (2 ** attempt))
                continue
            if resp.status_code == 200:
                data = resp.json()
                # pseudocode: validate shape, return data["choices"][0]["message"]["content"]
                ...
            if 400 <= resp.status_code < 500:
                raise LLMError(f"LLM 4xx: {resp.status_code} {resp.text[:200]}",
                               code="llm_http_4xx")  # NOT retryable
            # 5xx => retryable, fall through to backoff
            if attempt == cfg.max_retries:
                raise LLMError(f"LLM 5xx after retries: {resp.status_code}",
                               code="llm_http_5xx", retryable=True)
            await asyncio.sleep(cfg.retry_backoff_seconds * (2 ** attempt))
        raise LLMError("unreachable", retryable=True)

    async def ready(self) -> bool:
        """Used by /readyz: GET /api/tags (Ollama) or /v1/models with a
        short timeout; True iff reachable and responding."""
        # pseudocode: try/except -> True/False, 3s cap
        ...
