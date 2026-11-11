"""The plugin refuses to register on any system other than Linux."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _load():
    name = 'defuzz_componentize_linux_only_probe'
    spec = importlib.util.spec_from_file_location(name, ROOT / '__init__.py',
                                                  submodule_search_locations=[str(ROOT)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_non_linux_registers_nothing(capsys):
    module = _load()
    for fake in ('darwin', 'win32'):
        ctx = mock.MagicMock()
        with mock.patch.object(sys, 'platform', fake):
            assert module.register(ctx) is None
        assert ctx.method_calls == [], fake
        assert 'defuzz-componentize supports Linux only.' in capsys.readouterr().err


def test_old_python_registers_nothing(capsys):
    module = _load()
    for fake in ((3, 11, 9, 'final', 0), (3, 12, 7, 'final', 0)):
        ctx = mock.MagicMock()
        with mock.patch.object(sys, 'platform', 'linux'), mock.patch.object(sys, 'version_info', fake):
            assert module.register(ctx) is None
        assert ctx.method_calls == [], fake
        assert 'defuzz-componentize needs Python 3.13 or newer.' in capsys.readouterr().err


def test_supported_python_registers(capsys):
    module = _load()
    for fake in ((3, 13, 0, 'final', 0), (3, 14, 0, 'final', 0)):
        ctx = mock.MagicMock()
        ctx.get_config.return_value = ''
        with mock.patch.object(sys, 'platform', 'linux'), mock.patch.object(sys, 'version_info', fake):
            module.register(ctx)
        assert ctx.method_calls, fake
        assert 'needs Python' not in capsys.readouterr().err


def test_module_parses_as_python_311():
    """The guard must be reachable on 3.11, so the module source must parse there."""
    import ast
    ast.parse((ROOT / '__init__.py').read_text(encoding='utf-8'), feature_version=(3, 11))
