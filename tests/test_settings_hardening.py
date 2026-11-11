"""One timeout rule and plain errors for every settings value (routes, CLI, saved config)."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import random
import sys
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin import load  # noqa: E402
from test_settings_page_api import PREFIX, load_plugin_api  # noqa: E402

T_BAD = 'Invalid timeout: enter a finite number greater than 0.'
T_MAX = 'Invalid timeout: enter at most 86400 seconds (24 hours).'
HUGE = 10 ** 400

BAD_TIMEOUTS = [True, False, float('nan'), float('inf'), -float('inf'), 0, 0.0, -0.0, -5, '-0', '-0.0',
                'nan', 'NaN', 'inf', 'Infinity', '1_000', '0x10', '12abc', ' 12 x', '1e', '', '   ',
                [1], {'v': 1}, '\x00', '\ud800', '1' * 2000]
MAX_TIMEOUTS = ['1' * 100, 1e12, 86400.0001, 86401, HUGE, -(-HUGE), str(HUGE), '1e400', '1e999999']
GOOD_TIMEOUTS = [(1, 1.0), (0.5, 0.5), ('120', 120.0), (' 120 ', 120.0), ('1e3', 1000.0), (86400, 86400.0),
                 ('+5', 5.0), ('.5', 0.5)]


@pytest.fixture(scope='module')
def rule():
    api = load_plugin_api()
    return api._load('settings_rule')


@pytest.mark.parametrize('value', BAD_TIMEOUTS, ids=repr)
def test_timeout_rule_rejects_bad(rule, value):
    assert rule.check_timeout(value) == (None, T_BAD)


@pytest.mark.parametrize('value', MAX_TIMEOUTS, ids=lambda v: repr(v)[:30])
def test_timeout_rule_rejects_too_large(rule, value):
    assert rule.check_timeout(value) == (None, T_MAX)


@pytest.mark.parametrize('value,expected', GOOD_TIMEOUTS, ids=repr)
def test_timeout_rule_accepts(rule, value, expected):
    assert rule.check_timeout(value) == (expected, None)


def test_timeout_missing_is_default(rule):
    assert rule.check_timeout(None) == (900.0, None)


@pytest.mark.parametrize('value', [None, 5, 1.5, True, [], ['/x'], {'a': '/x'}], ids=repr)
def test_library_root_non_string(rule, value, tmp_path):
    root, err = rule.check_library_root(value, home=str(tmp_path))
    if value is None:
        assert (root, err) == ('', None)
    else:
        assert root is None and err == rule.M_ABS


@pytest.mark.parametrize('value,msg', [
    ('/srv/a\x00b', 'M_NUL'), ('/srv/a\nb', 'M_NUL'), ('/srv/a\x1bb', 'M_CTRL'), ('/srv/a\x7fb', 'M_CTRL'),
    ('/srv/a\ud800b', 'M_ENCODING'), ('/srv/' + 'a' * 5000, 'M_LONG'), ('/' + 'a' * 1100, 'M_LONG'),
], ids=lambda v: repr(v)[:20])
def test_library_root_plain_errors(rule, value, msg, tmp_path):
    assert rule.check_library_root(value, home=str(tmp_path)) == (None, getattr(rule, msg))


class _Ctx:
    def __init__(self, config):
        self.config, self.calls = config, {}

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        raise AssertionError('nothing may be saved')

    def register_cli_command(self, **kw): self.calls['cli'] = kw
    def register_command(self, **kw): self.calls['slash'] = kw
    def register_skill(self, **kw): self.calls['skill'] = kw


@pytest.mark.parametrize('saved,msg', [(HUGE, T_MAX), (str(HUGE), T_MAX), (1e12, T_MAX), ('12abc', T_BAD),
                                       (float('nan'), T_BAD), (True, T_BAD), (-0.0, T_BAD)],
                         ids=lambda v: repr(v)[:20])
def test_cli_saved_timeout_plain_one_line(saved, msg, tmp_path, monkeypatch):
    plugin = load()
    ctx = _Ctx({'timeout': saved})
    plugin.register(ctx)
    parser = argparse.ArgumentParser()
    ctx.calls['cli']['setup_fn'](parser)
    monkeypatch.chdir(tmp_path)
    import importlib
    bridge = importlib.import_module(plugin.__name__ + '.bridge')
    with mock.patch.object(bridge, 'run', side_effect=AssertionError('bridge must not run')):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ctx.calls['cli']['handler_fn'](parser.parse_args(['status']))
        assert rc == 2
        assert err.getvalue() == f'componentize: timeout setting: {msg}\n'
        assert out.getvalue() == ''
        slash = ctx.calls['slash']['handler']('status')
        assert slash == f'componentize exit=2\ncomponentize: timeout setting: {msg}\n'


# ---- routes ---------------------------------------------------------------

@pytest.fixture()
def client_and_api(tmp_path):
    api = load_plugin_api()
    for stem in ('library_setup', 'kit_commands', 'settings_rule'):
        api._load(stem)
    app = FastAPI()
    app.include_router(api.router, prefix=PREFIX)
    home = tmp_path / 'home' / 'user'
    (home / '.hermes').mkdir(parents=True)
    return TestClient(app, raise_server_exceptions=False), api, home


def _profile(api, home, settings):
    return mock.patch.object(api, '_read_profile_settings', return_value=(
        dict(settings), str(home), (str(home),), (str(home / '.hermes'),)))


def _raw_json(value):
    # json.dumps cannot write lone surrogates as UTF-8; keep them escaped.
    return json.dumps(value, ensure_ascii=True, allow_nan=True)


@pytest.mark.parametrize('timeout,msg', [(HUGE, T_MAX), (1e12, T_MAX), ('12abc', T_BAD), (True, T_BAD),
                                         (-0.0, T_BAD), ('NaN', T_BAD)], ids=lambda v: repr(v)[:20])
def test_validate_route_timeout(client_and_api, timeout, msg):
    client, api, home = client_and_api
    with _profile(api, home, {}):
        resp = client.post(PREFIX + '/settings/validate', content=_raw_json({'timeout': timeout}),
                           headers={'content-type': 'application/json'})
    assert resp.status_code == 200
    assert resp.json() == {'ok': False, 'errors': {'timeout': msg}}


@pytest.mark.parametrize('saved,msg', [(HUGE, T_MAX), (1e12, T_MAX), (str(HUGE), T_MAX), ('x', T_BAD)],
                         ids=lambda v: repr(v)[:20])
def test_status_route_timeout(client_and_api, saved, msg):
    client, api, home = client_and_api
    with _profile(api, home, {'timeout': saved}):
        resp = client.get(PREFIX + '/status')
    assert resp.status_code == 200, resp.text
    settings = resp.json()['settings']
    assert settings['timeout_valid'] is False
    assert settings['timeout'] == 900.0
    assert settings['timeout_message'] == msg


def test_status_route_surrogate_library_root(client_and_api):
    client, api, home = client_and_api
    with _profile(api, home, {'library_root': '/srv/a\ud800'}):
        resp = client.get(PREFIX + '/status')
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['settings']['library_root'] == ''
    assert body['library']['state'] == 'invalid_path'


@pytest.mark.parametrize('body', ['[]', '"x"', '1', 'null', '{"timeout": 5, "extra": 1}', '{"__proto__": 1}',
                                  '{', 'not json', '[' * 5000 + ']' * 5000, b'{"timeout": "\xff"}'],
                         ids=lambda v: repr(v)[:20])
def test_validate_route_request_errors(client_and_api, body):
    client, api, home = client_and_api
    with _profile(api, home, {}):
        resp = client.post(PREFIX + '/settings/validate', content=body, headers={'content-type': 'application/json'})
    assert resp.status_code == 400
    assert set(resp.json()) == {'error'}
    assert resp.json()['error'].startswith('Invalid request')


def test_validate_route_oversized_body(client_and_api):
    client, api, home = client_and_api
    big = json.dumps({'library_root': '/srv/' + 'a' * (api.MAX_BODY_BYTES + 10)})
    with _profile(api, home, {}):
        resp = client.post(PREFIX + '/settings/validate', content=big, headers={'content-type': 'application/json'})
        chunked = client.post(PREFIX + '/settings/validate', content=iter([big.encode()]),
                              headers={'content-type': 'application/json'})
    for r in (resp, chunked):
        assert r.status_code == 400
        assert r.json() == {'error': 'Invalid request: the request body is too large.'}


def _gen_value(rng, depth=0):
    pick = rng.randrange(16)
    if pick == 0:
        return rng.choice([10 ** rng.randrange(300, 500), -(10 ** 400), 2 ** 1024])
    if pick == 1:
        return rng.choice([float('nan'), float('inf'), -0.0, 1e308, 5e-324])
    if pick == 2:
        return rng.choice([True, False, None])
    if pick == 3:
        return rng.uniform(-1e6, 1e6)
    if pick == 4:
        return rng.randrange(-10, 200000)
    if pick == 5:
        return ''.join(chr(rng.randrange(0, 0x110000)) for _ in range(rng.randrange(0, 12)))
    if pick == 6:
        return '/' + ''.join(rng.choice('ab/.~\x00\n\x1b\ud800 é') for _ in range(rng.randrange(0, 40)))
    if pick == 7:
        return '/srv/' + 'x' * rng.choice([1000, 1100, 4097, 9000])
    if pick == 8 and depth < 3:
        return [_gen_value(rng, depth + 1) for _ in range(rng.randrange(0, 3))]
    if pick == 9 and depth < 3:
        return {rng.choice(['a', 'timeout', 'library_root']): _gen_value(rng, depth + 1)}
    if pick == 10:
        return rng.choice(['120', ' 1e3 ', '1_0', '0x1', '12abc', '-0', 'Infinity', '+.5', '1e99999', str(10 ** 400)])
    if pick == 11:
        return rng.choice(['/etc', '/', '~', '~/x', '/home', 'relative', '/srv/ok', '/usr/bin'])
    return rng.uniform(0, 100000)


def _gen_body(rng):
    kind = rng.randrange(10)
    if kind == 0:
        return _raw_json(_gen_value(rng))  # not an object (sometimes)
    if kind == 1:
        return rng.choice(['{', '{"timeout":}', '\x00', '', 'NaN', '{"timeout": 1e999}', '[' * 3000])
    body = {}
    for key in rng.sample(['library_root', 'timeout', 'extra', 'Timeout', ''], rng.randrange(0, 3)):
        body[key] = _gen_value(rng)
    if rng.random() < 0.7:
        body.setdefault('timeout', _gen_value(rng))
    if rng.random() < 0.7:
        body.setdefault('library_root', _gen_value(rng))
    return _raw_json(body)


def test_fuzz_settings_routes(client_and_api):
    client, api, home = client_and_api
    rng = random.Random(20261110)
    rule = api._load('settings_rule')
    field_messages = {
        'library_root': {v for k, v in vars(rule).items() if k.startswith('M_')},
        'timeout': {T_BAD, T_MAX},
    }
    seen = {'field': 0, 'request': 0, 'ok': 0, 'status': 0}
    for _ in range(240):
        body = _gen_body(rng)
        with _profile(api, home, {}), mock.patch.dict(os.environ, {}):
            resp = client.post(PREFIX + '/settings/validate', content=body,
                               headers={'content-type': 'application/json'})
        assert resp.status_code < 500, (body[:200], resp.text)
        data = resp.json()
        if resp.status_code == 400:
            assert set(data) == {'error'} and data['error'].startswith('Invalid request'), data
            seen['request'] += 1
        else:
            assert resp.status_code == 200
            if data['ok']:
                assert set(data) == {'ok', 'values'} and set(data['values']) == {'library_root', 'timeout'}
                assert 0 < data['values']['timeout'] <= 86400
                seen['ok'] += 1
            else:
                assert set(data) == {'ok', 'errors'} and data['errors']
                for key, msg in data['errors'].items():
                    assert msg in field_messages[key], (key, msg)
                seen['field'] += 1
        # The same value saved by hand must give a plain status, never a 500.
        try:
            saved = json.loads(body)
        except (ValueError, RecursionError):  # deep nesting raises RecursionError on Python 3.11
            continue
        if isinstance(saved, dict):
            with _profile(api, home, saved):
                st = client.get(PREFIX + '/status')
            assert st.status_code == 200, (body[:200], st.text)
            assert isinstance(st.json()['settings']['timeout_valid'], bool)
            seen['status'] += 1
    assert all(seen.values()), seen
    # Nothing is saved: the routes never write the config and no file appears in the fake home.
    assert sorted(p.name for p in home.rglob('*')) == ['.hermes']
