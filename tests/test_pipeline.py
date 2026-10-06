"""pipeline.py — the core state-machine suite, fake LLM (spec §7).

fake_llm (conftest) scripts chat() responses per model, in order, and
records every call (model, messages) for assertions.
"""

import pytest

from pipeline import (
    CODE_NO_FRAMES, CODE_SUMMARY_INVALID, CODE_TIMEOUT,
    CODE_VIDEO_INVALID, Pipeline, validate_result,
)


def _run(pipeline, job_id, video_path, store):
    """Helper: create job in store, run pipeline.run(), return final record."""
    # pseudocode:
    rec = store.create(idempotency_key=None)
    store.start(rec.job_id)
    import anyio; anyio.run(pipeline.run, job_id=rec.job_id, video_path=video_path)
    return store.get(rec.job_id)


def test_happy_path_completes_with_five_fields(cfg, fake_llm, store, video_dir, tmp_path):
    # fake video model -> a plain-english description; fake summary model
    # -> the exact 5-field JSON. Run with a real fixture mp4.
    # assert record.status == COMPLETED and result has exactly the five keys,
    # all non-empty (confidence_note present here since stub supplies it)
    ...


def test_summary_non_json_then_valid_completes_after_reprompt(cfg, fake_llm, store, video_dir):
    # summary model: call 1 "sorry, here is..." (not JSON),
    #             call 2 valid JSON
    # assert COMPLETED, and the 2nd summary call's messages contain the
    # "not valid JSON" re-prompt
    ...


def test_summary_always_invalid_fails_with_typed_code(cfg, fake_llm, store, video_dir):
    # summary model: always garbage -> record FAILED,
    # error_code == CODE_SUMMARY_INVALID, attempts == cfg.llm.max_retries + 1
    ...


def test_video_model_error_maps_to_code(cfg, fake_llm, store, video_dir):
    # fake video model raises LLMError(code="llm_http_5xx")
    # -> FAILED, error_code in the closed set (video_llm_error)
    ...


def test_no_frames_fails_and_cleanup(cfg, store, tmp_path, video_dir):
    # point at a zero-byte .mp4 (or non-decodable fixture)
    # -> FAILED with code no_frames AND (cfg.scratch.root / job_id) is gone
    #    (cleanup contractual even on failure)
    ...


def test_timeout_budget_fails(cfg, fake_llm, store, video_dir):
    # cfg.jobs.max_runtime_seconds = 0 (or fake client sleeps)
    # -> worker-level wait_for raises -> FAILED code == timeout
    ...


def test_validate_result_rejects_extra_field(cfg):
    # valid dict + {"extra": 1} -> raise (exact key set)
    ...


def test_validate_result_rejects_missing_required(cfg):
    # omit "subjects" -> raise; omit "confidence_note" -> OK (only optional)
    ...


def test_validate_result_enforces_max_items_and_length(cfg):
    # actions with max_items+1 entries -> raise; summary over max_length -> raise
    ...
