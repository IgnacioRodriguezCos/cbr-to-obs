# coding: utf-8
"""Stage 4: Create an IMS image from a restored EVS volume or ECS instance."""

from __future__ import annotations

import logging
import uuid

from huaweicloudsdkims.v2.ims_client import ImsClient
from huaweicloudsdkims.v2 import (
    CreateImageRequest, CreateImageRequestBody, CreateDataImage,
)

from poller import poll_ims_job

logger = logging.getLogger(__name__)


def create_image(
    ims_client: ImsClient,
    image_name: str,
    volume_id: str = "",
    instance_id: str = "",
    poll_interval: int = 15,
    poll_timeout: int = 3600,
) -> str:
    """Create a private image. Returns image_id.

    If instance_id is provided, creates from the ECS instance.
    Otherwise creates a data disk image from the volume.

    Note: for data disk images the volume MUST be attached to an ECS —
    IMS derives the OS type from the owning ECS. There is no os_type
    parameter in this flow (os_version is only valid for system disk images).
    """
    unique_name = f"{image_name}-{uuid.uuid4().hex[:6]}"

    if instance_id:
        logger.info("Creating image '%s' from ECS %s", unique_name, instance_id)
        body = CreateImageRequestBody(
            name=unique_name,
            instance_id=instance_id,
        )
    else:
        logger.info("Creating data disk image '%s' from volume %s", unique_name, volume_id)
        data_image = CreateDataImage(
            name=unique_name,
            volume_id=volume_id,
            description=f"Data disk image from volume {volume_id}",
        )
        body = CreateImageRequestBody(
            name=f"{unique_name}-root",
            data_images=[data_image],
        )

    request = CreateImageRequest(body=body)
    response = ims_client.create_image(request)
    job_id = response.job_id
    if not job_id:
        raise RuntimeError("CreateImage returned no job_id")

    result = poll_ims_job(ims_client, job_id, interval=poll_interval, timeout=poll_timeout)
    if result["status"] != "success":
        raise RuntimeError(f"Image creation failed: {result.get('error')}")

    image_id = result.get("image_id")
    if not image_id:
        raise RuntimeError("Image creation succeeded but no image_id returned")
    logger.info("Image created: %s (name='%s')", image_id, unique_name)
    return image_id
