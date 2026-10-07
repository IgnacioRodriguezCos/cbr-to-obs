# coding: utf-8
"""Stage 3c: OBS gateway VPC endpoint — private upload path (intl regions).

International regions (LA/AP/EU) have NO obs.<region>.internal.
myhuaweicloud.com domain. Private OBS access works differently there:
1. The subnet's private DNS resolves the PUBLIC domain
   (bucket.obs.<region>.myhuaweicloud.com) to a private 100.125.x.x IP.
2. A gateway VPC endpoint for com.myhuaweicloud.<region>.obs adds a route
   for 100.125.0.0/16 (OBS's reserved private CIDR) to the VPC's route
   table, keeping the traffic inside the cloud network — the EIP's
   bandwidth cap never applies to it.
The endpoint is created per run and deleted in cleanup (gateway
endpoints for cloud services carry no hourly charge; is_charge is logged).
"""

from __future__ import annotations

import logging
import time

from huaweicloudsdkvpcep.v1.vpcep_client import VpcepClient
from huaweicloudsdkvpcep.v1 import (
    CreateEndpointRequest,
    CreateEndpointRequestBody,
    ListEndpointInfoDetailsRequest,
    ListServicePublicDetailsRequest,
)

logger = logging.getLogger(__name__)


def ensure_obs_gateway_endpoint(
    vpcep_client: VpcepClient,
    vpc_id: str,
    region: str,
    description: str = "cbr-to-obs-temp",
) -> str:
    """Create the OBS gateway VPC endpoint in `vpc_id`; returns its id.

    The cloud service com.myhuaweicloud.<region>.obs is looked up in the
    public VPCEP catalog (ListServicePublicDetails) to get its UUID, then
    a gateway endpoint is created (routetables omitted = the VPC's default
    route table receives the 100.125.0.0/16 route) and waited until
    accepted.
    """
    service_name = f"com.myhuaweicloud.{region}.obs"
    resp = vpcep_client.list_service_public_details(
        ListServicePublicDetailsRequest(endpoint_service_name=service_name, limit=100)
    )
    matches = [
        s for s in (resp.endpoint_services or [])
        if (s.service_name or "") == service_name
    ]
    if not matches:
        raise RuntimeError(
            f"El servicio VPC endpoint publico '{service_name}' no existe en {region}. "
            "Sin el gateway endpoint de OBS la subida iria por el EIP (5Mbps). "
            "Revisar el catalogo de VPCEP en la consola o abrir un ticket."
        )
    svc = matches[0]
    logger.info(
        "Servicio VPCEP de OBS: %s (tipo=%s, is_charge=%s)",
        svc.service_name, svc.service_type, svc.is_charge,
    )
    if (svc.service_type or "").lower() not in ("gateway", ""):
        logger.warning(
            "  El servicio %s no es tipo gateway (%r) — continuando igual",
            service_name, svc.service_type,
        )

    created = vpcep_client.create_endpoint(CreateEndpointRequest(
        body=CreateEndpointRequestBody(
            endpoint_service_id=svc.id,
            vpc_id=vpc_id,
            description=description,
        )
    ))
    endpoint_id = created.id
    logger.info(
        "VPC endpoint %s creado (status=%s, route_tables=%s)",
        endpoint_id, created.status, created.routetables,
    )

    status = created.status or ""
    deadline = time.time() + 120
    while time.time() < deadline and status != "accepted":
        time.sleep(5)
        info = vpcep_client.list_endpoint_info_details(
            ListEndpointInfoDetailsRequest(vpc_endpoint_id=endpoint_id)
        )
        status = info.status or ""
        logger.info("  VPC endpoint status: %s (esperando 'accepted')...", status)
    if status == "accepted":
        logger.info("VPC endpoint de OBS aceptado — ruta privada 100.125.0.0/16 activa")
    else:
        logger.warning(
            "VPC endpoint %s sigue en status=%s tras 120s — continuando "
            "(el pre-flight de OBS validara la conectividad)", endpoint_id, status,
        )
    return endpoint_id
