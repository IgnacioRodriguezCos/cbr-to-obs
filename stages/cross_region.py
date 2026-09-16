# coding: utf-8
"""Stage 4: Copy an image cross-region (only when source != target region)."""

from __future__ import annotations

import logging

from huaweicloudsdkims.v2.ims_client import ImsClient
from huaweicloudsdkims.v2 import (
    CopyImageCrossRegionRequest,
    CopyImageCrossRegionRequestBody,
)

from poller import poll_ims_job

logger = logging.getLogger(__name__)


def copy_image_cross_region(
    ims_client: ImsClient,
    image_id: str,
    target_region: str,
    target_project_name: str,
    agency_name: str,
    new_name: str,
    poll_interval: int = 15,
    poll_timeout: int = 3600,
) -> str:
    """Copy a private image to another region. Returns the new image_id in target region.

    Requires a pre-configured IAM agency with IMS delegation permissions.
    """
    body = CopyImageCrossRegionRequestBody(
        agency_name=agency_name,
        name=new_name,
        project_name=target_project_name,
        region=target_region,
    )
    request = CopyImageCrossRegionRequest(image_id=image_id, body=body)

    logger.info(
        "Copying image %s -> region %s (agency=%s)",
        image_id, target_region, agency_name,
    )
    response = ims_client.copy_image_cross_region(request)
    job_id = response.job_id
    if not job_id:
        raise RuntimeError("CopyImageCrossRegion returned no job_id")

    result = poll_ims_job(ims_client, job_id, interval=poll_interval, timeout=poll_timeout)
    if result["status"] != "success":
        raise RuntimeError(f"Cross-region copy failed: {result.get('error')}")

    new_image_id = result.get("image_id")
    if not new_image_id:
        raise RuntimeError("Cross-region copy succeeded but no image_id returned")
    logger.info("Image copied to %s: new image_id=%s", target_region, new_image_id)
    return new_image_id
