"""ECS (Elastic Cloud Server) API client.

Used by the raw export path (volumes larger than the IMS 1TB limit):
provisions a temporary Linux ECS, attaches the restored volume to it,
and lets cloud-init stream the disk to OBS with dd + obsutil.
"""

import base64
import json

import requests

from .huawei_auth import HuaweiAuth
from .regions import get_endpoint


class ECSClient:
    """Client for Huawei Cloud ECS (Nova v2.1) operations."""

    def __init__(self, auth):
        self.auth = auth

    def create_server(
        self,
        region_input,
        name,
        user_data="",
        admin_pass=None,
        availability_zone=None,
    ):
        """Create a temporary ECS from configured image/flavor/network.

        Args:
            region_input: Region alias or ID.
            name: Server name.
            user_data: Cloud-init user data script (plain text).
            admin_pass: Optional admin password. Required if no keypair is set.
            availability_zone: Optional AZ override; defaults to temp AZ config.

        Returns:
            Server ID string.
        """
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        ecs_config = config.get_temp_ecs_config(region_input)
        az = availability_zone or config.get_temp_az(region_input)

        server = {
            "name": name,
            "imageRef": ecs_config["image_id"],
            "flavorRef": ecs_config["flavor_id"],
            "availability_zone": az,
            "networks": [{"uuid": ecs_config["network_id"]}],
        }
        if config.ecs_keypair:
            server["key_name"] = config.ecs_keypair
        elif admin_pass:
            server["adminPass"] = admin_pass
        if user_data:
            server["user_data"] = base64.b64encode(user_data.encode("utf-8")).decode("ascii")

        url = f"{get_endpoint(region_input, 'ecs')}/v2.1/{project_id}/servers"
        body_str = json.dumps({"server": server})

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("POST", url, headers, body_str)
        resp = requests.post(url, headers=headers, data=body_str, timeout=30)
        resp.raise_for_status()

        return resp.json().get("server", {}).get("id", "")

    def get_server(self, region_input, server_id):
        """Get server details including status."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        url = f"{get_endpoint(region_input, 'ecs')}/v2.1/{project_id}/servers/{server_id}"

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()

        return resp.json().get("server", {})

    def delete_server(self, region_input, server_id):
        """Delete a server."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        url = f"{get_endpoint(region_input, 'ecs')}/v2.1/{project_id}/servers/{server_id}"

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("DELETE", url, headers)
        resp = requests.delete(url, headers=headers, timeout=30)
        return resp.status_code in (200, 202, 204)

    def attach_volume(self, region_input, server_id, volume_id, device="/dev/vdb"):
        """Attach a volume to a server.

        Args:
            region_input: Region alias or ID.
            server_id: ECS server ID.
            volume_id: EVS volume ID to attach.
            device: Device name inside the guest (e.g. /dev/vdb).

        Returns:
            Attachment dict.
        """
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        url = (
            f"{get_endpoint(region_input, 'ecs')}/v2.1/{project_id}"
            f"/servers/{server_id}/os-volume_attachments"
        )
        body_str = json.dumps(
            {"volumeAttachment": {"volumeId": volume_id, "device": device}}
        )

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("POST", url, headers, body_str)
        resp = requests.post(url, headers=headers, data=body_str, timeout=30)
        resp.raise_for_status()

        return resp.json().get("volumeAttachment", {})

    def list_volume_attachments(self, region_input, server_id):
        """List all volume attachments of a server."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        url = (
            f"{get_endpoint(region_input, 'ecs')}/v2.1/{project_id}"
            f"/servers/{server_id}/os-volume_attachments"
        )

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()

        return resp.json().get("volumeAttachments", [])

    def get_attachment(self, region_input, server_id, volume_id):
        """Return the attachment for a volume, or None if not attached yet."""
        attachments = self.list_volume_attachments(region_input, server_id)
        for att in attachments:
            if str(att.get("volumeId", "")).lower() == str(volume_id).lower():
                return att
        return None
