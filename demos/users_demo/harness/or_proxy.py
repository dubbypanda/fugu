import json
import os
import sys

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

UPSTREAM = "https://openrouter.ai/api/v1"
DUMP = os.environ.get("OR_PROXY_DUMP", "0") == "1"
DUMP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "or_proxy_bodies.jsonl")
app = FastAPI()


def log(msg: str) -> None:
    print(f"[or_proxy] {msg}", flush=True)


def _fwd_headers(req: Request) -> dict:
    keep = {"authorization", "content-type", "accept", "http-referer", "x-title"}
    return {k: v for k, v in req.headers.items() if k.lower() in keep}


def _strip_reasoning(item) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("type") == "reasoning":
        return True
    if item.get("type") in ("thinking", "reasoning_text"):
        return True
    return False


@app.post("/api/v1/responses")
async def responses(req: Request):
    raw = await req.body()
    try:
        body = json.loads(raw)
    except Exception as e:
        log(f"bad json body: {e}")
        return Response(content=raw, status_code=400)

    inp = body.get("input")
    types = []
    stripped = 0
    if isinstance(inp, list):
        types = [it.get("type") if isinstance(it, dict) else type(it).__name__ for it in inp]
        kept = [it for it in inp if not _strip_reasoning(it)]
        stripped = len(inp) - len(kept)
        body["input"] = kept
    log(f"model={body.get('model','?')} n_input={len(inp) if isinstance(inp,list) else '?'} "
        f"types={types} stripped={stripped}")
    if DUMP:
        try:
            with open(DUMP_PATH, "a") as f:
                f.write(json.dumps({"model": body.get("model"), "types": types,
                                    "stripped": stripped, "sample": inp if isinstance(inp, list) else None},
                                   default=str)[:20000] + "\n")
        except Exception:
            pass

    headers = _fwd_headers(req)

    async def gen():
        try:
            async with httpx.AsyncClient(timeout=None) as c:
                async with c.stream("POST", f"{UPSTREAM}/responses", json=body, headers=headers) as r:
                    async for chunk in r.aiter_raw():
                        yield chunk
        except Exception as e:
            log(f"upstream stream error: {e!r}")
            yield (b'data: {"type":"error","error":{"message":"proxy upstream error"}}\n\n')

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.api_route("/api/v1/{path:path}", methods=["GET", "POST"])
async def passthrough(path: str, req: Request):
    headers = _fwd_headers(req)
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            if req.method == "GET":
                r = await c.get(f"{UPSTREAM}/{path}", headers=headers, params=dict(req.query_params))
            else:
                r = await c.post(f"{UPSTREAM}/{path}", headers=headers, content=await req.body())
        return Response(content=r.content, status_code=r.status_code,
                        media_type=r.headers.get("content-type", "application/json"))
    except Exception as e:
        log(f"passthrough error {path}: {e!r}")
        return Response(content=b'{"error":"proxy error"}', status_code=502)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    log(f"starting on :{os.environ.get('PORT','8765')} dump={DUMP}")
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8765")), log_level="warning")
