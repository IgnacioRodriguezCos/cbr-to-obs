"""Tests for CBR client module."""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.shared.cbr_client import CBRClient


class TestCBRClient:
    @patch("src.shared.cbr_client.requests")
    @patch.dict(os.environ, {
        "HW_ACCESS_KEY": "test-ak",
        "HW_SECRET_KEY": "test-sk",
        "HW_PROJECT_ID_BUENOSAIRES": "test-pid-ba",
        "HW_PROJECT_ID_SANTIAGO": "test-pid-cl",
    })
    def test_list_evs_backups(self, mock_requests):
        mock_auth = MagicMock()
        mock_auth.get_auth_headers.return_value = {"X-Auth-Token": "token"}

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "backups": [
                {"id": "backup-1", "name": "test-backup", "resource_type": "OS::Cinder::Volume"},
                {"id": "backup-2", "name": "test-backup-2", "resource_type": "OS::Cinder::Volume"},
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_response

        client = CBRClient(mock_auth)
        backups = client.list_evs_backups("buenosaires")

        assert len(backups) == 2
        assert backups[0]["id"] == "backup-1"
        mock_requests.get.assert_called_once()

    @patch("src.shared.cbr_client.requests")
    @patch.dict(os.environ, {
        "HW_ACCESS_KEY": "test-ak",
        "HW_SECRET_KEY": "test-sk",
        "HW_PROJECT_ID_BUENOSAIRES": "test-pid-ba",
        "HW_PROJECT_ID_SANTIAGO": "test-pid-cl",
    })
    def test_get_backup(self, mock_requests):
        mock_auth = MagicMock()
        mock_auth.get_auth_headers.return_value = {"X-Auth-Token": "token"}

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "backup": {"id": "backup-1", "name": "test", "status": "available"}
        }
        mock_response.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_response

        client = CBRClient(mock_auth)
        backup = client.get_backup("buenosaires", "backup-1")

        assert backup["id"] == "backup-1"
        assert backup["status"] == "available"

    @patch("src.shared.cbr_client.requests")
    @patch.dict(os.environ, {
        "HW_ACCESS_KEY": "test-ak",
        "HW_SECRET_KEY": "test-sk",
        "HW_PROJECT_ID_BUENOSAIRES": "test-pid-ba",
        "HW_PROJECT_ID_SANTIAGO": "test-pid-cl",
    })
    def test_restore_to_volume(self, mock_requests):
        mock_auth = MagicMock()
        mock_auth.get_auth_headers.return_value = {"X-Auth-Token": "token"}

        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.raise_for_status = MagicMock()
        mock_requests.post.return_value = mock_response

        client = CBRClient(mock_auth)
        result = client.restore_to_volume("buenosaires", "backup-1")

        assert result is True
