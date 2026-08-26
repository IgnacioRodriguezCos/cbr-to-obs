"""Status checker function for CBR-to-OBS migration.

This FunctionGraph function is triggered by a Timer (every 5 minutes).
It polls all pending migration jobs and advances them to the next step.

Steps:
  replicating    -> wait for CBR replication, then create volume
  restoring      -> wait for EVS volume available, then create image
  creating_image -> wait for IMS image active, then export to OBS
  exporting      -> wait for IMS export job complete, then (cross-region) copy OBS
  copying_obs    -> wait for OBS cross-region copy, then mark completed
"""

import json
import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.shared.config import load_config
from src.shared.huawei_auth import HuaweiAuth
from src.shared.cbr_client import CBRClient
from src.shared.evs_client import EVSClient
from src.shared.ims_client import IMSClient
from src.shared.ecs_client import ECSClient
from src.shared.obs_client import OBSClient
from src.shared.raw_export import build_user_data, generate_password, marker_key
from src.shared.regions import get_bucket_name
from src.functions.orchestrator.job_model import (
    STEP_REPLICATING,
    STEP_RESTORING,
    STEP_ATTACHING_ECS,
    STEP_UPLOADING_RAW,
    STEP_CREATING_IMAGE,
    STEP_EXPORTING,
    STEP_COPYING_OBS,
    STEP_COMPLETED,
    STEP_CLEANUP_PENDING,
    update_step,
    mark_failed,
    is_active,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def handler(event, context):
    """FunctionGraph entry point for the status checker.

    Triggered by Timer. Processes all pending migration jobs.

    Returns:
        Dict with summary of processed jobs.
    """
    try:
        config = load_config()
        config.validate()

        auth = HuaweiAuth(config.access_key, config.secret_key)
        clients = {
            "cbr": CBRClient(auth),
            "evs": EVSClient(auth),
            "ims": IMSClient(auth),
            "ecs": ECSClient(auth),
            "obs": OBSClient(config.access_key, config.secret_key),
        }

        obs = clients["obs"]
        jobs = obs.list_pending_jobs(config.state_region, config.state_bucket)

        processed = 0
        advanced = 0
        failed = 0
        completed = 0

        for job in jobs:
            if not is_active(job):
                continue

            processed += 1
            try:
                result = _process_job(job, clients, config)
                if result == "advanced":
                    advanced += 1
                elif result == "completed":
                    completed += 1
                elif result == "failed":
                    failed += 1

                obs.save_job_state(
                    config.state_region, config.state_bucket, job["job_id"], job
                )
            except Exception as e:
                logger.error(f"Error processing job {job['job_id']}: {e}")
                mark_failed(job, str(e))
                obs.save_job_state(
                    config.state_region, config.state_bucket, job["job_id"], job
                )
                failed += 1

        summary = {
            "processed": processed,
            "advanced": advanced,
            "completed": completed,
            "failed": failed,
        }
        logger.info(f"Status check complete: {summary}")
        return summary

    except Exception as e:
        logger.error(f"Status checker failed: {e}")
        return {"error": str(e)}


def _process_job(job, clients, config):
    """Process a single migration job and advance it if possible.

    Args:
        job: Job state dict.
        clients: Dict of API clients.
        config: Config instance.

    Returns:
        'pending' if waiting, 'advanced' if moved to next step,
        'completed' if finished, 'failed' if error.
    """
    step = job["step"]

    if step == STEP_REPLICATING:
        return _handle_replicating(job, clients, config)
    elif step == STEP_RESTORING:
        return _handle_restoring(job, clients, config)
    elif step == STEP_ATTACHING_ECS:
        return _handle_attaching_ecs(job, clients, config)
    elif step == STEP_UPLOADING_RAW:
        return _handle_uploading_raw(job, clients, config)
    elif step == STEP_CREATING_IMAGE:
        return _handle_creating_image(job, clients, config)
    elif step == STEP_EXPORTING:
        return _handle_exporting(job, clients, config)
    elif step == STEP_COPYING_OBS:
        return _handle_copying_obs(job, clients, config)

    return "pending"


def _handle_replicating(job, clients, config):
    """Check if cross-region replication is complete, then start volume creation.

    Args:
        job: Job state dict.
        clients: Dict of API clients.
        config: Config instance.

    Returns:
        'advanced' if replication complete and volume creation started,
        'pending' if still replicating.
    """
    cbr = clients["cbr"]

    replication_records = cbr.get_replication_status(
        job["source_region"], job["backup_id"]
    )

    for record in replication_records:
        status = record.get("status", "")
        if status == "success":
            dest_backup_id = record.get("destination_backup_id", "")
            if dest_backup_id:
                job["destination_backup_id"] = dest_backup_id

                evs = clients["evs"]
                volume_id = evs.create_volume_from_backup(
                    region_input=job["target_region"],
                    backup_id=dest_backup_id,
                    name=f"cbr-mig-{job['job_id'][:8]}",
                    volume_type=config.temp_volume_type,
                )
                update_step(job, STEP_RESTORING, volume_id=volume_id)
                return "advanced"
        elif status == "fail":
            mark_failed(job, f"Replication failed: {record.get('extra_info', {}).get('fail_reason', 'unknown')}")
            return "failed"

    return "pending"


def _handle_restoring(job, clients, config):
    """Check if EVS volume is available, then create IMS image from it.

    Args:
        job: Job state dict.
        clients: Dict of API clients.
        config: Config instance.

    Returns:
        'advanced' if volume is available and image creation started,
        'pending' if volume still creating.
    """
    evs = clients["evs"]

    region = job["target_region"] if job["cross_region"] else job["source_region"]
    status = evs.get_volume_status(region, job["volume_id"])

    if status == "available":
        if job.get("path") == "raw":
            ecs = clients["ecs"]
            if not job.get("temp_server_id"):
                user_data = build_user_data(job, config)
                admin_pass = generate_password()
                server_id = ecs.create_server(
                    region,
                    name=f"cbr-mig-{job['job_id'][:8]}",
                    user_data=user_data,
                    admin_pass=admin_pass,
                )
                update_step(
                    job,
                    STEP_ATTACHING_ECS,
                    temp_server_id=server_id,
                )
                return "advanced"
            update_step(job, STEP_ATTACHING_ECS)
            return "advanced"

        image_result = evs.create_image_from_volume(
            region_input=region,
            volume_id=job["volume_id"],
            image_name=f"cbr-mig-{job['job_id'][:8]}",
        )
        update_step(job, STEP_CREATING_IMAGE, image_id=image_result["image_id"])
        return "advanced"
    elif status == "error":
        mark_failed(job, f"Volume creation failed. Volume ID: {job['volume_id']}")
        return "failed"

    return "pending"


def _handle_attaching_ecs(job, clients, config):
    """Wait for temp ECS active and volume attached, then start raw upload watch.

    Args:
        job: Job state dict.
        clients: Dict of API clients.
        config: Config instance.

    Returns:
        'advanced' once volume is attached to the ECS,
        'pending' while the ECS builds or attach is in progress.
    """
    ecs = clients["ecs"]

    region = job["target_region"] if job["cross_region"] else job["source_region"]
    server = ecs.get_server(region, job["temp_server_id"])
    server_status = server.get("status", "")

    if server_status == "ERROR":
        mark_failed(job, f"Temp ECS entered ERROR state. Server ID: {job['temp_server_id']}")
        return "failed"

    if server_status != "ACTIVE":
        return "pending"

    attachment = ecs.get_attachment(region, job["temp_server_id"], job["volume_id"])
    if attachment:
        update_step(job, STEP_UPLOADING_RAW)
        return "advanced"

    if not job.get("attach_requested"):
        ecs.attach_volume(
            region,
            job["temp_server_id"],
            job["volume_id"],
            device=job.get("temp_device", "/dev/vdb"),
        )
        job["attach_requested"] = True

    return "pending"


def _handle_uploading_raw(job, clients, config):
    """Check for the raw upload success marker in OBS.

    The temp ECS streams the raw disk with dd | obsutil and writes a
    .SUCCESS marker object when done. This handler only polls for it.

    Args:
        job: Job state dict.
        clients: Dict of API clients.
        config: Config instance.

    Returns:
        'advanced'/'completed' when marker found, 'pending' otherwise.
    """
    obs = clients["obs"]

    region = job["target_region"] if job["cross_region"] else job["source_region"]
    exists = obs.object_exists(region, job["bucket_name"], marker_key(job["object_key"]))

    if exists:
        if config.cleanup_after_export:
            update_step(job, STEP_CLEANUP_PENDING)
            return "advanced"
        update_step(job, STEP_COMPLETED)
        return "completed"

    return "pending"


def _handle_creating_image(job, clients, config):
    """Check if IMS image is active, then start export to OBS.

    Args:
        job: Job state dict.
        clients: Dict of API clients.
        config: Config instance.

    Returns:
        'advanced' if image is active and export started,
        'pending' if image still creating.
    """
    ims = clients["ims"]

    region = job["target_region"] if job["cross_region"] else job["source_region"]
    status = ims.get_image_status(region, job["image_id"])

    if status == "active":
        export_job_id = ims.export_image_to_obs(
            region_input=region,
            image_id=job["image_id"],
            bucket_name=job["bucket_name"],
            object_key=job["object_key"],
        )
        update_step(job, STEP_EXPORTING, export_job_id=export_job_id)
        return "advanced"
    elif status in ("killed", "error"):
        mark_failed(job, f"Image creation failed. Image ID: {job['image_id']}")
        return "failed"

    return "pending"


def _handle_exporting(job, clients, config):
    """Check if IMS export job is complete, then handle OBS copy or completion.

    Args:
        job: Job state dict.
        clients: Dict of API clients.
        config: Config instance.

    Returns:
        'advanced' or 'completed' if export is done,
        'pending' if still exporting.
    """
    ims = clients["ims"]

    region = job["target_region"] if job["cross_region"] else job["source_region"]
    job_status = ims.get_job_status(region, job["export_job_id"])

    status = job_status.get("status", "")

    if status == "SUCCESS":
        if config.cleanup_after_export:
            update_step(job, STEP_CLEANUP_PENDING)
        else:
            update_step(job, STEP_COMPLETED)
        return "advanced" if config.cleanup_after_export else "completed"
    elif status in ("FAIL", "FAILED"):
        mark_failed(job, f"Image export failed. Job ID: {job['export_job_id']}")
        return "failed"

    return "pending"


def _handle_copying_obs(job, clients, config):
    """Handle OBS cross-region copy (if needed) and mark job completed.

    Args:
        job: Job state dict.
        clients: Dict of API clients.
        config: Config instance.

    Returns:
        'completed' if copy is done.
    """
    update_step(job, STEP_COMPLETED)
    return "completed"
