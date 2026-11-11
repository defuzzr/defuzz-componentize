"""One library_root rule for the settings page, the status route and the CLI."""
from __future__ import annotations

import errno
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "/api/plugins/defuzz-componentize"

M_ABS = "Enter an absolute path starting with /, such as /home/user/components."
M_DOTDOT = 'Remove ".." segments: enter the full path of the folder.'
M_ROOT = "Refused: pick a dedicated folder, not the filesystem root (/)."
M_HOME = "Refused: pick a dedicated folder inside your home folder, not the home folder itself."
M_SYSTEM = "Refused: pick a dedicated folder, not a system folder such as /etc or /usr."
M_HERMES = "Refused: pick a dedicated folder outside Hermes home (such as ~/.hermes)."
T_BAD = "Invalid timeout: enter a finite number greater than 0."
T_MAX = "Invalid timeout: enter at most 86400 seconds (24 hours)."


def load_api():
    name = "hermes_dashboard_plugin_defuzz_componentize_rule"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "dashboard" / "plugin_api.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "componentize_plugin_rule_test", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Base(unittest.TestCase):
    def setUp(self):
        self.api = load_api()
        self.rule = self.api._load("settings_rule")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(os.path.realpath(tmp.name))
        self.home = self.base / "home" / "user"
        self.hermes = self.base / "hermes-profile-home"
        self.home.mkdir(parents=True)
        self.hermes.mkdir()
        app = FastAPI()
        app.include_router(self.api.router, prefix=PREFIX)
        self.client = TestClient(app)

    def patch_profile(self, settings=None):
        return mock.patch.object(self.api, "_read_profile_settings", return_value=(
            dict(settings or {}), str(self.home), (str(self.home),), (str(self.hermes),)))

    def refused(self):
        link = self.base / "link-to-etc"
        if not link.exists():
            link.symlink_to("/etc")
        return [
            ("relative/lib", M_ABS), ("~user/lib", M_ABS),
            (str(self.home) + "/../x", M_DOTDOT), ("/srv/../etc", M_DOTDOT),
            ("/", M_ROOT), ("//", M_ROOT),
            ("~", M_HOME), (str(self.home), M_HOME), (str(self.home) + "/", M_HOME),
            ("/etc", M_SYSTEM), ("/etc/cz-probe", M_SYSTEM), ("/usr/bin", M_SYSTEM), ("/var", M_SYSTEM),
            ("/home", M_SYSTEM), (str(link), M_SYSTEM), (str(link) + "/sub", M_SYSTEM),
            (str(self.hermes), M_HERMES), (str(self.hermes / "profiles" / "x"), M_HERMES),
            (str(self.home / ".hermes" / "lib"), M_HERMES),
        ]


class RuleTests(Base):
    def test_each_refused_class_has_its_plain_message(self):
        for raw, msg in self.refused():
            with self.subTest(raw=raw):
                value, err = self.rule.check_library_root(
                    raw, home=str(self.home), env={}, hermes_homes=(str(self.hermes),))
                self.assertIsNone(value)
                self.assertEqual(err, msg)

    def test_normalized_value_and_empty(self):
        chk = lambda raw: self.rule.check_library_root(raw, home=str(self.home), env={})
        self.assertEqual(chk("  ~/components/ "), (str(self.home / "components"), None))
        self.assertEqual(chk(str(self.home) + "//a/./b/"), (str(self.home / "a" / "b"), None))
        self.assertEqual(chk(""), ("", None))
        self.assertEqual(chk(None), ("", None))

    def test_timeout_bounds(self):
        self.assertEqual(self.rule.check_timeout(1200), (1200.0, None))
        self.assertEqual(self.rule.check_timeout(None), (900.0, None))
        for bad in ("x", 0, -1, float("nan"), float("inf"), True, ""):
            self.assertEqual(self.rule.check_timeout(bad), (None, T_BAD), bad)
        self.assertEqual(self.rule.check_timeout(86401), (None, T_MAX))


class UnresolvablePathTests(Base):
    """Python 3.11's realpath raises OSError (EINVAL) when a link is swapped during the call."""

    def check(self, raw):
        return self.rule.check_library_root(
            raw, home=str(self.home), env={}, hermes_homes=(str(self.hermes),))

    def test_realpath_error_on_the_typed_folder_is_a_plain_refusal(self):
        typed = str(self.home / "swapped" / "lib")
        real = os.path.realpath

        def realpath(path, *args, **kwargs):
            if str(path).startswith(typed):
                raise OSError(errno.EINVAL, "Invalid argument")
            return real(path, *args, **kwargs)

        with mock.patch("os.path.realpath", realpath):
            self.assertEqual(self.check(typed), (None, M_SYSTEM))

    def test_realpath_error_everywhere_is_a_plain_refusal(self):
        with mock.patch("os.path.realpath", side_effect=OSError(errno.EINVAL, "Invalid argument")):
            self.assertEqual(self.check(str(self.home / "components")), (None, M_SYSTEM))


class ValidateRouteTests(Base):
    def test_validate_refuses_each_class_and_returns_no_values(self):
        for raw, msg in self.refused():
            with self.subTest(raw=raw), self.patch_profile():
                res = self.client.post(PREFIX + "/settings/validate", json={"library_root": raw, "timeout": 900})
                self.assertEqual(res.status_code, 200)
                body = res.json()
                self.assertEqual(body["ok"], False)
                self.assertEqual(body["errors"], {"library_root": msg})
                self.assertNotIn("values", body)

    def test_validate_returns_normalized_values(self):
        with self.patch_profile():
            res = self.client.post(PREFIX + "/settings/validate",
                                   json={"library_root": "~/lib//a/./", "timeout": "1200"})
        self.assertEqual(res.json(), {"ok": True, "values": {"library_root": str(self.home / "lib" / "a"), "timeout": 1200.0}})

    def test_validate_refuses_bad_timeout_and_bad_body(self):
        with self.patch_profile():
            res = self.client.post(PREFIX + "/settings/validate", json={"library_root": "", "timeout": 999999})
            self.assertEqual(res.json(), {"ok": False, "errors": {"timeout": T_MAX}})
            self.assertEqual(self.client.post(PREFIX + "/settings/validate", json=[1]).status_code, 400)

    def test_status_refuses_hand_edited_value_with_same_message(self):
        for raw, msg in (("/etc/cz-probe", M_SYSTEM), ("/usr/bin", M_SYSTEM), ("~", M_HOME), ("rel", M_ABS)):
            with self.subTest(raw=raw), self.patch_profile({"library_root": raw, "timeout": 900}):
                lib = self.client.get(PREFIX + "/status").json()["library"]
                self.assertEqual(lib["ok"], False)
                self.assertEqual(lib["message"], msg)


class CliConsumerTests(Base):
    def make_ctx(self, config):
        saved = {}

        class Ctx:
            def get_config(self, key, default=None): return config.get(key, default)
            def set_config(self, key, value): saved[key] = value
            def register_cli_command(self, **kw): self.cli = kw
            def register_command(self, **kw): self.slash = kw
            def register_skill(self, **kw): pass
        ctx = Ctx()
        plugin = load_plugin()
        plugin.register(ctx)
        return plugin, ctx, saved

    def dispatch(self, plugin, ctx, argv):
        handler = ctx.slash["handler"]
        bridge = importlib.import_module(plugin.__name__ + ".bridge")
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), \
                mock.patch.object(bridge, "run", side_effect=AssertionError("bridge must not run")):
            return handler(" ".join(argv))

    def test_hand_edited_library_root_refused_before_bridge(self):
        for raw, msg in (("/etc/cz-probe", M_SYSTEM), ("/usr/bin", M_SYSTEM), ("/", M_ROOT), ("~", M_HOME)):
            with self.subTest(raw=raw):
                plugin, ctx, _ = self.make_ctx({"library_root": raw})
                out = self.dispatch(plugin, ctx, ["status"])
                self.assertIn(msg, out)

    def test_hand_edited_timeout_refused_before_bridge(self):
        plugin, ctx, _ = self.make_ctx({"library_root": str(self.home / "lib"), "timeout": "nope"})
        self.assertIn(T_BAD, self.dispatch(plugin, ctx, ["status"]))

    def test_init_refuses_and_saves_nothing(self):
        for raw, msg in (("/etc/cz-probe", M_SYSTEM), ("/", M_ROOT), (str(self.home), M_HOME)):
            with self.subTest(raw=raw):
                plugin, ctx, saved = self.make_ctx({})
                out = self.dispatch(plugin, ctx, ["init", raw])
                self.assertIn(msg, out)
                self.assertEqual(saved, {})


if __name__ == "__main__":
    unittest.main()
