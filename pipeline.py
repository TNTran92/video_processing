"""Pipeline — the per-job state machine.

Stages (spec §3): extracting -> analyzing -> summarizing -> completed
                                                           \\-> failed
Every LLM call goes through llm_client.chat(); both models use it.
The five output fields are DATA: they come from
cfg.output_schema.fields, and the prompt + validator are generated
from that same list (spec §5) — so API contract, prompt, and
validator can't drift apart.

No globals, no state — the pipeline is a stateless service object that
receives (job_id, video_path) and drives the store.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import AppConfig, OutputFieldConfig
from frames import FrameExtractionError, FramesExtractor
from job_store import JobStore, JobStatus
from llm_client import LLMClient, LLMError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors -> job failure codes (spec §4, closed set)
# ---------------------------------------------------------------------------
class PipelineError(Exception):
    """Code is one of the closed failure codes; message is caller-safe."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# closed failure codes (single source of truth; job_store / main just pass through)
CODE_NO_FRAMES = "no_frames"
CODE_VIDEO_LLM_ERROR = "video_llm_error"
CODE_VIDEO_INVALID = "video_llm_invalid_output"
CODE_SUMMARY_LLM_ERROR = "summary_llm_error"
CODE_SUMMARY_INVALID = "summary_invalid_output"
CODE_TIMEOUT = "timeout"
CODE_INTERNAL = "internal_error"


# ---------------------------------------------------------------------------
# Schema-driven prompt + validation (generated from config, not hardcoded)
# ---------------------------------------------------------------------------
def _schema_block(fields: dict[str, OutputFieldConfig]) -> str:
    """Render the field list as a short JSON-schema-shaped string for the
    prompt, e.g.
      {"title": "string <= 120 chars",
       "summary": "string <= 600 chars",
       "actions": ["string", ...]  (<= 25),
       "subjects": ["string", ...] (<= 20),
       "confidence_note": "string <= 200 chars (optional)"}
    """
    # pseudocode: iterate fields, emit one line each, using max_length/max_items
    ...


def validate_result(result: Any, fields: dict[str, OutputFieldConfig]) -> dict[str, str]:
    """Full all-or-nothing validation against the configured schema.

    Returns the validated dict. Raises PipelineError(CODE_SUMMARY_INVALID |
    ...) on any mismatch. Rules (from spec §5):
      - required fields present (confidence_note optional, else all required)
      - string fields: str, len <= max_length
      - array fields: list[str], len <= max_items
      - exact key set (no extra fields, no missing required)
    """
    # pseudocode: walk the dict, type-check each field by the config
    ...


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
@dataclass
class Pipeline:
    cfg: AppConfig
    client: LLMClient
    frames: FramesExtractor
    store: JobStore

    async def run(self, *, job_id: str, video_path: Path) -> None:
        """Drive one job to a terminal state. ALWAYS ends with either
        complete() or fail() in the store — never a silent no-op.
        Scratch cleanup is contractual (finally).
        """
        scratch = Path(self.cfg.scratch.root) / job_id
        try:
            # 1) EXTRACTING
            self.store.set_stage(job_id, JobStatus.EXTRACTING)
            try:
                frame_paths = self.frames.extract(video_path, scratch / "frames")
            except FrameExtractionError as exc:
                raise PipelineError(CODE_NO_FRAMES, str(exc)) from exc
            if not frame_paths:
                raise PipelineError(CODE_NO_FRAMES, "ffmpeg produced no frames")
            log.info("job=%s stage=extracting done frames=%d", job_id, len(frame_paths))

            # 2) ANALYZING (vision model over frames)
            self.store.set_stage(job_id, JobStatus.ANALYZING)
            description = await self._video_llm(job_id, frame_paths)
            log.info("job=%s stage=analyzing done desc_len=%d", job_id, len(description))

            # 3) SUMMARIZING (text model over description)
            self.store.set_stage(job_id, JobStatus.SUMMARIZING)
            result = await self._summary_llm(job_id, description)
            log.info("job=%s stage=summarizing done", job_id)

        except PipelineError as exc:
            self.store.fail(job_id, code=exc.code, message=exc.message)
            return
        except LLMError as exc:
            code = CODE_VIDEO_LLM_ERROR if exc.code.startswith(("llm_")) else CODE_INTERNAL
            # NOTE: caller distinguishes by stage; here we're generic — refine to
            # CODE_VIDEO_LLM_ERROR vs CODE_SUMMARY_LLM_ERROR by which _llm call raised.
            self.store.fail(job_id, code=code, message=str(exc))
            return
        except Exception as exc:  # defensive: unknown failure
            log.exception("job=%s unexpected error", job_id)
            self.store.fail(job_id, code=CODE_INTERNAL, message=str(exc))
            return

        self.store.complete(job_id, result)

    async def _video_llm(self, job_id: str, frame_paths: list[Path]) -> str:
        """Send frames to the vision model, retrying with a re-prompt on
        non-JSON. Returns the assistant content (string) — NOT parsed
        (the description is free-form per the sample).
        """
        # pseudocode:
        #   messages = [
        #     {"role": "system", "content": cfg.prompting.video_system
        #              + "\n\n" + _schema_block(cfg.output_schema.fields)},
        #     {"role": "user", "content":
        #         "(frame sequence from " + str(len(frame_paths)) +
        #         " frames)\n" + cfg.prompting.video_user_suffix},
        #   ]
        #   for attempt in range(cfg.llm.max_retries + 1):
        #       content = await client.chat(model=cfg.models.video_model,
        #                                    messages=messages,
        #                                    temperature=cfg.llm.temperature_video,
        #                                    max_tokens=cfg.llm.max_tokens_video)
        #       if _looks_like_json(content): return content
        #       messages.append({"role": "assistant", "content": content})
        #       messages.append({"role": "user", "content":
        #           "Your last reply was not valid JSON. Respond only with JSON "
        #           "matching the schema."})
        #   raise PipelineError(CODE_VIDEO_INVALID,
        #                       "video model produced no parseable JSON after retries")
        raise NotImplementedError  # pseudocode placeholder

    async def _summary_llm(self, job_id: str, description: str) -> dict[str, Any]:
        """Send description to the summary model, retry with re-prompt on
        non-JSON / invalid schema, return the validated dict.
        """
        # pseudocode:
        #   messages = [
        #     {"role": "system", "content": cfg.prompting.summary_system},
        #     {"role": "user", "content":
        #         cfg.prompting.summary_user.replace("{description}", description)
        #         + "\n\n" + _schema_block(cfg.output_schema.fields)},
        #   ]
        #   for attempt in range(cfg.llm.max_retries + 1):
        #       content = await client.chat(model=cfg.models.summary_model,
        #                                    messages=messages,
        #                                    temperature=cfg.llm.temperature_summary,
        #                                    max_tokens=cfg.llm.max_tokens_summary)
        #       try:
        #           parsed = json.loads(content)
        #           return validate_result(parsed, cfg.output_schema.fields)
        #       except (json.JSONDecodeError, PipelineError):
        #           messages.append({"role": "assistant", "content": content})
        #           messages.append({"role": "user", "content":
        #               "Your last reply was not valid JSON. Respond only with JSON "
        #               "matching the schema."})
        #   raise PipelineError(CODE_SUMMARY_INVALID,
        #                       "summary model produced no valid result after retries")
        raise NotImplementedError  # pseudocode placeholder


def _looks_like_json(s: str) -> bool:
    """Cheap pre-check before a full json.loads: starts with { or [ and
    ends with } or ] (after strip). Used to decide re-prompt, not to
    parse — the summary path always does a full parse+validate."""
    # pseudocode
    ...


def cleanup_job_scratch(cfg: AppConfig, job_id: str) -> None:
    """rmtree <scratch.root>/<job_id>. Called in pipeline.run()'s
    finally block. Idempotent: ignore if already gone."""
    # pseudocode: shutil.rmtree(Path(cfg.scratch.root) / job_id, ignore_errors=True)
    ...
