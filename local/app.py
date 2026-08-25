"""Local web application for CBR-to-OBS migration.

Runs a FastAPI server on localhost that serves both the web UI
and the REST API backend. No FunctionGraph or APIG needed.

Usage:
    python local/app.py
    # Then open http://localhost:8080 in your browser

Or use the launcher:
    python run.py
    .\run.ps1
"""

import os
import sys
import json
import webbrowser
import threading

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.shared.huawei_auth import HuaweiAuth
from src.shared.cbr_client import CBRClient
from src.shared.evs_client import EVSClient
from src.shared.ims_client import IMSClient
from src.shared.obs_client import OBSClient
from src.shared.config import Config
from src.shared.regions import get_bucket_name
from src.functions.orchestrator.job_model import (
    create_job,
    update_step,
    mark_failed,
    STEP_RESTORING,
    STEP_REPLICATING,
)

app = FastAPI(title="CBR-to-OBS Migration")

HTML_PATH = os.path.join(os.path.dirname(__file__), "static", "index.html")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    import traceback

    traceback.print_exc()
    return JSONResponse(status_code=500, content={"error": str(exc)})


class Credentials(BaseModel):
    ak: str
    sk: str
    pid_ba: str = ""
    pid_cl: str = ""


class MigrateRequest(BaseModel):
    backup_id: str
    source_region: str
    target_region: str | None = None


class LoginRequest(BaseModel):
    ak: str
    sk: str


def _setup_env(ak, sk, pid_ba="", pid_cl=""):
    os.environ["HW_ACCESS_KEY"] = ak
    os.environ["HW_SECRET_KEY"] = sk
    if pid_ba:
        os.environ["HW_PROJECT_ID_BUENOSAIRES"] = pid_ba
    if pid_cl:
        os.environ["HW_PROJECT_ID_SANTIAGO"] = pid_cl


def _get_clients_from_headers(request: Request):
    ak = request.headers.get("X-HW-AK", "")
    sk = request.headers.get("X-HW-SK", "")
    pid_ba = request.headers.get("X-HW-Project-Id-BA", "")
    pid_cl = request.headers.get("X-HW-Project-Id-CL", "")
    _setup_env(ak, sk, pid_ba, pid_cl)
    auth = HuaweiAuth(ak, sk)
    return {
        "auth": auth,
        "cbr": CBRClient(auth),
        "evs": EVSClient(auth),
        "ims": IMSClient(auth),
        "obs": OBSClient(ak, sk),
        "config": Config(),
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


@app.post("/api/auth/validate")
async def validate_auth(req: LoginRequest):
    try:
        auth = HuaweiAuth(req.ak, req.sk)
        url = "https://iam.myhuaweicloud.com/v3/projects"
        headers = {"Content-Type": "application/json"}
        headers = auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30)
        if not resp.ok:
            try:
                err = resp.json()
                msg = err.get("error_msg") or str(err)[:200]
            except Exception:
                msg = resp.text[:200]
            return JSONResponse(status_code=401, content={"error": f"IAM: {msg}"})
        return {"valid": True}
    except Exception as e:
        return JSONResponse(status_code=401, content={"error": str(e)})


@app.get("/api/projects")
async def list_projects(request: Request, name: str | None = None):
    try:
        ak = request.headers.get("X-HW-AK", "")
        sk = request.headers.get("X-HW-SK", "")
        auth = HuaweiAuth(ak, sk)
        url = "https://iam.myhuaweicloud.com/v3/projects"
        if name:
            from urllib.parse import quote

            url += f"?name={quote(name)}"
        headers = {"Content-Type": "application/json"}
        headers = auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/backups")
async def list_backups(
    request: Request,
    region: str = "buenosaires",
    status: str = "available",
):
    try:
        clients = _get_clients_from_headers(request)
        backups = clients["cbr"].list_evs_backups(region, status=status)
        return {"backups": backups, "count": len(backups)}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/backups/{backup_id}")
async def get_backup(backup_id: str, request: Request, region: str = "buenosaires"):
    clients = _get_clients_from_headers(request)
    backup = clients["cbr"].get_backup(region, backup_id)
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")
    return {"backup": backup}


@app.get("/api/jobs")
async def list_jobs(request: Request):
    clients = _get_clients_from_headers(request)
    jobs = clients["obs"].list_pending_jobs(
        clients["config"].state_region, clients["config"].state_bucket
    )
    return {"jobs": jobs, "count": len(jobs)}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    clients = _get_clients_from_headers(request)
    job = clients["obs"].load_job_state(
        clients["config"].state_region, clients["config"].state_bucket, job_id
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": job}


@app.post("/api/migrate")
async def migrate(req: MigrateRequest, request: Request):
    clients = _get_clients_from_headers(request)
    cbr = clients["cbr"]
    evs = clients["evs"]
    obs = clients["obs"]
    config = clients["config"]

    target_region = req.target_region or req.source_region

    backup = cbr.get_backup(req.source_region, req.backup_id)
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    if backup.get("resource_type") != "OS::Cinder::Volume":
        raise HTTPException(
            status_code=400,
            detail=f"Backup is not EVS. Type: {backup.get('resource_type')}",
        )

    backup_name = backup.get("name", req.backup_id)
    resource_size = backup.get("resource_size", 0)

    job = create_job(
        backup_id=req.backup_id,
        backup_name=backup_name,
        source_region=req.source_region,
        target_region=target_region,
        resource_size_gb=resource_size,
    )

    job["bucket_name"] = get_bucket_name(target_region)
    job["object_key"] = f"backups/{req.backup_id}/{backup_name}.vhd"

    if job["cross_region"]:
        target_vault_id = config.get_vault_id(target_region)
        result = cbr.replicate_backup(
            source_region=req.source_region,
            backup_id=req.backup_id,
            target_region=target_region,
            target_vault_id=target_vault_id,
            name=f"migration-{job['job_id'][:8]}",
        )
        job["replication_record_id"] = result.get("replication_record_id", "")
        job["destination_backup_id"] = result.get("backup_id", "")
    else:
        volume_id = evs.create_volume_from_backup(
            region_input=req.source_region,
            backup_id=req.backup_id,
            name=f"cbr-mig-{job['job_id'][:8]}",
            volume_type=config.temp_volume_type,
        )
        job["volume_id"] = volume_id

    obs.save_job_state(config.state_region, config.state_bucket, job["job_id"], job)

    return {"job_id": job["job_id"], "step": job["step"], "message": "Migration started"}


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, request: Request):
    clients = _get_clients_from_headers(request)
    obs = clients["obs"]
    config = clients["config"]

    job = obs.load_job_state(config.state_region, config.state_bucket, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.get("step") != "failed":
        raise HTTPException(status_code=400, detail="Only failed jobs can be retried")

    job["step"] = STEP_RESTORING if not job.get("cross_region") else STEP_REPLICATING
    job["error"] = None
    job["retry_count"] = job.get("retry_count", 0) + 1
    import datetime
    job["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"

    obs.save_job_state(config.state_region, config.state_bucket, job_id, job)
    return {"job_id": job_id, "step": job["step"], "message": "Job retry initiated"}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str, request: Request):
    clients = _get_clients_from_headers(request)
    obs = clients["obs"]
    config = clients["config"]

    deleted = obs.delete_job_state(config.state_region, config.state_bucket, job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"message": "Job deleted"}


def open_browser():
    threading.Timer(1.0, lambda: webbrowser.open("http://localhost:8080")).start()


if __name__ == "__main__":
    import uvicorn

    open_browser()
    uvicorn.run(app, host="0.0.0.0", port=8080)
