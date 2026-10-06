"""Load and validate runtime configuration.

Source of truth: config.yaml (JSON also accepted) pointed to by the
APP_CONFIG env var (default: ./config.yaml relative to CWD; the Docker
image sets it to /config.yaml, mounted read-only).

Rules (spec §5):
- Every tunable lives in the config file; this module holds only
  structural scaffolding.
- `${ENV_VAR}` interpolation: values like "${OPENAI_API_KEY}" resolve
  against the process environment so secrets never sit in the file.
- Invalid config => load_config() raises ConfigError => app startup
  fails loudly at boot, never mid-request.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception):
    """Raised for any invalid config; surfaced as a boot failure."""


def _interpolate(value: str) -> str:
    # pseudocode: replace each ${NAME} with os.environ[NAME];
    # missing/empty var -> ConfigError (never silently empty secret)
    def subs(m: re.Match) -> str:
        env = os.environ.get(m.group(1))
        if env is None:
            raise ConfigError(f"config references ${{{m.group(1)}}} but env var is not set")
        return env
    return _ENV_REF.sub(subs, value)


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    max_queue_size: int = Field(default=16, ge=1)
    max_parallel_jobs: int = Field(default=1, ge=1)  # GPU-bound: keep at 1


class PathsConfig(BaseModel):
    host_prefix: str        # what callers send, e.g. "/data/videos"
    container_prefix: str   # what the API opens, e.g. "/srv/videos"
    allowed_extensions: list[str]
    max_file_size_mb: int = Field(default=2048, gt=0)

    @field_validator("host_prefix", "container_prefix")
    @classmethod
    def _absolute(cls, v: str) -> str:
        if not os.path.isabs(v):
            raise ConfigError(f"prefix must be an absolute path, got {v!r}")
        return v

    @field_validator("allowed_extensions")
    @classmethod
    def _dot_exts(cls, v: list[str]) -> list[str]:
        if not v or any(not e.startswith(".") for e in v):
            raise ConfigError("allowed_extensions must be non-empty, each starting with '.'")
        return [e.lower() for e in v]


class JobsConfig(BaseModel):
    ttl_seconds: int = Field(default=86400, gt=0)
    idempotency_window_seconds: int = Field(default=600, ge=0)
    max_runtime_seconds: int = Field(default=900, gt=0)


class FramesConfig(BaseModel):
    fps_sample_rate: float = Field(default=1.0, gt=0)
    max_frames: int = Field(default=900, gt=0)
    frame_format: Literal["jpg", "png", "webp"] = "jpg"


class ModelsConfig(BaseModel):
    base_url: str
    video_model: str
    summary_model: str

    @field_validator("base_url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        # pseudocode: http(s):// check (pydantic AnyHttpUrl semantics)
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ConfigError(f"models.base_url must be http(s) URL, got {v!r}")
        return v.rstrip("/")

    @field_validator("video_model", "summary_model")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ConfigError("model name must be non-empty")
        return v


class LLMConfig(BaseModel):
    api_key: str | None = None  # "${OPENAI_API_KEY}" style; interpolated, None if unset
    timeout_seconds: int = Field(default=300, gt=0)
    max_retries: int = Field(default=2, ge=0)
    retry_backoff_seconds: float = Field(default=3.0, ge=0)
    temperature_video: float = Field(default=0.5, ge=0, le=2)
    temperature_summary: float = Field(default=0.2, ge=0, le=2)
    max_tokens_video: int = Field(default=4096, gt=0)
    max_tokens_summary: int = Field(default=1500, gt=0)


class PromptingConfig(BaseModel):
    video_system: str
    video_user_suffix: str
    summary_system: str
    summary_user: str  # must contain "{description}"


class OutputFieldConfig(BaseModel):
    type: Literal["string", "array"]
    item: Literal["string"] | None = None  # required when type == "array"
    max_length: int | None = None          # for string
    max_items: int | None = None           # for array
    optional: bool = False

    @model_validator(mode="after")
    def _item_rules(self) -> "OutputFieldConfig":
        if self.type == "array" and self.item != "string":
            raise ConfigError("array fields must have item: 'string'")
        return self


class OutputSchemaConfig(BaseModel):
    fields: dict[str, OutputFieldConfig]

    @model_validator(mode="after")
    def _required_fields(self) -> "OutputSchemaConfig":
        # spec §5: exactly these five; confidence_note is the only optional one
        expected = {"title", "summary", "actions", "subjects", "confidence_note"}
        if set(self.fields) != expected:
            raise ConfigError(f"output_schema.fields must be exactly {sorted(expected)}")
        opts = [n for n, f in self.fields.items() if f.optional]
        if set(opts) != {"confidence_note"}:
            raise ConfigError("only confidence_note may be optional")
        return self


class ScratchConfig(BaseModel):
    root: str = "/tmp/video_processing"
    startup_sweep: bool = True


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: Literal["json", "text"] = "json"


class AppConfig(BaseModel):
    server: ServerConfig
    paths: PathsConfig
    jobs: JobsConfig
    frames: FramesConfig
    models: ModelsConfig
    llm: LLMConfig
    prompting: PromptingConfig
    output_schema: OutputSchemaConfig
    scratch: ScratchConfig
    logging: LoggingConfig


def load_config(path: str | os.PathLike | None = None) -> AppConfig:
    """Load, interpolate secrets, and validate the config file.

    Raises ConfigError on anything invalid — the caller (main.py) treats
    this as a fatal startup error.
    """
    # pseudocode:
    p = Path(path or os.environ.get("APP_CONFIG", "config.yaml"))
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    text = p.read_text()
    raw = json.loads(text) if p.suffix == ".json" else yaml.safe_load(text)
    raw = _deep_interpolate(raw)          # recursive ${ENV} over all strings
    try:
        return AppConfig.model_validate(raw)
    except Exception as exc:               # pydantic ValidationError -> ConfigError
        raise ConfigError(f"invalid config: {exc}") from exc


def _deep_interpolate(node):
    # pseudocode: recurse dicts/lists; str -> _interpolate(s); else pass through
    ...


def rewrite_path(cfg: PathsConfig, host_path: str) -> Path:
    """Map a caller-supplied host path into the container path.

    Contract (spec §4):
    - host_path must live UNDER cfg.host_prefix  -> else PathOutsideRoot
    - returns Path(cfg.container_prefix) / relative part
    Also used to reject traversal (..), symlinks escaping the root.
    """
    # pseudocode:
    host = Path(host_path).resolve()
    prefix = Path(cfg.host_prefix).resolve()
    if host != prefix and prefix not in host.parents:
        raise PathOutsideRoot(host_path)
    rel = host.relative_to(prefix)
    return Path(cfg.container_prefix) / rel


class PathOutsideRoot(ValueError):
    """400 path_outside_allowed_root"""
