"""EVS (Elastic Volume Service) API client.

Handles volume creation from backups, status queries,
and creating images from volumes.
"""

import json

import requests

from .huawei_auth import HuaweiAuth
from .regions import get_endpoint


class EVSClient:
    """Client for Huawei Cloud EVS API operations."""

    def __init__(self, auth):
        self.auth = auth

    def create_volume_from_backup(
        self,
        region_input,
        backup_id,
        name="cbr-migration-temp",
        volume_type="SATA",
        availability_zone=None,
    ):
        """Create a new EVS volume from a CBR backup."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)

        if not availability_zone:
            availability_zone = config.get_temp_az(region_input)

        endpoint = get_endpoint(region_input, "evs")
        url = f"{endpoint}/v2/{project_id}/cloudvolumes"

        body = {
            "volume": {
                "name": name,
                "volume_type": volume_type,
                "availability_zone": availability_zone,
                "backup_id": backup_id,
            }
        }
        body_str = json.dumps(body)

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("POST", url, headers, body_str)
        resp = requests.post(url, headers=headers, data=body_str, timeout=30)
        resp.raise_for_status()

        return resp.json().get("volume", {}).get("id", "")

    def get_volume(self, region_input, volume_id):
        """Get volume details including status."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        endpoint = get_endpoint(region_input, "evs")
        url = f"{endpoint}/v2/{project_id}/cloudvolumes/{volume_id}"

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()

        return resp.json().get("volume", {})

    def get_volume_status(self, region_input, volume_id):
        """Get the status of a volume."""
        volume = self.get_volume(region_input, volume_id)
        return volume.get("status", "unknown")

    def create_image_from_volume(
        self,
        region_input,
        volume_id,
        image_name="cbr-migration-image",
        description="Temporary image for CBR-to-OBS migration",
    ):
        """Create an IMS image from an EVS volume."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        endpoint = get_endpoint(region_input, "evs")
        url = f"{endpoint}/v2/{project_id}/cloudvolumes/{volume_id}/action"

        body = {
            "os-volume_upload_image": {
                "image_name": image_name,
                "image_description": description,
                "force": True,
            }
        }
        body_str = json.dumps(body)

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("POST", url, headers, body_str)
        resp = requests.post(url, headers=headers, data=body_str, timeout=30)
        resp.raise_for_status()

        data = resp.json()
        image_info = data.get("os-volume_upload_image", {})
        return {
            "image_id": image_info.get("image_id", ""),
            "location": image_info.get("location", ""),
        }

    def delete_volume(self, region_input, volume_id):
        """Delete an EVS volume."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        endpoint = get_endpoint(region_input, "evs")
        url = f"{endpoint}/v2/{project_id}/cloudvolumes/{volume_id}"

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("DELETE", url, headers)
        resp = requests.delete(url, headers=headers, timeout=30)
        return resp.status_code in (200, 202, 204)
