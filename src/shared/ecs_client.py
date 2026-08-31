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
            "vpcid": ecs_config["vpc_id"],
            "nics": [{"subnet_id": ecs_config["network_id"]}],
            "root_volume": {"volumetype": "GPSSD", "size": 40},
        }
        if config.ecs_keypair:
            server["key_name"] = config.ecs_keypair
        elif admin_pass:
            server["adminPass"] = admin_pass
        if user_data:
            server["user_data"] = base64.b64encode(user_data.encode("utf-8")).decode("ascii")

        url = f"{get_endpoint(region_input, 'ecs')}/v1.1/{project_id}/cloudservers"
        body_str = json.dumps({"server": server})

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("POST", url, headers, body_str)
        resp = requests.post(url, headers=headers, data=body_str, timeout=30, verify=False)
        if not resp.ok:
            try:
                err = resp.json()
                msg = err.get("message") or err.get("error_msg") or str(err)[:400]
            except Exception:
                msg = resp.text[:400]
            raise RuntimeError(f"ECS create server error {resp.status_code}: {msg}")

        return resp.json().get("server", {}).get("id", "")

    def get_server(self, region_input, server_id):
        """Get server details including status."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        url = f"{get_endpoint(region_input, 'ecs')}/v1.1/{project_id}/cloudservers/{server_id}"

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
        resp.raise_for_status()

        return resp.json().get("server", {})

    def delete_server(self, region_input, server_id):
        """Delete a server."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        url = f"{get_endpoint(region_input, 'ecs')}/v1.1/{project_id}/cloudservers/{server_id}"

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("DELETE", url, headers)
        resp = requests.delete(url, headers=headers, timeout=30, verify=False)
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
            f"{get_endpoint(region_input, 'ecs')}/v1.1/{project_id}"
            f"/cloudservers/{server_id}/os-volume_attachments"
        )
        body_str = json.dumps(
            {"volumeAttachment": {"volumeId": volume_id, "device": device}}
        )

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("POST", url, headers, body_str)
        resp = requests.post(url, headers=headers, data=body_str, timeout=30, verify=False)
        resp.raise_for_status()

        return resp.json().get("volumeAttachment", {})

    def list_volume_attachments(self, region_input, server_id):
        """List all volume attachments of a server."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        url = (
            f"{get_endpoint(region_input, 'ecs')}/v1.1/{project_id}"
            f"/cloudservers/{server_id}/os-volume_attachments"
        )

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
        resp.raise_for_status()

        return resp.json().get("volumeAttachments", [])

    def get_attachment(self, region_input, server_id, volume_id):
        """Return the attachment for a volume, or None if not attached yet."""
        attachments = self.list_volume_attachments(region_input, server_id)
        for att in attachments:
            if str(att.get("volumeId", "")).lower() == str(volume_id).lower():
                return att
        return None

    def list_flavors(self, region_input):
        """List available ECS flavors in a region."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        url = f"{get_endpoint(region_input, 'ecs')}/v2.1/{project_id}/flavors"

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
        if not resp.ok:
            return []
        return resp.json().get("flavors", [])

    def list_images(self, region_input):
        """List available public Linux images in a region."""
        from .regions import get_endpoint as _gep

        url = f"{_gep(region_input, 'ims')}/v1/images?__imagetype=gold&__os_type=Linux&limit=50"
        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
        if resp.ok:
            images = resp.json().get("images", [])
            if images:
                return images

        url = f"{_gep(region_input, 'ims')}/v1/images?__imagetype=gold&limit=50"
        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
        if resp.ok:
            images = resp.json().get("images", [])
            if images:
                return [i for i in images if (i.get("__os_type", "Linux")).lower() in ("linux", "")]

        url = f"{_gep(region_input, 'ims')}/v1/images"
        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
        if not resp.ok:
            return []
        all_images = resp.json().get("images", [])
        return [i for i in all_images if i.get("__imagetype", "") != "market"]

    def list_networks(self, region_input):
        """List available VPCs/subnets in a region."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        url = f"{get_endpoint(region_input, 'vpc')}/v1/{project_id}/vpcs"

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
        if not resp.ok:
            return []
        vpcs = resp.json().get("vpcs", [])
        for vpc in vpcs:
            vpc_id = vpc.get("id")
            if vpc_id:
                sub_url = f"{get_endpoint(region_input, 'vpc')}/v1/{project_id}/subnets?vpc_id={vpc_id}"
                sub_headers = {"Content-Type": "application/json"}
                sub_headers = self.auth.sign_request("GET", sub_url, sub_headers)
                sub_resp = requests.get(sub_url, headers=sub_headers, timeout=30, verify=False)
                if sub_resp.ok:
                    subnets = sub_resp.json().get("subnets", [])
                    if subnets:
                        return {"vpc_id": vpc_id, "subnet": subnets[0]}
        return []

    def auto_detect_config(self, region_input):
        """Auto-detect ECS settings (image, flavor, network) for a region.

        Tries REST API first, falls back to hcloud CLI via subprocess.

        Args:
            region_input: Region alias or ID.

        Returns:
            Dict with image_id, flavor_id, network_id, vpc_id.

        Raises:
            RuntimeError: If no suitable resources are found.
        """
        from .regions import get_region_config
        region_id = get_region_config(region_input)["id"]

        try:
            return self._auto_detect_rest(region_input)
        except Exception as e:
            print(f"[ECS] REST auto-detect failed: {e}, trying hcloud CLI...")

        return self._auto_detect_hcloud(region_input, region_id)

    def _auto_detect_rest(self, region_input):
        """Auto-detect via REST API calls."""
        images = self.list_images(region_input)
        linux_image = None
        for img in images:
            name = (img.get("name", "") + img.get("__os_type", "")).lower()
            if any(k in name for k in ("hce", "euler", "centos", "debian", "ubuntu", "linux")):
                linux_image = img
                break
        if not linux_image and images:
            linux_image = images[0]
        if not linux_image:
            raise RuntimeError(f"No usable Linux image found in {region_input}")

        flavors = self.list_flavors(region_input)
        if not flavors:
            raise RuntimeError(f"No ECS flavors found in {region_input}")
        flavor = flavors[0]

        subnets = self.list_networks(region_input)
        if not subnets:
            raise RuntimeError(f"No VPC subnets found in {region_input}")
        vpc_id = subnets["vpc_id"]
        subnet = subnets["subnet"]

        result = {
            "image_id": linux_image.get("id", ""),
            "flavor_id": flavor.get("id", ""),
            "network_id": subnet.get("id", ""),
            "vpc_id": vpc_id,
        }
        if not all(result.values()):
            raise RuntimeError(f"Auto-detect incomplete: {result}")
        return result

    def _auto_detect_hcloud(self, region_input, region_id):
        """Auto-detect via hcloud CLI subprocess."""
        import subprocess
        import os as _os

        hcloud = r"C:\Users\i50055736\hcloud\hcloud.exe"
        if not _os.path.exists(hcloud):
            raise RuntimeError("hcloud CLI not found. Install with: npx huaweicloud-devkit install-hcloud")

        from .config import Config
        config = Config()

        env = dict(_os.environ)
        env["HW_ACCESS_KEY"] = config.access_key
        env["HW_SECRET_KEY"] = config.secret_key

        def run_hcloud(args):
            cmd = [hcloud] + args + ["--cli-region", region_id, "--cli-output", "json"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env, input="y\n")
            if r.returncode != 0:
                raise RuntimeError(f"hcloud failed: {r.stderr[:200]}")
            return json.loads(r.stdout)

        images_data = run_hcloud(["IMS", "ListImages", "--__imagetype", "gold", "--__os_type", "Linux", "--limit", "20"])
        images = images_data.get("images", [])
        if not images:
            images_data = run_hcloud(["IMS", "ListImages", "--__imagetype", "gold", "--limit", "20"])
            images = images_data.get("images", [])
        if not images:
            raise RuntimeError(f"hcloud: no images found in {region_id}")
        image_id = images[0].get("id", "")

        flavors_data = run_hcloud(["ECS", "ListFlavors", "--limit", "20"])
        flavors = flavors_data.get("flavors", [])
        if not flavors:
            raise RuntimeError(f"hcloud: no flavors found in {region_id}")
        flavor_id = flavors[0].get("id", "")

        vpcs_data = run_hcloud(["VPC", "ListVpcs"])
        vpcs = vpcs_data.get("vpcs", [])
        if not vpcs:
            raise RuntimeError(f"hcloud: no VPCs found in {region_id}")
        vpc_id = vpcs[0].get("id", "")

        subnets_data = run_hcloud(["VPC", "ListSubnets", "--vpc_id", vpc_id])
        subnets = subnets_data.get("subnets", [])
        if not subnets:
            raise RuntimeError(f"hcloud: no subnets found in VPC {vpc_id}")
        network_id = subnets[0].get("id", "")

        result = {"image_id": image_id, "flavor_id": flavor_id, "network_id": network_id, "vpc_id": vpc_id}
        if not all(result.values()):
            raise RuntimeError(f"hcloud auto-detect incomplete: {result}")
        print(f"[ECS] hcloud auto-detect OK: {result}")
        return result
