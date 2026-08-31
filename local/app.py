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

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.shared.huawei_auth import HuaweiAuth
from src.shared.cbr_client import CBRClient
from src.shared.evs_client import EVSClient
from src.shared.ims_client import IMSClient
from src.shared.ecs_client import ECSClient
from src.shared.obs_client import OBSClient
from src.shared.config import Config
from src.shared.regions import get_bucket_name
from src.functions.orchestrator.job_model import (
    create_job,
    update_step,
    mark_failed,
    is_active,
    STEP_RESTORING,
    STEP_REPLICATING,
)

app = FastAPI(title="CBR-to-OBS Migration")

HTML_PATH = os.path.join(os.path.dirname(__file__), "static", "index.html")
STATE_DIR = os.path.join(os.path.dirname(__file__), "state")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    import traceback

    traceback.print_exc()
    return JSONResponse(status_code=500, content={"error": str(exc)})


def _save_job_local(job_id, state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, f"{job_id}.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _load_job_local(job_id):
    path = os.path.join(STATE_DIR, f"{job_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _list_jobs_local():
    if not os.path.exists(STATE_DIR):
        return []
    jobs = []
    for fn in os.listdir(STATE_DIR):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(STATE_DIR, fn), "r", encoding="utf-8") as f:
                    jobs.append(json.load(f))
            except (json.JSONDecodeError, IOError):
                pass
    return jobs


def _delete_job_local(job_id):
    path = os.path.join(STATE_DIR, f"{job_id}.json")
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


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
        "ecs": ECSClient(auth),
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
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
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
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
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
    try:
        clients = _get_clients_from_headers(request)
        from src.functions.status_checker.handler import _process_job

        jobs = _list_jobs_local()
        for job in jobs:
            if not is_active(job):
                continue
            try:
                _process_job(job, clients, clients["config"])
                _save_job_local(job["job_id"], job)
            except Exception as e:
                mark_failed(job, str(e))
                _save_job_local(job["job_id"], job)

        jobs = _list_jobs_local()
        return {"jobs": jobs, "count": len(jobs)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    job = _load_job_local(job_id)
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
    if not resource_size:
        size_mb = backup.get("size", 0)
        if size_mb:
            resource_size = size_mb / 1024

    job = create_job(
        backup_id=req.backup_id,
        backup_name=backup_name,
        source_region=req.source_region,
        target_region=target_region,
        resource_size_gb=resource_size,
    )

    if resource_size > config.raw_export_threshold_gb:
        job["path"] = "raw"
        try:
            config.get_temp_ecs_config(job["target_region"] if job["cross_region"] else req.source_region)
        except ValueError:
            ecs = clients["ecs"]
            detect_region = job["target_region"] if job["cross_region"] else req.source_region
            detected = ecs.auto_detect_config(detect_region)
            from src.shared.regions import get_region_config
            suffix = "BA" if get_region_config(detect_region)["id"] == "sa-argentina-1" else "CL"
            os.environ[f"TEMP_ECS_IMAGE_ID_{suffix}"] = detected["image_id"]
            os.environ[f"TEMP_ECS_FLAVOR_{suffix}"] = detected["flavor_id"]
            os.environ[f"TEMP_ECS_NETWORK_ID_{suffix}"] = detected["network_id"]
            os.environ[f"TEMP_ECS_VPC_ID_{suffix}"] = detected["vpc_id"]

    job["bucket_name"] = get_bucket_name(target_region)
    ext = "raw" if job["path"] == "raw" else "vhd"
    job["object_key"] = f"backups/{req.backup_id}/{backup_name}.{ext}"

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

    obs.ensure_bucket(target_region, job["bucket_name"])

    _save_job_local(job["job_id"], job)

    return {"job_id": job["job_id"], "step": job["step"], "message": "Migration started"}


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, request: Request):
    job = _load_job_local(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.get("step") != "failed":
        raise HTTPException(status_code=400, detail="Only failed jobs can be retried")

    job["step"] = STEP_RESTORING if not job.get("cross_region") else STEP_REPLICATING
    job["error"] = None
    job["retry_count"] = job.get("retry_count", 0) + 1
    job["volume_id"] = None
    job["image_id"] = None
    job["image_job_id"] = None
    job["export_job_id"] = None
    job["temp_server_id"] = None
    job["attach_requested"] = False
    import datetime
    job["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"

    _save_job_local(job_id, job)
    return {"job_id": job_id, "step": job["step"], "message": "Job retry initiated"}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str, request: Request):
    deleted = _delete_job_local(job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"message": "Job deleted"}


def open_browser():
    threading.Timer(1.0, lambda: webbrowser.open("http://localhost:8080")).start()


if __name__ == "__main__":
    import uvicorn

    open_browser()
    uvicorn.run(app, host="0.0.0.0", port=8080)
