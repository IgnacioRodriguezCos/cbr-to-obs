"""Region definitions for Huawei Cloud CBR-to-OBS migration.

Supports Buenos Aires (sa-argentina-1) and Santiago (la-south-2) regions.
"""

REGIONS = {
    "buenosaires": {
        "id": "sa-argentina-1",
        "name": "Buenos Aires",
        "endpoints": {
            "cbr": "cbr.sa-argentina-1.myhuaweicloud.com",
            "evs": "evs.sa-argentina-1.myhuaweicloud.com",
            "ims": "ims.sa-argentina-1.myhuaweicloud.com",
            "obs": "obs.sa-argentina-1.myhuaweicloud.com",
            "iam": "iam.sa-argentina-1.myhuaweicloud.com",
        },
    },
    "santiago": {
        "id": "la-south-2",
        "name": "Santiago",
        "endpoints": {
            "cbr": "cbr.la-south-2.myhuaweicloud.com",
            "evs": "evs.la-south-2.myhuaweicloud.com",
            "ims": "ims.la-south-2.myhuaweicloud.com",
            "obs": "obs.la-south-2.myhuaweicloud.com",
            "iam": "iam.la-south-2.myhuaweicloud.com",
        },
    },
}

REGION_ID_TO_ALIAS = {
    "sa-argentina-1": "buenosaires",
    "la-south-2": "santiago",
}

BUCKET_NAMES = {
    "buenosaires": "cbr-evs-buenosaires",
    "santiago": "cbr-evs-santiago",
}


def get_region_config(region_input):
    """Get region configuration by alias (buenosaires/santiago) or ID (sa-argentina-1/la-south-2).

    Args:
        region_input: Region alias or region ID.

    Returns:
        Dict with region configuration including id, name, and endpoints.

    Raises:
        ValueError: If the region is not supported.
    """
    if region_input in REGIONS:
        return REGIONS[region_input]
    if region_input in REGION_ID_TO_ALIAS:
        return REGIONS[REGION_ID_TO_ALIAS[region_input]]
    raise ValueError(
        f"Unsupported region: {region_input}. "
        f"Supported: {list(REGIONS.keys())} or {list(REGION_ID_TO_ALIAS.keys())}"
    )


def get_endpoint(region_input, service):
    """Get the endpoint URL for a specific service in a region.

    Args:
        region_input: Region alias or ID.
        service: Service name (cbr, evs, ims, obs, iam).

    Returns:
        Full HTTPS endpoint URL string.
    """
    config = get_region_config(region_input)
    return f"https://{config['endpoints'][service]}"


def get_bucket_name(region_input):
    """Get the OBS bucket name for EVS backups in a region.

    Args:
        region_input: Region alias or ID.

    Returns:
        Bucket name string.
    """
    config = get_region_config(region_input)
    alias = REGION_ID_TO_ALIAS.get(config["id"], region_input)
    return BUCKET_NAMES[alias]


def is_cross_region(source_region, target_region):
    """Check if migration is cross-region.

    Args:
        source_region: Source region alias or ID.
        target_region: Target region alias or ID.

    Returns:
        True if regions differ, False otherwise.
    """
    src = get_region_config(source_region)["id"]
    dst = get_region_config(target_region)["id"]
    return src != dst
