"""Tests for the status checker handler."""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.functions.orchestrator.job_model import (
    create_job,
    update_step,
    STEP_RESTORING,
    STEP_CREATING_IMAGE,
    STEP_EXPORTING,
    STEP_COMPLETED,
)
from src.functions.status_checker.handler import (
    _handle_restoring,
    _handle_creating_image,
    _handle_exporting,
)


class TestHandleRestoring:
    @patch.dict(os.environ, {
        "HW_ACCESS_KEY": "test-ak",
        "HW_SECRET_KEY": "test-sk",
        "HW_PROJECT_ID_BUENOSAIRES": "test-pid-ba",
        "HW_PROJECT_ID_SANTIAGO": "test-pid-cl",
        "TEMP_VOLUME_TYPE": "SATA",
        "TEMP_AZ_BUENOSAIRES": "sa-argentina-1a",
    })
    def test_volume_available_advances_to_creating_image(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        job["volume_id"] = "vol-1"

        mock_evs = MagicMock()
        mock_evs.get_volume_status.return_value = "available"

        mock_ims = MagicMock()
        mock_ims.create_image_from_volume.return_value = "ims-job-1"
        clients = {"evs": mock_evs, "ims": mock_ims, "ecs": MagicMock(), "cbr": MagicMock(), "obs": MagicMock()}
        config = MagicMock()
        config.temp_volume_type = "GPSSD"

        result = _handle_restoring(job, clients, config)

        assert result == "advanced"
        assert job["step"] == STEP_CREATING_IMAGE
        assert job["image_job_id"] == "ims-job-1"

    def test_volume_creating_stays_pending(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        job["volume_id"] = "vol-1"

        mock_evs = MagicMock()
        mock_evs.get_volume_status.return_value = "creating"

        clients = {"evs": mock_evs, "ims": MagicMock(), "cbr": MagicMock(), "obs": MagicMock()}
        config = MagicMock()

        result = _handle_restoring(job, clients, config)

        assert result == "pending"
        assert job["step"] == STEP_RESTORING


class TestHandleCreatingImage:
    def test_image_active_advances_to_exporting(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        update_step(job, STEP_CREATING_IMAGE, image_id="img-1")
        job["bucket_name"] = "cbr-evs-buenosaires"
        job["object_key"] = "backups/b1/test.vhd"

        mock_ims = MagicMock()
        mock_ims.get_image_status.return_value = "active"
        mock_ims.export_image_to_obs.return_value = "export-job-1"

        clients = {"evs": MagicMock(), "ims": mock_ims, "cbr": MagicMock(), "obs": MagicMock()}
        config = MagicMock()

        result = _handle_creating_image(job, clients, config)

        assert result == "advanced"
        assert job["step"] == STEP_EXPORTING
        assert job["export_job_id"] == "export-job-1"

    def test_image_queued_stays_pending(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        update_step(job, STEP_CREATING_IMAGE, image_id="img-1")

        mock_ims = MagicMock()
        mock_ims.get_image_status.return_value = "queued"

        clients = {"evs": MagicMock(), "ims": mock_ims, "cbr": MagicMock(), "obs": MagicMock()}
        config = MagicMock()

        result = _handle_creating_image(job, clients, config)

        assert result == "pending"


class TestHandleExporting:
    def test_export_success_completes(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        update_step(job, STEP_EXPORTING, export_job_id="job-1")

        mock_ims = MagicMock()
        mock_ims.get_job_status.return_value = {"status": "SUCCESS"}

        clients = {"evs": MagicMock(), "ims": mock_ims, "cbr": MagicMock(), "obs": MagicMock()}
        config = MagicMock()
        config.cleanup_after_export = False

        result = _handle_exporting(job, clients, config)

        assert result == "completed"
        assert job["step"] == STEP_COMPLETED

    def test_export_in_progress_stays_pending(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        update_step(job, STEP_EXPORTING, export_job_id="job-1")

        mock_ims = MagicMock()
        mock_ims.get_job_status.return_value = {"status": "RUNNING"}

        clients = {"evs": MagicMock(), "ims": mock_ims, "cbr": MagicMock(), "obs": MagicMock()}
        config = MagicMock()

        result = _handle_exporting(job, clients, config)

        assert result == "pending"
