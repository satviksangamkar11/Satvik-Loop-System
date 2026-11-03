"""The Control Center API (docs/API_CONTRACT.md v1): a local FastAPI app under /api. Run from the repo root:

    SATVIK_HOME=<folder outside every worktree> python -m uvicorn monitor.api:app --port 8765

Reads are GET; actions are POST, PUT, PATCH or DELETE. Each action runs through store.respond, so a repeat with the same Idempotency-Key
returns the first response and runs nothing again. Errors use the contract's envelope. The routes hold no logic of their own: they parse the
request and call views (reads), config_api (models, stages, limits, budget, verify) or actions (queue, loops, STOP, plans, stage edits)."""
import json
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import actions, config_api, views
from .store import ApiError, home as satvik_home, respond

app = FastAPI(title="Satvik Loop Control Center API", version="1", docs_url="/api/docs", openapi_url="/api/openapi.json")

_ORIGINS = [o.strip() for o in os.environ.get("SATVIK_UI_ORIGIN", "http://127.0.0.1:5173,http://localhost:5173").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_ORIGINS, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["Content-Type", "Idempotency-Key"])


# ---------------------------------------------------------------- errors: always {"error": {code, message, details}}

@app.exception_handler(ApiError)
async def _api_error(_request, exc: ApiError):
    return JSONResponse(status_code=exc.status, content=exc.body())


@app.exception_handler(RequestValidationError)
async def _validation(_request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"error": {"code": "invalid-request", "message": "the request does not match the contract",
                                                            "details": {"problems": [str(e.get("msg")) for e in exc.errors()]}}})


@app.exception_handler(StarletteHTTPException)
async def _http(_request, exc: StarletteHTTPException):
    code = "not-found" if exc.status_code == 404 else "http-error"
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": str(exc.detail), "details": {}}})


@app.exception_handler(Exception)
async def _unexpected(_request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": {"code": "internal", "message": type(exc).__name__, "details": {}}})


# ---------------------------------------------------------------- helpers

async def _json(request: Request) -> dict:
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "invalid-request", "the body is not JSON")
    if not isinstance(data, dict):
        raise ApiError(400, "invalid-request", "the body is a JSON object")
    return data


def _home():
    return satvik_home()


def _run(request: Request, body: dict, fn):
    """A mutation: once per Idempotency-Key. fn(home, body) -> (status, body). Returns the response."""
    key = request.headers.get("Idempotency-Key")
    home = _home()
    status, out = respond(home, key, lambda: fn(home, body))
    if status == 204:
        return Response(status_code=204)
    return JSONResponse(status_code=status, content=out)


def _query_int(request: Request, name: str, default: int) -> int:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(400, "bad-query", f"{name} is a whole number")
    if value < 0:
        raise ApiError(400, "bad-query", f"{name} is at least 0")
    return value


# ---------------------------------------------------------------- reads

@app.get("/api/overview")
def overview():
    return views.overview(_home())


@app.get("/api/jobs")
def jobs(request: Request):
    filter_ = request.query_params.get("filter")
    if filter_ is not None and filter_ not in ("all", "running", "queued", "finished", "failed"):
        raise ApiError(400, "bad-filter", "filter is all, running, queued, finished or failed")
    return views.job_list(_home(), None if filter_ in (None, "all") else filter_)


@app.get("/api/loops/{job_id}")
def loop(job_id: str):
    home = _home()
    out = views.detail(home, views._view(home, job_id))
    if out is None:
        raise ApiError(404, "unknown-job", f"no job {job_id}")
    return out


@app.get("/api/loops/{job_id}/events")
def loop_events(job_id: str, request: Request):
    return views.events(_home(), job_id, _query_int(request, "after", 0))


@app.get("/api/models")
def models():
    return config_api.list_models(_home_or_none())


@app.get("/api/models/search")
def models_search(request: Request):
    return config_api.search_models(request.query_params.get("q", ""), _home_or_none())


@app.get("/api/stages")
def stages(request: Request):
    return config_api.stages_view(request.query_params.get("profile"))


@app.get("/api/plans/{plan_id}")
def plan(plan_id: str):
    return actions.get_plan(plan_id)


@app.get("/api/results/{job_id}")
def results(job_id: str):
    return views.results(_home(), job_id)


@app.get("/api/results/{job_id}/diff")
def results_diff(job_id: str):
    return views.diff(_home(), job_id)


@app.get("/api/results/{job_id}/download")
def results_download(job_id: str):
    data = views.download(_home(), job_id)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{job_id}.zip"'})


@app.get("/api/system/health")
def health():
    return views.health(_home_or_none())


@app.get("/api/system/limits")
def limits():
    return views.limits_view(_home())


def _home_or_none():
    try:
        return satvik_home()
    except ApiError:
        return None


# ---------------------------------------------------------------- checks that change nothing (no key needed)

@app.post("/api/jobs/validate")
async def jobs_validate(request: Request):
    return actions.validate_job(await _json(request))


@app.post("/api/stages/validate")
async def stages_validate(request: Request):
    body = await _json(request)
    return config_api.validate_stages(body.get("profile"), body.get("changes", []))


@app.post("/api/budget/validate")
async def budget_validate(request: Request):
    body = await _json(request)
    return config_api.validate_budget(None, body.get("stages") or {}, body.get("paid_budget_usd", 0))


# ---------------------------------------------------------------- actions (once per Idempotency-Key)

@app.post("/api/jobs")
async def jobs_queue(request: Request):
    body = await _json(request)
    return _run(request, body, lambda home, b: actions.queue_job(b))


@app.post("/api/loops/start")
async def loops_start(request: Request):
    body = await _json(request)
    return _run(request, body, lambda home, b: actions.start_loops(b.get("loops")))


@app.patch("/api/loops/{job_id}/stages")
async def loop_stage(job_id: str, request: Request):
    body = await _json(request)
    return _run(request, body, lambda home, b: (200, actions.patch_stage(job_id, b.get("stage"), b.get("change"), b.get("revision"))))


@app.post("/api/plans")
async def plans(request: Request):
    body = await _json(request)
    return _run(request, body, lambda home, b: actions.create_plan(b))


@app.post("/api/plans/{plan_id}/approve")
async def plan_approve(plan_id: str, request: Request):
    body = await _json(request)
    return _run(request, body, lambda home, b: actions.approve_plan(plan_id, b.get("job_sha256"), b.get("approve_assumed")))


@app.post("/api/models")
async def models_add(request: Request):
    body = await _json(request)
    return _run(request, body, lambda home, b: config_api.add_model(b.get("model_id"), b.get("hosting_provider"), home))


@app.delete("/api/models/{model_id:path}")
async def models_remove(model_id: str, request: Request):
    return _run(request, {}, lambda home, b: config_api.remove_model(model_id, home))


@app.post("/api/models/verify")
async def models_verify(request: Request):
    body = await _json(request)
    return _run(request, body, lambda home, b: (200, config_api.verify_contract(home, b.get("model_id"), b.get("contract"))))


@app.post("/api/models/verify-reasoning")
async def models_verify_reasoning(request: Request):
    body = await _json(request)
    return _run(request, body, lambda home, b: (200, config_api.verify_reasoning(home, b.get("model_id"), b.get("efforts"))))


@app.post("/api/models/refresh")
async def models_refresh(request: Request):
    body = await _json(request)
    return _run(request, body, lambda home, b: (200, config_api.refresh_prices(home)))


@app.put("/api/stages")
async def stages_save(request: Request):
    body = await _json(request)
    return _run(request, body, lambda home, b: config_api.save_stages(b.get("profile"), b.get("revision"), b.get("changes", [])))


@app.put("/api/system/limits")
async def limits_update(request: Request):
    body = await _json(request)
    changes = {k: v for k, v in body.items() if k != "revision"}
    slots = 0
    if "sandbox_slots" in changes:
        slots = len(views._gate_holders(_home()).get("sandbox", []))
    return _run(request, body, lambda home, b: config_api.update_limits(b.get("revision"), changes, slots))


@app.post("/api/system/stop")
async def system_stop(request: Request):
    body = await _json(request)
    return _run(request, body, lambda home, b: (200, actions.set_stop(b.get("on"))))
