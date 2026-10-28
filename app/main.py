"""SkySpeak server. Run:  python -m uvicorn app.main:app --port 8000   then open http://localhost:8000"""
import os
import re

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .commands import COMMANDS, describe
from .drone import make_drone
from .llm import LLMError, translate
from .logger import SessionLogger
from .safety import validate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = yaml.safe_load(open(os.environ.get("SKYSPEAK_CONFIG", os.path.join(ROOT, "config.yaml")), encoding="utf-8"))
LOG = SessionLogger(os.path.join(ROOT, CFG["logging"]["directory"]))
DRONE = make_drone(CFG, LOG)

app = FastAPI(title="SkySpeak")
app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "static")), name="static")


class GenReq(BaseModel):
    text: str
    student_code: str = ""


class ExecReq(BaseModel):
    program: list
    student_code: str = ""


def _code(c):
    # anonymous codes only, e.g. S01, A-12
    c = (c or "").strip()[:12]
    return c if re.fullmatch(r"[A-Za-z0-9_-]*", c) else "invalid"


def _labelled(program):
    lang = CFG["ui"]["language"]
    return [{**c, "label": describe(c, lang)} for c in program]


@app.get("/")
def index():
    return FileResponse(os.path.join(ROOT, "static", "index.html"))


@app.get("/api/info")
def info():
    return {"mode": CFG["mode"], "provider": CFG["llm"]["provider"], "model": CFG["llm"].get("model", ""),
            "safety": CFG["safety"], "language": CFG["ui"]["language"], "session": LOG.session_id,
            "commands": {k: {"needs_value": v[0], "min": v[1], "max": v[2], "unit": v[3], "label": v[4]}
                         for k, v in COMMANDS.items()}}


@app.post("/api/generate")
def generate(req: GenReq):
    text = req.text.strip()[:1000]
    if not text:
        raise HTTPException(400, "Κενή οδηγία.")
    code = _code(req.student_code)
    try:
        out = translate(text, CFG)
    except LLMError as e:
        LOG.log("llm_error", student=code, prompt=text, error=str(e))
        raise HTTPException(502, str(e))
    res = validate(out["program"], CFG)
    LOG.log("generate", student=code, prompt=text, provider=out["provider"], model=out["model"],
            latency_ms=out["latency_ms"], raw=out["raw"], validated=res.program,
            errors=res.errors, warnings=res.warnings)
    d = res.to_dict()
    d["program"] = _labelled(res.program)
    d["explanation"] = out["explanation"]
    d["latency_ms"] = out["latency_ms"]
    return d


@app.post("/api/validate")
def validate_only(req: ExecReq):
    res = validate(req.program, CFG)
    d = res.to_dict()
    d["program"] = _labelled(res.program)
    return d


@app.post("/api/execute")
def execute(req: ExecReq):
    res = validate(req.program, CFG)          # never trust the client: re-validate
    if not res.ok:
        raise HTTPException(400, " ".join(res.errors))
    LOG.log("approve", student=_code(req.student_code), program=res.program)
    try:
        DRONE.execute(res.program)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"started": True, "program": _labelled(res.program)}


@app.post("/api/emergency")
def emergency():
    DRONE.stop_and_land()
    return {"landed": True}


@app.get("/api/status")
def status():
    return DRONE.status()


@app.post("/api/note")
def note(body: dict):
    """Researcher note or student self-report (e.g. 'success', 'retry')."""
    LOG.log("note", student=_code(body.get("student_code", "")), text=str(body.get("text", ""))[:500])
    return {"ok": True}
