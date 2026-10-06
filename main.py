"""FastAPI app: routes, worker pool, startup/shutdown.

Routes (spec §4):
  POST /api/jobs          submit -> 202 job_id (400/404/413/415/422/429)
  GET  /api/jobs/{job_id} status -> 200 (404 unknown/expired)
  GET  /healthz           liveness, no external calls
  GET  /readyz            probes Ollama reachability
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from config import (
    AppConfig,
    ConfigError,
    PathOutsideRoot,
    PathsConfig,
    load_config,
    rewrite_path,
)
from frames import FramesExtractor
from job_store import JobStore, QueueFull
from pipeline import Pipeline, cleanup_job_scratch
from llm_client import LLMClient

log = logging.getLogger("video_processing")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class JobSubmit(BaseModel):
    path: str

    @field_validator("path")
    @classmethod
    def _abs_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path must be non-empty")
        return v


def _err(code: str, message: str, *, details: dict[str, Any] | None = None,
         status: int) -> JSONResponse:
    """Standard error envelope (spec §4)."""
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # -- startup: fail loud on anything invalid ----------------------------
    try:
        cfg: AppConfig = load_config()  # APP_CONFIG env or ./config.yaml
    except ConfigError as exc:
        log.critical("config invalid, refusing to start: %s", exc)
        raise SystemExit(2) from exc

    logging.basicConfig(level=cfg.logging.level)
    # pseudocode: when cfg.logging.format == "json", install a dict JSON
    # formatter on the root logger (a small structlog-free JSONFormatter)

    client = LLMClient(cfg.llm, cfg.models)
    await client.open()

    extractor = FramesExtractor(cfg.frames)
    extractor.check_available()  # ffmpeg present, else boot failure

    store = JobStore(ttl_seconds=cfg.jobs.ttl_seconds,
                     idempotency_window_seconds=cfg.jobs.idempotency_window_seconds,
                     max_queue_size=cfg.server.max_queue_size)

    pipeline = Pipeline(cfg=cfg, client=client, frames=extractor, store=store)

    # scratch dir + startup sweep (safe: namespaced by job id; spec §6)
    scratch_root = Path(cfg.scratch.root)
    scratch_root.mkdir(parents=True, exist_ok=True)
    if cfg.scratch.startup_sweep:
        for child in scratch_root.iterdir():
            _sweep(child)  # rmtree dirs, unlink files

    app.state.cfg = cfg
    app.state.client = client
    app.state.store = store
    app.state.pipeline = pipeline

    # -- worker pool: max_parallel_jobs consumers of an asyncio queue ------
    work_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=cfg.server.max_queue_size)
    app.state.work_queue = work_queue

    async def worker():
        while True:
            job_id = await work_queue.get()
            try:
                await _run_job_with_budget(app, job_id)
            except Exception:
                log.exception("worker: unhandled failure for job %s", job_id)
            finally:
                work_queue.task_done()

    workers = [asyncio.create_task(worker())
               for _ in range(max(1, cfg.server.max_parallel_jobs))]

    log.info("started: models=%s/%s base_url=%s",
             cfg.models.video_model, cfg.models.summary_model, cfg.models.base_url)
    try:
        yield
    finally:
        # -- shutdown: stop workers, close client ---------------------------
        for w in workers:
            w.cancel()
        await client.close()


async def _run_job_with_budget(app: FastAPI, job_id: str) -> None:
    """Per-job soft budget (jobs.max_runtime_seconds): a stuck decode or
    slow LLM can't hold the worker slot forever (spec §6)."""
    cfg: AppConfig = app.state.cfg
    started = time.monotonic()
    remaining = lambda: cfg.jobs.max_runtime_seconds - (time.monotonic() - started)
    # pseudocode:
    #   store.start(job_id)
    #   video_path = app.state._video_paths.pop(job_id)  # set at submit time
    #   try:
    #       await asyncio.wait_for(app.state.pipeline.run(job_id=job_id,
    #                                                      video_path=video_path),
    #                               timeout=remaining())
    #   except asyncio.TimeoutError:
    #       store.fail(job_id, code="timeout", message="job exceeded max_runtime_seconds")
    #   finally:
    #       cleanup_job_scratch(cfg, job_id)
    raise NotImplementedError  # pseudocode placeholder


def _sweep(child: Path) -> None:
    # pseudocode: dir -> shutil.rmtree, file -> unlink, both ignore_errors=True
    ...


app = FastAPI(title="video-processing-api", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.post("/api/jobs", status_code=202)
async def submit_job(body: JobSubmit,
                     request: Request,
                     idempotency_key: str | None = Header(default=None,
                                                          alias="Idempotency-Key")):
    cfg: AppConfig = request.app.state.cfg
    store: JobStore = request.app.state.store

    # -- deterministic checks BEFORE any job id exists (spec §6.3) ---------
    # 1) must be under host_prefix (allow-list + traversal guard)
    try:
        container_path: Path = rewrite_path(cfg.paths, body.path)
    except PathOutsideRoot:
        return _err("path_outside_allowed_root",
                    f"path must be under {cfg.paths.host_prefix}", status=400)

    # 2) extension allowed
    ext = container_path.suffix.lower()
    if ext not in cfg.paths.allowed_extensions:
        return _err("unsupported_format",
                    f"extension {ext or '(none)'} not in {cfg.paths.allowed_extensions}",
                    status=415)

    # 3) exists?
    if not container_path.exists() or not container_path.is_file():
        return _err("path_not_found", f"no file at {body.path}", status=404)

    # 4) size cap
    if container_path.stat().st_size > cfg.paths.max_file_size_mb * 1024 * 1024:
        return _err("file_too_large",
                    f"file exceeds {cfg.paths.max_file_size_mb} MB", status=413)

    # -- enqueue (with idempotency + queue-full signal) --------------------
    try:
        store.reserve_slot()
        record = store.create(idempotency_key=idempotency_key)
    except QueueFull:
        await asyncio.sleep(0)  # pseudocode: Retry-After derived from estimated drain
        return _err("queue_full", "server is at max queue size; retry later",
                    status=429)
    # pseudocode: set Retry-After header on the 429 response
    request.app.state._video_paths = getattr(request.app.state, "_video_paths", {})
    request.app.state._video_paths[record.job_id] = container_path
    await request.app.state.work_queue.put(record.job_id)

    return JSONResponse(status_code=202, content={
        "job_id": record.job_id,
        "status": record.status.value,
        "status_url": f"/api/jobs/{record.job_id}",
        "created_at": _iso(record.created_at),
    })


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, request: Request):
    store: JobStore = request.app.state.store
    record = store.get(job_id)
    if record is None:
        return _err("job_not_found", f"unknown or expired job: {job_id}", status=404)
    return JSONResponse(status_code=200, content=store.to_public_dict(record))


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request):
    client: LLMClient = request.app.state.client
    try:
        ok = await client.ready()
    except Exception as exc:  # defensive: transport error => not ready
        ok = False
    if not ok:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ready"}


def _iso(ts: float) -> str:
    # pseudocode: datetime.fromtimestamp(ts, tz=UTC).isoformat()
    ...


# Run via: uvicorn main:app (see Dockerfile ENTRYPOINT)
