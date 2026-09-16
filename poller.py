# coding: utf-8
"""Async job polling utilities for IMS and EVS."""

from __future__ import annotations

import time
import logging
from typing import Callable, Optional

from huaweicloudsdkims.v2.ims_client import ImsClient
from huaweicloudsdkims.v2 import ShowJobRequest as ImsShowJobRequest

from huaweicloudsdkevs.v2.evs_client import EvsClient
from huaweicloudsdkevs.v2 import ShowJobRequest as EvsShowJobRequest
from huaweicloudsdkevs.v2 import ShowVolumeRequest

logger = logging.getLogger(__name__)

_SUCCESS = "SUCCESS"
_FAIL = "FAIL"
_RUNNING = "RUNNING"
_INIT = "INIT"


def poll_ims_job(
    ims_client: ImsClient,
    job_id: str,
    interval: int = 15,
    timeout: int = 3600,
) -> dict:
    """Poll an IMS async job until completion. Returns dict with status, image_id, error."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = ims_client.show_job(ImsShowJobRequest(job_id=job_id))
        status = (resp.status or "").upper()
        entities = resp.entities
        image_id = entities.image_id if entities else None
        progress = entities.process_percent if entities else None
        logger.info(
            "  IMS job %s: status=%s progress=%s image_id=%s",
            job_id, status, progress, image_id,
        )
        if status == _SUCCESS:
            return {"status": "success", "job_id": job_id, "image_id": image_id}
        if status == _FAIL:
            err = resp.error_code or ""
            reason = resp.fail_reason or ""
            if entities and entities.error_code:
                err = entities.error_code
            return {
                "status": "failed",
                "job_id": job_id,
                "error": f"{err}: {reason}",
            }
        time.sleep(interval)
    raise TimeoutError(f"IMS job {job_id} timed out after {timeout}s")


def poll_evs_job(
    evs_client: EvsClient,
    job_id: str,
    interval: int = 15,
    timeout: int = 3600,
) -> dict:
    """Poll an EVS async job until completion. Returns dict with status, volume_id, error."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = evs_client.show_job(EvsShowJobRequest(job_id=job_id))
        status = (resp.status or "").upper()
        entities = resp.entities
        volume_id = entities.volume_id if entities else None
        logger.info(
            "  EVS job %s: status=%s volume_id=%s",
            job_id, status, volume_id,
        )
        if status == _SUCCESS:
            return {"status": "success", "job_id": job_id, "volume_id": volume_id}
        if status == _FAIL:
            err = resp.error_code or ""
            reason = resp.fail_reason or ""
            return {
                "status": "failed",
                "job_id": job_id,
                "error": f"{err}: {reason}",
            }
        time.sleep(interval)
    raise TimeoutError(f"EVS job {job_id} timed out after {timeout}s")


def wait_volume_available(
    evs_client: EvsClient,
    volume_id: str,
    interval: int = 10,
    timeout: int = 1800,
) -> str:
    """Wait until an EVS volume reaches 'available' status. Returns the status.

    Tolerates 404 responses while the volume is still being created.
    """
    from huaweicloudsdkcore.exceptions import exceptions as _exc
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = evs_client.show_volume(ShowVolumeRequest(volume_id=volume_id))
            status = resp.volume.status or ""
        except _exc.ClientRequestException as e:
            if e.status_code == 404:
                logger.info("  Volume %s not found yet (404) — still creating...", volume_id)
                time.sleep(interval)
                continue
            raise
        logger.info("  Volume %s status=%s", volume_id, status)
        if status == "available":
            return status
        if status == "error":
            raise RuntimeError(f"Volume {volume_id} entered error state")
        time.sleep(interval)
    raise TimeoutError(f"Volume {volume_id} did not become available after {timeout}s")
