"""Stdlib tests for scene timeline validation and safe persistence."""

import concurrent.futures
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock


SERVER_PATH = Path(__file__).with_name("server.py")
SPEC = importlib.util.spec_from_file_location("video_workbench_server", SERVER_PATH)
SERVER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
ORIGINAL_ARGV = sys.argv
try:
    sys.argv = [str(SERVER_PATH), "video-1"]
    SPEC.loader.exec_module(SERVER)
finally:
    sys.argv = ORIGINAL_ARGV

CONTRACTS = sys.modules["contracts"]
TEST_CATALOG = [
    {"id": "BrandProof", "durationInFrames": 300, "fps": 60, "width": 1920, "height": 1080},
    {"id": "BigStatement", "durationInFrames": 300, "fps": 60, "width": 1920, "height": 1080},
]


def payload(*shots):
    return {
        "master": "videos/video-1/output/master-tight.mp4",
        "master_fps": 60,
        "remotion_out": "remotion/out",
        "shots": list(shots),
        "preview": {
            "end_s": 20,
            "out": "videos/video-1/output/preview.mp4",
            "width": 1920,
            "height": 1080,
            "fps": 30,
        },
    }


class TimelineTests(unittest.TestCase):
    def setUp(self):
        # Unit tests must work in a clean clone before `npm run gen` creates the
        # ignored Remotion registry.
        catalog = mock.patch.object(SERVER, "remotion_catalog", return_value=TEST_CATALOG)
        catalog.start()
        self.addCleanup(catalog.stop)

    def test_remotion_and_disabled_hyperframe_scene_are_valid(self):
        timeline, errors, warnings = SERVER.normalize_timeline(payload(
            {
                "id": "BrandProof", "engine": "remotion", "type": "cutaway",
                "master_in_s": 1, "master_out_s": 6, "enabled": True,
            },
            {
                "id": "hf-draft", "engine": "hyperframe", "type": "overlay",
                "master_in_s": 8, "master_out_s": 12, "enabled": False,
            },
        ))
        self.assertEqual(errors, [])
        self.assertEqual(warnings, ["scene is disabled and will be skipped"])
        self.assertEqual(timeline["shots"][0]["id"], "BrandProof")
        self.assertEqual(timeline["shots"][1]["id"], timeline["shots"][1]["scene_uid"])
        self.assertEqual(timeline["shots"][0]["composition_id"], "BrandProof")

    def test_legacy_scene_uid_is_stable_after_first_normalization(self):
        first, errors, _ = SERVER.normalize_timeline(payload({
            "id": "BrandProof", "type": "cutaway",
            "master_in_s": 1, "master_out_s": 6, "enabled": True,
        }))
        self.assertEqual(errors, [])
        uid = first["shots"][0]["scene_uid"]
        second, errors, _ = SERVER.normalize_timeline(first)
        self.assertEqual(errors, [])
        self.assertEqual(second["shots"][0]["scene_uid"], uid)
        self.assertEqual(second["shots"][0]["id"], "BrandProof")

    def test_exact_xfade_claims_same_lane_overlap(self):
        timeline, errors, _ = SERVER.normalize_timeline(payload(
            {
                "id": "BrandProof", "engine": "remotion", "type": "cutaway",
                "master_in_s": 1, "master_out_s": 6, "enabled": True,
            },
            {
                "id": "BigStatement", "engine": "remotion", "type": "cutaway",
                "master_in_s": 5, "master_out_s": 9, "enabled": True,
                "transition_in": {"kind": "xfade", "frames": 60},
            },
        ))
        self.assertIsNotNone(timeline)
        self.assertFalse(any("overlapping active cutaway" in error for error in errors))

    def test_outside_legacy_asset_is_reported_and_never_rewritten(self):
        timeline = SERVER.migrate_timeline(payload({
            "id": "external", "engine": "media", "type": "cutaway",
            "master_in_s": 1, "master_out_s": 6, "enabled": True,
            "asset": "C:/outside/clip.mp4",
        }), set(), SERVER.ROOT, SERVER.PROJECT)
        issues = SERVER.validate_timeline(timeline, {}, SERVER.ROOT, SERVER.PROJECT)
        self.assertTrue(any(item["code"] == "E_ASSET_OUTSIDE_PROJECT" for item in issues))
        shot = timeline["shots"][0]
        self.assertEqual(shot["takes"][0]["file"], "C:/outside/clip.mp4")

    def test_legacy_asset_hash_is_cached_until_file_changes(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-hash-") as folder:
            project = Path(folder)
            asset = project / "work" / "legacy.mp4"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"first asset")
            document = payload({
                "id": "legacy", "engine": "media", "type": "cutaway",
                "master_in_s": 1, "master_out_s": 6, "enabled": True,
                "asset": "work/legacy.mp4",
            })
            with CONTRACTS._SHA256_CACHE_LOCK:
                CONTRACTS._SHA256_CACHE.clear()
            self.addCleanup(CONTRACTS._SHA256_CACHE.clear)
            with mock.patch.object(CONTRACTS, "sha256_file", wraps=CONTRACTS.sha256_file) as hasher:
                CONTRACTS.migrate_timeline(document, set(), project, project)
                CONTRACTS.migrate_timeline(document, set(), project, project)
                self.assertEqual(hasher.call_count, 1)

                asset.write_bytes(b"second asset with a new size")
                CONTRACTS.migrate_timeline(document, set(), project, project)
                self.assertEqual(hasher.call_count, 2)

    def test_nonlegacy_take_must_use_canonical_generated_namespace(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-takes-") as folder:
            project = Path(folder)
            scene_uid = "scn_ABCDEFGH"
            take_uid = "take_ABCDEFGH"
            canonical = project / "work" / "generated" / scene_uid / take_uid / "asset.mp4"
            canonical.parent.mkdir(parents=True)
            canonical.write_bytes(b"canonical")

            scene = {
                "scene_uid": scene_uid, "id": scene_uid, "engine": "media", "type": "cutaway",
                "master_in_s": 1, "master_out_s": 6, "enabled": True, "status": "draft",
                "fit": "hold", "z": 0, "transition_in": {"kind": "cut"},
                "active_take_uid": take_uid,
                "takes": [{
                    "take_uid": take_uid,
                    "file": f"work/generated/{scene_uid}/{take_uid}/asset.mp4",
                    "created_at": SERVER.utc_now(),
                    "sha256": "0" * 64,
                    "conform_profile": "cutaway_h264",
                    "provenance": {"provider": "human"},
                }],
            }
            document = payload(scene)
            issues = SERVER.validate_timeline(document, {}, project, project)
            self.assertFalse(any(item["code"] == "E_ASSET_NONCANONICAL" for item in issues))

            noncanonical = project / "work" / "elsewhere" / "asset.mp4"
            noncanonical.parent.mkdir(parents=True)
            noncanonical.write_bytes(b"wrong namespace")
            scene["takes"][0]["file"] = "work/elsewhere/asset.mp4"
            issues = SERVER.validate_timeline(document, {}, project, project)
            self.assertTrue(any(item["code"] == "E_ASSET_NONCANONICAL" for item in issues))

            legacy = CONTRACTS.migrate_timeline(payload({
                "id": "legacy", "engine": "media", "type": "cutaway",
                "master_in_s": 1, "master_out_s": 6, "enabled": True,
                "asset": "work/elsewhere/asset.mp4",
            }), set(), project, project)
            issues = SERVER.validate_timeline(legacy, {}, project, project)
            self.assertFalse(any(item["code"] == "E_ASSET_NONCANONICAL" for item in issues))
            self.assertTrue(any(item["code"] == "E_ASSET_UNCONFORMED" for item in issues))

    def test_two_placements_can_share_one_composition(self):
        timeline, errors, _ = SERVER.normalize_timeline(payload(
            {
                "id": "BrandProof", "engine": "remotion", "type": "cutaway",
                "master_in_s": 1, "master_out_s": 3, "enabled": True,
            },
            {
                "id": "BrandProof", "engine": "remotion", "type": "cutaway",
                "master_in_s": 4, "master_out_s": 6, "enabled": True,
            },
        ))
        self.assertEqual(errors, [])
        self.assertNotEqual(timeline["shots"][0]["scene_uid"], timeline["shots"][1]["scene_uid"])
        self.assertEqual({shot["composition_id"] for shot in timeline["shots"]}, {"BrandProof"})

    def test_active_same_layer_overlap_is_rejected(self):
        _, errors, _ = SERVER.normalize_timeline(payload(
            {
                "id": "BrandProof", "engine": "remotion", "type": "cutaway",
                "master_in_s": 1, "master_out_s": 6, "enabled": True,
            },
            {
                "id": "BigStatement", "engine": "remotion", "type": "cutaway",
                "master_in_s": 4, "master_out_s": 9, "enabled": True,
            },
        ))
        self.assertTrue(any("overlapping active cutaway" in error for error in errors))

    def test_second_write_creates_loadable_backup(self):
        timeline, errors, _ = SERVER.normalize_timeline(payload())
        self.assertEqual(errors, [])
        with tempfile.TemporaryDirectory(prefix="video-workbench-test-") as folder:
            path = Path(folder) / "work" / "timeline.json"
            first = SERVER.backup_and_write(path, timeline, "backups", "timeline")
            self.assertIsNone(first)
            timeline["preview"]["end_s"] = 10
            second = SERVER.backup_and_write(path, timeline, "backups", "timeline")
            self.assertIsNotNone(second)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["preview"]["end_s"], 10)
            backups = list((path.parent / "backups").glob("timeline-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8"))["preview"]["end_s"], 20)

    def test_timeline_etag_changes_only_after_atomic_write(self):
        timeline, errors, _ = SERVER.normalize_timeline(payload())
        self.assertEqual(errors, [])
        with tempfile.TemporaryDirectory(prefix="video-workbench-etag-") as folder:
            path = Path(folder) / "work" / "timeline.json"
            project = Path(folder)
            with mock.patch.object(SERVER, "TIMELINE", path), mock.patch.object(SERVER, "PROJECT", project):
                before = SERVER.timeline_etag()
                SERVER.backup_and_write(path, timeline, "backups", "timeline")
                after = SERVER.timeline_etag()
                self.assertNotEqual(before, after)
                self.assertFalse(list(path.parent.glob(".timeline.json.*.tmp")))

    def test_restart_reconciliation_marks_dead_worker_orphaned(self):
        now = SERVER.utc_now()
        record = {
            "job_id": SERVER.new_uid("job"),
            "kind": "render",
            "state": "running",
            "pid": 2_147_483_000,
            "start_token": "2147483000:old",
            "created_at": now,
            "updated_at": now,
        }
        with tempfile.TemporaryDirectory(prefix="video-workbench-jobs-") as folder:
            jobs_dir = Path(folder) / "jobs"
            with mock.patch.object(SERVER, "JOBS_DIR", jobs_dir):
                SERVER.write_job(record)
                jobs = SERVER.reconcile_jobs()
                self.assertEqual(jobs[0]["state"], "orphaned")
                self.assertIsNotNone(jobs[0]["completed_at"])

    def test_process_start_token_identifies_current_worker(self):
        token = SERVER.process_start_token(os.getpid())
        self.assertTrue(token and token.startswith(f"{os.getpid()}:") )

    def test_http_timeline_save_requires_current_etag(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-http-") as folder:
            project = Path(folder)
            timeline_path = project / "work" / "timeline.json"
            manifest = project / "work" / "editor" / "manifest.json"
            proxy = project / "work" / "editor" / "proxy.mp4"
            patches = (
                mock.patch.object(SERVER, "PROJECT", project),
                mock.patch.object(SERVER, "TIMELINE", timeline_path),
                mock.patch.object(SERVER, "EDITOR_MANIFEST", manifest),
                mock.patch.object(SERVER, "PROXY", proxy),
            )
            for patcher in patches:
                patcher.start()
            server = ThreadingHTTPServer(("127.0.0.1", 0), SERVER.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                health = urllib.request.urlopen(base + "/api/health", timeout=5)
                try:
                    self.assertEqual(json.load(health)["service"], "video-workbench")
                finally:
                    health.close()
                with self.assertRaises(urllib.error.HTTPError) as unauthorized:
                    urllib.request.urlopen(base + "/api/scenes", timeout=5)
                unauthorized.exception.close()
                load_request = urllib.request.Request(
                    base + "/api/scenes", headers={"X-Workbench-Token": SERVER.SESSION_TOKEN}
                )
                with urllib.request.urlopen(load_request, timeout=5) as response:
                    loaded = json.load(response)
                etag = loaded["etag"]
                body = json.dumps({"timeline": loaded["timeline"]}).encode("utf-8")
                request = urllib.request.Request(
                    base + "/api/timeline", data=body, method="PUT",
                    headers={
                        "Content-Type": "application/json", "If-Match": etag,
                        "X-Workbench-Token": SERVER.SESSION_TOKEN,
                    },
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    saved = json.load(response)
                self.assertTrue(saved["saved"])
                self.assertNotEqual(saved["etag"], etag)

                stale = urllib.request.Request(
                    base + "/api/timeline", data=body, method="PUT",
                    headers={
                        "Content-Type": "application/json", "If-Match": etag,
                        "X-Workbench-Token": SERVER.SESSION_TOKEN,
                    },
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(stale, timeout=5)
                self.assertEqual(raised.exception.code, 409)
                try:
                    conflict = json.loads(raised.exception.read())
                finally:
                    raised.exception.close()
                self.assertEqual(conflict["code"], "E_ETAG_MISMATCH")
                self.assertIn("server_doc", conflict)
            finally:
                server.shutdown()
                server.server_close()
                for patcher in reversed(patches):
                    patcher.stop()

    def test_concurrent_saves_with_one_etag_allow_exactly_one_winner(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-race-") as folder:
            project = Path(folder)
            timeline_path = project / "work" / "timeline.json"
            patches = (
                mock.patch.object(SERVER, "PROJECT", project),
                mock.patch.object(SERVER, "TIMELINE", timeline_path),
                mock.patch.object(SERVER, "EDITOR_MANIFEST", project / "work" / "editor" / "manifest.json"),
                mock.patch.object(SERVER, "PROXY", project / "work" / "editor" / "proxy.mp4"),
            )
            for patcher in patches:
                patcher.start()
            server = ThreadingHTTPServer(("127.0.0.1", 0), SERVER.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                load_request = urllib.request.Request(
                    base + "/api/scenes", headers={"X-Workbench-Token": SERVER.SESSION_TOKEN}
                )
                with urllib.request.urlopen(load_request, timeout=5) as response:
                    loaded = json.load(response)

                first = json.loads(json.dumps(loaded["timeline"]))
                second = json.loads(json.dumps(loaded["timeline"]))
                first["preview"]["end_s"] = 11
                second["preview"]["end_s"] = 12
                barrier = threading.Barrier(2)
                original_normalize = SERVER.normalize_timeline

                def synchronized_normalize(value):
                    result = original_normalize(value)
                    barrier.wait(timeout=5)
                    return result

                def put(document):
                    request = urllib.request.Request(
                        base + "/api/timeline",
                        data=json.dumps({"timeline": document}).encode("utf-8"),
                        method="PUT",
                        headers={
                            "Content-Type": "application/json",
                            "If-Match": loaded["etag"],
                            "X-Workbench-Token": SERVER.SESSION_TOKEN,
                        },
                    )
                    try:
                        with urllib.request.urlopen(request, timeout=10) as response:
                            return response.status, json.load(response)
                    except urllib.error.HTTPError as error:
                        try:
                            return error.code, json.loads(error.read())
                        finally:
                            error.close()

                with mock.patch.object(SERVER, "normalize_timeline", side_effect=synchronized_normalize):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                        results = list(executor.map(put, (first, second)))

                self.assertEqual(sorted(status for status, _ in results), [200, 409])
                winner = next(body for status, body in results if status == 200)
                conflict = next(body for status, body in results if status == 409)
                persisted = json.loads(timeline_path.read_text(encoding="utf-8"))
                self.assertEqual(conflict["code"], "E_ETAG_MISMATCH")
                self.assertEqual(persisted["preview"]["end_s"], winner["timeline"]["preview"]["end_s"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                for patcher in reversed(patches):
                    patcher.stop()

    def test_bake_check_uses_the_shared_validator(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-bake-") as folder:
            project = Path(folder)
            path = project / "work" / "timeline.json"
            path.parent.mkdir(parents=True)
            document = payload({
                "id": "BrandProof", "engine": "remotion", "type": "cutaway",
                "master_in_s": 1, "master_out_s": 6, "enabled": False,
            })
            path.write_text(json.dumps(document), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SERVER.ROOT / "tools" / "bake.py"), str(path), "--check"],
                cwd=SERVER.ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["issues"][0]["code"], "W_SCENE_DISABLED")


if __name__ == "__main__":
    unittest.main()
