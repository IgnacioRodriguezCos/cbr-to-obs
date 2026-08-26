"""Migration job state model and step definitions.

Defines the job lifecycle and state transitions for the CBR-to-OBS migration.
"""

import datetime
import uuid

STEP_REPLICATING = "replicating"
STEP_RESTORING = "restoring"
STEP_ATTACHING_ECS = "attaching_ecs"
STEP_UPLOADING_RAW = "uploading_raw"
STEP_CREATING_IMAGE = "creating_image"
STEP_EXPORTING = "exporting"
STEP_COPYING_OBS = "copying_obs"
STEP_COMPLETED = "completed"
STEP_FAILED = "failed"
STEP_CLEANUP_PENDING = "cleanup_pending"

ACTIVE_STEPS = {
    STEP_REPLICATING,
    STEP_RESTORING,
    STEP_ATTACHING_ECS,
    STEP_UPLOADING_RAW,
    STEP_CREATING_IMAGE,
    STEP_EXPORTING,
    STEP_COPYING_OBS,
}

STEP_ORDER = [
    STEP_REPLICATING,
    STEP_RESTORING,
    STEP_CREATING_IMAGE,
    STEP_EXPORTING,
    STEP_COPYING_OBS,
    STEP_COMPLETED,
]

RAW_STEP_ORDER = [
    STEP_REPLICATING,
    STEP_RESTORING,
    STEP_ATTACHING_ECS,
    STEP_UPLOADING_RAW,
    STEP_COMPLETED,
]


def create_job(backup_id, backup_name, source_region, target_region, resource_size_gb=0):
    """Create a new migration job state.

    Args:
        backup_id: CBR backup ID.
        backup_name: Backup name for display.
        source_region: Source region alias or ID.
        target_region: Target region alias or ID.
        resource_size_gb: Size of the backup resource in GB.

    Returns:
        Job state dict.
    """
    from ...shared.regions import is_cross_region

    cross_region = is_cross_region(source_region, target_region)
    initial_step = STEP_REPLICATING if cross_region else STEP_RESTORING

    now = datetime.datetime.utcnow().isoformat() + "Z"

    return {
        "job_id": str(uuid.uuid4()),
        "backup_id": backup_id,
        "backup_name": backup_name,
        "source_region": source_region,
        "target_region": target_region,
        "cross_region": cross_region,
        "step": initial_step,
        "path": "ims",
        "temp_server_id": None,
        "temp_device": "/dev/vdb",
        "volume_id": None,
        "image_id": None,
        "export_job_id": None,
        "replication_record_id": None,
        "destination_backup_id": None,
        "bucket_name": None,
        "object_key": None,
        "resource_size_gb": resource_size_gb,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "retry_count": 0,
    }


def update_step(job, step, **extra):
    """Update a job's step and additional fields.

    Args:
        job: Job state dict.
        step: New step value.
        **extra: Additional fields to update.

    Returns:
        Updated job state dict.
    """
    job["step"] = step
    job["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    for key, value in extra.items():
        job[key] = value
    return job


def mark_failed(job, error):
    """Mark a job as failed.

    Args:
        job: Job state dict.
        error: Error message.

    Returns:
        Updated job state dict.
    """
    return update_step(job, STEP_FAILED, error=error)


def is_active(job):
    """Check if a job is in an active (non-terminal) state.

    Args:
        job: Job state dict.

    Returns:
        True if the job is still being processed.
    """
    return job.get("step") in ACTIVE_STEPS


def is_terminal(job):
    """Check if a job has reached a terminal state.

    Args:
        job: Job state dict.

    Returns:
        True if the job is completed or failed.
    """
    return job.get("step") in {STEP_COMPLETED, STEP_FAILED}
