# coding: utf-8
"""Stage 7: Cleanup temporary resources after successful export to OBS."""

from __future__ import annotations

import logging
import re
import time

from huaweicloudsdkecs.v2.ecs_client import EcsClient
from huaweicloudsdkecs.v2 import (
    DeleteServersRequest, DeleteServersRequestBody, ServerId,
    ShowServerRequest, NovaDeleteKeypairRequest,
    ListServersDetailsRequest, NovaListKeypairsRequest,
)
from huaweicloudsdkvpc.v2.vpc_client import VpcClient
from huaweicloudsdkvpc.v2 import (
    DeleteVpcRequest, DeleteSubnetRequest, DeleteSecurityGroupRequest,
    NeutronListRoutersRequest, NeutronRemoveRouterInterfaceRequest,
    RouterInterfaceRequestBody,
    ListVpcsRequest, ListSubnetsRequest, ListSecurityGroupsRequest,
)
from huaweicloudsdkims.v2.ims_client import ImsClient
from huaweicloudsdkims.v2 import GlanceDeleteImageRequest
from huaweicloudsdkevs.v2.evs_client import EvsClient
from huaweicloudsdkevs.v2 import DeleteVolumeRequest, ListVolumesRequest
from huaweicloudsdkcore.exceptions import exceptions

logger = logging.getLogger(__name__)


def _safe(fn, label: str):
    try:
        fn()
        logger.info("Deleted %s", label)
    except exceptions.ClientRequestException as e:
        if e.status_code == 404:
            logger.info("%s already gone (404)", label)
        else:
            logger.warning("Failed to delete %s: %s", label, e)
    except Exception as e:
        logger.warning("Failed to delete %s: %s", label, e)


def _wait_server_gone(ecs_client: EcsClient, server_id: str, timeout: int = 600) -> None:
    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        try:
            resp = ecs_client.show_server(ShowServerRequest(server_id=server_id))
            status = getattr(resp.server, "status", "?") if hasattr(resp, "server") else "?"
            if status != last_status:
                logger.info("ECS %s status: %s", server_id[:8], status)
                last_status = status
            time.sleep(5)
        except exceptions.ClientRequestException as e:
            if e.status_code == 404:
                logger.info("ECS %s deleted", server_id[:8])
                return
            raise
        except (exceptions.ConnectionException, exceptions.RequestTimeoutException) as e:
            logger.warning("Transient network error polling ECS %s (will retry): %s", server_id[:8], e)
            time.sleep(5)
    logger.warning("ECS %s still deleting after %ds, continuing cleanup", server_id[:8], timeout)


_ORPHAN_PREFIXES = {
    "server": "ecs-restore-",
    "vpc": "vpc-restore-",
    "subnet": "subnet-restore-",
    "sg": "sg-restore-",
    "keypair": "kp-restore-",
}

# Restored volumes are named restore-<8 hex chars of the backup id>; scratch
# volumes scratch-restore-<6 hex chars>. The regex makes accidental collision
# with user volumes virtually impossible.
_RESTORE_VOL_RE = re.compile(r"^restore-[0-9a-f]{8}$")
_ORPHAN_VOL_PREFIX = "scratch-restore-"


def cleanup_orphaned_resources(
    ecs_client: EcsClient,
    vpc_client: VpcClient,
    evs_client: EvsClient | None = None,
    protect_volume_ids: set[str] | None = None,
) -> None:
    """Garbage-collect resources left behind by failed pipeline runs.

    Only touches resources named with the pipeline prefixes (*-restore-*).
    Volumes are matched by exact name pattern (restore-<hex8> or
    scratch-restore-*), must be unattached ('available') and not in
    protect_volume_ids (the current run's restored volume is unattached
    at GC time and MUST be protected).
    Best-effort: resources still in use fail to delete and are skipped.
    Frees VPC/router and EVS quota so a new run can provision.
    """
    protect = protect_volume_ids or set()
    # 1. Orphan ECS servers (they pin subnets/VPCs and cost money)
    try:
        servers = ecs_client.list_servers_details(
            ListServersDetailsRequest(limit=1000)
        ).servers or []
        orphan_servers = [s for s in servers
                          if (s.name or "").startswith(_ORPHAN_PREFIXES["server"])]
    except Exception as e:
        logger.warning("Orphan ECS listing failed: %s", e)
        orphan_servers = []
    for s in orphan_servers:
        logger.info("Deleting orphan ECS %s ('%s')", s.id, s.name)
        _safe(lambda sid=s.id: ecs_client.delete_servers(DeleteServersRequest(
            body=DeleteServersRequestBody(
                servers=[ServerId(id=sid)],
                delete_volume=True,
                delete_publicip=True,
            )
        )), f"orphan ECS {s.id}")
        _wait_server_gone(ecs_client, s.id, timeout=300)

    # 2. Orphan subnets (must go before their VPC)
    deleted_subnet = False
    try:
        subnets = vpc_client.list_subnets(ListSubnetsRequest(limit=500)).subnets or []
        orphan_subnets = [sn for sn in subnets
                          if (sn.name or "").startswith(_ORPHAN_PREFIXES["subnet"])]
    except Exception as e:
        logger.warning("Orphan subnet listing failed: %s", e)
        orphan_subnets = []
    for sn in orphan_subnets:
        _safe(lambda s=sn: vpc_client.delete_subnet(
            DeleteSubnetRequest(vpc_id=s.vpc_id, subnet_id=s.id)
        ), f"orphan subnet {sn.id} ('{sn.name}')")
        deleted_subnet = True
    if deleted_subnet:
        time.sleep(3)

    # 3. Orphan VPCs (frees the router quota)
    orphan_vpc_ids = {s.vpc_id for s in orphan_subnets}
    try:
        vpcs = vpc_client.list_vpcs(ListVpcsRequest(limit=500)).vpcs or []
        orphan_vpc_ids.update(v.id for v in vpcs
                              if (v.name or "").startswith(_ORPHAN_PREFIXES["vpc"]))
    except Exception as e:
        logger.warning("Orphan VPC listing failed: %s", e)
    for vid in orphan_vpc_ids:
        _safe(lambda v=vid: vpc_client.delete_vpc(DeleteVpcRequest(vpc_id=v)),
              f"orphan VPC {vid}")

    # 4. Orphan security groups
    try:
        sgs = vpc_client.list_security_groups(
            ListSecurityGroupsRequest(limit=500)
        ).security_groups or []
        orphan_sgs = [sg for sg in sgs
                      if (sg.name or "").startswith(_ORPHAN_PREFIXES["sg"])]
    except Exception as e:
        logger.warning("Orphan SG listing failed: %s", e)
        orphan_sgs = []
    for sg in orphan_sgs:
        _safe(lambda g=sg: vpc_client.delete_security_group(
            DeleteSecurityGroupRequest(security_group_id=g.id)
        ), f"orphan SG {sg.id} ('{sg.name}')")

    # 5. Orphan keypairs
    try:
        kps = ecs_client.nova_list_keypairs(NovaListKeypairsRequest(limit=100)).keypairs or []
        orphan_kps = [kp for kp in kps
                      if (getattr(getattr(kp, "keypair", None), "name", "") or "")
                      .startswith(_ORPHAN_PREFIXES["keypair"])]
    except Exception as e:
        logger.warning("Orphan keypair listing failed: %s", e)
        orphan_kps = []
    for kp in orphan_kps:
        name = getattr(kp.keypair, "name", "")
        _safe(lambda n=name: ecs_client.nova_delete_keypair(
            NovaDeleteKeypairRequest(keypair_name=n)
        ), f"orphan keypair {name}")

    # 6. Orphan volumes from crashed runs (restore-<hex8> / scratch-restore-*).
    #    Attached ones die with their orphan ECS in pass 1; this catches the
    #    created-but-never-attached leftovers that silently drain EVS quota.
    if evs_client is not None:
        try:
            vols = evs_client.list_volumes(ListVolumesRequest(limit=1000)).volumes or []
            orphan_vols = [
                v for v in vols
                if v.id not in protect
                and (v.status or "") == "available"
                and (
                    bool(_RESTORE_VOL_RE.match(v.name or ""))
                    or (v.name or "").startswith(_ORPHAN_VOL_PREFIX)
                )
            ]
        except Exception as e:
            logger.warning("Orphan volume listing failed: %s", e)
            orphan_vols = []
        for v in orphan_vols:
            logger.info(
                "Deleting orphan volume %s ('%s', %sGB)",
                v.id, v.name, getattr(v, "size", "?"),
            )
            _safe(
                lambda vid=v.id: evs_client.delete_volume(DeleteVolumeRequest(volume_id=vid)),
                f"orphan volume {v.id}",
            )


def _remove_subnet_from_routers(vpc_client: VpcClient, subnet_id: str) -> None:
    """Remove a subnet from all routers that reference it."""
    try:
        routers = vpc_client.neutron_list_routers(NeutronListRoutersRequest())
        for router in (routers.routers or []):
            try:
                vpc_client.neutron_remove_router_interface(
                    NeutronRemoveRouterInterfaceRequest(
                        router_id=router.id,
                        body=RouterInterfaceRequestBody(subnet_id=subnet_id),
                    )
                )
                logger.info("Removed subnet %s from router %s", subnet_id, router.id)
            except Exception:
                pass
    except Exception as e:
        logger.warning("Failed to list/remove router interfaces: %s", e)


def cleanup_resources(
    ecs_client: EcsClient | None = None,
    vpc_client: VpcClient | None = None,
    ims_client: ImsClient | None = None,
    evs_client: EvsClient | None = None,
    server_id: str = "",
    vpc_id: str = "",
    subnet_id: str = "",
    security_group_id: str = "",
    image_id: str = "",
    volume_id: str = "",
    keypair_name: str = "",
    scratch_volume_id: str = "",
) -> None:
    """Delete all temporary resources created during the pipeline.

    Order: ECS -> volume -> scratch volume -> keypair -> subnet ->
    security group -> VPC -> image. CBR backups are never touched.
    """
    logger.info("=== Cleanup de recursos temporales ===")

    if ecs_client and server_id:
        logger.info("Requesting deletion of ECS %s...", server_id)
        _safe(lambda: ecs_client.delete_servers(DeleteServersRequest(
            body=DeleteServersRequestBody(
                servers=[ServerId(id=server_id)],
                delete_volume=True,
                delete_publicip=True,
            )
        )), f"ECS {server_id} deletion requested")
        _wait_server_gone(ecs_client, server_id)

    if evs_client and volume_id:
        logger.info("Deleting volume %s...", volume_id)
        _safe(lambda: evs_client.delete_volume(DeleteVolumeRequest(volume_id=volume_id)), f"volume {volume_id}")

    if evs_client and scratch_volume_id:
        logger.info("Deleting scratch volume %s...", scratch_volume_id)
        _safe(
            lambda: evs_client.delete_volume(DeleteVolumeRequest(volume_id=scratch_volume_id)),
            f"scratch volume {scratch_volume_id}",
        )

    if ecs_client and keypair_name:
        logger.info("Deleting keypair %s...", keypair_name)
        _safe(lambda: ecs_client.nova_delete_keypair(NovaDeleteKeypairRequest(keypair_name=keypair_name)), f"keypair {keypair_name}")

    if vpc_client and subnet_id and vpc_id:
        logger.info("Deleting subnet %s...", subnet_id)
        _safe(lambda: vpc_client.delete_subnet(DeleteSubnetRequest(vpc_id=vpc_id, subnet_id=subnet_id)), f"subnet {subnet_id}")

    if vpc_client and security_group_id:
        logger.info("Deleting security group %s...", security_group_id)
        _safe(lambda: vpc_client.delete_security_group(DeleteSecurityGroupRequest(security_group_id=security_group_id)), f"security group {security_group_id}")

    if vpc_client and vpc_id:
        if subnet_id:
            _remove_subnet_from_routers(vpc_client, subnet_id)
            time.sleep(3)
        logger.info("Deleting VPC %s...", vpc_id)
        _safe(lambda: vpc_client.delete_vpc(DeleteVpcRequest(vpc_id=vpc_id)), f"VPC {vpc_id}")

    if ims_client and image_id:
        logger.info("Deleting image %s...", image_id)
        _safe(lambda: ims_client.glance_delete_image(GlanceDeleteImageRequest(image_id=image_id)), f"image {image_id}")

    logger.info("=== Cleanup completado ===")
