# coding: utf-8
"""Stage 1: Discover CBR backups of EVS disks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from huaweicloudsdkcbr.v1.cbr_client import CbrClient
from huaweicloudsdkcbr.v1 import ListBackupsRequest

logger = logging.getLogger(__name__)

_RESOURCE_TYPE_VOLUME = "OS::Cinder::Volume"


@dataclass
class BackupInfo:
    backup_id: str
    name: str
    resource_id: str
    resource_name: str
    resource_size_gb: int
    resource_az: str
    vault_id: str
    status: str


def discover_backups(
    cbr_client: CbrClient,
    vault_id: Optional[str] = None,
    name_filter: Optional[str] = None,
) -> list[BackupInfo]:
    """List available CBR backups for EVS volumes.

    Args:
        cbr_client: CBR client for the source region.
        vault_id: Optional vault ID to filter by.
        name_filter: Optional backup name substring to filter by.

    Returns:
        List of BackupInfo for available disk backups.
    """
    request = ListBackupsRequest(
        resource_type=_RESOURCE_TYPE_VOLUME,
        status="available",
        vault_id=vault_id,
        limit=200,
    )
    response = cbr_client.list_backups(request)
    backups = response.backups or []

    results: list[BackupInfo] = []
    for b in backups:
        if b.status != "available":
            continue
        if name_filter and name_filter.lower() not in (b.name or "").lower():
            continue
        info = BackupInfo(
            backup_id=b.id,
            name=b.name or "",
            resource_id=b.resource_id or "",
            resource_name=b.resource_name or "",
            resource_size_gb=b.resource_size or 0,
            resource_az=b.resource_az or "",
            vault_id=b.vault_id or "",
            status=b.status,
        )
        results.append(info)

    logger.info("Discovered %d available EVS disk backup(s)", len(results))
    for r in results:
        logger.info(
            "  backup=%s name='%s' size=%dGB az=%s",
            r.backup_id, r.name, r.resource_size_gb, r.resource_az,
        )
    return results
