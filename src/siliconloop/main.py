"""FastAPI routes: intake, plan, build, compile, verify, share. Also serves the built website, so one URL covers both.

Swagger UI: /docs. Flow: POST /api/builds -> POST /api/builds/{id}/design -> POST /api/builds/{id}/firmware.
"""
import json
import os
import queue
import secrets
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import pipeline, report, simspec
from .allocator import BOARDS, PARTS, AllocationError

app = FastAPI(title="SiliconLoop", description="Photo of parts + a sentence -> proven Arduino firmware. Built with Muse Spark Open.")
MAX_PHOTO_BYTES = 8 * 1024 * 1024
HIDDEN = {"hex", "sketch"}  # large fields served by their own download routes


class ConfirmedPart(BaseModel):
    name: str = Field(examples=["HC-SR04 ultrasonic sensor"])
    part: str | None = Field(default=None, description="Catalogue key from recognition, or null for an unknown part")
    count: int = Field(default=1, ge=1, le=20)


class DesignRequest(BaseModel):
    parts: list[ConfirmedPart]
    wish: str = Field(examples=["A night light that turns on when it gets dark and waves a flag when someone walks past"])


class IdeasRequest(BaseModel):
    parts: list[ConfirmedPart]


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(max_length=30)


def _public(build):
    return {k: v for k, v in build.items() if k not in HIDDEN} | {"has_hex": bool(build.get("hex"))}


def _run(fn, *args):
    try:
        return _public(fn(*args))
    except pipeline.PipelineError as e:
        raise HTTPException(400, str(e))
    except AllocationError as e:
        raise HTTPException(422, str(e))
    except ValueError as e:  # model output never became valid
        raise HTTPException(502, str(e))


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/boards")
def boards():
    return [{"key": k, "name": b["name"]} for k, b in BOARDS.items()]


@app.get("/api/parts")
def supported_parts():
    return [{"key": k, "name": c["name"], "aliases": c.get("aliases", []), "notes": c.get("notes", []),
             "pins": {p: s.get("label", p) for p, s in c["pins"].items()},
             "generic": c["sim"] if isinstance(c.get("sim"), str) else None,
             "models": [i["model"] for i in simspec.instances(c)], "rules": simspec.io_of(c)}
            for k, c in PARTS.items()]


@app.post("/api/builds")
def create_build(
    board: str = Form(..., examples=["uno"]),
    photo1: UploadFile = File(..., description="Photo of your components"),
    photo2: UploadFile | None = File(None, description="Optional second photo"),
    photo3: UploadFile | None = File(None, description="Optional third photo"),
):
    """Step 1: board + component photos -> recognised parts list for the user to confirm.
    (Separate photo fields because Swagger UI cannot show a file picker for a list of files.)"""
    images = []
    for f in (photo1, photo2, photo3):
        if f is None or not f.filename:
            continue
        data = f.file.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(413, f"{f.filename} is larger than 8 MB.")
        if not (f.content_type or "").startswith("image/"):
            raise HTTPException(415, f"{f.filename} is not an image.")
        images.append((data, f.content_type))
    return _run(pipeline.recognise, board, images)


@app.post("/api/builds/{build_id}/design")
def design(build_id: str, req: DesignRequest):
    """Step 2: confirmed parts + wish -> grounded plan, behaviour rules, wiring and safety result."""
    return _run(pipeline.design, build_id, [p.model_dump() for p in req.parts], req.wish)


@app.post("/api/builds/{build_id}/firmware")
def firmware(build_id: str):
    """Step 3: Spark Open writes the complete sketch; lint, compile, run the simulation test and repair up to 3 times.
    Status becomes "verified" only when every behaviour rule and health check passes; only then is the hex served."""
    return _run(pipeline.firmware, build_id)


@app.get("/api/builds/{build_id}")
def get_build(build_id: str):
    return _run(pipeline.load, build_id)


@app.post("/api/builds/{build_id}/ideas")
def ideas(build_id: str, req: IdeasRequest):
    """Three gadget ideas Muse Spark Open proposes for the confirmed parts, each pre-checked by the pin allocator."""
    try:
        return {"ideas": pipeline.ideas(build_id, [p.model_dump() for p in req.parts])}
    except pipeline.PipelineError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(502, str(e))


@app.post("/api/builds/{build_id}/run")
def run(build_id: str, req: DesignRequest):
    """Steps 2 and 3 in one go, streamed as Server-Sent Events for the live execution trace.
    Each event is a JSON object: {"event": "stage", "stage", "state", "text", "t", ...data}, then one
    {"event": "done", "build": {...}} or {"event": "error", "message"}."""
    _load_or_404(build_id)
    q, t0 = queue.Queue(), time.monotonic()

    def emit(stage, state, text, **data):
        q.put({"event": "stage", "stage": stage, "state": state, "text": text, "t": round(time.monotonic() - t0, 2), **data})

    def work():
        try:
            b = pipeline.design(build_id, [p.model_dump() for p in req.parts], req.wish, emit=emit)
            if b.get("status") == "designed":
                b = pipeline.firmware(build_id, emit=emit)
            q.put({"event": "done", "build": _public(b), "t": round(time.monotonic() - t0, 2)})
        except (pipeline.PipelineError, AllocationError, ValueError) as e:
            q.put({"event": "error", "message": str(e)})
        except Exception as e:  # never leave the client waiting on a dead stream
            q.put({"event": "error", "message": f"Internal error: {type(e).__name__}: {e}"})
        finally:
            q.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            try:
                item = q.get(timeout=10)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if item is None:
                return
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/builds/{build_id}/twin")
def twin(build_id: str):
    """The verified binary plus part models and rule-key directions, for the twin running in the browser."""
    try:
        return pipeline.twin_spec(build_id)
    except pipeline.PipelineError as e:
        raise HTTPException(400, str(e))


@app.get("/api/builds/{build_id}/report")
def report_json(build_id: str):
    b = _load_or_404(build_id)
    if not b.get("plan"):
        raise HTTPException(400, "Design the gadget first.")
    return report.data(b, pipeline._cards_for(b))


@app.get("/api/builds/{build_id}/report.md", response_class=PlainTextResponse)
def report_md(build_id: str):
    b = _load_or_404(build_id)
    if not b.get("plan"):
        raise HTTPException(400, "Design the gadget first.")
    name = "".join(c if c.isalnum() else "-" for c in b["plan"].get("title", "gadget").lower()).strip("-") or "gadget"
    return PlainTextResponse(report.markdown(b, pipeline._cards_for(b)),
                             headers={"Content-Disposition": f'attachment; filename="{name}-report.md"'})


@app.post("/api/builds/{build_id}/chat")
def chat(build_id: str, req: ChatRequest):
    """Ask Muse Spark Open a basic question about this build (grounded in its report, rules and sketch)."""
    try:
        return {"reply": pipeline.chat(build_id, [m.model_dump() for m in req.messages])}
    except pipeline.PipelineError as e:
        raise HTTPException(400, str(e))


# ---------- phone camera hand-off (QR code): photos live in memory for 15 minutes ----------
HANDOFF_TTL_S, HANDOFF_MAX = 15 * 60, 6
_handoffs, _handoff_lock = {}, threading.Lock()


def _handoff(token):
    now = time.time()
    with _handoff_lock:
        for k in [k for k, v in _handoffs.items() if now - v["created"] > HANDOFF_TTL_S]:
            del _handoffs[k]
        h = _handoffs.get(token)
    if not h:
        raise HTTPException(404, "This phone link has expired. Scan a new QR code.")
    return h


@app.post("/api/handoff")
def handoff_create():
    token = secrets.token_urlsafe(9)
    with _handoff_lock:
        _handoffs[token] = {"created": time.time(), "photos": []}
    return {"token": token, "ttl_s": HANDOFF_TTL_S}


@app.get("/api/handoff/{token}")
def handoff_status(token: str):
    h = _handoff(token)
    return {"count": len(h["photos"]), "photos": [{"i": i, "mime": m} for i, (_, m) in enumerate(h["photos"])]}


@app.post("/api/handoff/{token}")
def handoff_upload(token: str, photo: UploadFile = File(...)):
    h = _handoff(token)
    if len(h["photos"]) >= HANDOFF_MAX:
        raise HTTPException(409, f"At most {HANDOFF_MAX} photos per link.")
    data = photo.file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "The photo is larger than 8 MB.")
    if not (photo.content_type or "").startswith("image/"):
        raise HTTPException(415, "That file is not an image.")
    h["photos"].append((data, photo.content_type))
    return {"count": len(h["photos"])}


@app.get("/api/handoff/{token}/{i}")
def handoff_photo(token: str, i: int):
    h = _handoff(token)
    if not 0 <= i < len(h["photos"]):
        raise HTTPException(404, "No such photo.")
    data, mime = h["photos"][i]
    return Response(content=data, media_type=mime, headers={"Cache-Control": "no-store"})


@app.get("/api/builds/{build_id}/sketch.ino", response_class=PlainTextResponse)
def sketch(build_id: str):
    b = _load_or_404(build_id)
    if not b.get("sketch"):
        raise HTTPException(404, "No sketch yet.")
    return PlainTextResponse(b["sketch"], headers={"Content-Disposition": 'attachment; filename="sketch.ino"'})


@app.get("/api/builds/{build_id}/firmware.hex", response_class=PlainTextResponse)
def hex_file(build_id: str):
    b = _load_or_404(build_id)
    if not b.get("hex"):
        raise HTTPException(404, "No compiled firmware yet.")
    return PlainTextResponse(b["hex"], headers={"Content-Disposition": 'attachment; filename="firmware.hex"'})


def _load_or_404(build_id):
    try:
        return pipeline.load(build_id)
    except pipeline.PipelineError as e:
        raise HTTPException(404, str(e))


# All API routes live under /api and must be registered above this line.
# The website (vite build output) is mounted last so it never shadows an API route.
STATIC_DIR = Path(os.getenv("STATIC_DIR") or Path(__file__).resolve().parents[1] / "web" / "dist")
class SiteFiles(StaticFiles):
    """The page itself is always re-checked, so a rebuilt site shows up on a normal refresh; the hashed assets it
    points to never change, so browsers may keep those for a year."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.startswith("assets/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        return resp


if STATIC_DIR.is_dir():
    app.mount("/", SiteFiles(directory=STATIC_DIR, html=True), name="web")
