"""Stdlib tests for scene timeline validation and safe persistence."""

import concurrent.futures
import importlib.util
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
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
BAKE_PATH = SERVER.ROOT / "tools" / "bake.py"
BAKE_SPEC = importlib.util.spec_from_file_location("video_workbench_bake", BAKE_PATH)
BAKE = importlib.util.module_from_spec(BAKE_SPEC)
assert BAKE_SPEC and BAKE_SPEC.loader
BAKE_SPEC.loader.exec_module(BAKE)
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


def multipart_body(filename: str, content: bytes, note: str = "", class_hint: str = "cutaway"):
    boundary = "----workbench-test-boundary"
    chunks = []
    for name, value in (("note", note), ("class_hint", class_hint)):
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    chunks.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        "Content-Type: video/mp4\r\n\r\n".encode()
    )
    chunks.extend((content, f"\r\n--{boundary}--\r\n".encode()))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


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

    def test_legacy_scene_uid_is_stable_across_repeated_raw_loads(self):
        legacy = payload({
            "id": "BrandProof", "type": "cutaway",
            "master_in_s": 1, "master_out_s": 6, "enabled": True,
        })
        first, errors, _ = SERVER.normalize_timeline(legacy)
        self.assertEqual(errors, [])
        uid = first["shots"][0]["scene_uid"]
        second, errors, _ = SERVER.normalize_timeline(legacy)
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

    def test_take_history_is_immutable_and_server_owned(self):
        first = {"take_uid": "take_ABCDEFGH", "sha256": "a" * 64}
        second = {"take_uid": "take_IJKLMNOP", "sha256": "b" * 64}
        appended = {"take_uid": "take_QRSTUVWX", "sha256": "c" * 64}
        current = {"shots": [{"scene_uid": "scn_ABCDEFGH", "takes": [first, second]}]}
        reordered = {"shots": [{"scene_uid": "scn_ABCDEFGH", "takes": [second, first]}]}
        extended = {"shots": [{"scene_uid": "scn_ABCDEFGH", "takes": [first, second, appended]}]}
        self.assertTrue(SERVER.take_immutability_errors(current, reordered))
        self.assertTrue(SERVER.take_immutability_errors(current, extended))
        new_scene_with_take = {"shots": [{"scene_uid": "scn_IJKLMNOP", "takes": [appended]}]}
        self.assertTrue(SERVER.take_immutability_errors({"shots": []}, new_scene_with_take))

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

    def test_timeline_snapshot_cannot_mix_old_document_with_new_etag(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-snapshot-") as folder:
            root = Path(folder)
            project = root / "videos" / "project-snapshot"
            timeline_path = project / "work" / "timeline.json"
            timeline_path.parent.mkdir(parents=True, exist_ok=True)
            first = payload()
            first["preview"]["end_s"] = 10
            second = payload()
            second["preview"]["end_s"] = 20
            timeline_path.write_text(json.dumps(first), encoding="utf-8")
            patches = (
                mock.patch.object(SERVER, "ROOT", root),
                mock.patch.object(SERVER, "PROJECT", project),
                mock.patch.object(SERVER, "TIMELINE", timeline_path),
                mock.patch.object(SERVER, "EDITOR_MANIFEST", project / "work" / "editor" / "manifest.json"),
            )
            for patcher in patches:
                patcher.start()
            initial_etag = SERVER.timeline_etag()
            load_entered = threading.Event()
            release_load = threading.Event()
            writer_done = threading.Event()
            original_load = SERVER.load_timeline

            def slow_load():
                document = original_load()
                load_entered.set()
                self.assertTrue(release_load.wait(timeout=5))
                return document

            def write_second():
                with SERVER.timeline_lock:
                    SERVER.backup_and_write(timeline_path, second, "backups", "timeline")
                writer_done.set()

            try:
                with mock.patch.object(SERVER, "load_timeline", side_effect=slow_load):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                        snapshot = executor.submit(SERVER.timeline_snapshot)
                        self.assertTrue(load_entered.wait(timeout=5))
                        writer = executor.submit(write_second)
                        self.assertFalse(writer_done.wait(timeout=0.05))
                        release_load.set()
                        document, etag = snapshot.result(timeout=5)
                        writer.result(timeout=5)
                self.assertEqual(document["preview"]["end_s"], 10)
                self.assertEqual(etag, initial_etag)
                self.assertEqual(json.loads(timeline_path.read_text(encoding="utf-8"))["preview"]["end_s"], 20)
            finally:
                release_load.set()
                for patcher in reversed(patches):
                    patcher.stop()

    def test_multipart_import_dedupes_preserves_source_and_promotes(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-import-") as folder:
            root = Path(folder)
            project = root / "videos" / "project-a"
            timeline_path = project / "work" / "timeline.json"
            timeline_path.parent.mkdir(parents=True)
            timeline_path.write_text(json.dumps(payload({
                "id": "legacy-media-placement",
                "engine": "media",
                "type": "cutaway",
                "master_in_s": 1,
                "master_out_s": 2,
                "enabled": False,
                "status": "planned",
                "takes": [],
                "active_take_uid": None,
            })), encoding="utf-8")
            patches = (
                mock.patch.object(SERVER, "ROOT", root),
                mock.patch.object(SERVER, "PROJECT", project),
                mock.patch.object(SERVER, "TIMELINE", timeline_path),
                mock.patch.object(SERVER, "EDITOR_MANIFEST", project / "work" / "editor" / "manifest.json"),
                mock.patch.object(SERVER, "PROXY", project / "work" / "editor" / "proxy.mp4"),
            )
            for patcher in patches:
                patcher.start()

            def fake_conform(source, staging, _scene, _timeline):
                artifact = staging / "asset.mp4"
                artifact.write_bytes(b"conformed:" + source.read_bytes())
                return artifact, "cutaway_h264", {
                    "w": 1920, "h": 1080, "fps": "30/1", "dur_s": 1.0,
                    "alpha": False, "conformed": True,
                }

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
                scene_uid = loaded["timeline"]["shots"][0]["scene_uid"]

                upload, content_type = multipart_body("candidate.mp4", b"source-video", "slower camera")

                def import_request(etag):
                    return urllib.request.Request(
                        base + f"/api/scene/{scene_uid}/takes/import",
                        data=upload,
                        method="POST",
                        headers={
                            "Content-Type": content_type,
                            "If-Match": etag,
                            "X-Workbench-Token": SERVER.SESSION_TOKEN,
                        },
                    )

                with mock.patch.object(SERVER, "conform_take", side_effect=fake_conform):
                    with urllib.request.urlopen(import_request(loaded["etag"]), timeout=5) as response:
                        self.assertEqual(response.status, 201)
                        imported = json.load(response)
                    with urllib.request.urlopen(import_request(imported["etag"]), timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        duplicate = json.load(response)

                self.assertTrue(imported["created"])
                self.assertTrue(duplicate["deduped"])
                self.assertEqual(len(duplicate["scene"]["takes"]), 1)
                take = imported["take"]
                artifact_path = root / Path(take["file"])
                source_path = root / Path(take["source_file"])
                self.assertEqual(artifact_path.read_bytes(), b"conformed:source-video")
                self.assertEqual(source_path.read_bytes(), b"source-video")
                generated = project / "work" / "generated" / scene_uid
                self.assertEqual(len([item for item in generated.iterdir() if item.is_dir()]), 1)

                promote = urllib.request.Request(
                    base + f"/api/scene/{scene_uid}/takes/{take['take_uid']}/promote",
                    data=b"{}",
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "If-Match": duplicate["etag"],
                        "X-Workbench-Token": SERVER.SESSION_TOKEN,
                    },
                )
                with urllib.request.urlopen(promote, timeout=5) as response:
                    promoted = json.load(response)
                self.assertEqual(promoted["scene"]["active_take_uid"], take["take_uid"])
                self.assertEqual(promoted["scene"]["asset"], take["file"])
                self.assertEqual(promoted["scene"]["status"], "draft")
                self.assertTrue(list((timeline_path.parent / "backups").glob("timeline-*.json")))

                for mutation in ("modify", "remove"):
                    changed = json.loads(json.dumps(promoted["timeline"]))
                    if mutation == "modify":
                        changed["shots"][0]["takes"][0]["provenance"]["note"] = "rewritten"
                    else:
                        changed["shots"][0]["takes"] = []
                        changed["shots"][0]["active_take_uid"] = None
                    immutable = urllib.request.Request(
                        base + "/api/timeline",
                        data=json.dumps({"timeline": changed}).encode(),
                        method="PUT",
                        headers={
                            "Content-Type": "application/json",
                            "If-Match": promoted["etag"],
                            "X-Workbench-Token": SERVER.SESSION_TOKEN,
                        },
                    )
                    with self.assertRaises(urllib.error.HTTPError) as rejected:
                        urllib.request.urlopen(immutable, timeout=5)
                    self.assertEqual(rejected.exception.code, 400)
                    self.assertEqual(json.loads(rejected.exception.read())["code"], "E_TAKE_IMMUTABLE")
                    rejected.exception.close()

                media_url = base + f"/media/take/{scene_uid}/{take['take_uid']}?token={SERVER.SESSION_TOKEN}"
                with urllib.request.urlopen(media_url, timeout=5) as response:
                    self.assertEqual(response.read(), b"conformed:source-video")
                ranged = urllib.request.Request(media_url, headers={"Range": "bytes=0-4"})
                with urllib.request.urlopen(ranged, timeout=5) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.headers["Content-Range"], "bytes 0-4/22")
                    self.assertEqual(response.read(), b"confo")
                unsatisfiable = urllib.request.Request(media_url, headers={"Range": "bytes=999-"})
                with self.assertRaises(urllib.error.HTTPError) as bad_range:
                    urllib.request.urlopen(unsatisfiable, timeout=5)
                self.assertEqual(bad_range.exception.code, 416)
                self.assertEqual(bad_range.exception.headers["Content-Range"], "bytes */22")
                bad_range.exception.close()
                cross_origin = urllib.request.Request(media_url, headers={"Origin": "http://evil.invalid"})
                with self.assertRaises(urllib.error.HTTPError) as rejected_origin:
                    urllib.request.urlopen(cross_origin, timeout=5)
                self.assertEqual(rejected_origin.exception.code, 403)
                rejected_origin.exception.close()
                with self.assertRaises(urllib.error.HTTPError) as unauthorized:
                    urllib.request.urlopen(base + f"/media/take/{scene_uid}/{take['take_uid']}", timeout=5)
                self.assertEqual(unauthorized.exception.code, 401)
                unauthorized.exception.close()

                browser_path_import = urllib.request.Request(
                    base + f"/api/scene/{scene_uid}/takes/import",
                    data=json.dumps({"path": take["source_file"]}).encode(),
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "If-Match": promoted["etag"],
                        "X-Workbench-Token": SERVER.SESSION_TOKEN,
                    },
                )
                with self.assertRaises(urllib.error.HTTPError) as forbidden:
                    urllib.request.urlopen(browser_path_import, timeout=5)
                self.assertEqual(forbidden.exception.code, 403)
                self.assertEqual(json.loads(forbidden.exception.read())["code"], "E_PATH_IMPORT_FORBIDDEN")
                forbidden.exception.close()

                cli_outside_import = urllib.request.Request(
                    base + f"/api/scene/{scene_uid}/takes/import",
                    data=json.dumps({"path": str(root.parent / "outside.mp4")}).encode(),
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "If-Match": promoted["etag"],
                        "X-Workbench-CLI-Token": SERVER.CLI_IMPORT_TOKEN,
                        "X-Workbench-Token": SERVER.SESSION_TOKEN,
                    },
                )
                with self.assertRaises(urllib.error.HTTPError) as outside:
                    urllib.request.urlopen(cli_outside_import, timeout=5)
                self.assertEqual(outside.exception.code, 400)
                self.assertEqual(json.loads(outside.exception.read())["code"], "E_ASSET_OUTSIDE_PROJECT")
                outside.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                for patcher in reversed(patches):
                    patcher.stop()

    def test_courier_generate_lifecycle_dedupes_promotes_cancels_and_accepts_late_candidate(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-courier-") as folder:
            root = Path(folder)
            project = root / "videos" / "project-courier"
            timeline_path = project / "work" / "timeline.json"
            jobs_dir = project / "work" / "jobs"
            inbox_dir = project / "work" / "inbox"
            timeline_path.parent.mkdir(parents=True)
            scene_uid = "scn_COURIER1"
            timeline_path.write_text(json.dumps(payload({
                "scene_uid": scene_uid,
                "id": scene_uid,
                "engine": "fable",
                "type": "cutaway",
                "master_in_s": 4,
                "master_out_s": 7,
                "cue": "A calm product reveal",
                "enabled": False,
                "status": "planned",
                "takes": [],
                "active_take_uid": None,
            })), encoding="utf-8")
            patches = (
                mock.patch.object(SERVER, "ROOT", root),
                mock.patch.object(SERVER, "PROJECT", project),
                mock.patch.object(SERVER, "TIMELINE", timeline_path),
                mock.patch.object(SERVER, "JOBS_DIR", jobs_dir),
                mock.patch.object(SERVER, "INBOX_DIR", inbox_dir),
                mock.patch.object(SERVER, "EDITOR_MANIFEST", project / "work" / "editor" / "manifest.json"),
                mock.patch.object(SERVER, "PROXY", project / "work" / "editor" / "proxy.mp4"),
                mock.patch.object(SERVER, "COURIER_SETTLE_SECONDS", 0),
            )
            for patcher in patches:
                patcher.start()

            def fake_conform(source, staging, _scene, _timeline):
                artifact = staging / "asset.mp4"
                artifact.write_bytes(b"conformed:" + source.read_bytes())
                return artifact, "cutaway_h264", {
                    "w": 1920, "h": 1080, "fps": "30/1", "dur_s": 3.0,
                    "alpha": False, "conformed": True,
                }

            server = ThreadingHTTPServer(("127.0.0.1", 0), SERVER.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                api_headers = {"X-Workbench-Token": SERVER.SESSION_TOKEN}
                with urllib.request.urlopen(urllib.request.Request(base + "/api/scenes", headers=api_headers), timeout=5) as response:
                    loaded = json.load(response)

                def submit(etag, prompt):
                    request = urllib.request.Request(
                        base + f"/api/scene/{scene_uid}/revise",
                        data=json.dumps({
                            "prompt": prompt,
                            "provider_hint": "fable",
                            "duration_s": 3,
                        }).encode(),
                        method="POST",
                        headers={
                            **api_headers,
                            "Content-Type": "application/json",
                            "If-Match": etag,
                        },
                    )
                    with urllib.request.urlopen(request, timeout=5) as response:
                        self.assertEqual(response.status, 202)
                        return json.load(response)

                submitted = submit(loaded["etag"], "slow dolly across the product")
                job_id = submitted["job_id"]
                inbox = inbox_dir / job_id
                self.assertTrue(inbox.is_dir())
                self.assertEqual(submitted["job"]["state"], "submitted")
                self.assertEqual(submitted["job"]["spec"], {
                    "prompt": "slow dolly across the product",
                    "provider_hint": "fable",
                    "duration_s": 3.0,
                })
                self.assertEqual(submitted["scene"]["status"], "generating")

                (inbox / "first.mp4").write_bytes(b"first")
                with mock.patch.object(SERVER, "conform_take", side_effect=fake_conform):
                    SERVER.poll_courier_jobs()
                first_job = SERVER.read_job(job_id)
                self.assertEqual(first_job["state"], "awaiting_pick")
                self.assertEqual(len(first_job["candidates"]), 1)
                first_take_uid = first_job["candidates"][0]["take_uid"]
                first_timeline = SERVER.load_timeline()
                first_take = first_timeline["shots"][0]["takes"][0]
                self.assertEqual(first_take["provenance"]["job_id"], job_id)
                self.assertEqual(first_take["provenance"]["spec"], submitted["job"]["spec"])

                (inbox / "duplicate.mp4").write_bytes(b"first")
                with mock.patch.object(SERVER, "conform_take", side_effect=fake_conform):
                    SERVER.poll_courier_jobs()
                deduped_job = SERVER.read_job(job_id)
                self.assertEqual(len(deduped_job["candidates"]), 1)
                self.assertEqual(len(SERVER.load_timeline()["shots"][0]["takes"]), 1)

                promote = urllib.request.Request(
                    base + f"/api/scene/{scene_uid}/takes/{first_take_uid}/promote",
                    data=b"{}",
                    method="POST",
                    headers={
                        **api_headers,
                        "Content-Type": "application/json",
                        "If-Match": SERVER.timeline_etag(),
                    },
                )
                with urllib.request.urlopen(promote, timeout=5) as response:
                    promoted = json.load(response)
                self.assertEqual(promoted["scene"]["active_take_uid"], first_take_uid)
                self.assertEqual(promoted["scene"]["status"], "draft")
                self.assertEqual(SERVER.read_job(job_id)["state"], "succeeded")

                (inbox / "late.mp4").write_bytes(b"second")
                with mock.patch.object(SERVER, "conform_take", side_effect=fake_conform):
                    SERVER.poll_courier_jobs()
                late_job = SERVER.read_job(job_id)
                self.assertEqual(late_job["state"], "awaiting_pick")
                self.assertEqual(len(late_job["candidates"]), 2)
                self.assertEqual(len(SERVER.load_timeline()["shots"][0]["takes"]), 2)

                second = submit(SERVER.timeline_etag(), "alternate angle")
                cancel = urllib.request.Request(
                    base + f"/api/jobs/{second['job_id']}/cancel",
                    data=b"{}",
                    method="POST",
                    headers={
                        **api_headers,
                        "Content-Type": "application/json",
                        "If-Match": second["job"]["updated_at"],
                    },
                )
                with urllib.request.urlopen(cancel, timeout=5) as response:
                    canceled = json.load(response)
                self.assertEqual(canceled["job"]["state"], "canceled")
                canceled_inbox = inbox_dir / second["job_id"]
                (canceled_inbox / "too-late.mp4").write_bytes(b"ignored")
                with mock.patch.object(SERVER, "conform_take", side_effect=fake_conform):
                    SERVER.poll_courier_jobs()
                self.assertEqual(len(SERVER.load_timeline()["shots"][0]["takes"]), 2)
                self.assertEqual(SERVER.read_job(second["job_id"])["state"], "canceled")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                for patcher in reversed(patches):
                    patcher.stop()

    def test_concurrent_take_imports_with_one_etag_commit_exactly_one(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-import-race-") as folder:
            root = Path(folder)
            project = root / "videos" / "project-race"
            timeline_path = project / "work" / "timeline.json"
            timeline_path.parent.mkdir(parents=True)
            scene_uid = "scn_RACE1234"
            timeline_path.write_text(json.dumps(payload({
                "scene_uid": scene_uid,
                "id": scene_uid,
                "engine": "media",
                "type": "cutaway",
                "master_in_s": 1,
                "master_out_s": 2,
                "enabled": False,
                "status": "planned",
                "takes": [],
                "active_take_uid": None,
            })), encoding="utf-8")
            patches = (
                mock.patch.object(SERVER, "ROOT", root),
                mock.patch.object(SERVER, "PROJECT", project),
                mock.patch.object(SERVER, "TIMELINE", timeline_path),
                mock.patch.object(SERVER, "EDITOR_MANIFEST", project / "work" / "editor" / "manifest.json"),
                mock.patch.object(SERVER, "PROXY", project / "work" / "editor" / "proxy.mp4"),
            )
            for patcher in patches:
                patcher.start()
            barrier = threading.Barrier(2)

            def synchronized_conform(source, staging, _scene, _timeline):
                artifact = staging / "asset.mp4"
                artifact.write_bytes(b"conformed:" + source.read_bytes())
                barrier.wait(timeout=5)
                return artifact, "cutaway_h264", {
                    "w": 1920, "h": 1080, "fps": "30/1", "dur_s": 1.0,
                    "alpha": False, "conformed": True,
                }

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
                upload, content_type = multipart_body("same.mp4", b"same-source")

                def import_once(_index):
                    request = urllib.request.Request(
                        base + f"/api/scene/{scene_uid}/takes/import",
                        data=upload,
                        method="POST",
                        headers={
                            "Content-Type": content_type,
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

                with mock.patch.object(SERVER, "conform_take", side_effect=synchronized_conform):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                        results = list(executor.map(import_once, range(2)))

                self.assertEqual(sorted(status for status, _ in results), [201, 409])
                conflict = next(body for status, body in results if status == 409)
                self.assertEqual(conflict["code"], "E_ETAG_MISMATCH")
                persisted = json.loads(timeline_path.read_text(encoding="utf-8"))
                self.assertEqual(len(persisted["shots"][0]["takes"]), 1)
                generated = project / "work" / "generated" / scene_uid
                self.assertEqual(len([item for item in generated.iterdir() if item.is_dir()]), 1)
                for _ in range(50):
                    if not list((project / "work" / "tmp").glob(".take-import-*")):
                        break
                    time.sleep(0.01)
                self.assertFalse(list((project / "work" / "tmp").glob(".take-import-*")))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                for patcher in reversed(patches):
                    patcher.stop()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
    def test_conform_take_normalizes_10bit_input(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-conform-") as folder:
            staging = Path(folder)
            source = staging / "source.mkv"
            created = subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=1",
                "-c:v", "ffv1", "-pix_fmt", "yuv420p10le", str(source),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(created.returncode, 0, created.stderr)
            timeline = payload()
            timeline["preview"].update({"width": 640, "height": 360, "fps": 30})
            artifact, profile, probe = SERVER.conform_take(
                source, staging, {"type": "cutaway"}, timeline
            )
            self.assertEqual(profile, "cutaway_h264")
            self.assertTrue(probe["conformed"])
            self.assertEqual((probe["w"], probe["h"], probe["fps"]), (640, 360, "30/1"))
            self.assertAlmostEqual(probe["dur_s"], 1.0, delta=0.05)
            self.assertTrue(artifact.is_file())
            artifact_probe = SERVER.probe_media(artifact)
            self.assertEqual(artifact_probe["video_codec"], "h264")
            self.assertEqual(artifact_probe["pix_fmt"], "yuv420p")
            self.assertTrue(artifact_probe["cfr"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
    def test_vfr_h264_input_is_detected_and_forced_through_conform(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-vfr-") as folder:
            staging = Path(folder)
            source = staging / "source.mp4"
            created = subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=20:duration=0.5",
                "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=40:duration=0.5",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
                "-map", "[v]", "-fps_mode", "vfr", "-c:v", "libx264", "-crf", "18", "-an",
                str(source),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertFalse(SERVER.probe_media(source)["cfr"])
            timeline = payload()
            timeline["preview"].update({"width": 320, "height": 240, "fps": 30})
            artifact, profile, probe = SERVER.conform_take(
                source, staging, {"type": "cutaway"}, timeline
            )
            self.assertEqual(profile, "cutaway_h264")
            self.assertTrue(probe["conformed"])
            artifact_probe = SERVER.probe_media(artifact)
            self.assertTrue(artifact_probe["cfr"])
            self.assertEqual(artifact_probe["video_codec"], "h264")
            self.assertEqual(artifact_probe["pix_fmt"], "yuv420p")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
    def test_conform_take_preserves_overlay_alpha(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-alpha-") as folder:
            staging = Path(folder)
            source = staging / "source.mov"
            created = subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "color=c=red@0.5:s=320x240:r=24:d=0.25,format=rgba",
                "-c:v", "qtrle", str(source),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(created.returncode, 0, created.stderr)
            timeline = payload()
            timeline["preview"].update({"width": 640, "height": 360, "fps": 30})
            artifact, profile, probe = SERVER.conform_take(
                source, staging, {"type": "overlay"}, timeline
            )
            self.assertEqual(profile, "overlay_alpha")
            self.assertTrue(probe["alpha"])
            self.assertTrue(probe["conformed"])
            self.assertEqual((probe["w"], probe["h"], probe["fps"]), (640, 360, "30/1"))
            self.assertTrue(artifact.is_file())
            artifact_probe = SERVER.probe_media(artifact)
            self.assertEqual(artifact_probe["video_codec"], "prores")
            self.assertTrue(artifact_probe["pix_fmt"].startswith("yuva"))

    def test_project_write_path_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-write-path-") as folder:
            root = Path(folder)
            project = root / "videos" / "project-a"
            outside = root / "outside"
            work = project / "work"
            work.mkdir(parents=True)
            outside.mkdir()
            link = work / "tmp"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink creation is unavailable: {error}")
            with mock.patch.object(SERVER, "PROJECT", project):
                with self.assertRaises(SERVER.ApiError) as escaped:
                    SERVER.project_write_path(link / "upload.mp4")
            self.assertEqual(escaped.exception.code, "E_ASSET_OUTSIDE_PROJECT")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
    def test_range_bake_duration_and_audio_video_sync(self):
        videos = SERVER.ROOT / "videos"
        videos.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="range-bake-test-", dir=videos) as folder:
            project = Path(folder)
            master = project / "master.mp4"
            created = subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000",
                "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(master),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(created.returncode, 0, created.stderr)

            cut_scene_uid = "scn_cutaway01"
            cut_take_uid = "take_cutaway01"
            cut_asset = project / "work" / "generated" / cut_scene_uid / cut_take_uid / "asset.mp4"
            cut_asset.parent.mkdir(parents=True)
            shutil.copy2(master, cut_asset)

            overlay_scene_uid = "scn_overlay001"
            overlay_take_uid = "take_overlay001"
            overlay_asset = project / "work" / "generated" / overlay_scene_uid / overlay_take_uid / "asset.mov"
            overlay_asset.parent.mkdir(parents=True)
            overlay_created = subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "color=c=red@0.35:size=320x180:rate=30",
                "-t", "6", "-vf", "format=argb", "-c:v", "qtrle", str(overlay_asset),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(overlay_created.returncode, 0, overlay_created.stderr)

            def scene(scene_uid, take_uid, asset, scene_type, profile, alpha):
                return {
                    "scene_uid": scene_uid, "engine": "media", "type": scene_type,
                    "master_in_s": 0, "master_out_s": 6, "enabled": True,
                    "status": "draft", "fit": "hold", "z": 0,
                    "transition_in": {"kind": "cut"}, "active_take_uid": take_uid,
                    "takes": [{
                        "take_uid": take_uid, "file": SERVER.repo_relative(asset),
                        "sha256": "0" * 64, "created_at": "2026-01-01T00:00:00Z",
                        "conform_profile": profile,
                        "probe": {"w": 320, "h": 180, "fps": "30/1", "dur_s": 6, "alpha": alpha},
                        "provenance": {"provider": "media"},
                    }],
                }

            timeline = payload()
            timeline["master"] = SERVER.repo_relative(master)
            timeline["shots"] = [scene(
                cut_scene_uid, cut_take_uid, cut_asset, "cutaway", "cutaway_h264", False,
            )]
            timeline["preview"].update({
                "end_s": 6,
                "out": SERVER.repo_relative(project / "work" / "preview" / "full.mp4"),
                "width": 320,
                "height": 180,
                "fps": 30,
            })
            timeline_path = project / "work" / "timeline.json"
            timeline_path.parent.mkdir(parents=True, exist_ok=True)
            timeline_path.write_text(json.dumps(timeline), encoding="utf-8")

            baked = subprocess.run([
                sys.executable, str(SERVER.ROOT / "tools" / "bake.py"), str(timeline_path),
                "--from", "1", "--end", "4",
            ], cwd=SERVER.ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(baked.returncode, 0, baked.stdout + baked.stderr)
            self.assertIn("PROGRESS 0.980 verified; publishing artifact", baked.stdout)
            self.assertIn("cutaway:scn_cutaway01 @+1.00s", baked.stdout)
            output = project / "work" / "preview" / "range-1-4.mp4"
            self.assertTrue(output.is_file())

            timeline["shots"] = [scene(
                overlay_scene_uid, overlay_take_uid, overlay_asset, "overlay", "overlay_alpha", True,
            )]
            timeline_path.write_text(json.dumps(timeline), encoding="utf-8")
            overlay_baked = subprocess.run([
                sys.executable, str(SERVER.ROOT / "tools" / "bake.py"), str(timeline_path),
                "--from", "1", "--end", "4",
            ], cwd=SERVER.ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(overlay_baked.returncode, 0, overlay_baked.stdout + overlay_baked.stderr)
            self.assertIn("master+overlay:scn_overlay001 @+1.00s", overlay_baked.stdout)

            probe = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,duration",
                "-of", "json", str(output),
            ], capture_output=True, text=True, check=True)
            report = json.loads(probe.stdout)
            self.assertAlmostEqual(float(report["format"]["duration"]), 3.0, delta=0.1)
            durations = {
                stream["codec_type"]: float(stream["duration"])
                for stream in report["streams"] if stream.get("duration")
            }
            self.assertIn("video", durations)
            self.assertIn("audio", durations)
            self.assertLessEqual(abs(durations["video"] - durations["audio"]), 0.05)

    @unittest.skipIf(os.name == "nt", "POSIX process-group assertion runs in WSL and CI")
    def test_job_cancel_kills_the_process_group(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-cancel-") as folder:
            project = Path(folder)
            jobs_dir = project / "work" / "jobs"
            ready = project / "child-ready"
            terminated = project / "child-terminated"
            child_code = (
                "import pathlib,signal,sys,time;"
                f"ready=pathlib.Path({str(ready)!r});done=pathlib.Path({str(terminated)!r});"
                "signal.signal(signal.SIGTERM,lambda *_:(done.write_text('terminated'),sys.exit(0)));"
                "ready.write_text('ready');time.sleep(60)"
            )
            parent_code = (
                "import os,pathlib,subprocess,sys,time;"
                f"scratch_root=pathlib.Path({str(project / 'work' / 'preview')!r});"
                "scratch=(scratch_root/f'_bake_tmp_{os.getpid()}');scratch.mkdir(parents=True,exist_ok=True);"
                f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
                f"ready=pathlib.Path({str(ready)!r});"
                "deadline=time.time()+5;"
                "\nwhile not ready.exists() and time.time()<deadline: time.sleep(.01)"
                "\nprint(f'CHILD_PID={child.pid}',flush=True);time.sleep(60)"
            )
            local_jobs = {
                "still": {"running": False, "log": "", "ok": None, "progress": 0, "job_id": None},
                "render": {"running": False, "log": "", "ok": None, "progress": 0, "job_id": None},
                "bake": {"running": False, "log": "", "ok": None, "progress": 0, "job_id": None},
            }
            tracked_processes = {}
            with (
                mock.patch.object(SERVER, "PROJECT", project),
                mock.patch.object(SERVER, "PROJECT_ARG", str(project)),
                mock.patch.object(SERVER, "JOBS_DIR", jobs_dir),
                mock.patch.object(SERVER, "scene_jobs", local_jobs),
                mock.patch.object(SERVER, "active_processes", tracked_processes),
            ):
                job_id = SERVER.start_scene_job(
                    "bake", [sys.executable, "-c", parent_code], project,
                    output="work/preview/cancel-test.mp4",
                )
                self.assertIsNotNone(job_id)
                deadline = time.time() + 8
                record = None
                while time.time() < deadline:
                    record = SERVER.read_job(job_id)
                    if record and record.get("state") == "running" and "CHILD_PID=" in local_jobs["bake"]["log"]:
                        break
                    time.sleep(0.02)
                self.assertIsNotNone(record)
                self.assertEqual(record["state"], "running")
                parent_pid = record["pid"]
                canceled = SERVER.cancel_process_job(job_id, record["updated_at"])
                self.assertEqual(canceled["state"], "canceled")
                deadline = time.time() + 4
                while time.time() < deadline and not terminated.exists():
                    time.sleep(0.02)
                self.assertTrue(terminated.exists(), "child never received the process-group termination")
                deadline = time.time() + 4
                while time.time() < deadline and SERVER.pid_is_alive(parent_pid):
                    time.sleep(0.02)
                self.assertFalse(SERVER.pid_is_alive(parent_pid))
                deadline = time.time() + 4
                while time.time() < deadline and local_jobs["bake"]["running"]:
                    time.sleep(0.02)
                self.assertFalse(local_jobs["bake"]["running"])
                self.assertEqual(list((project / "work" / "preview").glob("_bake_tmp_*")), [])

    def test_process_job_persists_structured_progress(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-progress-") as folder:
            project = Path(folder)
            jobs_dir = project / "work" / "jobs"
            local_jobs = {
                "still": {"running": False, "log": "", "ok": None, "progress": 0, "job_id": None},
                "render": {"running": False, "log": "", "ok": None, "progress": 0, "job_id": None},
                "bake": {"running": False, "log": "", "ok": None, "progress": 0, "job_id": None},
            }
            code = "import time;print('PROGRESS 0.420 segment 2/5',flush=True);time.sleep(.4)"
            with (
                mock.patch.object(SERVER, "PROJECT", project),
                mock.patch.object(SERVER, "PROJECT_ARG", str(project)),
                mock.patch.object(SERVER, "JOBS_DIR", jobs_dir),
                mock.patch.object(SERVER, "scene_jobs", local_jobs),
                mock.patch.object(SERVER, "active_processes", {}),
            ):
                job_id = SERVER.start_scene_job("render", [sys.executable, "-c", code], project)
                deadline = time.time() + 5
                observed = None
                while time.time() < deadline:
                    observed = SERVER.read_job(job_id)
                    if observed and observed.get("state") == "running" and observed.get("progress", 0) >= 0.42:
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(observed)
                self.assertAlmostEqual(observed["progress"], 0.42)
                self.assertEqual(observed["message"], "segment 2/5")
                deadline = time.time() + 5
                while time.time() < deadline:
                    observed = SERVER.read_job(job_id)
                    if observed and observed.get("state") == "succeeded":
                        break
                    time.sleep(0.01)
                self.assertEqual(observed["state"], "succeeded")
                self.assertEqual(observed["progress"], 1)

    @unittest.skipIf(os.name == "nt", "POSIX process cleanup assertion runs in WSL and CI")
    def test_progress_persistence_failure_terminates_the_worker(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-progress-fail-") as folder:
            project = Path(folder)
            jobs_dir = project / "work" / "jobs"
            local_jobs = {
                "still": {"running": False, "log": "", "ok": None, "progress": 0, "job_id": None},
                "render": {"running": False, "log": "", "ok": None, "progress": 0, "job_id": None},
                "bake": {"running": False, "log": "", "ok": None, "progress": 0, "job_id": None},
            }
            code = "import time;print('PROGRESS 0.5 halfway',flush=True);time.sleep(60)"
            with (
                mock.patch.object(SERVER, "PROJECT", project),
                mock.patch.object(SERVER, "PROJECT_ARG", str(project)),
                mock.patch.object(SERVER, "JOBS_DIR", jobs_dir),
                mock.patch.object(SERVER, "scene_jobs", local_jobs),
                mock.patch.object(SERVER, "active_processes", {}),
                mock.patch.object(SERVER, "update_job_progress", side_effect=OSError("disk full")),
            ):
                job_id = SERVER.start_scene_job("render", [sys.executable, "-c", code], project)
                deadline = time.time() + 8
                record = None
                while time.time() < deadline:
                    record = SERVER.read_job(job_id)
                    if record and record.get("state") in {"failed", "unknown"}:
                        break
                    time.sleep(0.02)
                self.assertIsNotNone(record)
                self.assertEqual(record["state"], "failed")
                self.assertFalse(SERVER.pid_is_alive(record["pid"]))

    def test_range_bake_api_requires_etag_and_starts_a_snapshot_job(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-range-api-") as folder:
            project = Path(folder)
            timeline_path = project / "work" / "timeline.json"
            timeline_path.parent.mkdir(parents=True)
            timeline = payload()
            timeline["shots"] = []
            timeline_path.write_text(json.dumps(timeline), encoding="utf-8")
            patches = (
                mock.patch.object(SERVER, "PROJECT", project),
                mock.patch.object(SERVER, "PROJECT_ARG", str(project)),
                mock.patch.object(SERVER, "TIMELINE", timeline_path),
                mock.patch.object(SERVER, "JOBS_DIR", project / "work" / "jobs"),
            )
            for patcher in patches:
                patcher.start()
            server = ThreadingHTTPServer(("127.0.0.1", 0), SERVER.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                request_body = json.dumps({"from_s": 4, "to_s": 8}).encode("utf-8")
                missing = urllib.request.Request(
                    base + "/api/bake/range", data=request_body, method="POST",
                    headers={"Content-Type": "application/json", "X-Workbench-Token": SERVER.SESSION_TOKEN},
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(missing, timeout=5)
                self.assertEqual(raised.exception.code, 428)
                raised.exception.close()

                etag = SERVER.timeline_etag()
                with mock.patch.object(SERVER, "start_scene_job", return_value="job_ABCDEFGH") as starter:
                    request = urllib.request.Request(
                        base + "/api/bake/range", data=request_body, method="POST",
                        headers={
                            "Content-Type": "application/json", "If-Match": etag,
                            "X-Workbench-Token": SERVER.SESSION_TOKEN,
                        },
                    )
                    with urllib.request.urlopen(request, timeout=5) as response:
                        started = json.load(response)
                        self.assertEqual(response.status, 202)
                self.assertEqual(started["job_id"], "job_ABCDEFGH")
                args, kwargs = starter.call_args
                self.assertEqual(args[0], "bake")
                self.assertIn("--from", args[1])
                self.assertIn("--end", args[1])
                self.assertEqual(kwargs["range"], {"from_s": 4.0, "to_s": 8.0})
                self.assertEqual(kwargs["timeline_document"]["preview"]["end_s"], 20)
            finally:
                server.shutdown()
                server.server_close()
                for patcher in reversed(patches):
                    patcher.stop()

    def test_bake_jobs_keep_immutable_per_job_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-artifacts-") as folder:
            project = Path(folder)
            jobs_dir = project / "work" / "jobs"
            published = project / "work" / "preview" / "range-1-2.mp4"
            published.parent.mkdir(parents=True)
            with (
                mock.patch.object(SERVER, "PROJECT", project),
                mock.patch.object(SERVER, "PROJECT_ARG", str(project)),
                mock.patch.object(SERVER, "JOBS_DIR", jobs_dir),
            ):
                published.write_bytes(b"first render")
                first = SERVER.create_job_record(
                    "bake", ["bake"], project, {"output": "work/preview/range-1-2.mp4"},
                    job_id="job_ARTIFACT01",
                )
                first_artifact = SERVER.capture_job_artifact(first["job_id"], "work/preview/range-1-2.mp4")

                published.write_bytes(b"second render")
                second = SERVER.create_job_record(
                    "bake", ["bake"], project, {"output": "work/preview/range-1-2.mp4"},
                    job_id="job_ARTIFACT02",
                )
                second_artifact = SERVER.capture_job_artifact(second["job_id"], "work/preview/range-1-2.mp4")

                self.assertNotEqual(first["output_dir"], second["output_dir"])
                self.assertNotEqual(first["expected_artifacts"], second["expected_artifacts"])
                self.assertEqual(first_artifact.read_bytes(), b"first render")
                self.assertEqual(second_artifact.read_bytes(), b"second render")

    def test_live_durable_worker_blocks_a_second_job_after_restart(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-live-worker-") as folder:
            project = Path(folder)
            jobs_dir = project / "work" / "jobs"
            local_jobs = {
                "still": {"running": False, "job_id": None},
                "render": {"running": False, "job_id": None},
                "bake": {"running": False, "job_id": None},
            }
            with (
                mock.patch.object(SERVER, "PROJECT", project),
                mock.patch.object(SERVER, "PROJECT_ARG", str(project)),
                mock.patch.object(SERVER, "JOBS_DIR", jobs_dir),
                mock.patch.object(SERVER, "scene_jobs", local_jobs),
                mock.patch.object(SERVER, "active_processes", {}),
            ):
                record = SERVER.create_job_record(
                    "render", ["worker"], project, {}, job_id="job_LIVEWORK01",
                )
                record.update({
                    "state": "running", "pid": os.getpid(),
                    "start_token": SERVER.process_start_token(os.getpid()),
                })
                SERVER.write_job(record)
                self.assertIsNone(SERVER.start_scene_job("render", [sys.executable, "-c", "pass"], project))
                self.assertEqual(SERVER.read_job(record["job_id"])["state"], "running")

    def test_cancel_failure_never_reports_canceled(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-cancel-fail-") as folder:
            project = Path(folder)
            jobs_dir = project / "work" / "jobs"
            group_options = (
                {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                if os.name == "nt" else {"start_new_session": True}
            )
            proc = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], **group_options)
            try:
                with (
                    mock.patch.object(SERVER, "PROJECT", project),
                    mock.patch.object(SERVER, "PROJECT_ARG", str(project)),
                    mock.patch.object(SERVER, "JOBS_DIR", jobs_dir),
                    mock.patch.object(SERVER, "terminate_process_group", return_value=False),
                ):
                    record = SERVER.create_job_record(
                        "render", ["worker"], project, {}, job_id="job_CANCELFAIL",
                    )
                    record.update({
                        "state": "running", "pid": proc.pid,
                        "start_token": SERVER.process_start_token(proc.pid),
                    })
                    SERVER.write_job(record)
                    with self.assertRaises(SERVER.ApiError) as raised:
                        SERVER.cancel_process_job(record["job_id"], record["updated_at"])
                    self.assertEqual(raised.exception.code, "E_JOB_CANCEL_FAILED")
                    self.assertEqual(SERVER.read_job(record["job_id"])["state"], "unknown")
            finally:
                if proc.poll() is None:
                    if os.name == "nt":
                        proc.kill()
                    else:
                        os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=4)

    def test_read_jobs_orders_newest_created_at_first(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-job-order-") as folder:
            jobs_dir = Path(folder) / "work" / "jobs"
            with mock.patch.object(SERVER, "JOBS_DIR", jobs_dir):
                older = {"job_id": "job_ZZZZZZZZ", "created_at": "2026-01-01T00:00:00Z"}
                newer = {"job_id": "job_AAAAAAAA", "created_at": "2026-02-01T00:00:00Z"}
                SERVER.write_job(older)
                SERVER.write_job(newer)
                self.assertEqual([job["job_id"] for job in SERVER.read_jobs()], ["job_AAAAAAAA", "job_ZZZZZZZZ"])

    def test_invalid_bake_never_replaces_known_good_output(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-bake-validate-") as folder:
            partial = Path(folder) / "partial.mp4"
            published = Path(folder) / "published.mp4"
            partial.write_bytes(b"invalid candidate")
            published.write_bytes(b"known good")
            invalid_probe = json.dumps({
                "format": {"duration": "N/A"},
                "streams": [{"codec_type": "video"}],
            })
            with (
                mock.patch.object(BAKE, "run", return_value=invalid_probe),
                self.assertRaises(SystemExit),
            ):
                BAKE.publish_validated_bake(str(partial), str(published), 3.0)
            self.assertEqual(published.read_bytes(), b"known good")
            self.assertTrue(partial.exists())

    @unittest.skipIf(os.name == "nt", "POSIX process-group survivor assertion runs in WSL and CI")
    def test_process_group_cancel_kills_child_that_ignores_sigterm(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-group-survivor-") as folder:
            ready = Path(folder) / "ready"
            child_code = (
                "import pathlib,signal,time;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                f"pathlib.Path({str(ready)!r}).write_text('ready');time.sleep(60)"
            )
            parent_code = (
                "import pathlib,subprocess,sys,time;"
                f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                f"ready=pathlib.Path({str(ready)!r});deadline=time.time()+5;"
                "\nwhile not ready.exists() and time.time()<deadline: time.sleep(.01)"
                "\nprint(child.pid,flush=True);time.sleep(60)"
            )
            proc = subprocess.Popen(
                [sys.executable, "-c", parent_code], stdout=subprocess.PIPE, text=True,
                start_new_session=True,
            )
            try:
                child_pid = int(proc.stdout.readline().strip())
                self.assertTrue(ready.exists())
                token = SERVER.process_start_token(proc.pid)
                self.assertTrue(token)
                self.assertTrue(SERVER.terminate_process_group(proc.pid, token))
                proc.wait(timeout=4)
                deadline = time.time() + 4
                while time.time() < deadline and SERVER.pid_is_alive(child_pid):
                    time.sleep(0.02)
                self.assertFalse(SERVER.pid_is_alive(child_pid))
            finally:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=4)
                if proc.stdout is not None:
                    proc.stdout.close()

    @unittest.skipIf(os.name == "nt", "POSIX unverifiable-worker assertion runs in WSL and CI")
    def test_unverifiable_worker_stays_unknown_and_blocks_admission(self):
        with tempfile.TemporaryDirectory(prefix="video-workbench-no-token-") as folder:
            project = Path(folder)
            jobs_dir = project / "work" / "jobs"
            local_jobs = {
                "still": {"running": False, "log": "", "ok": None, "progress": 0, "job_id": None},
                "render": {"running": False, "log": "", "ok": None, "progress": 0, "job_id": None},
                "bake": {"running": False, "log": "", "ok": None, "progress": 0, "job_id": None},
            }
            tracked_processes = {}
            code = "import time;print('PROGRESS 0.5 halfway',flush=True);time.sleep(60)"
            pid = None
            with (
                mock.patch.object(SERVER, "PROJECT", project),
                mock.patch.object(SERVER, "PROJECT_ARG", str(project)),
                mock.patch.object(SERVER, "JOBS_DIR", jobs_dir),
                mock.patch.object(SERVER, "scene_jobs", local_jobs),
                mock.patch.object(SERVER, "active_processes", tracked_processes),
                mock.patch.object(SERVER, "process_start_token", return_value=None),
                mock.patch.object(SERVER, "update_job_progress", side_effect=OSError("disk full")),
            ):
                job_id = SERVER.start_scene_job("render", [sys.executable, "-c", code], project)
                deadline = time.time() + 8
                record = None
                while time.time() < deadline:
                    record = SERVER.read_job(job_id)
                    if record and record.get("state") == "unknown":
                        break
                    time.sleep(0.02)
                self.assertIsNotNone(record)
                self.assertEqual(record["state"], "unknown")
                pid = record["pid"]
                self.assertTrue(SERVER.pid_is_alive(pid))
                self.assertIsNone(SERVER.start_scene_job("render", [sys.executable, "-c", "pass"], project))
            if pid and SERVER.pid_is_alive(pid):
                os.killpg(pid, signal.SIGKILL)
                tracked = tracked_processes.pop(job_id, None)
                try:
                    if tracked is not None:
                        tracked.wait(timeout=4)
                    else:
                        os.waitpid(pid, 0)
                except ChildProcessError:
                    pass

    def test_windows_taskkill_failure_is_detected(self):
        failed = subprocess.CompletedProcess(["taskkill"], 1)
        with (
            mock.patch.object(SERVER.os, "name", "nt"),
            mock.patch.object(SERVER, "process_identity_matches", return_value=True),
            mock.patch.object(SERVER.subprocess, "run", return_value=failed),
        ):
            self.assertFalse(SERVER.terminate_process_group(1234, "1234:token"))

    def test_windows_liveness_probe_never_uses_os_kill(self):
        with (
            mock.patch.object(SERVER.os, "name", "nt"),
            mock.patch.object(SERVER, "linux_proc_stat_fields", return_value=None),
            mock.patch.object(SERVER, "process_start_token", return_value="1234:token"),
            mock.patch.object(SERVER.os, "kill", side_effect=AssertionError("os.kill is destructive on Windows")),
        ):
            self.assertTrue(SERVER.pid_is_alive(1234))

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
