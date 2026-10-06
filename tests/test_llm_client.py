"""llm_client.py — transport mocked with respx (no network) (spec §7)."""

import pytest
import anyio

from llm_client import LLMClient, LLMError, LLMTimeout

BASE = "http://localhost:11434/v1"


def _client(cfg):
    return LLMClient(cfg.llm, cfg.models)


def test_chat_success_returns_content(cfg):
    # respx.post(f"{BASE}/chat/completions").respond(200, {
    #   "choices": [{"message": {"role": "assistant", "content": "hello"}}]})
    # content = await client.chat(model="m", messages=[{"role": "user", "content": "hi"}],
    #                             temperature=0.5, max_tokens=100)
    # assert content == "hello"
    ...


def test_chat_retries_on_500_then_succeeds(cfg):
    # two 500s then 200 -> success, 3 requests recorded
    ...


def test_chat_no_retry_on_400(cfg):
    # 400 -> LLMError with code llm_http_4xx, exactly 1 request
    ...


def test_chat_gives_up_after_retries(cfg):
    # all 500 (cfg.llm.max_retries=2) -> LLMError after exactly 3 attempts
    # (assert backoff calls == 2)
    ...


def test_chat_timeout_is_retryable(cfg):
    # httpx.TimeoutException on every attempt -> LLMTimeout, max_retries+1 attempts
    ...


def test_ready_true_when_tags_respond(cfg):
    # respx.get(f"{BASE}/../api/tags") style: GET /api/tags 200 -> True
    ...


def test_ready_false_when_unreachable(cfg):
    # connection refused -> False (no exception)
    ...
