"""Receipt state protection survives the bridge's reduced environment."""
import importlib
import os
from types import SimpleNamespace

import pytest

from test_tools_root_pinned import layout  # shared throwaway job fixture


def _begin(layout, env, state):
    return layout['bridge'].run(
        ['--state-root', str(state), 'begin', 'toy_job', '--manifest',
         str(layout['job'] / 'm.json')],
        cwd=layout['job'], tools_root=layout['safe'] / 'sub', env=env)


def test_variable_home_refuses_state_before_environment_reduction(layout, tmp_path):
    hermes = tmp_path / 'srv' / 'private-profile'
    hermes.mkdir(parents=True)
    state = hermes / 'receipts'
    env = dict(os.environ, HERMES_HOME=str(tmp_path / 'srv' / '$PROFILE_DIR'),
               PROFILE_DIR='private-profile')
    out = _begin(layout, env, state)
    assert out['exit'] == 2, out
    assert 'Hermes' in out['stderr'], out
    assert not state.exists()


@pytest.mark.parametrize('spelling', ['absolute', 'tilde', 'unset', 'braced', 'relative'])
@pytest.mark.parametrize('inside', [True, False])
def test_home_forms_preserve_state_boundary(layout, tmp_path, monkeypatch, spelling, inside):
    env = dict(os.environ)
    hermes = layout['home'] / '.hermes'
    if spelling == 'unset':
        env.pop('HERMES_HOME', None)
    elif spelling == 'tilde':
        env['HERMES_HOME'] = '~/.hermes'
    elif spelling == 'braced':
        env.update(HERMES_HOME='${PROFILE_HOME}', PROFILE_HOME=str(hermes))
    elif spelling == 'relative':
        monkeypatch.chdir(layout['home'])
        env['HERMES_HOME'] = '.hermes'
    else:
        env['HERMES_HOME'] = str(hermes)
    state = (hermes if inside else tmp_path) / 'receipts'
    out = _begin(layout, env, state)
    assert out['exit'] == (2 if inside else 0), out
    if inside:
        assert 'Hermes' in out['stderr'], out
        assert not state.exists()
    else:
        assert (state / 'toy_job' / 'lock').exists(), out


def test_tilde_without_home_uses_parent_account_home(layout, monkeypatch):
    runtime = importlib.import_module(layout['bridge'].__package__ + '._kit_runtime')
    monkeypatch.setattr(runtime.pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_dir=str(layout['home'])))
    env = dict(os.environ, HERMES_HOME='~/.hermes')
    env.pop('HOME', None)
    child = runtime.reduced_child_env(env)
    assert child['HERMES_HOME'] == str(layout['home'] / '.hermes')
    assert 'HOME' not in child
    state = layout['home'] / '.hermes' / 'receipts'
    out = _begin(layout, env, state)
    assert out['exit'] == 2, out
    assert 'Hermes' in out['stderr'], out
    assert not state.exists()


@pytest.mark.parametrize('raw', ['/srv/$MISSING_PROFILE', '~nonexistent_componentize_test_user/x', '${NESTED}'])
def test_unresolvable_home_refuses_without_writes(layout, tmp_path, raw):
    env = dict(os.environ, HERMES_HOME=raw, NESTED='$MISSING_PROFILE')
    env.pop('MISSING_PROFILE', None)
    state = tmp_path / 'receipts'
    out = _begin(layout, env, state)
    assert out == {'exit': 2, 'stdout': '',
                   'stderr': 'componentize: cannot resolve HERMES_HOME to an absolute path\n'}
    assert not state.exists()


def test_parent_mapping_is_used_without_widening_allowlist(layout, tmp_path, monkeypatch):
    runtime = importlib.import_module(layout['bridge'].__package__ + '._kit_runtime')
    monkeypatch.setenv('PROFILE_DIR', 'wrong-ambient-profile')
    env = dict(os.environ, HERMES_HOME=str(tmp_path / '$PROFILE_DIR'), PROFILE_DIR='private-profile',
               SECRET_TOKEN='not-for-child')
    before = dict(env)
    child = runtime.reduced_child_env(env)
    assert child['HERMES_HOME'] == str(tmp_path / 'private-profile')
    assert 'PROFILE_DIR' not in child and 'SECRET_TOKEN' not in child
    assert set(child) <= {'PATH', 'HOME', 'HERMES_HOME', 'TMPDIR', 'TEMP', 'TMP'}
    assert env == before
