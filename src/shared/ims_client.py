"""IMS (Image Management Service) API client.

Handles exporting images to OBS buckets and querying image status.
"""

import json

import requests

from .huawei_auth import HuaweiAuth
from .regions import get_endpoint


class IMSClient:
    """Client for Huawei Cloud IMS API operations."""

    def __init__(self, auth):
        self.auth = auth

    def create_image_from_volume(
        self,
        region_input,
        volume_id,
        name="cbr-migration-image",
        description="Temporary image for CBR-to-OBS migration",
        os_type="Linux",
    ):
        """Create an IMS whole image from an EVS volume (async).

        Args:
            region_input: Region alias or ID.
            volume_id: EVS volume ID to create image from.
            name: Image name.
            description: Image description.
            os_type: OS type (Linux or Windows).

        Returns:
            IMS job ID string (poll with get_job_status).
        """
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        endpoint = get_endpoint(region_input, "ims")
        url = f"{endpoint}/v2/{project_id}/cloudimages"

        body = {
            "name": name,
            "volume_id": volume_id,
            "type": "WholeImage",
            "os_type": os_type,
            "description": description,
        }
        body_str = json.dumps(body)

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("POST", url, headers, body_str)
        resp = requests.post(url, headers=headers, data=body_str, timeout=30, verify=False)
        if not resp.ok:
            try:
                err = resp.json()
                msg = err.get("message") or err.get("error_msg") or str(err)[:300]
            except Exception:
                msg = resp.text[:300]
            raise RuntimeError(f"IMS create image error {resp.status_code}: {msg}")

        return resp.json().get("job_id", "")

    def get_image(self, region_input, image_id):
        """Get image details including status."""
        endpoint = get_endpoint(region_input, "ims")
        url = f"{endpoint}/v1/images/{image_id}"

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
        resp.raise_for_status()

        return resp.json()

    def get_image_status(self, region_input, image_id):
        """Get the status of an image."""
        image = self.get_image(region_input, image_id)
        return image.get("status", "unknown")

    def export_image_to_obs(
        self,
        region_input,
        image_id,
        bucket_name,
        object_key,
        image_format="vhd",
    ):
        """Export an IMS image to an OBS bucket."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        endpoint = get_endpoint(region_input, "ims")
        url = f"{endpoint}/v1/cloudimages/action"

        body = {
            "action": "export",
            "image_id": image_id,
            "bucket": bucket_name,
            "object": object_key,
            "format": image_format,
        }
        body_str = json.dumps(body)

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("POST", url, headers, body_str)
        resp = requests.post(url, headers=headers, data=body_str, timeout=30, verify=False)
        resp.raise_for_status()

        return resp.json().get("job_id", "")

    def get_job_status(self, region_input, job_id):
        """Get the status of an IMS async job."""
        endpoint = get_endpoint(region_input, "ims")
        url = f"{endpoint}/v1/{job_id}"

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
        resp.raise_for_status()

        return resp.json()

    def delete_image(self, region_input, image_id):
        """Delete an IMS image."""
        endpoint = get_endpoint(region_input, "ims")
        url = f"{endpoint}/v1/images/{image_id}"

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("DELETE", url, headers)
        resp = requests.delete(url, headers=headers, timeout=30, verify=False)
        return resp.status_code in (200, 204)
