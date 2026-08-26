"""Orchestrator function for CBR-to-OBS migration.

This FunctionGraph function is triggered via API Gateway (HTTP).
It initiates the migration process for a CBR EVS backup to an OBS bucket.

Flow:
  1. Parse request (backup_id, source_region, target_region)
  2. If cross-region: start CBR backup replication
  3. If same-region: create EVS volume from backup
  4. Save job state to OBS
  5. Return job_id for tracking

The status_checker function (timer-triggered) will poll and advance the job.
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.shared.config import load_config
from src.shared.huawei_auth import HuaweiAuth
from src.shared.cbr_client import CBRClient
from src.shared.evs_client import EVSClient
from src.shared.obs_client import OBSClient
from src.shared.regions import get_bucket_name, get_region_config
from src.functions.orchestrator.job_model import create_job, update_step, mark_failed


def handler(event, context):
    """FunctionGraph entry point for the orchestrator.

    Expected event (APIG trigger):
        {
            "httpMethod": "POST",
            "body": "base64-encoded JSON",
            "queryStringParameters": {...}
        }

    Request body (JSON):
        {
            "backup_id": "xxx-xxx-xxx",
            "source_region": "buenosaires",   # or "sa-argentina-1"
            "target_region": "santiago"        # or "la-south-2" (optional, defaults to source)
        }

    Returns:
        Dict with statusCode and body containing job_id.
    """
    try:
        params = _parse_request(event)

        config = load_config()
        config.validate()

        auth = HuaweiAuth(config.access_key, config.secret_key)
        cbr = CBRClient(auth)
        evs = EVSClient(auth)
        obs = OBSClient(config.access_key, config.secret_key)

        backup_id = params["backup_id"]
        source_region = params["source_region"]
        target_region = params.get("target_region", source_region)

        backup = cbr.get_backup(source_region, backup_id)
        if not backup:
            return _response(404, {"error": f"Backup {backup_id} not found in {source_region}"})

        if backup.get("resource_type") != "OS::Cinder::Volume":
            return _response(400, {
                "error": f"Backup is not an EVS disk backup. "
                         f"Resource type: {backup.get('resource_type')}"
            })

        backup_name = backup.get("name", backup_id)
        resource_size = backup.get("resource_size", 0)

        job = create_job(
            backup_id=backup_id,
            backup_name=backup_name,
            source_region=source_region,
            target_region=target_region,
            resource_size_gb=resource_size,
        )

        if resource_size > config.raw_export_threshold_gb:
            job["path"] = "raw"
            config.get_temp_ecs_config(job["target_region"] if job["cross_region"] else source_region)

        target_bucket = get_bucket_name(target_region)
        job["bucket_name"] = target_bucket
        ext = "raw" if job["path"] == "raw" else "vhd"
        job["object_key"] = f"backups/{backup_id}/{backup_name}.{ext}"

        if job["cross_region"]:
            _start_replication(cbr, job, config)
        else:
            _start_restore(cbr, evs, job, config)

        obs.save_job_state(
            config.state_region, config.state_bucket, job["job_id"], job
        )

        return _response(202, {
            "job_id": job["job_id"],
            "step": job["step"],
            "message": "Migration job started. Use the status_checker to monitor progress.",
        })

    except Exception as e:
        return _response(500, {"error": str(e)})


def _parse_request(event):
    """Parse the FunctionGraph event to extract request parameters.

    Handles both APIG trigger events and direct invocation.

    Args:
        event: FunctionGraph event dict.

    Returns:
        Dict with backup_id, source_region, target_region.

    Raises:
        ValueError: If required parameters are missing.
    """
    if "body" in event and event.get("httpMethod") == "POST":
        import base64

        body = event["body"]
        if event.get("isBase64Encoded", True):
            body = base64.b64decode(body).decode("utf-8")
        params = json.loads(body)
    elif "backup_id" in event:
        params = event
    else:
        raise ValueError("Request must include backup_id and source_region")

    if "backup_id" not in params:
        raise ValueError("backup_id is required")
    if "source_region" not in params:
        raise ValueError("source_region is required")

    return params


def _start_replication(cbr, job, config):
    """Start cross-region backup replication.

    Args:
        cbr: CBRClient instance.
        job: Job state dict.
        config: Config instance.
    """
    target_vault_id = config.get_vault_id(job["target_region"])

    result = cbr.replicate_backup(
        source_region=job["source_region"],
        backup_id=job["backup_id"],
        target_region=job["target_region"],
        target_vault_id=target_vault_id,
        name=f"migration-{job['job_id'][:8]}",
        description=f"CBR-to-OBS migration job {job['job_id']}",
    )

    job["replication_record_id"] = result.get("replication_record_id", "")
    job["destination_backup_id"] = result.get("backup_id", "")


def _start_restore(cbr, evs, job, config):
    """Start volume creation from backup (same-region).

    Args:
        cbr: CBRClient instance.
        evs: EVSClient instance.
        job: Job state dict.
        config: Config instance.
    """
    volume_id = evs.create_volume_from_backup(
        region_input=job["source_region"],
        backup_id=job["backup_id"],
        name=f"cbr-mig-{job['job_id'][:8]}",
        volume_type=config.temp_volume_type,
    )

    job["volume_id"] = volume_id


def _response(status_code, body):
    """Build a FunctionGraph APIG response.

    Args:
        status_code: HTTP status code.
        body: Response body dict.

    Returns:
        Response dict for FunctionGraph.
    """
    import base64

    body_str = json.dumps(body)
    return {
        "statusCode": status_code,
        "body": base64.b64encode(body_str.encode("utf-8")).decode("utf-8"),
        "isBase64Encoded": True,
        "headers": {"Content-Type": "application/json"},
    }
