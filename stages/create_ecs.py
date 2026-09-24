# coding: utf-8
"""Stage 2b: Create an ECS automatically and attach the restored volume.

Creates a VPC, subnet, security group, finds a CentOS public image and a
minimal flavor — all automatically. Then creates the ECS and attaches the
restored volume as a data disk.
"""

from __future__ import annotations

import logging
import time
import uuid

from huaweicloudsdkecs.v2.ecs_client import EcsClient
from huaweicloudsdkecs.v2 import (
    CreateServersRequest,
    CreateServersRequestBody,
    PrePaidServer,
    PrePaidServerExtendParam,
    PrePaidServerNic,
    PrePaidServerRootVolume,
    PrePaidServerSecurityGroup,
    PrePaidServerPublicip,
    PrePaidServerEip,
    PrePaidServerEipBandwidth,
    AttachServerVolumeRequest,
    AttachServerVolumeRequestBody,
    AttachServerVolumeOption,
    ShowServerRequest,
    ListFlavorsRequest,
    NovaCreateKeypairRequest,
    NovaCreateKeypairRequestBody,
    NovaCreateKeypairOption,
    NovaDeleteKeypairRequest,
)
from huaweicloudsdkims.v2.ims_client import ImsClient
from huaweicloudsdkims.v2 import ListImagesRequest

from huaweicloudsdkvpc.v2.vpc_client import VpcClient
from huaweicloudsdkvpc.v2 import (
    CreateVpcRequest, CreateVpcRequestBody, CreateVpcOption,
    CreateSubnetRequest, CreateSubnetRequestBody, CreateSubnetOption,
    CreateSecurityGroupRequest, CreateSecurityGroupRequestBody, CreateSecurityGroupOption,
    CreateSecurityGroupRuleRequest, CreateSecurityGroupRuleRequestBody, CreateSecurityGroupRuleOption,
)

from huaweicloudsdkcore.exceptions import exceptions

logger = logging.getLogger(__name__)

_VPC_CIDR = "192.168.0.0/16"
_SUBNET_CIDR = "192.168.0.0/24"
_GATEWAY_IP = "192.168.0.1"


def _create_vpc(vpc_client: VpcClient, name: str) -> str:
    request = CreateVpcRequest(
        body=CreateVpcRequestBody(vpc=CreateVpcOption(name=name, cidr=_VPC_CIDR))
    )
    resp = vpc_client.create_vpc(request)
    vpc_id = resp.vpc.id
    logger.info("VPC created: %s (%s)", vpc_id, _VPC_CIDR)
    return vpc_id


def _create_subnet(vpc_client: VpcClient, vpc_id: str, name: str) -> str:
    request = CreateSubnetRequest(
        body=CreateSubnetRequestBody(
            subnet=CreateSubnetOption(
                vpc_id=vpc_id, name=name, cidr=_SUBNET_CIDR, gateway_ip=_GATEWAY_IP,
            )
        )
    )
    resp = vpc_client.create_subnet(request)
    subnet_id = resp.subnet.id
    logger.info("Subnet created: %s (%s)", subnet_id, _SUBNET_CIDR)
    return subnet_id


def _create_security_group(vpc_client: VpcClient, name: str) -> str:
    request = CreateSecurityGroupRequest(
        body=CreateSecurityGroupRequestBody(security_group=CreateSecurityGroupOption(name=name))
    )
    resp = vpc_client.create_security_group(request)
    sg_id = resp.security_group.id
    logger.info("Security group created: %s", sg_id)
    return sg_id


def _add_ssh_rule(vpc_client: VpcClient, sg_id: str) -> None:
    request = CreateSecurityGroupRuleRequest(
        body=CreateSecurityGroupRuleRequestBody(
            security_group_rule=CreateSecurityGroupRuleOption(
                security_group_id=sg_id,
                direction="ingress",
                protocol="tcp",
                port_range_min=22,
                port_range_max=22,
                remote_ip_prefix="0.0.0.0/0",
            )
        )
    )
    vpc_client.create_security_group_rule(request)
    logger.info("SSH rule (port 22) added to SG %s", sg_id)


def _generate_and_import_keypair(ecs_client: EcsClient, name: str) -> str:
    """Generate an RSA keypair locally, import public key to ECS. Returns PEM private key."""
    import paramiko
    import io

    key = paramiko.RSAKey.generate(2048)
    pub_b64 = key.get_base64()
    public_key = f"ssh-rsa {pub_b64} pipeline"

    sio = io.StringIO()
    key.write_private_key(sio)
    private_key_pem = sio.getvalue()

    request = NovaCreateKeypairRequest(
        body=NovaCreateKeypairRequestBody(
            keypair=NovaCreateKeypairOption(name=name, public_key=public_key)
        )
    )
    ecs_client.nova_create_keypair(request)
    logger.info("Keypair imported: %s", name)
    return private_key_pem


def _get_public_ip(ecs_client: EcsClient, server_id: str) -> str:
    """Extract the floating (EIP) address from a running ECS."""
    resp = ecs_client.show_server(ShowServerRequest(server_id=server_id))
    addresses = resp.server.addresses or {}
    for _net, addrs in addresses.items():
        for addr in (addrs or []):
            if getattr(addr, "type", "") == "floating":
                return addr.addr
    raise RuntimeError(f"No public IP found on ECS {server_id}")


def _find_centos_image(ims_client: ImsClient) -> str:
    _EXCLUDE = ("tesla", "cuda", "gpu", "driver", "fusioncompute", "bms", "baremetal")
    resp = ims_client.list_images(ListImagesRequest(imagetype="gold", status="active", limit=100))
    images = resp.images or []

    plain = [img for img in images
             if "centos" in (img.name or "").lower()
             and not any(x in (img.name or "").lower() for x in _EXCLUDE)]
    plain.sort(key=lambda img: (img.name or ""))

    for img in plain:
        if "7" in (img.name or ""):
            logger.info("Found CentOS 7 image: %s (%s)", img.id, img.name)
            return img.id
    if plain:
        logger.info("Found CentOS image: %s (%s)", plain[0].id, plain[0].name)
        return plain[0].id

    for img in images:
        name = (img.name or "").lower()
        if "linux" in name and not any(x in name for x in _EXCLUDE):
            logger.info("Found Linux image: %s (%s)", img.id, img.name)
            return img.id
    raise RuntimeError("No se encontro imagen CentOS/Linux publica sin GPU")


def _find_windows_image(ims_client: ImsClient) -> str:
    """Find a plain Windows Server public image (no GPU/preinstalled extras)."""
    _EXCLUDE = ("tesla", "cuda", "gpu", "driver", "fusioncompute", "bms", "baremetal",
                "sql", "exchange", "sharepoint", "rds", "preinstalled")
    resp = ims_client.list_images(ListImagesRequest(imagetype="gold", status="active", limit=100))
    images = resp.images or []

    win = [img for img in images
           if "windows" in (img.name or "").lower()
           and not any(x in (img.name or "").lower() for x in _EXCLUDE)]
    # Prefer Standard edition, then English versions, then name order
    win.sort(key=lambda img: (
        0 if "standard" in (img.name or "").lower() else 1,
        0 if "english" in (img.name or "").lower() else 1,
        img.name or "",
    ))
    if win:
        logger.info("Found Windows image: %s (%s)", win[0].id, win[0].name)
        return win[0].id
    raise RuntimeError("No se encontro imagen Windows publica sin extras")


def _find_minimal_flavors(ecs_client: EcsClient, min_ram_mb: int = 0) -> list[str]:
    """Return flavor IDs sorted by RAM (smallest first), excluding GPSSD2/ESSD2-only."""
    resp = ecs_client.list_flavors(ListFlavorsRequest(limit=200))
    flavors = resp.flavors or []
    if not flavors:
        raise RuntimeError("No se encontraron flavors disponibles")
    if min_ram_mb:
        flavors = [f for f in flavors if (getattr(f, "ram", 0) or 0) >= min_ram_mb]
        if not flavors:
            raise RuntimeError(f"No hay flavors con >= {min_ram_mb} MB RAM")
    flavors.sort(key=lambda f: getattr(f, "ram", 999999) or 999999)
    result = [f.id for f in flavors]
    logger.info("Candidate flavors (by RAM): %s", result[:10])
    return result


def _wait_server_active(ecs_client: EcsClient, server_id: str, interval: int = 10, timeout: int = 600) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = ecs_client.show_server(ShowServerRequest(server_id=server_id))
            status = resp.server.status or ""
            logger.info("  ECS %s status=%s", server_id, status)
            if status == "ACTIVE":
                return
            if status == "ERROR":
                raise RuntimeError(f"ECS {server_id} entered ERROR state")
        except exceptions.ClientRequestException as e:
            if e.status_code == 404:
                logger.info("  ECS %s not found yet (404) — still creating...", server_id)
            else:
                raise
        time.sleep(interval)
    raise TimeoutError(f"ECS {server_id} did not become ACTIVE after {timeout}s")


def create_ecs_and_attach(
    ecs_client: EcsClient,
    vpc_client: VpcClient,
    ims_client: ImsClient,
    volume_id: str,
    availability_zone: str,
    enable_ssh: bool = False,
    os_type: str = "Linux",
) -> dict:
    """Create an ECS with auto-provisioned networking and attach the restored volume.

    os_type ("Windows"/"Linux") selects the base public image. IMS derives the
    data disk image OS type from the ECS that owns the volume, so use a Windows
    ECS when the restored disk contains Windows data.

    Returns dict with: server_id, vpc_id, subnet_id, security_group_id.
    When enable_ssh=True, also returns: keypair_name, private_key_pem, public_ip.
    """
    suffix = uuid.uuid4().hex[:6]

    logging.info("[3/6] Provisionando networking automatico...")
    vpc_id = _create_vpc(vpc_client, f"vpc-restore-{suffix}")
    subnet_id = _create_subnet(vpc_client, vpc_id, f"subnet-restore-{suffix}")
    sg_id = _create_security_group(vpc_client, f"sg-restore-{suffix}")

    keypair_name = None
    private_key_pem = None
    public_ip = None

    if enable_ssh:
        keypair_name = f"kp-restore-{suffix}"
        private_key_pem = _generate_and_import_keypair(ecs_client, keypair_name)
        _add_ssh_rule(vpc_client, sg_id)
        logging.info("[3/6] SSH habilitado: keypair=%s, regla port 22 agregada", keypair_name)

    if os_type == "Windows":
        logging.info("[3/6] Buscando imagen Windows y flavor (>=2GB RAM)...")
        image_id = _find_windows_image(ims_client)
        flavor_candidates = _find_minimal_flavors(ecs_client, min_ram_mb=2048)
    else:
        logging.info("[3/6] Buscando imagen CentOS y flavor minimo...")
        image_id = _find_centos_image(ims_client)
        flavor_candidates = _find_minimal_flavors(ecs_client)

    server_name = f"ecs-restore-{suffix}"
    server_spec_base = PrePaidServer(
        image_ref=image_id,
        name=server_name,
        vpcid=vpc_id,
        nics=[PrePaidServerNic(subnet_id=subnet_id)],
        root_volume=PrePaidServerRootVolume(volumetype="GPSSD", size=40),
        security_groups=[PrePaidServerSecurityGroup(id=sg_id)],
        availability_zone=availability_zone,
        extendparam=PrePaidServerExtendParam(charging_mode="postPaid"),
    )

    if enable_ssh:
        server_spec_base.key_name = keypair_name
        server_spec_base.publicip = PrePaidServerPublicip(
            eip=PrePaidServerEip(
                iptype="5_bgp",
                bandwidth=PrePaidServerEipBandwidth(size=5, sharetype="PER"),
            ),
            delete_on_termination=True,
        )

    server_id = None
    for flavor_id in flavor_candidates:
        server_spec_base.flavor_ref = flavor_id
        request = CreateServersRequest(body=CreateServersRequestBody(server=server_spec_base))
        try:
            logging.info("[3/6] Creando ECS: name=%s flavor=%s az=%s", server_name, flavor_id, availability_zone)
            response = ecs_client.create_servers(request)
            if response.server_ids:
                server_id = response.server_ids[0]
            if server_id:
                break
        except exceptions.ClientRequestException as e:
            if e.error_code in ("Ecs.0019", "Ecs.0005", "Ecs.0047"):
                logger.warning("Flavor %s no valido (%s) — probando siguiente", flavor_id, e.error_code)
                continue
            raise
    if not server_id:
        raise RuntimeError("Ningun flavor funciono para crear la ECS")

    logger.info("ECS created: %s — waiting for ACTIVE", server_id)
    _wait_server_active(ecs_client, server_id)

    if enable_ssh:
        public_ip = _get_public_ip(ecs_client, server_id)
        logging.info("ECS public IP: %s", public_ip)

    attach_req = AttachServerVolumeRequest(
        server_id=server_id,
        body=AttachServerVolumeRequestBody(
            volume_attachment=AttachServerVolumeOption(volume_id=volume_id)
        ),
    )
    logger.info("Attaching volume %s to ECS %s", volume_id, server_id)
    ecs_client.attach_server_volume(attach_req)
    time.sleep(5)
    _wait_server_active(ecs_client, server_id)

    logger.info("ECS %s lista con disco %s attachado", server_id, volume_id)
    result = {
        "server_id": server_id,
        "vpc_id": vpc_id,
        "subnet_id": subnet_id,
        "security_group_id": sg_id,
    }
    if enable_ssh:
        result["keypair_name"] = keypair_name
        result["private_key_pem"] = private_key_pem
        result["public_ip"] = public_ip
    return result
