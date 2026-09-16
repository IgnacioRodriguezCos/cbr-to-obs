# coding: utf-8
"""Stage 5: Ensure OBS bucket exists and export the image to it."""

from __future__ import annotations

import logging

from huaweicloudsdkims.v2.ims_client import ImsClient
from huaweicloudsdkims.v2 import ExportImageRequest, ExportImageRequestBody

from huaweicloudsdkobs.v1.obs_client import ObsClient
from huaweicloudsdkobs.v1 import (
    CreateBucketRequest,
    CreateBucketRequestBody,
    ListObjectsRequest,
    GetBucketMetadataRequest,
)

from poller import poll_ims_job

logger = logging.getLogger(__name__)

_STORAGE_STANDARD = "STANDARD"


def ensure_bucket(
    obs_client: ObsClient,
    bucket_name: str,
    region_id: str,
) -> None:
    """Create an OBS bucket with Standard storage class if it does not exist."""
    try:
        obs_client.get_bucket_metadata(GetBucketMetadataRequest(bucket_name=bucket_name))
        logger.info("Bucket '%s' already exists", bucket_name)
        return
    except Exception:
        pass

    logger.info("Creating bucket '%s' in %s (Standard)", bucket_name, region_id)
    request = CreateBucketRequest(
        bucket_name=bucket_name,
        x_obs_storage_class=_STORAGE_STANDARD,
        body=CreateBucketRequestBody(location=region_id),
    )
    obs_client.create_bucket(request)
    logger.info("Bucket created: %s", bucket_name)


def export_image_to_obs(
    ims_client: ImsClient,
    image_id: str,
    bucket_name: str,
    object_key: str,
    fast_export: bool = True,
    file_format: str = "qcow2",
    poll_interval: int = 15,
    poll_timeout: int = 7200,
) -> str:
    """Export a private image to an OBS bucket. Returns the job_id.

    When fast_export=True the image is exported in zvhd2 format (fast, supports >128GiB).
    When fast_export=False, file_format must be one of: qcow2, vhd, zvhd, vmdk.
    """
    body_kwargs: dict = {
        "bucket_url": f"{bucket_name}:{object_key}",
        "is_quick_export": fast_export,
    }
    if not fast_export:
        body_kwargs["file_format"] = file_format

    body = ExportImageRequestBody(**body_kwargs)
    request = ExportImageRequest(image_id=image_id, body=body)

    fmt_label = "zvhd2 (fast)" if fast_export else file_format
    logger.info(
        "Exporting image %s -> obs://%s/%s [%s]",
        image_id, bucket_name, object_key, fmt_label,
    )
    response = ims_client.export_image(request)
    job_id = response.job_id
    if not job_id:
        raise RuntimeError("ExportImage returned no job_id")

    result = poll_ims_job(ims_client, job_id, interval=poll_interval, timeout=poll_timeout)
    if result["status"] != "success":
        raise RuntimeError(f"Image export failed: {result.get('error')}")
    logger.info("Export complete: obs://%s/%s", bucket_name, object_key)
    return job_id


def verify_object_exists(
    obs_client: ObsClient,
    bucket_name: str,
    object_key: str,
) -> bool:
    """Verify that an object exists in an OBS bucket."""
    request = ListObjectsRequest(bucket_name=bucket_name, prefix=object_key, max_keys=10)
    response = obs_client.list_objects(request)
    for obj in (response.contents or []):
        if obj.key == object_key:
            logger.info(
                "Verified: obs://%s/%s exists (size=%s)", bucket_name, object_key, obj.size,
            )
            return True
    logger.warning("Object obs://%s/%s NOT found after export", bucket_name, object_key)
    return False
