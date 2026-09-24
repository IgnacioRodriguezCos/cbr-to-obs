# coding: utf-8
"""Stage 4: Create an IMS image from a restored EVS volume or ECS instance."""

from __future__ import annotations

import logging
import uuid

from huaweicloudsdkims.v2.ims_client import ImsClient
from huaweicloudsdkims.v2 import (
    CreateImageRequest, CreateImageRequestBody, CreateDataImage,
    ListImagesRequest, GlanceUpdateImageRequest, GlanceUpdateImageRequestBody,
)

from poller import poll_ims_job

logger = logging.getLogger(__name__)


def _log_and_fix_os_type(ims_client: ImsClient, image_id: str, expected_os_type: str) -> None:
    """Log the created image's OS metadata; best-effort fix via GlanceUpdateImage."""
    try:
        imgs = ims_client.list_images(ListImagesRequest(id=image_id, limit=1)).images or []
        if not imgs:
            logger.warning("Image %s not found in ListImages", image_id)
            return
        img = imgs[0]
        logger.info(
            "Image metadata: name='%s' os_type=%s os_version=%s virtual_env_type=%s",
            img.name, img.os_type, img.os_version, img.virtual_env_type,
        )
        if not expected_os_type:
            return
        if (img.os_type or "").lower() == expected_os_type.lower():
            logger.info("Image OS type matches expected '%s'", expected_os_type)
            return
        logger.warning("Image OS type is '%s', expected '%s' — attempting metadata fix",
                       img.os_type, expected_os_type)
        try:
            ims_client.glance_update_image(GlanceUpdateImageRequest(
                image_id=image_id,
                body=[GlanceUpdateImageRequestBody(
                    op="replace", path="/__os_type", value=expected_os_type,
                )],
            ))
            logger.info("Image __os_type updated to '%s'", expected_os_type)
        except Exception as e:
            logger.warning("Could not update __os_type (continuing anyway): %s", e)
    except Exception as e:
        logger.warning("Could not inspect image metadata (continuing anyway): %s", e)


def create_image(
    ims_client: ImsClient,
    image_name: str,
    volume_id: str = "",
    instance_id: str = "",
    expected_os_type: str = "",
    poll_interval: int = 15,
    poll_timeout: int = 3600,
) -> str:
    """Create a private image. Returns image_id.

    If instance_id is provided, creates from the ECS instance.
    Otherwise creates a data disk image from the volume.

    Notes learned from IMG.0009/IMG.0138 errors:
    - The CreateImage API is multi-scenario. When body-level `name` is set
      alongside `data_images` (without instance_id/volume_id/image_url), IMS
      appears to also attempt a system disk image creation with no OS source,
      failing with IMG.0138 (or IMG.0009 when names collide).
    - Therefore, for the data_images flow the body must contain ONLY
      data_images. The image name comes from data_images[0].name.
    - The volume should be attached to an ECS (per API docs); IMS derives
      the OS type from the owning ECS context.
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

    if not instance_id:
        _log_and_fix_os_type(ims_client, image_id, expected_os_type)

    return image_id
