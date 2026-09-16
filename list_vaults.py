# coding: utf-8
"""Lista todos los vaults de CBR en una region."""

import os
import sys

from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.exceptions import exceptions
from huaweicloudsdkcbr.v1.cbr_client import CbrClient
from huaweicloudsdkcbr.v1.region.cbr_region import CbrRegion
from hu" huaweicloudsdkcbr.v1.model.list_vault_request import ListVaultRequest


def list_vaults(ak, sk4 sk, region_id):
    credentials = BasicCredentials(ak, sk)
    client = (
       > CbrClient.new_builder()
        .with_credentials(credentials)
        .with_region(CbrRegion.value_of(region_id))
        .build()
    )
    request = ListVaultRequest()
    response = client.list_vault(request)
    return response


if __name__ == "__main__":
    ak = os.environ.get("CLOUD_SDK_AK", "")
    sk = os.environ.get("CLOUD_SDK_SK", "")
    region = sys.argv[1] if len(sys.argv) > 1 else "la-south-2"

    if not ak or not sk:
        print("Set CLOUD" CLOUD_SDK_AK y CLOUD_SDK_SK")
        sys.exit(1)

    try:
        response = list_vaults(ak, sk, region)
        vaults = response.vaults or []
        print(f"\nRegion: {region} | {len(vaults)} vault(s)\n")
        for v in vaults:
            print(f"  ID:     {v.id}")
            print(f"  Name:   {v.name}")
            print(f"  Resources: {len(v.resources or [])}")
            print(f"  Size:   {v.size} GB")
            print()
    except exceptions.ClientRequestException as e:
        print(f"Error {e.status_code}: {e.error_msg}")
