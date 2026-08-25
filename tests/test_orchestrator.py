"""Tests for the orchestrator handler and job model."""

import pytest
import sys
import os
import json
import base64

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.functions.orchestrator.job_model import (
    create_job,
    update_step,
    mark_failed,
    is_active,
    is_terminal,
    STEP_REPLICATING,
    STEP_RESTORING,
    STEP_CREATING_IMAGE,
    STEP_EXPORTING,
    STEP_COMPLETED,
    STEP_FAILED,
)


class TestCreateJob:
    def test_same_region_job(self):
        job = create_job(
            backup_id="backup-1",
            backup_name="test-backup",
            source_region="buenosaires",
            target_region="buenosaires",
        )
        assert job["backup_id"] == "backup-1"
        assert job["cross_region"] is False
        assert job["step"] == STEP_RESTORING
        assert job["job_id"] is not None

    def test_cross_region_job(self):
        job = create_job(
            backup_id="backup-1",
            backup_name="test-backup",
            source_region="buenosaires",
            target_region="santiago",
        )
        assert job["cross_region"] is True
        assert job["step"] == STEP_REPLICATING

    def test_job_has_timestamps(self):
        job = create_job(
            backup_id="backup-1",
            backup_name="test",
            source_region="buenosaires",
            target_region="buenosaires",
        )
        assert job["created_at"] is not None
        assert job["updated_at"] is not None


class TestUpdateStep:
    def test_update_step(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        update_step(job, STEP_CREATING_IMAGE, image_id="img-1")

        assert job["step"] == STEP_CREATING_IMAGE
        assert job["image_id"] == "img-1"

    def test_update_step_preserves_other_fields(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        original_created = job["created_at"]
        update_step(job, STEP_RESTORING, volume_id="vol-1")

        assert job["created_at"] == original_created
        assert job["volume_id"] == "vol-1"


class TestMarkFailed:
    def test_mark_failed(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        mark_failed(job, "Something went wrong")

        assert job["step"] == STEP_FAILED
        assert job["error"] == "Something went wrong"


class TestJobStatus:
    def test_is_active(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        assert is_active(job) is True

    def test_is_active_completed(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        update_step(job, STEP_COMPLETED)
        assert is_active(job) is False

    def test_is_terminal_completed(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        update_step(job, STEP_COMPLETED)
        assert is_terminal(job) is True

    def test_is_terminal_failed(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        mark_failed(job, "error")
        assert is_terminal(job) is True

    def test_is_terminal_active(self):
        job = create_job("b1", "test", "buenosaires", "buenosaires")
        assert is_terminal(job) is False
