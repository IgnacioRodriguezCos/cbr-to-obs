# coding: utf-8
"""Stage 7: Cleanup temporary resources after successful export to OBS."""

from __future__ import annotations

import logging
import time

from huaweicloudsdkecs.v2.ecs_client import EcsClient
from huaweicloudsdkecs.v2 import (
    DeleteServersRequest, DeleteServersRequestBody, ServerId,
    ShowServerRequest, NovaDeleteKeypairRequest,
)
from huaweicloudsdkvpc.v2.vpc_client import VpcClient
from huaweicloudsdkvpc.v2 import (
    DeleteVpcRequest, DeleteSubnetRequest, DeleteSecurityGroupRequest,
    NeutronListRoutersRequest, NeutronRemoveRouterInterfaceRequest,
    RouterInterfaceRequestBody,
)
from huaweicloudsdkims.v2.ims_client import ImsClient
from huaweicloudsdkims.v2 import GlanceDeleteImageRequest
from huaweicloudsdkevs.v2.evs_client import EvsClient
from huaweicloudsdkevs.v2 import DeleteVolumeRequest
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
    logger.warning("ECS %s still deleting after %ds, continuing cleanup", server_id[:8], timeout)


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
) -> None:
    """Delete all temporary resources created during the pipeline.

    Order: ECS -> volume -> keypair -> subnet -> security group -> VPC -> image.
    CBR backups are never touched.
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
