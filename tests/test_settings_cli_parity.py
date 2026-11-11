"""One library folder rule for the settings page and for `componentize init`.

The page checks a draft through POST /settings/validate. The command checks the
folder the user typed through library_setup.check_typed_folder. Both must give
the same decision and the same sentence for every input below. A folder typed
on the command line may be relative to the folder the command runs in (by
design); the page sees that same folder as its absolute form.
"""
from __future__ import annotations

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

M_NUL = "Invalid path: remove NUL characters and line breaks."
M_LONG = "Path is too long: use at most 1024 characters."
M_ABS = "Enter an absolute path starting with /, such as /home/user/components."
M_DOTDOT = 'Remove ".." segments: enter the full path of the folder.'
M_ROOT = "Refused: pick a dedicated folder, not the filesystem root (/)."
M_HOME = "Refused: pick a dedicated folder inside your home folder, not the home folder itself."
M_SYSTEM = "Refused: pick a dedicated folder, not a system folder such as /etc or /usr."
M_HERMES = "Refused: pick a dedicated folder outside Hermes home (such as ~/.hermes)."
M_CTRL = "Invalid path: remove control characters."
M_EMPTY = "Enter the folder for the component library, such as ~/components."

UNSET = object()  # the page treats an empty value as "no folder set"


def load_api():
    name = "hermes_dashboard_plugin_defuzz_componentize_parity"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "dashboard" / "plugin_api.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class ParityBase(unittest.TestCase):
    def setUp(self):
        self.api = load_api()
        self.rule = self.api._load("settings_rule")
        self.lib = self.api._load("library_setup")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(os.path.realpath(tmp.name))
        self.home = self.base / "home" / "user"
        self.cwd = self.home / "work"
        self.cwd.mkdir(parents=True)
        self.hermes = self.base / "hermes-profile-home"
        (self.hermes / "profiles" / "x").mkdir(parents=True)
        (self.home / "links").mkdir()
        (self.home / "links" / "to-etc").symlink_to("/etc")
        app = FastAPI()
        app.include_router(self.api.router, prefix=PREFIX)
        self.client = TestClient(app)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "HERMES_HOME": str(self.hermes)})
        env.start()
        self.addCleanup(env.stop)

    def table(self):
        """(input, expected) rows. expected: a message, a normalized path, or UNSET."""
        H = str(self.home)
        C = str(self.cwd)
        fill = "a" * (1024 - len(H) - 1)
        rows = [
            # accepted folders
            (H + "/lib", H + "/lib"),
            ("~/lib", H + "/lib"),
            ("  ~/lib/  ", H + "/lib"),
            (H + "/lib/.", H + "/lib"),
            (H + "/lib/...", H + "/lib/..."),
            (H + "/.lib", H + "/.lib"),
            (H + "//lib//sub", H + "/lib/sub"),
            (H + "/./lib", H + "/lib"),
            (H + "/" + fill, H + "/" + fill),
            # relative folders typed on the command line: relative to where it runs
            ("lib", C + "/lib"),
            ("./lib", C + "/lib"),
            ("nested/lib/", C + "/nested/lib"),
            # ".." in every position: always refused, never normalised away
            ("..", M_DOTDOT),
            ("../lib", M_DOTDOT),
            ("lib/..", M_DOTDOT),
            ("a/../b", M_DOTDOT),
            ("~/..", M_DOTDOT),
            ("~/../x", M_DOTDOT),
            (H + "/../x", M_DOTDOT),
            (H + "/a/../components", M_DOTDOT),
            (H + "/components/../..", M_DOTDOT),
            (H + "/x/..", M_DOTDOT),
            (H + "/a/..//b", M_DOTDOT),
            (H + "/a\\..\\b", M_DOTDOT),
            (H + "/../../.hermes", M_DOTDOT),
            (H + "/lib/../../../etc", M_DOTDOT),
            ("/..", M_DOTDOT),
            ("/../etc", M_DOTDOT),
            ("/srv/../etc", M_DOTDOT),
            (str(self.hermes) + "/../lib", M_DOTDOT),
            # the home itself and its ancestors
            ("~", M_HOME),
            ("~/", M_HOME),
            (H, M_HOME),
            (H + "/", M_HOME),
            (H + "//", M_HOME),
            (H + "/.", M_HOME),
            (str(self.home.parent), M_SYSTEM),
            ("/", M_ROOT),
            ("//", M_ROOT),
            ("///", M_ROOT),
            # ~user forms are not supported
            ("~user", M_ABS),
            ("~user/lib", M_ABS),
            ("~root/x", M_ABS),
            # system folders
            ("/etc", M_SYSTEM),
            ("/etc/cz-probe", M_SYSTEM),
            ("/usr/lib/x", M_SYSTEM),
            ("/var", M_SYSTEM),
            ("/home", M_SYSTEM),
            ("/mnt", M_SYSTEM),
            (H + "/links/to-etc", M_SYSTEM),
            (H + "/links/to-etc/sub", M_SYSTEM),
            # Hermes homes
            ("~/.hermes", M_HERMES),
            (H + "/.hermes/plugins/lib", M_HERMES),
            (str(self.hermes), M_HERMES),
            (str(self.hermes / "profiles" / "x"), M_HERMES),
            # unusable text
            ("/" + "a" * 1024, M_LONG),
            (H + "/" + fill + "b", M_LONG),
            ("/" + "a" * 4999, M_LONG),
            (H + "/lib\x01", M_CTRL),
            (H + "/lib\x1b[31m", M_CTRL),
            (H + "/lib\n/etc", M_NUL),
            # not a folder at all
            ("", UNSET),
            ("   ", UNSET),
            (None, UNSET),
            (5, M_ABS),
            ([H + "/lib"], M_ABS),
            ({"path": H + "/lib"}, M_ABS),
        ]
        return rows

    def page(self, raw):
        """POST /settings/validate; returns (path or None, message or None)."""
        settings = (dict(), str(self.home), (str(self.home),), (str(self.hermes),))
        with mock.patch.object(self.api, "_read_profile_settings", return_value=settings):
            res = self.client.post(PREFIX + "/settings/validate", json={"library_root": raw, "timeout": 900})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        if body["ok"]:
            return body["values"]["library_root"], None
        return None, body["errors"]["library_root"]

    def command(self, raw):
        """The check `componentize init <folder>` applies to the folder it was given."""
        return self.lib.check_typed_folder(raw, cwd=str(self.cwd), home=str(self.home))

    def page_input(self, raw):
        """The page has no working folder: a typed relative folder is sent in its absolute form."""
        if isinstance(raw, str):
            text = raw.strip()
            if text and not text.startswith(("/", "~")):
                return str(self.cwd) + "/" + text
        return raw


class ParityTableTests(ParityBase):
    def test_table_is_large_enough(self):
        self.assertGreaterEqual(len(self.table()), 40)

    def test_page_and_command_agree_on_every_input(self):
        for raw, expected in self.table():
            with self.subTest(raw=raw):
                page_raw = self.page_input(raw)
                page_path, page_msg = self.page(page_raw)
                cmd_path, cmd_msg = self.command(raw)
                if expected is UNSET:
                    # The page clears the setting; the command needs a folder to create.
                    self.assertEqual((page_path, page_msg), ("", None))
                    self.assertEqual((cmd_path, cmd_msg), (None, M_EMPTY))
                elif expected.startswith("/"):
                    self.assertEqual((page_path, page_msg), (expected, None))
                    self.assertEqual((cmd_path, cmd_msg), (expected, None))
                else:
                    self.assertEqual((page_path, page_msg), (None, expected))
                    self.assertEqual((cmd_path, cmd_msg), (None, expected))

    def test_command_never_accepts_a_folder_the_page_refuses(self):
        for raw, _expected in self.table():
            with self.subTest(raw=raw):
                page_raw = self.page_input(raw)
                _p, page_msg = self.page(page_raw)
                cmd_path, _c = self.command(raw)
                if page_msg is not None:
                    self.assertIsNone(cmd_path)

    def test_relative_dotdot_is_refused_even_when_it_would_land_in_a_safe_folder(self):
        (self.cwd / "inner").mkdir()
        for raw in ("inner/../lib", "../work/lib", "./inner/.."):
            with self.subTest(raw=raw):
                self.assertEqual(self.command(raw), (None, M_DOTDOT))

    def test_relative_folder_without_a_working_folder_is_refused(self):
        for cwd in (None, "", "not-absolute", 5):
            with self.subTest(cwd=cwd):
                self.assertEqual(self.lib.check_typed_folder("lib", cwd=cwd, home=str(self.home)), (None, M_ABS))

    def test_tilde_without_a_home_is_refused(self):
        for home in (None, "", "relative-home"):
            with self.subTest(home=home):
                self.assertEqual(self.lib.check_typed_folder("~/lib", cwd=str(self.cwd), home=home), (None, M_ABS))

    def test_command_uses_the_settings_rule(self):
        with mock.patch.object(self.rule, "check_library_root", return_value=(None, "sentinel")) as spy:
            self.assertEqual(self.command("~/lib"), (None, "sentinel"))
        spy.assert_called_once()
        self.assertEqual(spy.call_args.kwargs.get("cwd"), str(self.cwd))

    def test_page_ignores_any_working_folder(self):
        # The page rule stays absolute-only: no cwd means a relative value is refused.
        self.assertEqual(self.rule.check_library_root("lib", home=str(self.home)), (None, M_ABS))
        self.assertEqual(self.rule.check_library_root("../lib", home=str(self.home)), (None, M_DOTDOT))


FIRST_RUN_START = "componentize: no component library is set up yet."


def load_plugin(name):
    """The real plugin package, loaded under its own module name."""
    from importlib import import_module
    if name in sys.modules:
        return sys.modules[name], import_module(name + ".bridge"), import_module(name + ".library_setup")
    spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod, import_module(name + ".bridge"), import_module(name + ".library_setup")


class _Ctx:
    def __init__(self, settings):
        self.settings = dict(settings)
        self.saved = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def set_config(self, key, value):
        self.saved[key] = value

    def register_cli_command(self, **kw):
        self.cli = kw

    def register_command(self, **kw):
        self.slash = kw

    def register_skill(self, **kw):
        pass


class SurfaceParityTests(ParityBase):
    """Every row through the real command handler, not only the shared function.

    Surfaces: the settings page, `componentize init <folder>`, `componentize status
    --tools-root <folder>`, and a saved library_root read back by `library` and `status`.
    Only text can be typed on a command line or saved by hand, so rows whose input is
    not a string (None, 5, a list, a mapping) are checked on the page only.
    """

    def setUp(self):
        super().setUp()
        self.plugin, self.bridge, self.plib = load_plugin("componentize_plugin_surface_parity")

    def handler(self, argv, settings=None):
        """Run the registered `hermes componentize` handler; return (init roots, bridge roots, inspected, out, code)."""
        import argparse
        import contextlib
        import io
        ctx = _Ctx(settings or {})
        self.plugin.register(ctx)
        made, ran, inspected = [], [], []

        def fake_init(root, forbidden=()):
            made.append(root)
            return {"ok": True, "status": "pass", "error": None, "findings": [], "root": root, "kit": "x", "items": []}

        def fake_inspect(root, forbidden=()):
            inspected.append(root)
            return {"ok": True, "status": "pass", "error": None, "findings": [], "root": root, "kit": "x", "items": []}

        def fake_run(argv, **kw):
            ran.append(kw.get("tools_root"))
            return {"exit": 0, "stdout": "", "stderr": ""}

        parser = argparse.ArgumentParser()
        ctx.cli["setup_fn"](parser)
        tools_root = None
        if argv[:1] == ["--tools-root"]:
            tools_root, argv = argv[1], argv[2:]
        ns = parser.parse_args([])
        ns.componentize_argv, ns.tools_root = list(argv), tools_root
        out = io.StringIO()
        old = os.getcwd()
        os.chdir(self.cwd)
        try:
            with mock.patch.object(self.bridge, "run", side_effect=fake_run), \
                    mock.patch.object(self.plib, "init_library", side_effect=fake_init), \
                    mock.patch.object(self.plib, "inspect_library", side_effect=fake_inspect), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = ctx.cli["handler_fn"](ns)
        finally:
            os.chdir(old)
        return made, ran, inspected, out.getvalue(), code

    def typed_rows(self):
        return [(raw, expected) for raw, expected in self.table() if isinstance(raw, str)]

    def test_init_with_a_folder_agrees_with_the_page(self):
        for raw, expected in self.typed_rows():
            with self.subTest(raw=raw):
                made, ran, _i, out, code = self.handler(["init", raw])
                self.assertEqual(ran, [], out)
                page_path, page_msg = self.page(self.page_input(raw))
                if expected is UNSET:
                    # The page clears the setting; init needs a folder to create. An
                    # empty argument is "no folder given" (the first-run help), blank
                    # text is the empty-folder sentence.
                    self.assertEqual((page_path, page_msg), ("", None))
                    self.assertEqual(made, [], out)
                    self.assertEqual(code, 2)
                    if raw == "":
                        self.assertTrue(out.startswith(FIRST_RUN_START), out)
                    else:
                        self.assertEqual(out, f"componentize init: {M_EMPTY}\n")
                elif expected.startswith("/"):
                    self.assertEqual((page_path, page_msg), (expected, None))
                    self.assertEqual(made, [expected], out)
                    self.assertEqual(code, 0, out)
                else:
                    self.assertEqual((page_path, page_msg), (None, expected))
                    self.assertEqual(made, [], out)
                    self.assertEqual((out, code), (f"componentize init: {expected}\n", 2))

    def test_tools_root_agrees_with_the_page(self):
        for raw, expected in self.typed_rows():
            with self.subTest(raw=raw):
                made, ran, _i, out, code = self.handler(["--tools-root", raw, "status"])
                self.assertEqual(made, [], out)
                page_path, page_msg = self.page(self.page_input(raw))
                if expected is UNSET:
                    # The page clears the setting; an explicit --tools-root needs a folder.
                    self.assertEqual((page_path, page_msg), ("", None))
                    self.assertEqual((ran, out, code), ([], f"componentize: --tools-root: {M_EMPTY}\n", 2))
                elif expected.startswith("/"):
                    self.assertEqual((page_path, page_msg), (expected, None))
                    self.assertEqual((ran, code), ([expected], 0), out)
                else:
                    self.assertEqual((page_path, page_msg), (None, expected))
                    self.assertEqual((ran, out, code), ([], f"componentize: --tools-root: {expected}\n", 2))

    def test_saved_value_is_judged_raw_like_the_page(self):
        # A saved value has no working folder: it is sent to the page exactly as saved.
        for raw, _expected in self.typed_rows():
            if not raw.strip():
                continue  # the page saves no folder; the commands then show the first-run help
            with self.subTest(raw=raw):
                page_path, page_msg = self.page(raw)
                settings = {"library_root": raw}
                made, _r, inspected, lib_out, lib_code = self.handler(["library"], settings)
                _m, ran, _i, st_out, st_code = self.handler(["status"], settings)
                self.assertEqual(made, [])
                if page_msg is None:
                    self.assertEqual((inspected, lib_code), ([page_path], 0), lib_out)
                    self.assertEqual((ran, st_code), ([page_path], 0), st_out)
                else:
                    self.assertEqual((inspected, lib_out, lib_code), ([], f"componentize library: library_root setting: {page_msg}\n", 2))
                    self.assertEqual((ran, st_out, st_code), ([], f"componentize: library_root setting: {page_msg}\n", 2))

    def test_typed_dotdot_is_refused_before_it_is_normalised(self):
        # These all normalise to a safe folder; each is still refused on every surface.
        (self.cwd / "inner").mkdir()
        H = str(self.home)
        for raw in ("~/a/../components", "../work/lib", "inner/../lib", H + "/a/../components"):
            with self.subTest(raw=raw):
                made, _r, _i, out, code = self.handler(["init", raw])
                self.assertEqual((made, out, code), ([], f"componentize init: {M_DOTDOT}\n", 2))
                _m, ran, _i, out, code = self.handler(["--tools-root", raw, "status"])
                self.assertEqual((ran, out, code), ([], f"componentize: --tools-root: {M_DOTDOT}\n", 2))
                made, _r, _i, out, code = self.handler(["init", "--tools-root", raw])
                self.assertEqual((made, out, code), ([], f"componentize init: {M_DOTDOT}\n", 2))


if __name__ == "__main__":
    unittest.main()
