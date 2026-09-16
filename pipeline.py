# coding: utf-8
"""
CBR -> Image -> OBS Pipeline.

Trae backups de CBR de discos EVS, los restaura a volumenes nuevos,
crea imagenes IMS desde esos volumenes, y las exporta a un bucket OBS
en la region que elijas.

Uso:
    python pipeline.py                     # usa variables de entorno
    python pipeline.py --dry-run           # solo descubre backups, no ejecuta
    python pipeline.py --vault-id <id>     # filtra por vault
    python pipeline.py --backup-name <str> # filtra backups por nombre

Variables de entorno (ver .env.example):
    CLOUD_SDK_AK, CLOUD_SDK_SK     — credenciales
    SOURCE_REGION                  — region de los backups CBR
    TARGET_REGION                  — region del bucket OBS destino
    VAULT_ID                       — (opcional) filtrar por vault
    OBS_BUCKET_NAME                — nombre del bucket destino
    FAST_EXPORT                    — true|false (default true = zvhd2)
    FILE_FORMAT                    — qcow2|vhd|zvhd|vmdk (si fast_export=false)
    AGENCY_NAME                    — agency IAM para cross-region
    TARGET_PROJECT_NAME            — project name en region destino (cross-region)
    DRY_RUN                        — true|false
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from typing import Optional

from config import PipelineConfig
from huawei_clients import (
    build_cbr_client,
    build_evs_client,
    build_ims_client,
    build_obs_client,
)
from stages.discover_backups import BackupInfo, discover_backups
from stages.restore_volume import restore_backup
from stages.create_image import create_image_from_volume
from stages.cross_region import copy_image_cross_region
from stages.export_to_obs import (
    ensure_bucket,
    export_image_to_obs,
    verify_object_exists,
)

logger = logging.getLogger("pipeline")


@dataclass
class BackupResult:
    backup_id: str
    backup_name: str
    volume_id: Optional[str] = None
    image_id: Optional[str] = None
    final_image_id: Optional[str] = None
    object_key: Optional[str] = None
    exported: bool = False
    error: Optional[str] = None


def process_single_backup(
    config: PipelineConfig,
    backup: BackupInfo,
    cbr_client,
    evs_client,
    ims_client_source,
    ims_client_target,
    obs_client_target,
) -> BackupResult:
    """Process one backup through all pipeline stages."""
    result = BackupResult(backup_id=backup.backup_id, backup_name=backup.name)
    image_name = f"{config.image_name_prefix}-{backup.backup_id[:8]}-{int(time.time())}"

    try:
        # Stage 2: Restore backup to a new volume
        logger.info("[2/5] Restoring backup to new volume...")
        volume_id = restore_backup(
            cbr_client=cbr_client,
            evs_client=evs_client,
            backup=backup,
            volume_type=config.volume_type,
        )
        result.volume_id = volume_id

        # Stage 3: Create image from the restored volume
        logger.info("[3/5] Creating image from volume...")
        image_id = create_image_from_volume(
            ims_client=ims_client_source,
            volume_id=volume_id,
            image_name=image_name,
            os_version=config.os_version,
            poll_interval=config.poll_interval_seconds,
            poll_timeout=config.poll_timeout_seconds,
        )
        result.image_id = image_id

        # Stage 4: Cross-region copy (if needed)
        if config.is_cross_region:
            if not config.agency_name or not config.target_project_name:
                raise RuntimeError(
                    "Cross-region requiere AGENCY_NAME y TARGET_PROJECT_NAME. "
                    "Configuralos en .env o variables de entorno."
                )
            logger.info("[4/5] Copying image cross-region %s -> %s...",
                        config.source_region, config.target_region)
            final_image_id = copy_image_cross_region(
                ims_client=ims_client_source,
                image_id=image_id,
                target_region=config.target_region,
                target_project_name=config.target_project_name,
                agency_name=config.agency_name,
                new_name=f"{image_name}-xrgn",
                poll_interval=config.poll_interval_seconds,
                poll_timeout=config.poll_timeout_seconds,
            )
            result.final_image_id = final_image_id
        else:
            logger.info("[4/5] Same region — skipping cross-region copy.")
            result.final_image_id = image_id

        # Stage 5: Export to OBS
        logger.info("[5/5] Exporting image to OBS bucket...")
        ext = "zvhd2" if config.fast_export else config.file_format
        object_key = f"{config.bucket_prefix}/{backup.backup_id}.{ext}"
        result.object_key = object_key

        export_image_to_obs(
            ims_client=ims_client_target,
            image_id=result.final_image_id,
            bucket_name=config.bucket_name,
            object_key=object_key,
            fast_export=config.fast_export,
            file_format=config.file_format,
            poll_interval=config.poll_interval_seconds,
            poll_timeout=config.poll_timeout_seconds,
        )

        verified = verify_object_exists(obs_client_target, config.bucket_name, object_key)
        result.exported = verified

    except Exception as exc:
        result.error = str(exc)
        logger.error("Backup %s failed: %s", backup.backup_id, exc, exc_info=True)

    return result


def run_pipeline(config: PipelineConfig) -> list[BackupResult]:
    """Run the full pipeline for all discovered backups."""
    logger.info("=" * 60)
    logger.info("CBR -> Image -> OBS Pipeline")
    logger.info("  Source region: %s", config.source_region)
    logger.info("  Target region: %s", config.target_region)
    logger.info("  Cross-region:  %s", config.is_cross_region)
    logger.info("  Fast export:   %s", config.fast_export)
    logger.info("  Bucket:        %s", config.bucket_name)
    logger.info("  Dry run:       %s", config.dry_run)
    logger.info("=" * 60)

    # Build clients
    cbr_client = build_cbr_client(config.ak, config.sk, config.source_region)
    evs_client = build_evs_client(config.ak, config.sk, config.source_region)
    ims_client_source = build_ims_client(config.ak, config.sk, config.source_region)
    ims_client_target = build_ims_client(config.ak, config.sk, config.target_region)
    obs_client_target = build_obs_client(config.ak, config.sk, config.target_region)

    # Stage 1: Discover backups
    logger.info("[1/5] Discovering CBR backups of EVS disks...")
    backups = discover_backups(
        cbr_client=cbr_client,
        vault_id=config.vault_id,
        name_filter=config.backup_name_filter,
    )

    if not backups:
        logger.info("No backups found. Nothing to do.")
        return []

    if config.dry_run:
        logger.info("DRY RUN — %d backup(s) would be processed. Exiting.", len(backups))
        return []

    # Ensure OBS bucket exists before processing
    logger.info("Ensuring OBS bucket exists in %s...", config.target_region)
    ensure_bucket(obs_client_target, config.bucket_name, config.target_region)

    # Process each backup
    results: list[BackupResult] = []
    for i, backup in enumerate(backups, 1):
        logger.info("-" * 60)
        logger.info("Processing backup %d/%d: %s ('%s')",
                    i, len(backups), backup.backup_id, backup.name)
        start = time.time()
        result = process_single_backup(
            config=config,
            backup=backup,
            cbr_client=cbr_client,
            evs_client=evs_client,
            ims_client_source=ims_client_source,
            ims_client_target=ims_client_target,
            obs_client_target=obs_client_target,
        )
        elapsed = time.time() - start
        logger.info("Completed in %.1fs — exported=%s", elapsed, result.exported)
        results.append(result)

    # Summary
    logger.info("=" * 60)
    succeeded = sum(1 for r in results if r.exported)
    failed = sum(1 for r in results if r.error)
    logger.info("Summary: %d succeeded, %d failed, %d total", succeeded, failed, len(results))
    for r in results:
        status = "OK" if r.exported else ("FAIL" if r.error else "PARTIAL")
        logger.info("  %s  backup=%s  %s", status, r.backup_id, r.object_key or "")
    logger.info("=" * 60)

    return results


def main():
    parser = argparse.ArgumentParser(description="CBR -> Image -> OBS pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Only discover backups, don't execute")
    parser.add_argument("--vault-id", help="Filter by vault ID")
    parser.add_argument("--backup-name", help="Filter backups by name substring")
    parser.add_argument("--source-region", help="Override source region")
    parser.add_argument("--target-region", help="Override target region")
    parser.add_argument("--bucket", help="Override OBS bucket name")
    parser.add_argument("--fast-export", choices=["true", "false"], help="Override fast export setting")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config = PipelineConfig.from_env()

    if args.dry_run:
        config.dry_run = True
    if args.vault_id:
        config.vault_id = args.vault_id
    if args.backup_name:
        config.backup_name_filter = args.backup_name
    if args.source_region:
        config.source_region = args.source_region
    if args.target_region:
        config.target_region = args.target_region
    if args.bucket:
        config.bucket_name = args.bucket
    if args.fast_export:
        config.fast_export = args.fast_export == "true"

    results = run_pipeline(config)
    if any(r.error for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
