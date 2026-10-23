"""Public update workflow; HTTP and process launch are external boundaries."""
import hashlib
from http.client import IncompleteRead
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError

REPO = "https://github.com/jminbae/pdf-extractor"
LATEST = "https://api.github.com/repos/jminbae/pdf-extractor/releases/latest"
NAME = "PDF-Extractor-Setup.exe"
PAYLOAD = b"MZ synthetic installer bytes; never execute"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def release(tag="v1.3.0"):
    base = REPO + "/releases/download/" + tag + "/"
    checksum = (DIGEST + "  " + NAME + "\n").encode("ascii")
    return {"tag_name": tag, "name": "Not used as version", "draft": False, "prerelease": False,
            "assets": [{"name": NAME, "size": len(PAYLOAD), "digest": "sha256:" + DIGEST,
                        "browser_download_url": base + NAME},
                       {"name": NAME + ".sha256", "size": len(checksum),
                        "browser_download_url": base + NAME + ".sha256"}]}


class Response(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}
        self.url = url
    def geturl(self):
        return self.url


class HTTP:
    def __init__(self, metadata=None):
        self.metadata = metadata or release()
        assets = self.metadata["assets"]
        self.responses = {LATEST: json.dumps(self.metadata).encode("utf-8")}
        for asset in assets:
            self.responses[asset["browser_download_url"]] = ((DIGEST + "  " + NAME + "\n").encode() if asset["name"].endswith(".sha256") else PAYLOAD)
        self.requests = []
    def open(self, request, timeout=None):
        self.requests.append(request.full_url)
        value = self.responses[request.full_url]
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value()
        if isinstance(value, Response):
            return value
        return Response(value, request.full_url)


class UpdateTests(unittest.TestCase):
    def test_incomplete_http_stream_sets_retryable_error_in_snapshot(self):
        from pubnexus.updater import UpdateManager
        class BrokenResponse(Response):
            def read(self, size=-1):
                raise IncompleteRead(b"partial", 100)
        for phase in ("metadata", "installer"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                http = HTTP()
                url = LATEST if phase == "metadata" else http.metadata["assets"][0]["browser_download_url"]
                http.responses[url] = lambda: BrokenResponse(b"", url)
                with patch("urllib.request.build_opener", return_value=http):
                    manager = UpdateManager(temporary)
                    result = manager.check_and_download()
                self.assertEqual(result["status"], "error", result)
                self.assertTrue(result["retryable"])
                self.assertEqual(manager.snapshot()["status"], "error")
                self.assertFalse(list(Path(temporary).rglob("*.part")))

    def test_default_cache_respects_test_store_override(self):
        from pubnexus.updater import UpdateManager
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (patch.dict(os.environ, {"PUBNEXUS_STORE": str(root / "isolated"), "LOCALAPPDATA": str(root / "local")}),
                  patch("urllib.request.build_opener", return_value=HTTP(release("v1.0")))):
                self.assertEqual(UpdateManager().check()["status"], "up_to_date")
            self.assertTrue((root / "isolated" / "updates" / ".update.lock").is_file())
            self.assertFalse((root / "local").exists())

    def test_cache_link_escape_does_not_create_any_external_folder(self):
        from pubnexus.updater import UpdateManager
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache, outside = root / "cache", root / "outside"
            cache.mkdir()
            outside.mkdir()
            try:
                (cache / "1.3.0").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Directory links are unavailable on this test machine")
            with patch("urllib.request.build_opener", return_value=HTTP()):
                result = UpdateManager(cache).check_and_download()
            self.assertEqual(result["status"], "error", result)
            self.assertEqual(result["error"], "unsafe_path")
            self.assertEqual(list(outside.iterdir()), [])

    def test_real_manager_app_exit_contract_records_success_and_launches_once(self):
        import test_app_export as support
        from pubnexus.updater import UpdateManager
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (patch.dict(os.environ, {"PUBNEXUS_STORE": str(root / "store"), "PUBNEXUS_OUTPUT": str(root / "output")}),
                  patch.object(support.app_module.utils, "load_config", return_value={}),
                  patch("urllib.request.build_opener", return_value=HTTP()), patch("subprocess.Popen") as launch,
                  patch.object(sys, "frozen", True, create=True), patch.object(sys, "argv", ["PDF Extractor.exe"])):
                app = support.app_module.App()
                app._updater = UpdateManager(root / "updates")
                app._updater.check_and_download()
                app._closing = True
                result = app.apply_update_on_exit()
                self.assertTrue(result["ok"], result)
                self.assertTrue(app._update_applied)
                self.assertIsNone(app.apply_update_on_exit())
                self.assertEqual(launch.call_count, 1)

    def test_same_or_older_release_never_downloads_or_downgrades(self):
        from pubnexus.updater import UpdateManager
        for tag in ("v1.0", "v1.2.0"):
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as temporary:
                http = HTTP(release(tag))
                with patch("urllib.request.build_opener", return_value=http), patch("subprocess.Popen") as launch:
                    result = UpdateManager(temporary).check_and_download()
                self.assertEqual(result["status"], "up_to_date", result)
                self.assertEqual(http.requests, [LATEST])
                launch.assert_not_called()

    def test_untrusted_metadata_fails_closed_before_installer_download(self):
        from pubnexus.updater import UpdateManager
        cases = []
        for key, value in (("draft", True), ("prerelease", True), ("tag_name", "v1.3.0-rc1"),
                           ("tag_name", "v1.3.0 & calc.exe")):
            metadata = release()
            metadata[key] = value
            cases.append(metadata)
        metadata = release()
        metadata["assets"] = metadata["assets"][:1]
        cases.append(metadata)
        metadata = release()
        metadata["assets"][0]["browser_download_url"] = "https://evil.invalid/" + NAME
        cases.append(metadata)
        metadata = release()
        metadata["assets"][0]["digest"] = "sha256:" + "0" * 64
        cases.append(metadata)
        for metadata in cases:
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as temporary:
                http = HTTP(metadata)
                with patch("urllib.request.build_opener", return_value=http), patch("subprocess.Popen") as launch:
                    result = UpdateManager(temporary).check_and_download()
                self.assertEqual(result["status"], "error", result)
                self.assertFalse(any(url.endswith(".exe") for url in http.requests))
                launch.assert_not_called()

    def test_checksum_filename_hash_and_response_size_are_required(self):
        from pubnexus.updater import UpdateManager
        for fault in ("checksum_name", "corrupt_bytes", "truncated", "too_large", "redirect"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                http = HTTP()
                url = http.metadata["assets"][0]["browser_download_url"]
                if fault == "checksum_name":
                    checksum_url = http.metadata["assets"][1]["browser_download_url"]
                    http.responses[checksum_url] = (DIGEST + "  " + "X" * len(NAME) + "\n").encode()
                elif fault == "corrupt_bytes":
                    http.responses[url] = b"MZ" + b"x" * (len(PAYLOAD) - 2)
                else:
                    response = Response(PAYLOAD, url)
                    if fault == "truncated":
                        response = Response(PAYLOAD[:-1], url)
                        response.headers["Content-Length"] = str(len(PAYLOAD))
                    elif fault == "too_large":
                        response.headers["Content-Length"] = str(len(PAYLOAD) + 1)
                    else:
                        response.url = "https://evil.invalid/installer.exe"
                    http.responses[url] = response
                with patch("urllib.request.build_opener", return_value=http), patch("subprocess.Popen") as launch:
                    result = UpdateManager(temporary).check_and_download()
                self.assertEqual(result["status"], "error", result)
                self.assertFalse(list(Path(temporary).rglob("*.part")))
                self.assertFalse(list(Path(temporary).rglob(NAME)))
                launch.assert_not_called()

    def test_offline_is_retryable_error_and_requests_are_bounded(self):
        from pubnexus.updater import UpdateManager
        http = HTTP()
        http.responses[LATEST] = URLError("offline")
        with tempfile.TemporaryDirectory() as temporary, patch("urllib.request.build_opener", return_value=http):
            result = UpdateManager(temporary).check_and_download()
        self.assertEqual(result["status"], "error", result)
        self.assertEqual(result["error"], "offline")
        self.assertTrue(result["retryable"])
        self.assertEqual(http.requests, [LATEST, LATEST])

    def test_install_rejects_modified_installer_even_if_snapshot_is_forged(self):
        from pubnexus.updater import UpdateManager
        with tempfile.TemporaryDirectory() as temporary, patch("urllib.request.build_opener", return_value=HTTP()):
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "argv", ["PDF Extractor.exe"]), patch("subprocess.Popen") as launch:
                manager = UpdateManager(temporary)
                result = manager.check_and_download()
                Path(result["installer"]).write_bytes(b"MZ" + b"x" * (len(PAYLOAD) - 2))
                result["sha256"] = hashlib.sha256(Path(result["installer"]).read_bytes()).hexdigest()
                result["installer"] = str(Path(temporary) / "different.exe")
                result = manager.install()
                self.assertEqual(result["status"], "error", result)
                self.assertEqual(result["error"], "checksum_mismatch")
                launch.assert_not_called()

    def test_source_hidden_and_busy_never_launch_and_busy_can_resume(self):
        from pubnexus.updater import UpdateManager
        for frozen, argv in ((False, ["main.py"]), (True, ["PDF Extractor.exe", "--hidden"]),
                             (True, ["PDF Extractor.exe", "--ocr-worker"])):
            with self.subTest(argv=argv), tempfile.TemporaryDirectory() as temporary:
                with (patch("urllib.request.build_opener", return_value=HTTP()), patch("subprocess.Popen") as launch,
                      patch.object(sys, "frozen", frozen, create=True), patch.object(sys, "argv", argv)):
                    manager = UpdateManager(temporary)
                    manager.check_and_download()
                    self.assertEqual(manager.install()["status"], "unsupported")
                    launch.assert_not_called()
        with tempfile.TemporaryDirectory() as temporary:
            with (patch("urllib.request.build_opener", return_value=HTTP()), patch("subprocess.Popen") as launch,
                  patch.object(sys, "frozen", True, create=True), patch.object(sys, "argv", ["PDF Extractor.exe"])):
                manager = UpdateManager(temporary)
                manager.check_and_download()
                self.assertEqual(manager.install(busy=True)["status"], "ready")
                launch.assert_not_called()
                self.assertEqual(manager.install()["status"], "installing")
                self.assertNotIn("/PNXRESTART=1", launch.call_args.args[0])

    def test_launch_failure_is_retryable_without_marking_installation_started(self):
        from pubnexus.updater import UpdateManager
        with tempfile.TemporaryDirectory() as temporary:
            with (patch("urllib.request.build_opener", return_value=HTTP()),
                  patch("subprocess.Popen", side_effect=[OSError("locked"), unittest.mock.Mock(pid=123)]) as launch,
                  patch.object(sys, "frozen", True, create=True), patch.object(sys, "argv", ["PDF Extractor.exe"])):
                manager = UpdateManager(temporary)
                manager.check_and_download()
                self.assertEqual(manager.install()["status"], "error")
                self.assertTrue(manager.snapshot()["retryable"])
                self.assertEqual(manager.install()["status"], "installing")
                self.assertEqual(launch.call_count, 2)

    def test_new_manager_revalidates_remote_metadata_and_cached_bytes(self):
        from pubnexus.updater import UpdateManager
        http = HTTP()
        with tempfile.TemporaryDirectory() as temporary, patch("urllib.request.build_opener", return_value=http):
            first = UpdateManager(temporary).check_and_download()
            http.requests.clear()
            second = UpdateManager(temporary).check_and_download()
            self.assertEqual(first["installer"], second["installer"])
            self.assertEqual(second["status"], "ready")
            self.assertEqual(http.requests, [LATEST, http.metadata["assets"][1]["browser_download_url"]])

    def test_concurrent_checks_share_one_operation_and_notifications_allow_snapshot(self):
        from pubnexus.updater import UpdateManager
        entered, proceed, notified = threading.Event(), threading.Event(), threading.Event()
        http = HTTP()
        ordinary_open = http.open
        def blocked_open(req, timeout=None):
            if req.full_url == LATEST:
                entered.set()
                if not proceed.wait(2):
                    raise TimeoutError()
            return ordinary_open(req, timeout)
        http.open = blocked_open
        manager = None
        def notify(event):
            manager.snapshot()
            if event["status"] == "ready":
                notified.set()
        with tempfile.TemporaryDirectory() as temporary, patch("urllib.request.build_opener", return_value=http):
            manager = UpdateManager(temporary, notify=notify)
            worker = threading.Thread(target=manager.check_and_download)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(manager.check_and_download()["status"], "checking")
                self.assertEqual(UpdateManager(temporary).check()["status"], "idle")
            finally:
                proceed.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(notified.wait(1))
            self.assertEqual(http.requests.count(LATEST), 1)

    def test_install_revalidates_then_launches_once_with_fixed_quiet_arguments(self):
        from pubnexus.updater import UpdateManager
        with tempfile.TemporaryDirectory(prefix="pubnexus-update-install-") as temporary:
            root = Path(temporary)
            with (patch("urllib.request.build_opener", return_value=HTTP()), patch("subprocess.Popen") as launch,
                 patch.object(sys, "frozen", True, create=True), patch.object(sys, "argv", ["PDF Extractor.exe"]),
                 patch.object(sys, "executable", str(root / "app" / "PDF Extractor.exe"))):
                manager = UpdateManager(root / "cache")
                self.assertEqual(manager.check_and_download()["status"], "ready")
                result = manager.install(restart=True)
                self.assertEqual(result["status"], "installing", result)
                self.assertTrue(result["ok"], result)
                command = launch.call_args.args[0]
                self.assertEqual(command[0], result["installer"])
                self.assertIn("/VERYSILENT", command)
                self.assertIn("/NORESTART", command)
                self.assertIn("/NOCLOSEAPPLICATIONS", command)
                self.assertIn("/PNXRESTART=1", command)
                self.assertFalse(launch.call_args.kwargs["shell"])
                manager.install(restart=True)
                self.assertEqual(launch.call_count, 1)

    def test_verified_release_download_becomes_ready_without_running_installer(self):
        from pubnexus.updater import UpdateManager
        http, events, ready = HTTP(), [], threading.Event()
        def notified(event):
            events.append(event)
            if event["status"] == "ready":
                ready.set()
        with tempfile.TemporaryDirectory(prefix="pubnexus-update-") as temporary:
            with patch("urllib.request.build_opener", return_value=http), patch("subprocess.Popen") as launch:
                manager = UpdateManager(Path(temporary), current_version="1.2.0", notify=notified)
                result = manager.check_and_download()
                self.assertEqual(result["status"], "ready", result)
                self.assertEqual(result["version"], "1.3.0")
                self.assertEqual(Path(result["installer"]).read_bytes(), PAYLOAD)
                self.assertEqual(result["sha256"], DIGEST)
                self.assertEqual(manager.snapshot()["status"], "ready")
                launch.assert_not_called()
                self.assertTrue(ready.wait(1))
            self.assertIn("checking", [event["status"] for event in events])
            self.assertIn("downloading", [event["status"] for event in events])
            self.assertEqual(events[-1]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
