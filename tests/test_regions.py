"""Tests for shared.regions module."""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.shared.regions import (
    REGIONS,
    REGION_ID_TO_ALIAS,
    BUCKET_NAMES,
    get_region_config,
    get_endpoint,
    get_bucket_name,
    is_cross_region,
)


class TestGetRegionConfig:
    def test_get_by_alias_buenosaires(self):
        config = get_region_config("buenosaires")
        assert config["id"] == "sa-argentina-1"
        assert config["name"] == "Buenos Aires"

    def test_get_by_alias_santiago(self):
        config = get_region_config("santiago")
        assert config["id"] == "la-south-2"
        assert config["name"] == "Santiago"

    def test_get_by_id_buenosaires(self):
        config = get_region_config("sa-argentina-1")
        assert config["name"] == "Buenos Aires"

    def test_get_by_id_santiago(self):
        config = get_region_config("la-south-2")
        assert config["name"] == "Santiago"

    def test_invalid_region_raises(self):
        with pytest.raises(ValueError, match="Unsupported region"):
            get_region_config("invalid-region")


class TestGetEndpoint:
    def test_cbr_endpoint_buenosaires(self):
        endpoint = get_endpoint("buenosaires", "cbr")
        assert endpoint == "https://cbr.sa-argentina-1.myhuaweicloud.com"

    def test_obs_endpoint_santiago(self):
        endpoint = get_endpoint("santiago", "obs")
        assert endpoint == "https://obs.la-south-2.myhuaweicloud.com"

    def test_iam_endpoint_by_id(self):
        endpoint = get_endpoint("sa-argentina-1", "iam")
        assert endpoint == "https://iam.sa-argentina-1.myhuaweicloud.com"


class TestGetBucketName:
    def test_bucket_buenosaires(self):
        assert get_bucket_name("buenosaires") == "cbr-evs-buenosaires"

    def test_bucket_santiago(self):
        assert get_bucket_name("santiago") == "cbr-evs-santiago"

    def test_bucket_by_region_id(self):
        assert get_bucket_name("sa-argentina-1") == "cbr-evs-buenosaires"


class TestIsCrossRegion:
    def test_same_region(self):
        assert is_cross_region("buenosaires", "buenosaires") is False

    def test_cross_region(self):
        assert is_cross_region("buenosaires", "santiago") is True

    def test_cross_region_by_id(self):
        assert is_cross_region("sa-argentina-1", "la-south-2") is True

    def test_same_region_by_id(self):
        assert is_cross_region("sa-argentina-1", "sa-argentina-1") is False
