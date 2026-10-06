# Video Processing API — Design Spec

Date: 2026-10-06
Status: approved (per section), pending final review
Path: architectural (new project)

## 1. Purpose

A Python API service that, given a path to an mp4 (or .mov/.avi) video on the
host filesystem:

1. Extracts frames at a configured sample rate.
2. Sends the frames to a vision-LLM (Ollama, `minicpm-v4.5:8b` by default) to
   produce a chronological description.
3. Sends that description to a second LLM (also Ollama) to produce a structured
   JSON summary.
4. Exposes asynchronous job submission and status polling via HTTP.

Every tunable (models, endpoints, prompts, fps, timeouts, path mapping,
output schema) lives in a YAML config file — code holds only structural
defaults (bind port, scratch dir layout).

Deployed via Docker: one API container; Ollama runs externally (host or LAN)
and is addressed by `base_url` in the config.

Reference implementation: `sample_processing.py` (Ollama chat with frames,
1 fps sampling, temperature 0.5, 16k context).

## 2. Decisions (from brainstorming)

| # | Decision |
|---|----------|
| 1 | Async job-based API: `POST /api/jobs` → 202 + job id, `GET /api/jobs/{id}` polls |
| 2 | Videos live on the host at a fixed prefix; config rewrites host → container path |
| 3 | LLMs run externally on Ollama (not a sidecar) |
| 4 | Summarizer is also a local Ollama model |
| 5 | Output is structured JSON: `title`, `summary`, `actions[]`, `subjects[]`, `confidence_note` |
| 6 | Fields are exactly those five; `confidence_note` is the only optional one |
| 7 | Approach A: FastAPI + in-process job manager + bounded worker pool |

## 3. Architecture

One Python container.

```
+--------------------------------------------------------------+
|  api container (FastAPI)                                     |
|                                                              |
|  main.py            config.py         job_store.py           |
|  (app, routes,      (load + validate  (in-memory store:      |
|   uvicorn startup,   YAML/JSON →      status, stage, result, |
|   /api/jobs,         Pydantic models;  TTL reaping,          |
|   /healthz,          fails at boot    idempotency window,    |
|   /readyz)           on bad config)    queue-full signal)    |
|                                                              |
|  pipeline.py        llm_client.py     frames.py              |
|  (job state machine:  (one chat() for (ffmpeg-based frame    |
|   extract → video     both models,    extraction, per-job     |
|   LLM → summarize →   Ollama          temp dir, cleanup in    |
|   validate)           OpenAI-compat   finally)               |
|                      endpoint)                          |
+--------------------------------------------------------------+
            |                            ^
            |  POST /api/jobs            |
            v                            |
   +----------------+   http    +---------------------+
   |  caller        |---------->|  Ollama (external)  |
   |                |           |  minicpm-v4.5:8b    |
   +----------------+           |  + summary model    |
                                +---------------------+
```

**Components and boundaries**

- **`config.py`** — loads config from `APP_CONFIG` env (default `config.yaml`).
  Pydantic-validated at startup; invalid config fails the container at boot.
  Secret interpolation: `${ENV_VAR}` references resolve against the process
  environment, never stored in the file.
- **`llm_client.py`** — single `chat(model, messages, **opts)` against
  `{base_url}/v1/chat/completions` (Ollama's OpenAI-compatible endpoint).
  Typed retries on transient failures (timeout/5xx) with exponential
  backoff. Both models use this one client.
- **`frames.py`** — extracts JPG frames at `fps_sample_rate` via ffmpeg
  subprocess into `<scratch_root>/<job_id>/frames/`; honors `max_frames`
  (uniform down-sampling, not failure); cleanup guaranteed by pipeline
  `finally`.
- **`pipeline.py`** — per-job state machine driven by the worker:
  `queued → extracting → analyzing → summarizing → completed | failed`.
  Injected with config + clients; no globals. Validates the summarizer's
  JSON against the configured schema on every attempt.
- **`job_store.py`** — `dict` + `threading.Lock`, key = `uuid4`. Fields:
  status, stage, created/started/finished timestamps, result, error
  `{code, message}`. Lazily reaps jobs past TTL (default 24 h) on read.
  Emits `queue_full` when `len(queue)+len(active) > max_queue_size`.
  **The only component that must be swapped to add durability later.**
- **`main.py`** — app factory, route registration, startup (config + clients
  + scratch dir sweep of `<scratch_root>/*`), shutdown.

**Concurrency**

Bounded worker pool, size = `server.max_parallel_jobs` (default 1 —
GPU-bound work). Jobs are pushed to an in-process `queue.Queue`; `POST`
returns **429 `queue_full`** with `Retry-After` when rejected. No job is
ever silently dropped; every submit either enqueues or gets a 4xx/429.

**Data flow**

```
POST /api/jobs {"path": "/data/videos/clip.mp4"}
  → prefix-rewrite + existence + extension + size checks (sync, at submit)
  → job created (queued) → 202 {"job_id", "status_url", "created_at"}
worker:
  extracting   → ffmpeg frames (≤ max_frames)
  analyzing    → video LLM (re-prompt on non-JSON, ≤ max_retries)
  summarizing  → summary LLM (re-prompt on non-JSON, ≤ max_retries)
  validating   → full schema match, or failed with typed code
  → completed  → result stored, scratch dir removed (finally)
GET /api/jobs/{id}
  → 200 {"status":"completed","result":{five fields}}
  → 200 {"status":"<intermediate>"} while running
  → 200 {"status":"failed","error":{"code","message"}}
  → 404 job_not_found (unknown or TTL-expired)
```

## 4. API contract

Common error envelope (all errors):

```json
{"error": {"code": "stable_code", "message": "human text", "details": {}?}}
```

`code` is a closed set; callers branch on it, never on `message`.

### POST /api/jobs

| resp | body |
|------|------|
| `202` | `{"job_id": "...", "status": "queued", "status_url": "/api/jobs/...", "created_at": "...", "estimated_wait": null?}` |
| `400 path_outside_allowed_root` | path not under `paths.host_prefix` |
| `404 path_not_found` | rewritten path doesn't exist |
| `413 file_too_large` | exceeds `paths.max_file_size_mb` |
| `415 unsupported_format` | extension not in `paths.allowed_extensions` |
| `422 validation_error` | request body shape invalid (FastAPI standard) |
| `429 queue_full` | headers: `Retry-After: <s>` |

Headers: optional `Idempotency-Key` — re-POSTing the same key inside
`jobs.idempotency_window_seconds` returns the original job (202 with the
existing `job_id`), no double-enqueue.

### GET /api/jobs/{job_id}

`status` ∈ `queued | extracting | analyzing | summarizing | completed | failed`.

`completed` bodies:

```json
{
  "job_id": "...", "status": "completed",
  "created_at": "...", "started_at": "...", "finished_at": "...",
  "result": {
    "title": "...",
    "summary": "...",
    "actions": ["...", "..."],
    "subjects": ["...", "..."],
    "confidence_note": "..."
  }
}
```

`failed` bodies: `"error": {"code": "<typed>", "message": "..."}` with
`code` ∈

- `no_frames`
- `video_llm_error`
- `video_llm_invalid_output`
- `summary_llm_error`
- `summary_invalid_output`
- `timeout`
- `internal_error`

`404 job_not_found` for unknown/TTL-expired ids.

### GET /healthz

`200 {"status": "ok"}` — process is up; no external calls.

### GET /readyz

Probes Ollama (`GET {base_url}/api/tags` or `/v1/models`) with a short
timeout. `200 {"status": "ready"}` if reachable, `503
{"status": "not_ready"}` otherwise. Suitable for Docker `HEALTHCHECK` and
operator debug.

**Deliberate non-goals:** no SSE/streaming (long-poll is the contract; SSE
added later would reuse the job store), no auth (single-host trust; TLS/auth
is the operator's reverse-proxy concern), no pagination (single job at a
time by design), no persistent queue (see §7).

## 5. Configuration

Loaded from `APP_CONFIG` (default `config.yaml`). YAML is the shipped
format; JSON is also accepted by the same parser. `${ENV_VAR}` interpolation for
secrets.

```yaml
server:
  host: "0.0.0.0"
  port: 8080
  max_queue_size: 16
  max_parallel_jobs: 1      # GPU-bound: default 1

paths:
  host_prefix: "/data/videos"
  container_prefix: "/srv/videos"
  allowed_extensions: [".mp4", ".mov", ".avi"]
  max_file_size_mb: 2048

jobs:
  ttl_seconds: 86400
  idempotency_window_seconds: 600
  max_runtime_seconds: 900  # per-job soft budget

frames:
  fps_sample_rate: 1
  max_frames: 900
  frame_format: "jpg"

models:
  base_url: "http://host.docker.internal:11434"
  video_model: "minicpm-v4.5:8b"
  summary_model: "llama3.1:8b"

llm:
  timeout_seconds: 300
  max_retries: 2
  retry_backoff_seconds: 3
  temperature_video: 0.5
  temperature_summary: 0.2
  max_tokens_video: 4096
  max_tokens_summary: 1500

prompting:
  video_system: "You are a video understanding model. You receive a video clip as a sequence of sampled frames. Describe what happens in chronological order, using only what is visible."
  video_user_suffix: " This is a video clip. Describe the actions in chronological order."
  summary_system: "You summarize video descriptions. Return JSON only."
  summary_user: "Given the video description below, produce the final JSON.\n{description}"

output_schema:
  fields:
    title: {type: "string", max_length: 120}
    summary: {type: "string", max_length: 600}
    actions: {type: "array", item: "string", max_items: 25}
    subjects: {type: "array", item: "string", max_items: 20}
    confidence_note: {type: "string", max_length: 200, optional: true}

scratch:
  root: "/tmp/video_processing"
  startup_sweep: true

logging:
  level: "INFO"
  format: "json"
```

**Startup validation (fail at boot, never mid-request):**

- `host_prefix`, `container_prefix` present and absolute.
- `allowed_extensions` non-empty, each starts with `.`.
- `max_queue_size ≥ 1`, `max_parallel_jobs ≥ 1`, `max_file_size_mb > 0`.
- `ttl_seconds > 0`, `idempotency_window_seconds ≥ 0`, `max_runtime_seconds > 0`.
- `fps_sample_rate > 0`, `max_frames > 0`, `frame_format` ∈ {jpg, png, webp}.
- `models.base_url` is a valid http(s) URL; both model names non-empty.
- `llm.timeout_seconds > 0`, `max_retries ≥ 0`, `retry_backoff_seconds ≥ 0`,
  both temperatures in [0, 2], both `max_tokens_* > 0`.
- `output_schema.fields` — each field's `type` is `string` or `array` of
  `string`; `confidence_note` is the one optional field.

**Config-driven code generation:** the pipeline's JSON validator and prompt
suffixes are generated from the same `output_schema.fields` list. The five
output fields are *data*, not code — changing the schema in YAML changes the
prompt, the validator, and the documented API shape in one place.

**Secrets:** `models.base_url` today is a host IP (no key needed). If a
hosted provider is ever substituted, its `api_key: "${OPENAI_API_KEY}"`
interpolation reads the key from the environment — never the config file,
never the image.

## 6. Error handling

**Principles**

1. Fail loud at boundaries, fail contained inside a job. A bad path = clean
   4xx at submit. A mid-job failure (Ollama 5xx, ffmpeg crash, non-JSON
   output) = that one job goes to `failed` with a typed code; the API keeps
   serving and the queue keeps draining.
2. No partial results. A job is `completed` with a full validated result or
   `failed`; there is no halfway state.
3. Deterministic failures (missing file, bad extension, oversized file) fail
   **at submit** — no `job_id` is created.
4. Retries are typed: transient LLM failures (timeout/5xx) retry
   `max_retries` times with exponential backoff; structural failures
   (model returned non-JSON) retry the *same* budget with a re-prompt suffix
   ("Your last reply was not valid JSON. Respond only with JSON matching the
   schema."). The video-model step expects a **plain-text chronological
   description, not JSON**: `video_llm_invalid_output` fires when its reply
   is empty or has fewer than 10 non-whitespace characters after one
   re-prompt. The JSON re-prompt applies to the summary step only.
5. Cleanup is contractual: pipeline `finally` removes
   `<scratch_root>/<job_id>/`. A `startup` hook sweeps
   `<scratch_root>/*` (safe because every scratch path is namespaced by job
   ID — we never touch files we didn't create).
6. Timeouts are layered: per-LLM-call (`llm.timeout_seconds`) <
   job-level soft budget (`jobs.max_runtime_seconds`) < nothing else. A job
   that hits the budget is `failed: timeout`, scratch cleaned, slot freed.
7. JSON structured logging. Every line carries `job_id` (when in-job context
   exists) and `stage`; `docker logs api | jq 'select(.job_id=="...")'`
   works out of the box.

## 7. Testing

**Unit (no network, no models)**

- `config.py`: valid YAML → typed object; one assertion per validation rule
  for each invalid variant (missing prefix, bad port, negative fps, relative
  path, bad token type, empty model name).
- `frames.py`: fixture mp4 (~50 KB, committed test asset, generated once by
  ffmpeg) → correct frame count/format; `max_frames` down-samples uniformly;
  bad codec → clear error.
- `llm_client.py` (against httpx transport mock): retry on 500, no retry on
  400, backoff timing, timeout surface, prompt shape.
- `pipeline.py` (fake LLM client): (a) valid JSON → `completed` + exact
  schema; (b) bad→valid → completes after one re-prompt; (c) always bad →
  `failed: *_invalid_output`; (d) video LLM raises → `failed:
  video_llm_error`; (e) missing video → `failed: no_frames` **and** scratch
  dir gone afterwards.
- `job_store.py`: status transitions, TTL reaping, idempotency dedup,
  queue-full signal.

**Integration (one, end-to-end)**

`docker compose up` in CI (or a testcontainers-style harness) with a **stub
Ollama** (a tiny FastAPI service that returns canned `chat/completions`
bodies). POST a real small mp4, poll to `completed`, assert the full
five-field schema. This is the test that proves the shipped unit actually
behaves.

**Deliberately out of scope** (YAGNI)

- Real-model output quality (product decision, not a code bug).
- Load testing beyond queue-full (already covered by unit test).
- Durability across restarts (documented design trade-off; the swap point
  to add a broker-backed queue is `job_store.py` alone).

## 8. Docker

**Dockerfile (multi-stage, slim):**

```dockerfile
# builder
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip wheel --wheelhouse /wheels -r requirements.txt

# runtime
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels
COPY . /app
ENV APP_CONFIG=/config.yaml
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"
ENTRYPOINT ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

ffmpeg is required at runtime (frame extraction). No secrets baked in.
`/config.yaml` is a **mount**, not a COPY — the real config file lives on
the host, gitignored.

**docker-compose.yml (local dev; production uses the same image + host
volumes)**

```yaml
services:
  api:
    build: .
    ports: ["8080:8080"]
    volumes:
      - ./config.yaml:/config.yaml:ro
      - /data/videos:/srv/videos:ro
      - scratch:/tmp/video_processing
    depends_on: []          # Ollama is external by design
    environment:
      - APP_CONFIG=/config.yaml

volumes:
  scratch:
```

**Runtime expectations:** the caller's machine can reach the host at
`paths.host_prefix`; `base_url` in the config points at an Ollama instance
that has both `minicpm-v4.5:8b` and the summary model pulled. `GET /readyz`
verifies Ollama reachability from the container at any time.

## 9. Repo layout (target)

```
video_processing/
├── main.py
├── config.py
├── pipeline.py
├── llm_client.py
├── frames.py
├── job_store.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── config.yaml.example
├── .gitignore               # config.yaml, scratch/, __pycache__/
├── tests/
│   ├── conftest.py
│   ├── fixtures/test_video.mp4
│   ├── stub_ollama.py
│   ├── test_config.py
│   ├── test_frames.py
│   ├── test_llm_client.py
│   ├── test_pipeline.py
│   └── test_job_store.py
└── docs/superpowers/specs/
    └── 2026-10-06-video-processing-api-design.md   (this file)
```

`sample_processing.py` stays as reference material; it is not deleted.

## 10. Open items / next steps

- Confirm `summary_model` (placeholder `llama3.1:8b` — pick the model that's
  actually pulled on the Ollama host).
- Confirm `host_prefix` and `container_prefix` values for each target
  deployment; these are per-deployment and land in the (gitignored) real
  `config.yaml`.
- After spec approval → invoke `writing-plans` to produce the build plan.
