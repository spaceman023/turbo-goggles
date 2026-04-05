"""PRISM Web UI — FastAPI application with real-time pipeline monitoring."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request as FastAPIRequest,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

app = FastAPI(title="PRISM", version="0.1.0")

_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# ── Run state ────────────────────────────────────────────────────────────────


@dataclass
class RunState:
    proc: asyncio.subprocess.Process | None = None
    job_id: str = ""
    job_dir: str = ""
    events: list[dict] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    exit_code: int | None = None
    stderr_tail: str = ""
    done: bool = False


_runs: dict[str, RunState] = {}
_api_key: str = os.environ.get("OPENROUTER_API_KEY", "")
_launch_cwd: str = os.getcwd()


# ── Page route ───────────────────────────────────────────────────────────────


@app.get("/")
async def index():
    return FileResponse(_static_dir / "index.html", media_type="text/html")


# ── Config routes ────────────────────────────────────────────────────────────


@app.get("/api/configs")
async def list_configs():
    configs: list[dict] = []
    search_dirs = [Path.cwd() / "examples", Path.cwd()]
    prism_configs = Path.home() / ".prism" / "configs"
    if prism_configs.exists():
        search_dirs.append(prism_configs)

    seen: set[str] = set()
    for d in search_dirs:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            if f.name in seen:
                continue
            try:
                raw = json.loads(f.read_text())
                if "layers" not in raw:
                    continue
                seen.add(f.name)
                configs.append(
                    {
                        "path": str(f),
                        "filename": f.name,
                        "name": raw.get("name", f.stem),
                        "description": raw.get("description", ""),
                        "layers": len(raw.get("layers", [])),
                        "max_workers": raw.get("max_workers", 1),
                    }
                )
            except Exception:
                pass
    return configs


# ── Job routes ───────────────────────────────────────────────────────────────


@app.get("/api/jobs")
async def list_jobs():
    jobs_dir = Path.home() / ".prism" / "jobs"
    if not jobs_dir.exists():
        return []
    jobs: list[dict] = []
    for d in sorted(jobs_dir.iterdir(), reverse=True):
        manifest_path = d / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            raw = _read_manifest(d)
            if raw is None:
                continue
            fmt = _output_format(raw)
            output_file = d / "final" / f"merged-output.{fmt}"
            chunks = raw.get("chunks", [])
            completed = sum(1 for c in chunks if c.get("status") == "complete")
            jobs.append(
                {
                    "job_id": raw["job_id"],
                    "status": raw["status"],
                    "input_file": raw.get("input_file", ""),
                    "total_pages": raw.get("total_pages", 0),
                    "total_chunks": len(chunks),
                    "completed_chunks": completed,
                    "created_at": raw.get("created_at", ""),
                    "updated_at": raw.get("updated_at", ""),
                    "pipeline_name": raw.get("pipeline", {}).get("name", ""),
                    "has_output": output_file.exists(),
                    "output_format": fmt,
                    "output_size": output_file.stat().st_size
                    if output_file.exists()
                    else 0,
                    "path": str(d),
                }
            )
        except Exception:
            pass
    return jobs


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job_dir = _find_job_dir(job_id)
    if not job_dir:
        raise HTTPException(404, "Job not found")
    raw = _read_manifest(job_dir)
    if raw is None:
        raise HTTPException(503, "Manifest temporarily unavailable (pipeline writing)")
    raw["_path"] = str(job_dir)
    return raw


@app.get("/api/jobs/{job_id}/output")
async def get_output(job_id: str):
    job_dir = _find_job_dir(job_id)
    if not job_dir:
        raise HTTPException(404, "Job not found")
    raw = _read_manifest(job_dir)
    if raw is None:
        raise HTTPException(503, "Manifest temporarily unavailable")
    fmt = _output_format(raw)
    output_file = job_dir / "final" / f"merged-output.{fmt}"
    if not output_file.exists():
        raise HTTPException(404, "Output not ready")
    return {"content": output_file.read_text(encoding="utf-8"), "format": fmt}


@app.get("/api/jobs/{job_id}/output/download")
async def download_output(job_id: str):
    job_dir = _find_job_dir(job_id)
    if not job_dir:
        raise HTTPException(404, "Job not found")
    raw = _read_manifest(job_dir)
    if raw is None:
        raise HTTPException(503, "Manifest temporarily unavailable")
    fmt = _output_format(raw)
    output_file = job_dir / "final" / f"merged-output.{fmt}"
    if not output_file.exists():
        raise HTTPException(404, "Output not ready")
    return FileResponse(
        output_file,
        filename=f"{job_id}.{fmt}",
        media_type="application/octet-stream",
    )


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    job_dir = _find_job_dir(job_id)
    if not job_dir:
        raise HTTPException(404, "Job not found")
    shutil.rmtree(job_dir)
    return {"ok": True, "job_id": job_id}


# ── Run routes (start / resume) ─────────────────────────────────────────────


@app.post("/api/runs")
async def start_run(
    pdf: UploadFile = File(...),
    config_path: str = Form(""),
    pipeline_json: str = Form(""),
    workers: int = Form(4),
    api_key: str = Form(""),
):
    # Save uploaded PDF
    tmp_dir = Path(tempfile.mkdtemp(prefix="prism-upload-"))
    pdf_path = tmp_dir / (pdf.filename or "input.pdf")
    with open(pdf_path, "wb") as f:
        f.write(await pdf.read())

    # Resolve config: either a file path or inline JSON from pipeline builder
    if pipeline_json:
        cfg_path = tmp_dir / "pipeline.json"
        cfg_path.write_text(pipeline_json, encoding="utf-8")
        config_path = str(cfg_path)
    elif not config_path:
        raise HTTPException(400, "Either config_path or pipeline_json is required")

    run_id = uuid.uuid4().hex[:8]
    key = api_key or _api_key

    env = os.environ.copy()
    if key:
        env["OPENROUTER_API_KEY"] = key

    prism = shutil.which("prism")
    if not prism:
        raise HTTPException(500, "prism CLI not found on PATH")

    proc = await asyncio.create_subprocess_exec(
        prism,
        "run",
        "--config",
        config_path,
        "--input",
        str(pdf_path),
        "-w",
        str(workers),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=_launch_cwd,
    )

    state = RunState(proc=proc)
    _runs[run_id] = state
    asyncio.create_task(_monitor(run_id, state))

    return {"run_id": run_id}


@app.post("/api/runs/{job_id}/resume")
async def resume_run(
    job_id: str,
    workers: int = 4,
    api_key: str = "",
):
    job_dir = _find_job_dir(job_id)
    if not job_dir:
        raise HTTPException(404, "Job not found")

    run_id = uuid.uuid4().hex[:8]
    key = api_key or _api_key

    env = os.environ.copy()
    if key:
        env["OPENROUTER_API_KEY"] = key

    prism = shutil.which("prism")
    if not prism:
        raise HTTPException(500, "prism CLI not found on PATH")

    proc = await asyncio.create_subprocess_exec(
        prism,
        "resume",
        "--job",
        str(job_dir),
        "-w",
        str(workers),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=_launch_cwd,
    )

    state = RunState(proc=proc, job_id=job_id, job_dir=str(job_dir))
    _runs[run_id] = state
    asyncio.create_task(_monitor(run_id, state))

    return {"run_id": run_id}


# ── Pipelines (CRUD) ─────────────────────────────────────────────────────────


def _pipelines_dir() -> Path:
    d = Path.home() / ".prism" / "pipelines"
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.get("/api/pipelines")
async def list_pipelines():
    pipelines: list[dict] = []
    for f in sorted(_pipelines_dir().glob("*.json")):
        try:
            raw = json.loads(f.read_text())
            pipelines.append(
                {
                    "filename": f.stem,
                    "name": raw.get("name", f.stem),
                    "description": raw.get("description", ""),
                    "layers": len(raw.get("layers", [])),
                }
            )
        except Exception:
            pass
    return pipelines


@app.get("/api/pipelines/{name}")
async def get_pipeline(name: str):
    f = _pipelines_dir() / f"{name}.json"
    if not f.exists():
        raise HTTPException(404, "Pipeline not found")
    return json.loads(f.read_text())


@app.put("/api/pipelines/{name}")
async def save_pipeline(name: str, request: FastAPIRequest):
    body = await request.json()
    f = _pipelines_dir() / f"{name}.json"
    f.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return {"ok": True, "name": name}


@app.delete("/api/pipelines/{name}")
async def delete_pipeline(name: str):
    f = _pipelines_dir() / f"{name}.json"
    if not f.exists():
        raise HTTPException(404, "Pipeline not found")
    f.unlink()
    return {"ok": True}


# ── Health checks ────────────────────────────────────────────────────────────


@app.get("/api/backends/health")
async def backends_health():
    from prism.backends.registry import check_backends

    return check_backends()


@app.get("/api/engines/health")
async def engines_health():
    from prism.engines.registry import check_engines

    return check_engines()


@app.get("/api/gpu")
async def gpu_status():
    try:
        from prism.engines.gpu import detect_gpu

        return detect_gpu()
    except ImportError:
        return {"cuda": False, "mps": False, "device": "cpu", "detail": "GPU detection not available"}


# ── OpenRouter models ────────────────────────────────────────────────────────

_models_cache: dict = {"models": [], "fetched_at": 0.0}


@app.get("/api/openrouter/models")
async def openrouter_models(q: str = ""):
    """Return OpenRouter models, cached for 10 minutes. Optional search query."""
    import time as _time

    now = _time.time()
    if now - _models_cache["fetched_at"] > 600 or not _models_cache["models"]:
        key = _api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            return []
        try:
            import requests

            resp = requests.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            resp.raise_for_status()
            raw = resp.json().get("data", [])
            _models_cache["models"] = [
                {
                    "id": m["id"],
                    "name": m.get("name", m["id"]),
                    "context_length": m.get("context_length"),
                }
                for m in raw
            ]
            _models_cache["fetched_at"] = now
        except Exception as exc:
            logger.warning("Failed to fetch OpenRouter models: %s", exc)
            if not _models_cache["models"]:
                return []

    models = _models_cache["models"]
    if q:
        ql = q.lower()
        models = [m for m in models if ql in m["id"].lower() or ql in m["name"].lower()]
    return models[:100]


# ── Retry job ────────────────────────────────────────────────────────────────


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, request: FastAPIRequest):
    """Re-run a completed or failed job from scratch with the same config and input."""
    job_dir = _find_job_dir(job_id)
    if not job_dir:
        raise HTTPException(404, "Job not found")

    raw = _read_manifest(job_dir)
    if raw is None:
        raise HTTPException(503, "Manifest temporarily unavailable")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    workers = body.get("workers", raw.get("pipeline", {}).get("max_workers", 4))

    # Write the pipeline config to a temp file
    tmp_dir = Path(tempfile.mkdtemp(prefix="prism-retry-"))
    cfg_path = tmp_dir / "pipeline.json"
    cfg_path.write_text(json.dumps(raw["pipeline"], indent=2), encoding="utf-8")

    # Find the original input PDF
    input_pdf = job_dir / raw.get("input_file", "input/input.pdf")
    if not input_pdf.exists():
        raise HTTPException(400, f"Original input file not found: {input_pdf}")

    run_id = uuid.uuid4().hex[:8]
    key = body.get("api_key", "") or _api_key

    env = os.environ.copy()
    if key:
        env["OPENROUTER_API_KEY"] = key

    prism = shutil.which("prism")
    if not prism:
        raise HTTPException(500, "prism CLI not found on PATH")

    proc = await asyncio.create_subprocess_exec(
        prism,
        "run",
        "--config",
        str(cfg_path),
        "--input",
        str(input_pdf),
        "-w",
        str(workers),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=_launch_cwd,
    )

    state = RunState(proc=proc)
    _runs[run_id] = state
    asyncio.create_task(_monitor(run_id, state))

    return {"run_id": run_id}


# ── Settings ─────────────────────────────────────────────────────────────────


@app.get("/api/settings")
async def get_settings():
    return {"has_api_key": bool(_api_key or os.environ.get("OPENROUTER_API_KEY"))}


@app.post("/api/settings")
async def update_settings(api_key: str = Form("")):
    global _api_key
    if api_key:
        _api_key = api_key
    return {"ok": True, "has_api_key": bool(_api_key)}


# ── Active runs ──────────────────────────────────────────────────────────────


@app.get("/api/runs")
async def list_runs():
    return {
        rid: {
            "job_id": s.job_id,
            "done": s.done,
            "exit_code": s.exit_code,
            "event_count": len(s.events),
        }
        for rid, s in _runs.items()
    }


# ── WebSocket ────────────────────────────────────────────────────────────────


@app.websocket("/ws/{run_id}")
async def ws_endpoint(websocket: WebSocket, run_id: str):
    await websocket.accept()

    state = _runs.get(run_id)
    if not state:
        await websocket.send_json({"type": "error", "error": "Run not found"})
        await websocket.close()
        return

    queue: asyncio.Queue[dict] = asyncio.Queue()
    state.subscribers.append(queue)

    try:
        # Replay history
        for event in list(state.events):
            await websocket.send_json(event)

        # Stream live events
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("type") == "done":
                break
    except WebSocketDisconnect:
        pass
    finally:
        if queue in state.subscribers:
            state.subscribers.remove(queue)


# ── Subprocess monitor ───────────────────────────────────────────────────────


async def _monitor(run_id: str, state: RunState) -> None:
    """Read JSONL from subprocess stdout and fan out to subscribers."""
    try:
        async for line in state.proc.stdout:
            text = line.decode().strip()
            if not text:
                continue
            try:
                event = json.loads(text)
                state.events.append(event)
                if not state.job_id and "job_id" in event:
                    state.job_id = event["job_id"]
                for q in list(state.subscribers):
                    await q.put(event)
            except json.JSONDecodeError:
                pass

        stderr_bytes = await state.proc.stderr.read()
        state.stderr_tail = stderr_bytes.decode(errors="replace")[-2000:]

        await state.proc.wait()
        state.exit_code = state.proc.returncode

        # Resolve job_dir from job_id
        if state.job_id and not state.job_dir:
            jd = _find_job_dir(state.job_id)
            if jd:
                state.job_dir = str(jd)

    except Exception as exc:
        logger.exception("Monitor error for run %s", run_id)
        state.exit_code = -1
        state.stderr_tail = str(exc)

    state.done = True
    done_event = {
        "type": "done",
        "job_id": state.job_id,
        "exit_code": state.exit_code,
        "stderr": state.stderr_tail,
    }
    state.events.append(done_event)
    for q in list(state.subscribers):
        await q.put(done_event)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _find_job_dir(job_id: str) -> Path | None:
    candidate = Path.home() / ".prism" / "jobs" / job_id
    if candidate.exists() and (candidate / "manifest.json").exists():
        return candidate
    return None


def _read_manifest(job_dir: Path) -> dict | None:
    """Read a manifest.json, tolerating partial writes from concurrent pipelines."""
    path = job_dir / "manifest.json"
    for _ in range(3):
        try:
            text = path.read_text(encoding="utf-8")
            if text.strip():
                return json.loads(text)
        except (json.JSONDecodeError, OSError):
            import time
            time.sleep(0.05)
    return None


def _output_format(manifest: dict) -> str:
    for layer in reversed(manifest.get("pipeline", {}).get("layers", [])):
        if layer.get("type") == "output":
            return layer.get("format", "txt")
    return "txt"
