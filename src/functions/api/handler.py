"""API router function for CBR-to-OBS migration frontend.

This FunctionGraph function serves as a REST API backend for the React frontend.
It handles all CRUD operations for backups, migration jobs, and migration triggers.

Routes:
    GET    /api/backups           - List CBR EVS backups (query: region)
    GET    /api/backups/{id}      - Get backup details (query: region)
    GET    /api/jobs              - List all migration jobs
    GET    /api/jobs/{id}         - Get specific job status
    POST   /api/migrate           - Start migration (body: backup_id, source_region, target_region?)
    POST   /api/jobs/{id}/retry   - Retry a failed job
    DELETE /api/jobs/{id}         - Delete job state

Authentication:
    Credentials are passed via headers from the frontend:
        X-HW-AK: Access Key
        X-HW-SK: Secret Key
        X-HW-Project-Id-BA: Project ID for Buenos Aires
        X-HW-Project-Id-CL: Project ID for Santiago
"""

import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.shared.huawei_auth import HuaweiAuth
from src.shared.cbr_client import CBRClient
from src.shared.evs_client import EVSClient
from src.shared.ims_client import IMSClient
from src.shared.obs_client import OBSClient
from src.shared.config import Config
from src.shared.regions import get_bucket_name, get_region_config
from src.functions.orchestrator.job_model import (
    create_job,
    update_step,
    mark_failed,
    STEP_RESTORING,
    STEP_REPLICATING,
)


def handler(event, context):
    """FunctionGraph entry point for the API router.

    Args:
        event: APIG trigger event dict.
        context: FunctionGraph context.

    Returns:
        APIG response dict with statusCode, body, and headers.
    """
    try:
        method, path, query, body = _parse_event(event)

        if method == "OPTIONS":
            return _cors_response(200, {})

        creds = _extract_credentials(event)
        if not creds["ak"] or not creds["sk"]:
            return _cors_response(401, {"error": "Missing credentials. Provide X-HW-AK and X-HW-SK headers."})

        env_backup = {
            "HW_ACCESS_KEY": creds["ak"],
            "HW_SECRET_KEY": creds["sk"],
            "HW_PROJECT_ID_BUENOSAIRES": creds["pid_ba"],
            "HW_PROJECT_ID_SANTIAGO": creds["pid_cl"],
            "HW_VAULT_ID_BUENOSAIRES": os.environ.get("HW_VAULT_ID_BUENOSAIRES", ""),
            "HW_VAULT_ID_SANTIAGO": os.environ.get("HW_VAULT_ID_SANTIAGO", ""),
            "OBS_STATE_BUCKET": os.environ.get("OBS_STATE_BUCKET", "cbr-migration-state"),
            "OBS_STATE_REGION": os.environ.get("OBS_STATE_REGION", "sa-argentina-1"),
            "TEMP_VOLUME_TYPE": os.environ.get("TEMP_VOLUME_TYPE", "SATA"),
            "TEMP_AZ_BUENOSAIRES": os.environ.get("TEMP_AZ_BUENOSAIRES", "sa-argentina-1a"),
            "TEMP_AZ_SANTIAGO": os.environ.get("TEMP_AZ_SANTIAGO", "la-south-2a"),
            "CLEANUP_AFTER_EXPORT": os.environ.get("CLEANUP_AFTER_EXPORT", "true"),
        }
        for k, v in env_backup.items():
            if v:
                os.environ[k] = v

        auth = HuaweiAuth(creds["ak"], creds["sk"])
        cbr = CBRClient(auth)
        evs = EVSClient(auth)
        ims = IMSClient(auth)
        obs = OBSClient(creds["ak"], creds["sk"])
        config = Config()

        result = _route(method, path, query, body, cbr, evs, ims, obs, config)

        return _cors_response(result[0], result[1])

    except Exception as e:
        return _cors_response(500, {"error": str(e)})


def _route(method, path, query, body, cbr, evs, ims, obs, config):
    """Route the request to the appropriate handler.

    Returns:
        Tuple of (status_code, response_body_dict).
    """
    segments = [s for s in path.strip("/").split("/") if s]

    if len(segments) == 0:
        return 200, {"service": "cbr-to-obs-api", "status": "ok"}

    if segments[0] != "api":
        return 404, {"error": "Not found"}

    segments = segments[1:]

    if segments == ["backups"] and method == "GET":
        return _list_backups(cbr, query)

    if len(segments) == 2 and segments[0] == "backups" and method == "GET":
        return _get_backup(cbr, segments[1], query)

    if segments == ["jobs"] and method == "GET":
        return _list_jobs(obs, config)

    if len(segments) == 2 and segments[0] == "jobs" and method == "GET":
        return _get_job(obs, segments[1], config)

    if segments == ["migrate"] and method == "POST":
        return _start_migration(cbr, evs, obs, body, config)

    if len(segments) == 3 and segments[0] == "jobs" and segments[2] == "retry" and method == "POST":
        return _retry_job(obs, segments[1], config)

    if len(segments) == 2 and segments[0] == "jobs" and method == "DELETE":
        return _delete_job(obs, segments[1], config)

    return 404, {"error": f"Route not found: {method} /api/{'/'.join(segments)}"}


def _list_backups(cbr, query):
    """GET /api/backups?region=buenosaires"""
    region = query.get("region", "buenosaires")
    status = query.get("status", "available")
    backups = cbr.list_evs_backups(region, status=status)
    return 200, {"backups": backups, "count": len(backups)}


def _get_backup(cbr, backup_id, query):
    """GET /api/backups/{id}?region=buenosaires"""
    region = query.get("region", "buenosaires")
    backup = cbr.get_backup(region, backup_id)
    if not backup:
        return 404, {"error": "Backup not found"}
    return 200, {"backup": backup}


def _list_jobs(obs, config):
    """GET /api/jobs"""
    jobs = obs.list_pending_jobs(config.state_region, config.state_bucket)
    return 200, {"jobs": jobs, "count": len(jobs)}


def _get_job(obs, job_id, config):
    """GET /api/jobs/{id}"""
    job = obs.load_job_state(config.state_region, config.state_bucket, job_id)
    if not job:
        return 404, {"error": "Job not found"}
    return 200, {"job": job}


def _start_migration(cbr, evs, obs, body, config):
    """POST /api/migrate"""
    backup_id = body.get("backup_id")
    source_region = body.get("source_region")
    target_region = body.get("target_region", source_region)

    if not backup_id or not source_region:
        return 400, {"error": "backup_id and source_region are required"}

    backup = cbr.get_backup(source_region, backup_id)
    if not backup:
        return 404, {"error": f"Backup {backup_id} not found"}

    if backup.get("resource_type") != "OS::Cinder::Volume":
        return 400, {"error": f"Backup is not EVS. Type: {backup.get('resource_type')}"}

    backup_name = backup.get("name", backup_id)
    resource_size = backup.get("resource_size", 0)

    job = create_job(
        backup_id=backup_id,
        backup_name=backup_name,
        source_region=source_region,
        target_region=target_region,
        resource_size_gb=resource_size,
    )

    target_bucket = get_bucket_name(target_region)
    job["bucket_name"] = target_bucket
    job["object_key"] = f"backups/{backup_id}/{backup_name}.vhd"

    if job["cross_region"]:
        target_vault_id = config.get_vault_id(target_region)
        result = cbr.replicate_backup(
            source_region=source_region,
            backup_id=backup_id,
            target_region=target_region,
            target_vault_id=target_vault_id,
            name=f"migration-{job['job_id'][:8]}",
        )
        job["replication_record_id"] = result.get("replication_record_id", "")
        job["destination_backup_id"] = result.get("backup_id", "")
    else:
        volume_id = evs.create_volume_from_backup(
            region_input=source_region,
            backup_id=backup_id,
            name=f"cbr-mig-{job['job_id'][:8]}",
            volume_type=config.temp_volume_type,
        )
        job["volume_id"] = volume_id

    obs.save_job_state(config.state_region, config.state_bucket, job["job_id"], job)

    return 202, {"job_id": job["job_id"], "step": job["step"], "message": "Migration started"}


def _retry_job(obs, job_id, config):
    """POST /api/jobs/{id}/retry"""
    job = obs.load_job_state(config.state_region, config.state_bucket, job_id)
    if not job:
        return 404, {"error": "Job not found"}

    if job.get("step") != "failed":
        return 400, {"error": "Only failed jobs can be retried"}

    job["step"] = STEP_RESTORING if not job.get("cross_region") else STEP_REPLICATING
    job["error"] = None
    job["retry_count"] = job.get("retry_count", 0) + 1
    job["updated_at"] = _now()

    obs.save_job_state(config.state_region, config.state_bucket, job_id, job)
    return 200, {"job_id": job_id, "step": job["step"], "message": "Job retry initiated"}


def _delete_job(obs, job_id, config):
    """DELETE /api/jobs/{id}"""
    deleted = obs.delete_job_state(config.state_region, config.state_bucket, job_id)
    if not deleted:
        return 404, {"error": "Job not found"}
    return 200, {"message": "Job deleted"}


def _parse_event(event):
    """Parse APIG event to extract method, path, query params, and body.

    Returns:
        Tuple of (method, path, query_dict, body_dict).
    """
    method = event.get("httpMethod", "GET")
    path = event.get("path", "/")

    raw_query = event.get("queryStringParameters", {})
    query = raw_query if isinstance(raw_query, dict) else {}

    raw_body = event.get("body", "")
    if event.get("isBase64Encoded", False) and raw_body:
        raw_body = base64.b64decode(raw_body).decode("utf-8")

    body = {}
    if raw_body:
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            pass

    return method, path, query, body


def _extract_credentials(event):
    """Extract AK/SK and project IDs from request headers.

    Returns:
        Dict with ak, sk, pid_ba, pid_cl.
    """
    headers = event.get("headers", {})
    headers_lower = {k.lower(): v for k, v in headers.items()}

    return {
        "ak": headers_lower.get("x-hw-ak", ""),
        "sk": headers_lower.get("x-hw-sk", ""),
        "pid_ba": headers_lower.get("x-hw-project-id-ba", ""),
        "pid_cl": headers_lower.get("x-hw-project-id-cl", ""),
    }


def _now():
    """Get current UTC timestamp."""
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


def _cors_response(status_code, body):
    """Build APIG response with CORS headers.

    Args:
        status_code: HTTP status code.
        body: Response body dict.

    Returns:
        APIG response dict.
    """
    body_str = json.dumps(body)
    return {
        "statusCode": status_code,
        "body": base64.b64encode(body_str.encode("utf-8")).decode("utf-8"),
        "isBase64Encoded": True,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-HW-AK, X-HW-SK, X-HW-Project-Id-BA, X-HW-Project-Id-CL",
        },
    }
