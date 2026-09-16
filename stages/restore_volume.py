# coding: utf-8
"""Stage 2: Restore a CBR disk backup onto a new EVS volume."""

from __future__ import annotations

import logging

from huaweicloudsdkcore.exceptions import exceptions

from huaweicloudsdkcbr.v1.cbr_client import CbrClient
from huaweicloudsdkcbr.v1 import (
    RestoreBackupRequest, BackupRestoreReq, BackupRestore,
    ShowBackupRequest,
)

from huaweicloudsdkevs.v2.evs_client import EvsClient
from huaweicloudsdkevs.v2 import (
    CreateVolumeRequest,
    CreateVolumeRequestBody,
    CreateVolumeOption,
    CinderListVolumeTypesRequest,
    ShowVolumeRequest,
)

from poller import poll_evs_job, wait_volume_available
from stages.discover_backups import BackupInfo

logger = logging.getLogger(__name__)

_FALLBACK_TYPES = ["SATA", "SAS", "GPSSD", "SSD", "ESSD"]
_TYPES_REQUIRING_EXTRA_PARAMS = {"GPSSD2", "ESSD2"}


def wait_restore_complete(
    cbr_client: CbrClient,
    backup_id: str,
    evs_client: EvsClient,
    volume_id: str,
    interval: int = 10,
    timeout: int = 3600,
) -> None:
    """Wait for a CBR restore to finish by polling backup and volume status."""
    import time as _time
    deadline = _time.time() + timeout
    _time.sleep(2)

    while _time.time() < deadline:
        try:
            backup_resp = cbr_client.show_backup(ShowBackupRequest(backup_id=backup_id))
            backup_status = backup_resp.backup.status if backup_resp.backup else ""
        except exceptions.ClientRequestException as e:
            logger.warning("ShowBackup failed (non-fatal): %s", e)
            backup_status = "unknown"
        try:
            vol_resp = evs_client.show_volume(ShowVolumeRequest(volume_id=volume_id))
            vol_status = vol_resp.volume.status if vol_resp.volume else ""
        except exceptions.ClientRequestException as e:
            logger.error("ShowVolume FAILED in wait_restore_complete: error_code=%s error_msg=%s", e.error_code, e.error_msg)
            raise
        logger.info("  Restore status: backup=%s volume=%s", backup_status, vol_status)

        if backup_status != "restoring" and vol_status == "available":
            logger.info("  Restore complete: backup=%s volume=%s", backup_status, vol_status)
            return
        if vol_status == "error":
            raise RuntimeError(f"Volume {volume_id} entered error state during restore")
        _time.sleep(interval)
    raise TimeoutError(f"Restore of backup {backup_id} timed out after {timeout}s")


def get_available_volume_types(evs_client: EvsClient) -> list[str]:
    """Query the region's available EVS volume types."""
    try:
        resp = evs_client.cinder_list_volume_types(CinderListVolumeTypesRequest())
        names = [vt.name for vt in (resp.volume_types or []) if vt.name]
        logger.info("Available volume types in region: %s", names)
        return names
    except Exception as e:
        logger.warning("Could not query volume types: %s — using fallback list", e)
        return list(_FALLBACK_TYPES)


def _try_create_volume(
    evs_client: EvsClient,
    size_gb: int,
    availability_zone: str,
    volume_type: str,
    name: str,
) -> str:
    """Attempt to create a volume with a specific type. Returns volume_id."""
    volume_opt = CreateVolumeOption(
        availability_zone=availability_zone,
        size=size_gb,
        volume_type=volume_type,
        name=name,
    )
    body = CreateVolumeRequestBody(volume=volume_opt)
    request = CreateVolumeRequest(body=body)

    logger.info("Creating empty volume: %dGB %s az=%s", size_gb, volume_type, availability_zone)
    response = evs_client.create_volume(request)
    logger.info(
        "CreateVolume response: job_id=%s order_id=%s volume_ids=%s",
        response.job_id, response.order_id, response.volume_ids,
    )

    volume_id = None
    if response.volume_ids:
        volume_id = response.volume_ids[0]

    if response.job_id:
        logger.info("Polling EVS job %s for volume creation...", response.job_id)
        result = poll_evs_job(evs_client, response.job_id)
        if result["status"] != "success":
            raise RuntimeError(f"Volume creation job failed: {result.get('error')}")
        if not volume_id and result.get("volume_id"):
            volume_id = result["volume_id"]

    if not volume_id:
        raise RuntimeError("Volume creation returned no volume_id or job_id")

    logger.info("Volume ID: %s — waiting for 'available'", volume_id)
    try:
        wait_volume_available(evs_client, volume_id)
    except exceptions.ClientRequestException as e:
        if e.status_code == 404:
            logger.warning("Volume %s returned 404 — retrying after delay", volume_id)
            import time as _time
            _time.sleep(10)
            wait_volume_available(evs_client, volume_id)
        else:
            logger.error("show_volume failed right after creation: %s", e)
            raise
    return volume_id


def create_empty_volume(
    evs_client: EvsClient,
    size_gb: int,
    availability_zone: str,
    volume_type: str,
    name: str,
) -> str:
    """Create an empty EVS volume, trying volume_type first with automatic fallback.

    If the requested volume_type is not available in the AZ, tries other
    available types in the region until one works.
    """
    available = get_available_volume_types(evs_client)
    available = [t for t in available if t not in _TYPES_REQUIRING_EXTRA_PARAMS]
    tried = [volume_type] + [t for t in available if t != volume_type] + [t for t in _FALLBACK_TYPES if t not in available and t != volume_type]
    seen = set()

    for vtype in tried:
        if vtype in seen or vtype in _TYPES_REQUIRING_EXTRA_PARAMS:
            continue
        seen.add(vtype)
        try:
            return _try_create_volume(evs_client, size_gb, availability_zone, vtype, name)
        except exceptions.ClientRequestException as e:
            if e.error_code in ("EVS.2071", "EVS.5400"):
                logger.warning("Volume type %s failed in AZ %s (%s) — trying next", vtype, availability_zone, e.error_code)
                continue
            raise
    raise RuntimeError(f"No volume type worked for AZ {availability_zone}. Tried: {list(seen)}")


def restore_backup_to_volume(
    cbr_client: CbrClient,
    backup_id: str,
    target_volume_id: str,
) -> None:
    """Restore a CBR disk backup onto an existing target volume."""
    restore_spec = BackupRestore(volume_id=target_volume_id)
    body = BackupRestoreReq(restore=restore_spec)
    request = RestoreBackupRequest(backup_id=backup_id, body=body)

    logger.info("Restoring backup %s -> volume %s", backup_id, target_volume_id)
    try:
        cbr_client.restore_backup(request)
        logger.info("RestoreBackup API call returned OK")
    except exceptions.ClientRequestException as e:
        logger.error("RestoreBackup FAILED: error_code=%s error_msg=%s", e.error_code, e.error_msg)
        raise


def restore_backup(
    cbr_client: CbrClient,
    evs_client: EvsClient,
    backup: BackupInfo,
    volume_type: str = "SATA",
) -> str:
    """Full restore: create empty volume + restore backup onto it. Returns volume_id."""
    az = backup.resource_az
    if not az:
        raise RuntimeError(
            f"Backup {backup.backup_id} has no availability_zone — cannot create target volume"
        )

    vol_name = f"restore-{backup.backup_id[:8]}"
    volume_id = create_empty_volume(
        evs_client=evs_client,
        size_gb=backup.resource_size_gb,
        availability_zone=az,
        volume_type=volume_type,
        name=vol_name,
    )
    restore_backup_to_volume(cbr_client, backup.backup_id, volume_id)
    wait_restore_complete(cbr_client, backup.backup_id, evs_client, volume_id)
    logger.info("Restore complete: volume %s is available", volume_id)
    return volume_id
