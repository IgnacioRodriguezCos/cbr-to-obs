"""Tests for the raw export path (volumes > IMS 1TB limit)."""

import unittest
from types import SimpleNamespace

from src.functions.orchestrator.job_model import (
    STEP_ATTACHING_ECS,
    STEP_UPLOADING_RAW,
    STEP_CLEANUP_PENDING,
    STEP_COMPLETED,
    create_job,
)
from src.functions.status_checker import handler as sc
from src.shared.raw_export import build_user_data, generate_password, marker_key


def make_config(**overrides):
    defaults = dict(
        access_key="AK",
        secret_key="SK",
        obsutil_url="https://example.com/obsutil.tar.gz",
        raw_part_mb=600,
        raw_concurrency=3,
        cleanup_after_export=True,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestRawExportHelpers(unittest.TestCase):
    def test_marker_key(self):
        self.assertEqual(marker_key("backups/b1/x.raw"), "backups/b1/x.raw.SUCCESS")

    def test_generate_password_length(self):
        pwd = generate_password()
        self.assertEqual(len(pwd), 16)

    def test_build_user_data_contains_essentials(self):
        job = {
            "bucket_name": "cbr-evs-buenosaires",
            "object_key": "backups/b1/disk.raw",
            "source_region": "buenosaires",
            "target_region": "buenosaires",
            "cross_region": False,
            "temp_device": "/dev/vdb",
        }
        script = build_user_data(job, make_config())
        self.assertIn("/dev/vdb", script)
        self.assertIn("obs://cbr-evs-buenosaires/backups/b1/disk.raw", script)
        self.assertIn("backups/b1/disk.raw.SUCCESS", script)
        self.assertIn("dd if=$DEVICE", script)
        self.assertIn("-ps=600MB", script)
        self.assertIn("-p=3", script)


class TestJobModelRawFields(unittest.TestCase):
    def test_create_job_has_path_and_temp_fields(self):
        job = create_job("b1", "test", "buenosaires", "santiago", resource_size_gb=5120)
        self.assertEqual(job["path"], "ims")
        self.assertIsNone(job["temp_server_id"])
        self.assertEqual(job["temp_device"], "/dev/vdb")

    def test_new_steps_are_active(self):
        job = create_job("b1", "t", "buenosaires", "buenosaires")
        from src.functions.orchestrator.job_model import is_active

        job["step"] = STEP_UPLOADING_RAW
        self.assertTrue(is_active(job))


class TestHandleRestoringRawBranch(unittest.TestCase):
    def test_volume_available_creates_ecs_and_advances(self):
        job = create_job("b1", "disk", "buenosaires", "buenosaires", resource_size_gb=5120)
        job.update({"path": "raw", "volume_id": "vol-123", "bucket_name": "bkt", "object_key": "k.raw"})

        evs = SimpleNamespace(get_volume_status=lambda r, v: "available")
        ecs = SimpleNamespace(create_server=lambda *a, **kw: "srv-999")

        clients = {"evs": evs, "ecs": ecs}
        result = sc._handle_restoring(job, clients, make_config())

        self.assertEqual(result, "advanced")
        self.assertEqual(job["step"], STEP_ATTACHING_ECS)
        self.assertEqual(job["temp_server_id"], "srv-999")


class TestHandleAttachingEcs(unittest.TestCase):
    def _job(self):
        job = create_job("b1", "disk", "buenosaires", "buenosaires")
        job.update({
            "path": "raw",
            "volume_id": "vol-123",
            "temp_server_id": "srv-1",
            "step": STEP_ATTACHING_ECS,
        })
        return job

    def test_building_waits(self):
        ecs = SimpleNamespace(
            get_server=lambda r, s: {"status": "BUILD"},
            get_attachment=lambda r, s, v: None,
            attach_volume=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not attach")),
        )
        result = sc._handle_attaching_ecs(self._job(), {"ecs": ecs}, make_config())
        self.assertEqual(result, "pending")

    def test_active_then_attach_requested(self):
        state = {}

        def fake_attach(region, sid, vid, device="/dev/vdb"):
            state["attached"] = True
            return {}

        ecs = SimpleNamespace(
            get_server=lambda r, s: {"status": "ACTIVE"},
            get_attachment=lambda r, s, v: None,
            attach_volume=fake_attach,
        )
        job = self._job()
        result = sc._handle_attaching_ecs(job, {"ecs": ecs}, make_config())

        self.assertTrue(state.get("attached"))
        self.assertTrue(job.get("attach_requested"))
        self.assertEqual(result, "pending")

    def test_attachment_present_advances(self):
        ecs = SimpleNamespace(
            get_server=lambda r, s: {"status": "ACTIVE"},
            get_attachment=lambda r, s, v: {"volumeId": v},
            attach_volume=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("double attach")),
        )
        job = self._job()
        result = sc._handle_attaching_ecs(job, {"ecs": ecs}, make_config())

        self.assertEqual(result, "advanced")
        self.assertEqual(job["step"], STEP_UPLOADING_RAW)

    def test_error_state_fails(self):
        ecs = SimpleNamespace(
            get_server=lambda r, s: {"status": "ERROR"},
            get_attachment=lambda r, s, v: None,
            attach_volume=lambda *a, **kw: None,
        )
        job = self._job()
        result = sc._handle_attaching_ecs(job, {"ecs": ecs}, make_config())
        self.assertEqual(result, "failed")


class TestHandleUploadingRaw(unittest.TestCase):
    def _job(self):
        job = create_job("b1", "disk", "buenosaires", "buenosaires")
        job.update({"path": "raw", "bucket_name": "bkt", "object_key": "k.raw"})
        job["step"] = STEP_UPLOADING_RAW
        return job

    def test_marker_found_goes_to_cleanup(self):
        obs = SimpleNamespace(object_exists=lambda r, b, k: k == "k.raw.SUCCESS")
        job = self._job()
        result = sc._handle_uploading_raw(job, {"obs": obs}, make_config(cleanup_after_export=True))

        self.assertEqual(result, "advanced")
        self.assertEqual(job["step"], STEP_CLEANUP_PENDING)

    def test_marker_found_no_cleanup_completes(self):
        obs = SimpleNamespace(object_exists=lambda r, b, k: True)
        job = self._job()
        result = sc._handle_uploading_raw(job, {"obs": obs}, make_config(cleanup_after_export=False))
        self.assertEqual(result, "completed")

    def test_no_marker_pends(self):
        obs = SimpleNamespace(object_exists=lambda r, b, k: False)
        result = sc._handle_uploading_raw(self._job(), {"obs": obs}, make_config())
        self.assertEqual(result, "pending")


if __name__ == "__main__":
    unittest.main()
