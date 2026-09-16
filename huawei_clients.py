# coding: utf-8
"""Factory for Huawei Cloud SDK clients with custom-region fallback."""

from __future__ import annotations

import os

from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.region.region import Region
from huaweicloudsdkcore.http.http_config import HttpConfig

from huaweicloudsdkcbr.v1.cbr_client import CbrClient
from huaweicloudsdkcbr.v1.region.cbr_region import CbrRegion

from huaweicloudsdkevs.v2.evs_client import EvsClient
from huaweicloudsdkevs.v2.region.evs_region import EvsRegion

from huaweicloudsdkims.v2.ims_client import ImsClient
from huaweicloudsdkims.v2.region.ims_region import ImsRegion

from huaweicloudsdkecs.v2.ecs_client import EcsClient
from huaweicloudsdkecs.v2.region.ecs_region import EcsRegion

from huaweicloudsdkvpc.v2.vpc_client import VpcClient
from huaweicloudsdkvpc.v2.region.vpc_region import VpcRegion

from huaweicloudsdkobs.v1.obs_client import ObsClient
from huaweicloudsdkobs.v1.obs_credentials import ObsCredentials
from huaweicloudsdkobs.v1.region.obs_region import ObsRegion


_ENDPOINTS = {
    "cbr": "cbr.{region}.myhuaweicloud.com",
    "evs": "evs.{region}.myhuaweicloud.com",
    "ims": "ims.{region}.myhuaweicloud.com",
    "ecs": "ecs.{region}.myhuaweicloud.com",
    "vpc": "vpc.{region}.myhuaweicloud.com",
    "obs": "obs.{region}.myhuaweicloud.com",
}


def _get_http_config() -> HttpConfig:
    """Build HttpConfig from environment: SSL_CA_CERT or IGNORE_SSL."""
    config = HttpConfig()
    ssl_ca_cert = os.environ.get("SSL_CA_CERT", "")
    ignore_ssl = os.environ.get("IGNORE_SSL", "false").lower() == "true"
    if ssl_ca_cert:
        config.ssl_ca_cert = ssl_ca_cert
    if ignore_ssl:
        config.ignore_ssl_verification = True
    return config


def _resolve_region(region_cls, region_id: str, service_key: str) -> Region:
    try:
        return region_cls.value_of(region_id)
    except (KeyError, Exception):
        endpoint = _ENDPOINTS[service_key].format(region=region_id)
        return Region(id=region_id, endpoint=f"https://{endpoint}")


def build_cbr_client(ak: str, sk: str, region_id: str) -> CbrClient:
    return (
        CbrClient.new_builder()
        .with_http_config(_get_http_config())
        .with_credentials(BasicCredentials(ak, sk))
        .with_region(_resolve_region(CbrRegion, region_id, "cbr"))
        .build()
    )


def build_evs_client(ak: str, sk: str, region_id: str) -> EvsClient:
    return (
        EvsClient.new_builder()
        .with_http_config(_get_http_config())
        .with_credentials(BasicCredentials(ak, sk))
        .with_region(_resolve_region(EvsRegion, region_id, "evs"))
        .build()
    )


def build_ims_client(ak: str, sk: str, region_id: str) -> ImsClient:
    return (
        ImsClient.new_builder()
        .with_http_config(_get_http_config())
        .with_credentials(BasicCredentials(ak, sk))
        .with_region(_resolve_region(ImsRegion, region_id, "ims"))
        .build()
    )


def build_ecs_client(ak: str, sk: str, region_id: str) -> EcsClient:
    return (
        EcsClient.new_builder()
        .with_http_config(_get_http_config())
        .with_credentials(BasicCredentials(ak, sk))
        .with_region(_resolve_region(EcsRegion, region_id, "ecs"))
        .build()
    )


def build_vpc_client(ak: str, sk: str, region_id: str) -> VpcClient:
    return (
        VpcClient.new_builder()
        .with_http_config(_get_http_config())
        .with_credentials(BasicCredentials(ak, sk))
        .with_region(_resolve_region(VpcRegion, region_id, "vpc"))
        .build()
    )


def build_obs_client(ak: str, sk: str, region_id: str) -> ObsClient:
    return (
        ObsClient.new_builder()
        .with_http_config(_get_http_config())
        .with_credentials(ObsCredentials(ak=ak, sk=sk))
        .with_region(_resolve_region(ObsRegion, region_id, "obs"))
        .build()
    )
