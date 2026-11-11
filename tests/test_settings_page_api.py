"""Tests for dashboard/manifest.json and read-only dashboard/plugin_api.py routes."""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
import unittest
from unittest import mock

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "/api/plugins/defuzz-componentize"

_AUDIT_LOCK_OPENED: list[Path] = []
_AUDIT_ACTIVE = False
_AUDIT_INSTALLED = False


def _install_open_audit_hook() -> None:
    global _AUDIT_INSTALLED
    if _AUDIT_INSTALLED:
        return

    def _hook(event: str, args: tuple[Any, ...]) -> None:
        if not _AUDIT_ACTIVE or event != "open" or not args:
            return
        target = args[0]
        if isinstance(target, (str, bytes, os.PathLike)):
            try:
                _AUDIT_LOCK_OPENED.append(Path(os.fsdecode(target)).resolve(strict=False))
            except OSError:
                _AUDIT_LOCK_OPENED.append(Path(str(target)))

    sys.addaudithook(_hook)
    _AUDIT_INSTALLED = True


@contextlib.contextmanager
def record_opened_files():
    global _AUDIT_ACTIVE
    _install_open_audit_hook()
    _AUDIT_LOCK_OPENED.clear()
    _AUDIT_ACTIVE = True
    try:
        yield _AUDIT_LOCK_OPENED
    finally:
        _AUDIT_ACTIVE = False


def load_plugin_api(mod_name: str = "hermes_dashboard_plugin_defuzz_componentize") -> Any:
    existing = sys.modules.get(mod_name)
    if existing is not None:
        return existing
    api_path = ROOT / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location(mod_name, api_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


class SettingsPageApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = load_plugin_api()
        # Warm private package modules before route audit hooks run so import-time .py reads are distinct from route I/O.
        self.api._load("library_setup")
        self.api._load("kit_commands")
        self.api._load("settings_rule")
        app = FastAPI()
        app.include_router(self.api.router, prefix=PREFIX)
        self.client = TestClient(app)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(os.path.realpath(self.tmp.name))
        self.fake_home = self.base / "home" / "user"
        self.fake_hermes = self.fake_home / ".hermes"
        (self.fake_hermes / "profiles" / "default").mkdir(parents=True)
        (self.fake_hermes / ".env").write_text("SECRET_TOKEN=do-not-read\n", encoding="utf-8")
        (self.fake_hermes / "auth.json").write_text('{"token":"secret"}\n', encoding="utf-8")

    def _patch_profile(self, settings: dict[str, Any]):
        return mock.patch.object(
            self.api,
            "_read_profile_settings",
            return_value=(
                dict(settings),
                str(self.fake_home),
                (str(self.fake_home),),
                (str(self.fake_hermes),),
            ),
        )

    def test_manifest_and_checker_version_match_source(self) -> None:
        manifest_path = ROOT / "dashboard" / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "defuzz-componentize")
        self.assertEqual(manifest["version"], "0.2.0")
        self.assertEqual(manifest["tab"], {"hidden": True})
        self.assertEqual(manifest["api"], "plugin_api.py")

        checker_src = (ROOT / "vendor" / "componentize" / "componentize.py").read_text(encoding="utf-8")
        match = re.search(r'^__version__\s*=\s*"([^"]+)"', checker_src, re.MULTILINE)
        assert match is not None
        self.assertEqual(self.api.CHECKER_VERSION, match.group(1))

    def test_load_does_not_mutate_sys_path_or_hermes_plugins(self) -> None:
        before_path = list(sys.path)
        isolated = load_plugin_api("hermes_dashboard_isolated_defuzz_componentize")
        isolated._load("library_setup")
        self.assertEqual(sys.path, before_path)
        self.assertNotIn("hermes_plugins.defuzz-componentize", sys.modules)

    def test_routes_are_get_only_and_preserve_host_auth_dependencies(self) -> None:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for route in ("/status", "/kits", "/kits/web-app"):
                resp = self.client.request(method, PREFIX + route)
                self.assertEqual(resp.status_code, 405, (method, route))
        # No write/settings route exists
        self.assertEqual(self.client.put(PREFIX + "/settings", json={}).status_code, 404)

        guarded = FastAPI()

        def deny() -> None:
            raise HTTPException(status_code=401, detail="Unauthorized")

        guarded.include_router(self.api.router, prefix=PREFIX, dependencies=[Depends(deny)])
        guarded_client = TestClient(guarded)
        self.assertEqual(guarded_client.get(PREFIX + "/status").status_code, 401)
        self.assertEqual(guarded_client.get(PREFIX + "/kits").status_code, 401)
        self.assertEqual(guarded_client.get(PREFIX + "/kits/web-app").status_code, 401)

    def test_status_states_first_run_configured_missing_and_hermes_home_refused(self) -> None:
        # 1. First-run (library_root empty)
        with self._patch_profile({"library_root": "", "timeout": 900}):
            res = self.client.get(PREFIX + "/status")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["plugin"], "defuzz-componentize")
        self.assertEqual(body["version"], "0.2.0")
        self.assertEqual(body["checker_version"], "1.3.0")
        self.assertEqual(body["kits_count"], 14)
        self.assertEqual(body["library"]["configured"], False)
        self.assertEqual(body["library"]["state"], "not_set")
        self.assertIsNone(body["library"]["count"])

        # 2. Configured and ready (supports ~/ expansion against profile home)
        lib_dir = self.fake_home / "components"
        lib_setup = self.api._load("library_setup")
        lib_setup.init_library(str(lib_dir), forbidden=(str(self.fake_home),))
        with open(lib_dir / "components" / "COMPONENTS.md", "a", encoding="utf-8") as handle:
            handle.write("- `sample.clean(data)` — clean input — returns `{ok}`.\n")
            handle.write("- `sample.format_row(row)` — format row — returns `{ok}`.\n")
            handle.write("- `other.validate(item)` — validate item — returns `{ok}`.\n")

        with self._patch_profile({"library_root": "~/components", "timeout": 1200}):
            res_ready = self.client.get(PREFIX + "/status")
        self.assertEqual(res_ready.status_code, 200)
        ready_json = res_ready.json()
        self.assertEqual(ready_json["settings"]["library_root"], "~/components")
        self.assertEqual(ready_json["settings"]["timeout"], 1200.0)
        self.assertEqual(ready_json["library"]["configured"], True)
        self.assertEqual(ready_json["library"]["ok"], True)
        self.assertEqual(ready_json["library"]["state"], "ready")
        self.assertEqual(ready_json["library"]["count"], 3)
        self.assertEqual(ready_json["library"]["index_rel"], "components/COMPONENTS.md")

        # 3. Missing folder
        missing_dir = self.fake_home / "gone-library"
        with self._patch_profile({"library_root": str(missing_dir), "timeout": "bad"}):
            res_missing = self.client.get(PREFIX + "/status")
        self.assertEqual(res_missing.status_code, 200)
        missing_json = res_missing.json()
        self.assertEqual(missing_json["library"]["state"], "missing")
        self.assertEqual(missing_json["settings"]["timeout"], 900.0)
        self.assertEqual(missing_json["settings"]["timeout_valid"], False)
        self.assertNotIn(str(missing_dir), missing_json["library"]["message"])

        # 4. Relative path, traversal, home, and Hermes home are refused
        for bad_root, expected_state in (
            ("relative/folder", "invalid_path"),
            ("~/../etc", "invalid_path"),
            ("/", "unsafe_path"),
            ("~", "unsafe_path"),
            (str(self.fake_hermes), "unsafe_path"),
            (str(self.fake_hermes / "profiles" / "default"), "unsafe_path"),
        ):
            with self.subTest(bad_root=bad_root):
                with self._patch_profile({"library_root": bad_root, "timeout": 900}):
                    bad_res = self.client.get(PREFIX + "/status")
                self.assertEqual(bad_res.status_code, 200)
                self.assertEqual(bad_res.json()["library"]["state"], expected_state)

    def test_kits_and_single_kit_preview_plus_hostile_kit_ids(self) -> None:
        kits_resp = self.client.get(PREFIX + "/kits")
        self.assertEqual(kits_resp.status_code, 200)
        kits = kits_resp.json()["kits"]
        self.assertEqual(len(kits), 14)
        availability = {row['id']: row['availability'] for row in kits}
        self.assertEqual(availability['web-app'], 'available')
        kit_ids = [row["id"] for row in kits]
        self.assertIn("web-app", kit_ids)
        self.assertIn("component-library", kit_ids)

        one_resp = self.client.get(PREFIX + "/kits/web-app")
        self.assertEqual(one_resp.status_code, 200)
        one = one_resp.json()
        self.assertEqual(one["kit"]["id"], "web-app")
        self.assertEqual(len(one["declared_dirs"]), 22)
        self.assertEqual(len(one["tree"]), 44)
        self.assertIn("AGENTS.md", one["tree"])

        # Hostile kit_id inputs via HTTP and direct handler invocation
        for hostile_http in (
            "/kits/%2e%2e",
            "/kits/%2e%2e%2fplugin.yaml",
            "/kits//etc/passwd",
            "/kits/kit%00bad",
            "/kits/UPPER_CASE",
            "/kits/" + ("a" * 300),
            "/kits/",
        ):
            with self.subTest(hostile_http=hostile_http):
                resp = self.client.get(PREFIX + hostile_http)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json(), {"error": "Invalid kit identifier."})

        for hostile_raw in ("../", "../../etc/passwd", "/etc/passwd", "web-app\x00", "a" * 5000, "..", ""):
            with self.subTest(hostile_raw=hostile_raw):
                direct_resp = self.api.get_kit(hostile_raw)
                self.assertEqual(direct_resp.status_code, 400)
                self.assertEqual(json.loads(direct_resp.body), {"error": "Invalid kit identifier."})

        unknown = self.client.get(PREFIX + "/kits/not-a-bundled-kit")
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json(), {"error": "Unknown project kit."})

    def test_unexpected_errors_return_fixed_plain_messages_without_paths(self) -> None:
        secret_exc = OSError("/private/internal/path/secret.db permission denied")
        with mock.patch.object(self.api, "_read_profile_settings", side_effect=secret_exc):
            r1 = self.client.get(PREFIX + "/status")
        self.assertEqual(r1.status_code, 500)
        self.assertEqual(r1.json(), {"error": "Componentize status is unavailable."})
        self.assertNotIn("secret.db", r1.text)

        with mock.patch.object(self.api, "_bundled_kits_envelope", side_effect=secret_exc):
            r2 = self.client.get(PREFIX + "/kits")
            r3 = self.client.get(PREFIX + "/kits/web-app")
        self.assertEqual(r2.status_code, 500)
        self.assertEqual(r2.json(), {"error": "Bundled project kits could not be read."})
        self.assertEqual(r3.status_code, 500)
        self.assertEqual(r3.json(), {"error": "Project kit preview is unavailable."})

    def test_audit_hook_proves_routes_open_only_bundled_kits_and_library_index_and_zero_subprocesses(self) -> None:
        lib_dir = self.fake_home / "verified-lib"
        lib_setup = self.api._load("library_setup")
        lib_setup.init_library(str(lib_dir), forbidden=(str(self.fake_home),))
        # Plant bait files inside library and inside Hermes home that must NEVER be opened by status/kits routes
        (lib_dir / "SECRET_BAIT.txt").write_text("do not open\n", encoding="utf-8")
        (lib_dir / ".env").write_text("LIB_SECRET=1\n", encoding="utf-8")
        oversize_dir = self.fake_home / "oversize-lib"
        (oversize_dir / "components").mkdir(parents=True)
        (oversize_dir / "components" / "COMPONENTS.md").write_text(
            "x" * (lib_setup.MAX_INDEX_BYTES + 64),
            encoding="utf-8",
        )

        kits_root = (ROOT / "vendor" / "kits").resolve()
        allowed_index = (lib_dir / "components" / "COMPONENTS.md").resolve()
        # Prime TestClient transport once before recording steady-state route file opens.
        self.assertEqual(self.client.get(PREFIX + "/kits").status_code, 200)

        with (
            mock.patch.object(subprocess, "Popen", side_effect=AssertionError("subprocess forbidden")),
            mock.patch.object(subprocess, "run", side_effect=AssertionError("subprocess forbidden")),
            record_opened_files() as opened,
        ):
            with self._patch_profile({"library_root": str(lib_dir), "timeout": 900}):
                self.assertEqual(self.client.get(PREFIX + "/status").status_code, 200)
            with self._patch_profile({"library_root": str(self.fake_hermes), "timeout": 900}):
                self.assertEqual(self.client.get(PREFIX + "/status").status_code, 200)
            with self._patch_profile({"library_root": str(oversize_dir), "timeout": 900}):
                over_status = self.client.get(PREFIX + "/status")
                self.assertEqual(over_status.status_code, 200)
                self.assertEqual(over_status.json()["library"]["state"], "index_too_large")
            self.assertEqual(self.client.get(PREFIX + "/kits").status_code, 200)
            self.assertEqual(self.client.get(PREFIX + "/kits/web-app").status_code, 200)
            self.assertEqual(self.client.get(PREFIX + "/kits/%2e%2e%2fplugin.yaml").status_code, 400)
            self.assertEqual(self.client.get(PREFIX + "/kits/unknown-kit").status_code, 404)

        self.assertTrue(opened, "audit hook did not capture any file opens")
        self.assertIn(allowed_index, opened)
        for path in opened:
            in_kits = path.parent.parent == kits_root and path.name == "kit.json"
            is_index = path == allowed_index
            self.assertTrue(
                in_kits or is_index,
                f"Route opened unexpected file outside vendor/kits/*/kit.json and library index: {path}",
            )


if __name__ == "__main__":
    unittest.main()
