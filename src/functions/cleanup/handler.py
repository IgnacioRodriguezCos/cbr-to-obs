"""Cleanup function for CBR-to-OBS migration.

This FunctionGraph function is triggered by a Timer.
It cleans up temporary resources (EVS volumes and IMS images)
for completed migration jobs.
"""

import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.shared.config import load_config
from src.shared.huawei_auth import HuaweiAuth
from src.shared.evs_client import EVSClient
from src.shared.ims_client import IMSClient
from src.shared.ecs_client import ECSClient
from src.shared.obs_client import OBSClient
from src.functions.orchestrator.job_model import (
    STEP_CLEANUP_PENDING,
    STEP_COMPLETED,
    STEP_FAILED,
    update_step,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def handler(event, context):
    """FunctionGraph entry point for cleanup.

    Triggered by Timer. Finds jobs in 'cleanup_pending' state,
    deletes temporary volumes and images, then marks jobs as completed.

    Returns:
        Dict with cleanup summary.
    """
    try:
        config = load_config()
        config.validate()

        auth = HuaweiAuth(config.access_key, config.secret_key)
        evs = EVSClient(auth)
        ims = IMSClient(auth)
        ecs = ECSClient(auth)
        obs = OBSClient(config.access_key, config.secret_key)

        jobs = obs.list_pending_jobs(config.state_region, config.state_bucket)

        cleaned = 0
        errors = 0

        for job in jobs:
            if job.get("step") != STEP_CLEANUP_PENDING:
                continue

            try:
                region = job["target_region"] if job.get("cross_region") else job["source_region"]

                if job.get("temp_server_id"):
                    try:
                        ecs.delete_server(region, job["temp_server_id"])
                        logger.info(f"Deleted temp ECS {job['temp_server_id']} for job {job['job_id']}")
                    except Exception as e:
                        logger.warning(f"Failed to delete ECS {job['temp_server_id']}: {e}")

                if job.get("volume_id"):
                    try:
                        evs.delete_volume(region, job["volume_id"])
                        logger.info(f"Deleted volume {job['volume_id']} for job {job['job_id']}")
                    except Exception as e:
                        logger.warning(f"Failed to delete volume {job['volume_id']}: {e}")

                if job.get("image_id"):
                    try:
                        ims.delete_image(region, job["image_id"])
                        logger.info(f"Deleted image {job['image_id']} for job {job['job_id']}")
                    except Exception as e:
                        logger.warning(f"Failed to delete image {job['image_id']}: {e}")

                update_step(job, STEP_COMPLETED)
                obs.save_job_state(
                    config.state_region, config.state_bucket, job["job_id"], job
                )
                cleaned += 1

            except Exception as e:
                logger.error(f"Cleanup error for job {job['job_id']}: {e}")
                errors += 1

        summary = {"cleaned": cleaned, "errors": errors}
        logger.info(f"Cleanup complete: {summary}")
        return summary

    except Exception as e:
        logger.error(f"Cleanup function failed: {e}")
        return {"error": str(e)}
