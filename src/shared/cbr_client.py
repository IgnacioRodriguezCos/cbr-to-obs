"""CBR (Cloud Backup and Recovery) API client.

Handles listing backups, restoring backups to volumes,
and replicating backups across regions.
"""

import json

import requests

from .huawei_auth import HuaweiAuth
from .regions import get_endpoint


EVS_PROVIDER_ID = "d1603440-187d-4516-af25-121250c7cc97"


class CBRClient:
    """Client for Huawei Cloud CBR API operations."""

    def __init__(self, auth):
        self.auth = auth

    def list_evs_backups(self, region_input, status="available", limit=1000):
        """List all EVS disk backups in a region."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        endpoint = get_endpoint(region_input, "cbr")
        url = (
            f"{endpoint}/v3/{project_id}/backups"
            f"?resource_type=OS::Cinder::Volume&status={status}&limit={limit}"
        )

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
        if not resp.ok:
            try:
                err = resp.json()
                msg = err.get("error_msg") or err.get("message") or str(err)[:200]
            except Exception:
                msg = resp.text[:200]
            raise RuntimeError(f"CBR API error {resp.status_code}: {msg}")

        return resp.json().get("backups", [])

    def get_backup(self, region_input, backup_id):
        """Get details of a specific backup."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        endpoint = get_endpoint(region_input, "cbr")
        url = f"{endpoint}/v3/{project_id}/backups/{backup_id}"

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("GET", url, headers)
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
        resp.raise_for_status()

        return resp.json().get("backup", {})

    def restore_to_volume(self, region_input, backup_id, volume_id=None):
        """Restore a backup to an EVS volume."""
        from .config import Config

        config = Config()
        project_id = config.get_project_id(region_input)
        endpoint = get_endpoint(region_input, "cbr")
        url = f"{endpoint}/v3/{project_id}/backups/{backup_id}/restore"

        restore_body = {"restore": {"power_on": False}}
        if volume_id:
            restore_body["restore"]["volume_id"] = volume_id
        body_str = json.dumps(restore_body)

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("POST", url, headers, body_str)
        resp = requests.post(url, headers=headers, data=body_str, timeout=30, verify=False)
        resp.raise_for_status()

        return resp.status_code == 202

    def replicate_backup(
        self,
        source_region,
        backup_id,
        target_region,
        target_vault_id=None,
        name=None,
        description=None,
    ):
        """Replicate a backup to another region."""
        from .config import Config
        from .regions import get_region_config

        config = Config()
        source_project_id = config.get_project_id(source_region)
        target_project_id = config.get_project_id(target_region)
        target_region_id = get_region_config(target_region)["id"]

        if not target_vault_id:
            target_vault_id = config.get_vault_id(target_region)

        endpoint = get_endpoint(source_region, "cbr")
        url = f"{endpoint}/v3/{source_project_id}/backups/{backup_id}/replicate"

        replicate_body = {
            "replicate": {
                "destination_project_id": target_project_id,
                "destination_region": target_region_id,
                "destination_vault_id": target_vault_id,
            }
        }
        if name:
            replicate_body["replicate"]["name"] = name
        if description:
            replicate_body["replicate"]["description"] = description
        body_str = json.dumps(replicate_body)

        headers = {"Content-Type": "application/json"}
        headers = self.auth.sign_request("POST", url, headers, body_str)
        resp = requests.post(url, headers=headers, data=body_str, timeout=30, verify=False)
        resp.raise_for_status()

        return resp.json().get("replication", {})

    def get_replication_status(self, region_input, backup_id):
        """Check the replication status of a backup."""
        backup = self.get_backup(region_input, backup_id)
        return backup.get("replication_records", [])
