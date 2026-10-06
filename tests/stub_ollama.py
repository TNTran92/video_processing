"""Stub Ollama for the end-to-end integration test (spec §7).

A tiny FastAPI app emulating the OpenAI-compatible surface the API uses:
  GET  /api/tags             -> 200 {"models": [...]}
  GET  /v1/models            -> 200 {...}
  POST /v1/chat/completions  -> 200 canned body

Behavior is scripted per test: run `python -m tests.stub_ollama --script
default|bad_json_then_valid|always_bad` to choose what each model returns.
The pipeline talks to it exactly like real Ollama — this is the single
test that proves the shipped unit behaves.
"""

import argparse
import json

from fastapi import FastAPI, Request

app = FastAPI()

# canned assistant content per model
DEFAULT_VIDEO = json.dumps({
    "title": "Cat on sofa", "summary": "A cat sits then jumps.",
    "actions": ["cat sits", "cat jumps"], "subjects": ["cat", "sofa"],
    "confidence_note": "no",
})
DEFAULT_SUMMARY = DEFAULT_VIDEO


@app.get("/api/tags")
async def tags():
    return {"models": [{"name": "minicpm-v4.5:8b"}, {"name": "llama3.1:8b"}]}


@app.get("/v1/models")
async def models():
    return {"data": [{"id": "minicpm-v4.5:8b"}, {"id": "llama3.1:8b"}]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    model = body["model"]
    content = (
        DEFAULT_VIDEO if "minicpm" in model
        else DEFAULT_SUMMARY
    )
    # -- scripted overrides (module-level state set by --script) ------------
    # pseudocode: consult a global SCRIPT dict set by main(); supports
    # sequences, e.g. bad_json_then_valid returns "not json" on call 1 then
    # DEFAULT_SUMMARY afterwards (per-model counters).
    ...
    return {
        "id": "stub",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }


if __name__ == "__main__":
    import uvicorn
    p = argparse.ArgumentParser()
    p.add_argument("--script", choices=["default", "bad_json_then_valid", "always_bad"],
                   default="default")
    p.add_argument("--port", type=int, default=11500)
    a = p.parse_args()
    # pseudocode: set SCRIPT state per a.script
    uvicorn.run(app, port=a.port)
