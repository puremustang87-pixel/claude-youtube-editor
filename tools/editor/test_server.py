"""Stdlib tests for scene timeline validation and safe persistence."""

import concurrent.futures
import importlib.util
import json
import os
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
            timeline_path.parent.mkdir(parents=True)
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
