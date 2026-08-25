"""Configuration management for CBR-to-OBS migration.

Reads configuration from environment variables (for FunctionGraph)
or from a .env file (for local development).
"""

import json
import os


class Config:
    """Configuration container loaded from environment variables."""

    def __init__(self):
        self.access_key = os.environ.get("HW_ACCESS_KEY", "")
        self.secret_key = os.environ.get("HW_SECRET_KEY", "")
        self.project_id_buenosaires = os.environ.get("HW_PROJECT_ID_BUENOSAIRES", "")
        self.project_id_santiago = os.environ.get("HW_PROJECT_ID_SANTIAGO", "")
        self.vault_id_buenosaires = os.environ.get("HW_VAULT_ID_BUENOSAIRES", "")
        self.vault_id_santiago = os.environ.get("HW_VAULT_ID_SANTIAGO", "")
        self.state_bucket = os.environ.get("OBS_STATE_BUCKET", "cbr-migration-state")
        self.state_region = os.environ.get("OBS_STATE_REGION", "sa-argentina-1")
        self.temp_volume_type = os.environ.get("TEMP_VOLUME_TYPE", "SATA")
        self.temp_volume_size_gb = int(os.environ.get("TEMP_VOLUME_SIZE_GB", "0"))
        self.temp_az_buenosaires = os.environ.get("TEMP_AZ_BUENOSAIRES", "sa-argentina-1a")
        self.temp_az_santiago = os.environ.get("TEMP_AZ_SANTIAGO", "la-south-2a")
        self.cleanup_after_export = os.environ.get("CLEANUP_AFTER_EXPORT", "true").lower() == "true"
        self.max_retries = int(os.environ.get("MAX_RETRIES", "5"))

    def get_project_id(self, region_input):
        """Get project ID for a region.

        Args:
            region_input: Region alias or ID.

        Returns:
            Project ID string.

        Raises:
            ValueError: If project ID is not configured for the region.
        """
        from .regions import get_region_config

        config = get_region_config(region_input)
        if config["id"] == "sa-argentina-1":
            pid = self.project_id_buenosaires
        else:
            pid = self.project_id_santiago
        if not pid:
            raise ValueError(f"Project ID not configured for region {config['name']}")
        return pid

    def get_vault_id(self, region_input):
        """Get CBR vault ID for a region (needed for cross-region replication).

        Args:
            region_input: Region alias or ID.

        Returns:
            Vault ID string.

        Raises:
            ValueError: If vault ID is not configured for the region.
        """
        from .regions import get_region_config

        config = get_region_config(region_input)
        if config["id"] == "sa-argentina-1":
            vid = self.vault_id_buenosaires
        else:
            vid = self.vault_id_santiago
        if not vid:
            raise ValueError(f"Vault ID not configured for region {config['name']}")
        return vid

    def get_temp_az(self, region_input):
        """Get availability zone for temporary resources in a region.

        Args:
            region_input: Region alias or ID.

        Returns:
            Availability zone string.
        """
        from .regions import get_region_config

        config = get_region_config(region_input)
        if config["id"] == "sa-argentina-1":
            return self.temp_az_buenosaires
        return self.temp_az_santiago

    def validate(self):
        """Validate that required configuration is present.

        Raises:
            ValueError: If required configuration is missing.
        """
        errors = []
        if not self.access_key:
            errors.append("HW_ACCESS_KEY is required")
        if not self.secret_key:
            errors.append("HW_SECRET_KEY is required")
        if not self.project_id_buenosaires:
            errors.append("HW_PROJECT_ID_BUENOSAIRES is required")
        if not self.project_id_santiago:
            errors.append("HW_PROJECT_ID_SANTIAGO is required")
        if errors:
            raise ValueError("Configuration errors:\n  " + "\n  ".join(errors))


def load_config():
    """Load configuration from environment variables.

    Returns:
        Config instance.
    """
    return Config()
