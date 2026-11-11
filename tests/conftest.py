"""Shared test isolation: pin HOME, HERMES_HOME and the temp folder for every test.

The componentize CLI refuses a state root that is the home folder or inside a Hermes
home. On some machines the ambient TMPDIR lives inside the real ~/.hermes, so tests that
create temp projects and run the CLI would depend on where they happen to run. Each test
gets a fresh layout under one throwaway root:

    HOME         a folder named "home" (sibling of tmp, so never an ancestor of test folders)
    HERMES_HOME  a ".hermes" folder inside that HOME (does not contain the tmp folder)
    TMPDIR       a folder named "tmp" (also tempfile.tempdir; where tests make projects)

Tests that need a specific HOME/HERMES_HOME still override these themselves.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_ENV_KEYS = ('HOME', 'HERMES_HOME', 'TMPDIR', 'TEMP', 'TMP', 'USERPROFILE')


def pinned_layout(root: Path) -> dict[str, str]:
    """Create the home / Hermes home / temp layout under root and return the env pins."""
    home = root / 'home'
    hermes = home / '.hermes'
    tmp = root / 'tmp'
    for path in (hermes, tmp):
        path.mkdir(parents=True, exist_ok=True)
    return {'HOME': str(home), 'USERPROFILE': str(home), 'HERMES_HOME': str(hermes),
            'TMPDIR': str(tmp), 'TEMP': str(tmp), 'TMP': str(tmp)}


@pytest.fixture(autouse=True)
def pinned_home_and_tmp(tmp_path_factory):
    # The root comes from pytest's base temp, resolved before the pins (so tmp_path keeps
    # working). Its ambient location is irrelevant: the pins below replace every
    # HOME/HERMES_HOME the product rule consults.
    root = tmp_path_factory.mktemp('pinned').resolve()
    saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
    saved_tempdir = tempfile.tempdir
    saved_cwd = os.getcwd()
    pins = pinned_layout(root)
    os.environ.update(pins)
    tempfile.tempdir = pins['TMPDIR']
    try:
        yield pins
    finally:
        os.chdir(saved_cwd)
        tempfile.tempdir = saved_tempdir
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
