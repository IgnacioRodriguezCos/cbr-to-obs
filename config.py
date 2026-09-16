# coding: utf-8
"""Configuration for the CBR -> Image -> OBS pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PipelineConfig:
    ak: str
    sk: str
    source_region: str
    target_region: str
    vault_id: Optional[str] = None
    bucket_name: str = "cbr-backup-images"
    bucket_prefix: str = "backups"
    fast_export: bool = True
    file_format: str = "qcow2"
    volume_type: str = "SATA"
    os_version: str = "CentOS 7.9 64bit"
    image_name_prefix: str = "img-from-backup"
    agency_name: Optional[str] = None
    target_project_name: Optional[str] = None
    cleanup_temp_resources: bool = False
    poll_interval_seconds: int = 15
    poll_timeout_seconds: int = 3600
    backup_name_filter: Optional[str] = None
    dry_run: bool = False

    @property
    def is_cross_region(self) -> bool:
        return self.source_region.strip().lower() != self.target_region.strip().lower()

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        ak = os.environ.get("CLOUD_SDK_AK", "")
        sk = os.environ.get("CLOUD_SDK_SK", "")
        if not ak or not sk:
            raise RuntimeError(
                "Faltan credenciales. Seteá CLOUD_SDK_AK y CLOUD_SDK_SK."
            )
        source_region = os.environ.get("SOURCE_REGION", "la-south-2")
        target_region = os.environ.get("TARGET_REGION", source_region)
        return cls(
            ak=ak,
            sk=sk,
            source_region=source_region,
            target_region=target_region,
            vault_id=os.environ.get("VAULT_ID") or None,
            bucket_name=os.environ.get("OBS_BUCKET_NAME", "cbr-backup-images"),
            bucket_prefix=os.environ.get("OBS_BUCKET_PREFIX", "backups"),
            fast_export=os.environ.get("FAST_EXPORT", "true").lower() == "true",
            file_format=os.environ.get("FILE_FORMAT", "qcow2"),
            volume_type=os.environ.get("VOLUME_TYPE", "SATA"),
            os_version=os.environ.get("OS_VERSION", "CentOS 7.9 64bit"),
            image_name_prefix=os.environ.get("IMAGE_NAME_PREFIX", "img-from-backup"),
            agency_name=os.environ.get("AGENCY_NAME") or None,
            target_project_name=os.environ.get("TARGET_PROJECT_NAME") or None,
            cleanup_temp_resources=os.environ.get("CLEANUP", "false").lower() == "true",
            poll_interval_seconds=int(os.environ.get("POLL_INTERVAL", "15")),
            poll_timeout_seconds=int(os.environ.get("POLL_TIMEOUT", "3600")),
            backup_name_filter=os.environ.get("BACKUP_NAME_FILTER") or None,
            dry_run=os.environ.get("DRY_RUN", "false").lower() == "true",
        )
