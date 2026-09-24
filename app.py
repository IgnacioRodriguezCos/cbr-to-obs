# coding: utf-8
"""FastAPI server for listing CBR vaults, EVS volumes, and running the CBR->Image->OBS pipeline."""

import os
import sys
import time
import logging
import webbrowser
import threading
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.exceptions import exceptions
from huaweicloudsdkcbr.v1.cbr_client import CbrClient
from huaweicloudsdkcbr.v1.region.cbr_region import CbrRegion
from huaweicloudsdkcbr.v1.model.list_vault_request import ListVaultRequest
from huaweicloudsdkevs.v2.evs_client import EvsClient
from huaweicloudsdkevs.v2.region.evs_region import EvsRegion
from huaweicloudsdkevs.v2.model.list_volumes_request import ListVolumesRequest

from config import PipelineConfig
from huawei_clients import (
    build_cbr_client, build_evs_client, build_ims_client, build_obs_client,
    build_ecs_client, build_vpc_client,
)
from stages.discover_backups import discover_backups
from stages.restore_volume import restore_backup
from stages.create_ecs import create_ecs_and_attach
from stages.create_image import create_image
from stages.cross_region import copy_image_cross_region
from stages.export_to_obs import ensure_bucket, export_image_to_obs, verify_object_exists
from stages.direct_export import direct_export_to_obs
from stages.cleanup import cleanup_resources

app = FastAPI()

REGIONS = [
    {"id": "sa-argentina-1", "name": "Buenos Aires"},
    {"id": "la-south-2", "name": "Santiago"},
]


class LoginRequest(BaseModel):
    ak: str
    sk: str


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(os.path.dirname(__file__), "static", "index.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.post("/api/login")
async def login(req: LoginRequest):
    try:
        client = build_cbr_client(req.ak, req.sk, "la-south-2")
        client.list_vault(ListVaultRequest())
        return {"valid": True}
    except exceptions.ClientRequestException as e:
        return JSONResponse(status_code=401, content={"error": f"{e.error_code}: {e.error_msg}"})
    except Exception as e:
        return JSONResponse(status_code=401, content={"error": str(e)})


@app.get("/api/vaults")
async def list_vaults(request: Request, region: str = "la-south-2"):
    ak = request.headers.get("X-AK", "")
    sk = request.headers.get("X-SK", "")
    try:
        client = build_cbr_client(ak, sk, region)
        response = client.list_vault(ListVaultRequest())
        vaults = []
        for v in (response.vaults or []):
            resources = []
            for r in (v.resources or []):
                resources.append({
                    "id": r.id,
                    "name": r.name,
                    "type": r.type,
                    "size": r.size,
                    "protect_status": r.protect_status,
                })
            billing = v.billing
            vaults.append({
                "id": v.id,
                "name": v.name,
                "size": billing.size if billing else 0,
                "used": round((billing.used or 0) / 1024, 1) if billing else 0,
                "spec_code": billing.spec_code if billing else None,
                "resources": resources,
                "billing": v.billing,
                "status": billing.status if billing else None,
            })
        return {"vaults": vaults, "count": len(vaults)}
    except exceptions.ClientRequestException as e:
        return JSONResponse(status_code=500, content={"error": f"{e.error_code}: {e.error_msg}"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/regions")
async def get_regions():
    return {"regions": REGIONS}


@app.get("/api/volumes")
async def list_volumes(request: Request, region: str = "la-south-2"):
    ak = request.headers.get("X-AK", "")
    sk = request.headers.get("X-SK", "")
    try:
        client = build_evs_client(ak, sk, region)
        response = client.list_volumes(ListVolumesRequest())
        volumes = []
        for v in (response.volumes or []):
            volumes.append({
                "id": v.id,
                "name": v.name,
                "size": v.size,
                "status": v.status,
                "volume_type": v.volume_type,
                "availability_zone": v.availability_zone,
                "bootable": v.bootable,
                "attachments": [{"server_id": a.server_id, "device": a.device} for a in (v.attachments or [])],
            })
        return {"volumes": volumes, "count": len(volumes)}
    except exceptions.ClientRequestException as e:
        return JSONResponse(status_code=500, content={"error": f"{e.error_code}: {e.error_msg}"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


class PipelineRunRequest(BaseModel):
    source_region: str = "la-south-2"
    target_region: str = "la-south-2"
    vault_id: str | None = None
    bucket_name: str = ""
    bucket_prefix: str = "backups"
    image_name_prefix: str = "img-from-backup"
    agency_name: str | None = None
    target_project_name: str | None = None
    backup_name_filter: str | None = None
    selected_backup_ids: list[str] = []
    os_type: str = "Windows"
    poll_interval_seconds: int = 15
    poll_timeout_seconds: int = 3600


class _ListLogHandler(logging.Handler):
    """Logging handler that appends records to a list for the frontend."""

    def __init__(self, log_list):
        super().__init__()
        self._log_list = log_list

    def emit(self, record):
        self._log_list.append({
            "time": datetime.utcnow().strftime("%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        })


_pipeline_state: dict = {
    "running": False,
    "status": "idle",
    "total": 0,
    "processed": 0,
    "current_backup": None,
    "results": [],
    "logs": [],
    "error": None,
    "started_at": None,
}
_pipeline_lock = threading.Lock()


def _reset_pipeline_state():
    _pipeline_state.update(
        running=False,
        status="idle",
        total=0,
        processed=0,
        current_backup=None,
        results=[],
        logs=[],
        error=None,
        started_at=None,
    )


def _run_pipeline_thread(ak: str, sk: str, req: PipelineRunRequest):
    """Background worker that runs the full pipeline."""
    log_list = _pipeline_state["logs"]
    handler = _ListLogHandler(log_list)
    handler.setLevel(logging.INFO)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S"))
    root_logger.addHandler(console_handler)
    root_logger.setLevel(logging.INFO)

    try:
        config = PipelineConfig(
            ak=ak, sk=sk,
            source_region=req.source_region,
            target_region=req.target_region,
            vault_id=req.vault_id or None,
            bucket_name=req.bucket_name,
            bucket_prefix=req.bucket_prefix,
            image_name_prefix=req.image_name_prefix,
            agency_name=req.agency_name or None,
            target_project_name=req.target_project_name or None,
            backup_name_filter=req.backup_name_filter or None,
            poll_interval_seconds=req.poll_interval_seconds,
            poll_timeout_seconds=req.poll_timeout_seconds,
        )

        logging.info("Pipeline iniciado: %s -> %s (cross=%s)",
                     config.source_region, config.target_region, config.is_cross_region)

        os_type = "Windows" if "windows" in (req.os_type or "").lower() else "Linux"
        logging.info("OS type seleccionado: %s", os_type)

        cbr_client = build_cbr_client(ak, sk, config.source_region)
        evs_client = build_evs_client(ak, sk, config.source_region)
        ims_src = build_ims_client(ak, sk, config.source_region)
        ims_tgt = build_ims_client(ak, sk, config.target_region)
        obs_tgt = build_obs_client(ak, sk, config.target_region)

        with _pipeline_lock:
            _pipeline_state["status"] = "running"
            _pipeline_state["running"] = True
            _pipeline_state["started_at"] = datetime.utcnow().isoformat()

        # Stage 1: discover
        logging.info("[1/5] Descubriendo backups de discos EVS...")
        backups = discover_backups(cbr_client, vault_id=config.vault_id,
                                   name_filter=config.backup_name_filter)

        if req.selected_backup_ids:
            selected_set = set(req.selected_backup_ids)
            backups = [b for b in backups if b.backup_id in selected_set]
            logging.info("Filtrando por backups seleccionados: %d de %d", len(backups), len(selected_set))

        with _pipeline_lock:
            _pipeline_state["total"] = len(backups)

        if not backups:
            logging.info("No se encontraron backups. Nada que hacer.")
            with _pipeline_lock:
                _pipeline_state["status"] = "completed"
                _pipeline_state["running"] = False
            return

        if not config.bucket_name:
            raise RuntimeError("No se selecciono un bucket OBS destino")

        # Process each backup
        def _check_stop():
            with _pipeline_lock:
                if _pipeline_state["status"] == "stopping":
                    raise RuntimeError("Pipeline detenido por el usuario")

        for i, backup in enumerate(backups, 1):
            with _pipeline_lock:
                if _pipeline_state["status"] == "stopping":
                    logging.info("Pipeline detenido por el usuario.")
                    break
                _pipeline_state["current_backup"] = {
                    "id": backup.backup_id, "name": backup.name,
                    "size_gb": backup.resource_size_gb, "az": backup.resource_az,
                }

            logging.info("=== Backup %d/%d: %s ('%s') ===", i, len(backups), backup.backup_id, backup.name)
            image_name = backup.name or f"img-{backup.backup_id[:8]}"
            result_entry = {"backup_id": backup.backup_id, "backup_name": backup.name, "exported": False, "error": None}

            volume_id = None
            ecs_info = None
            image_id = None
            ecs_client = None
            vpc_client = None
            is_large_disk = backup.resource_size_gb > 1024

            try:
                _check_stop()
                if is_large_disk:
                    logging.info("[2/6] Restaurando backup a volumen nuevo (DISCO >1TiB - export directo)...")
                else:
                    logging.info("[2/6] Restaurando backup a volumen nuevo...")
                volume_id = restore_backup(cbr_client, evs_client, backup, "SATA")

                if is_large_disk:
                    _check_stop()
                    logging.info("[3/6] Creando ECS automatica (Linux, SSH) y attachando disco...")
                    ecs_client = build_ecs_client(ak, sk, config.source_region)
                    vpc_client = build_vpc_client(ak, sk, config.source_region)
                    ecs_info = create_ecs_and_attach(
                        ecs_client=ecs_client,
                        vpc_client=vpc_client,
                        ims_client=ims_src,
                        volume_id=volume_id,
                        availability_zone=backup.resource_az,
                        enable_ssh=True,
                        os_type="Linux",
                    )
                    logging.info("ECS creada: %s con disco %s attachado", ecs_info["server_id"], volume_id)

                    _check_stop()
                    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                    object_key = f"{config.bucket_prefix}/{image_name}_{timestamp}.vhd"
                    logging.info("[4-6/6] Export directo a OBS (qemu-img VHD + obsutil)...")
                    success = direct_export_to_obs(
                        public_ip=ecs_info["public_ip"],
                        private_key_pem=ecs_info["private_key_pem"],
                        ak=ak, sk=sk,
                        region=config.source_region,
                        bucket_name=config.bucket_name,
                        object_key=object_key,
                    )
                    result_entry["exported"] = success
                    result_entry["object_key"] = object_key
                    result_entry["method"] = "direct_vhd"
                else:
                    _check_stop()
                    logging.info("[3/6] Creando ECS automatica (%s) y attachando disco...", os_type)
                    ecs_client = build_ecs_client(ak, sk, config.source_region)
                    vpc_client = build_vpc_client(ak, sk, config.source_region)
                    ecs_info = create_ecs_and_attach(
                        ecs_client=ecs_client,
                        vpc_client=vpc_client,
                        ims_client=ims_src,
                        volume_id=volume_id,
                        availability_zone=backup.resource_az,
                        enable_ssh=False,
                        os_type=os_type,
                    )
                    logging.info("ECS creada: %s con disco %s attachado", ecs_info["server_id"], volume_id)

                    _check_stop()
                    logging.info("[4/6] Creando data disk image desde volumen...")
                    image_id = create_image(
                        ims_src, image_name,
                        volume_id=volume_id,
                        expected_os_type=os_type,
                        poll_interval=config.poll_interval_seconds,
                        poll_timeout=config.poll_timeout_seconds,
                    )

                    _check_stop()
                    if config.is_cross_region:
                        if not config.agency_name or not config.target_project_name:
                            raise RuntimeError("Cross-region requiere agency_name y target_project_name")
                        logging.info("[5/6] Copiando imagen cross-region %s -> %s...",
                                     config.source_region, config.target_region)
                        final_id = copy_image_cross_region(
                            ims_src, image_id, config.target_region,
                            config.target_project_name, config.agency_name,
                            f"{image_name}-xrgn",
                            poll_interval=config.poll_interval_seconds,
                            poll_timeout=config.poll_timeout_seconds,
                        )
                    else:
                        logging.info("[5/6] Misma region — saltando cross-region.")
                        final_id = image_id

                    _check_stop()
                    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                    object_key = f"{config.bucket_prefix}/{image_name}_{timestamp}.zvhd2"
                    logging.info("[6/6] Exportando imagen a OBS (fast export zvhd2)...")
                    export_image_to_obs(
                        ims_tgt, final_id, config.bucket_name, object_key,
                        fast_export=True,
                        poll_interval=config.poll_interval_seconds,
                        poll_timeout=config.poll_timeout_seconds,
                    )
                    verified = verify_object_exists(obs_tgt, config.bucket_name, object_key)
                    result_entry["exported"] = verified
                    result_entry["object_key"] = object_key
                    result_entry["method"] = "ims_zvhd2"

            except Exception as exc:
                result_entry["error"] = str(exc)
                logging.error("Backup %s fallo: %s", backup.backup_id, exc)

            finally:
                if volume_id or ecs_info or image_id:
                    logging.info("[Cleanup] Eliminando recursos temporales...")
                    cleanup_resources(
                        ecs_client=ecs_client,
                        vpc_client=vpc_client,
                        ims_client=ims_src,
                        evs_client=evs_client,
                        server_id=(ecs_info or {}).get("server_id", ""),
                        vpc_id=(ecs_info or {}).get("vpc_id", ""),
                        subnet_id=(ecs_info or {}).get("subnet_id", ""),
                        security_group_id=(ecs_info or {}).get("security_group_id", ""),
                        image_id=image_id or "",
                        volume_id=volume_id or "",
                        keypair_name=(ecs_info or {}).get("keypair_name", ""),
                    )

            with _pipeline_lock:
                _pipeline_state["results"].append(result_entry)
                _pipeline_state["processed"] = i

        with _pipeline_lock:
            was_stopping = _pipeline_state["status"] == "stopping"
            _pipeline_state["status"] = "stopped" if was_stopping else "completed"
            _pipeline_state["running"] = False
            _pipeline_state["current_backup"] = None
        logging.info("Pipeline %s.", "detenido" if was_stopping else "completado")

    except Exception as exc:
        logging.error("Pipeline error fatal: %s", exc)
        with _pipeline_lock:
            _pipeline_state["status"] = "failed"
            _pipeline_state["running"] = False
            _pipeline_state["error"] = str(exc)
    finally:
        root_logger.removeHandler(handler)
        root_logger.removeHandler(console_handler)


@app.get("/api/backups")
async def list_backups(request: Request, region: str = "la-south-2", vault_id: str | None = None):
    """List available CBR backups of EVS disks (discovery stage)."""
    ak = request.headers.get("X-AK", "")
    sk = request.headers.get("X-SK", "")
    try:
        cbr_client = build_cbr_client(ak, sk, region)
        backups = discover_backups(cbr_client, vault_id=vault_id or None)
        return {"backups": [
            {
                "backup_id": b.backup_id,
                "name": b.name,
                "resource_id": b.resource_id,
                "resource_name": b.resource_name,
                "resource_size_gb": b.resource_size_gb,
                "resource_az": b.resource_az,
                "vault_id": b.vault_id,
                "status": b.status,
            }
            for b in backups
        ], "count": len(backups)}
    except exceptions.ClientRequestException as e:
        return JSONResponse(status_code=500, content={"error": f"{e.error_code}: {e.error_msg}"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/pipeline/run")
async def pipeline_run(request: Request, req: PipelineRunRequest):
    """Start the pipeline in a background thread."""
    ak = request.headers.get("X-AK", "")
    sk = request.headers.get("X-SK", "")
    if not ak or not sk:
        return JSONResponse(status_code=401, content={"error": "Faltan credenciales AK/SK"})

    with _pipeline_lock:
        if _pipeline_state["running"]:
            return JSONResponse(status_code=409, content={"error": "Ya hay un pipeline en ejecucion"})
        _reset_pipeline_state()
        _pipeline_state["status"] = "starting"
        _pipeline_state["running"] = True

    thread = threading.Thread(target=_run_pipeline_thread, args=(ak, sk, req), daemon=True)
    thread.start()
    return {"status": "started", "message": "Pipeline iniciado"}


@app.get("/api/pipeline/status")
async def pipeline_status():
    """Get current pipeline status."""
    with _pipeline_lock:
        return {
            "running": _pipeline_state["running"],
            "status": _pipeline_state["status"],
            "total": _pipeline_state["total"],
            "processed": _pipeline_state["processed"],
            "current_backup": _pipeline_state["current_backup"],
            "results": list(_pipeline_state["results"]),
            "logs": list(_pipeline_state["logs"]),
            "error": _pipeline_state["error"],
            "started_at": _pipeline_state["started_at"],
        }


@app.post("/api/pipeline/stop")
async def pipeline_stop():
    """Signal the pipeline to stop (best-effort)."""
    with _pipeline_lock:
        if not _pipeline_state["running"]:
            return {"status": "idle", "message": "No hay pipeline en ejecucion"}
        _pipeline_state["status"] = "stopping"
    return {"status": "stopping", "message": "Deteniendo (se completara el backup actual)"}


@app.get("/api/vpcs")
async def list_vpcs(request: Request, region: str = "la-south-2"):
    ak = request.headers.get("X-AK", "")
    sk = request.headers.get("X-SK", "")
    try:
        client = build_vpc_client(ak, sk, region)
        from huaweicloudsdkvpc.v2 import ListVpcsRequest
        resp = client.list_vpcs(ListVpcsRequest(limit=100))
        return {"vpcs": [{"id": v.id, "name": v.name} for v in (resp.vpcs or [])]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/subnets")
async def list_subnets(request: Request, region: str = "la-south-2", vpc_id: str = ""):
    ak = request.headers.get("X-AK", "")
    sk = request.headers.get("X-SK", "")
    try:
        client = build_vpc_client(ak, sk, region)
        from huaweicloudsdkvpc.v2 import ListSubnetsRequest
        req = ListSubnetsRequest(limit=100)
        if vpc_id:
            req.vpc_id = vpc_id
        resp = client.list_subnets(req)
        return {"subnets": [{"id": s.id, "name": s.name, "vpc_id": s.vpc_id} for s in (resp.subnets or [])]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/security-groups")
async def list_security_groups(request: Request, region: str = "la-south-2"):
    ak = request.headers.get("X-AK", "")
    sk = request.headers.get("X-SK", "")
    try:
        client = build_vpc_client(ak, sk, region)
        from huaweicloudsdkvpc.v2 import ListSecurityGroupsRequest
        resp = client.list_security_groups(ListSecurityGroupsRequest(limit=100))
        return {"security_groups": [{"id": s.id, "name": s.name} for s in (resp.security_groups or [])]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/flavors")
async def list_flavors(request: Request, region: str = "la-south-2"):
    ak = request.headers.get("X-AK", "")
    sk = request.headers.get("X-SK", "")
    try:
        client = build_ecs_client(ak, sk, region)
        from huaweicloudsdkecs.v2 import ListFlavorsRequest
        resp = client.list_flavors(ListFlavorsRequest(limit=200))
        flavors = []
        for f in (resp.flavors or []):
            flavors.append({"id": f.id, "name": f.name})
        return {"flavors": flavors}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/public-images")
async def list_public_images(request: Request, region: str = "la-south-2"):
    ak = request.headers.get("X-AK", "")
    sk = request.headers.get("X-SK", "")
    try:
        client = build_ims_client(ak, sk, region)
        from huaweicloudsdkims.v2 import ListImagesRequest
        resp = client.list_images(ListImagesRequest(imagetype="gold", status="active", limit=50))
        images = []
        for img in (resp.images or []):
            images.append({"id": img.id, "name": img.name})
        return {"images": images}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/buckets")
async def list_buckets(request: Request, region: str = "la-south-2"):
    """List existing OBS buckets in a region using KooCLI."""
    import subprocess, re
    ak = request.headers.get("X-AK", "")
    sk = request.headers.get("X-SK", "")
    try:
        endpoint = f"obs.{region}.myhuaweicloud.com"
        subprocess.run(
            ["hcloud", "OBS", "config", f"-e={endpoint}", f"-i={ak}", f"-k={sk}"],
            capture_output=True, text=True, timeout=15,
        )
        result = subprocess.run(
            ["hcloud", "OBS", "ls", "-limit=1000"],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout + result.stderr
        logging.info("OBS ls raw output:\n%s", output[:2000])
        buckets = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("Start at") or line.startswith("Bucket"):
                continue
            parts = line.split()
            if len(parts) < 3 or not parts[0].startswith("obs://"):
                continue
            name = parts[0].replace("obs://", "")
            location = parts[2] if len(parts) >= 3 else ""
            if location != region:
                continue
            if name and not name.startswith("Failed") and not name.startswith("Error"):
                logging.info("  bucket parsed: '%s' location='%s'", name, location)
                buckets.append({"name": name, "location": location})
        logging.info("OBS ls parsed %d buckets for region %s", len(buckets), region)
        return {"buckets": buckets}
    except Exception as e:
        logging.error("list_buckets failed: %s", e, exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


def open_browser():
    threading.Timer(1.0, lambda: webbrowser.open("http://localhost:8080")).start()


if __name__ == "__main__":
    import uvicorn
    open_browser()
    uvicorn.run(app, host="127.0.0.1", port=8080)
