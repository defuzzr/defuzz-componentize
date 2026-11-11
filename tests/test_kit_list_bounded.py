"""`kit list` / `kit show` JSON stays bounded and flat whatever is on disk.

Another plugin reads this output. Each listed kit is one flat object whose id follows
the kit id rule (lowercase kebab-case, at most 64 characters) and whose text fields are
capped. A malformed kit definition on disk (bad JSON, deep nesting, a huge file, an
engine error, an id outside the rule) makes only that kit absent, with one notice.
Every case uses a kits folder under the per-test temp folder.
"""
import json
import os
import re
import shutil
import unittest.mock
from pathlib import Path

import pytest

from test_kit_commands import ROOT, load_from

PKG = 'componentize_plugin_kit_list_bounded_test'
ID_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
KIT_KEYS = {'id', 'name', 'summary', 'tags', 'version', 'availability'}
FINDING_KEYS = {'id', 'severity', 'detail', 'fix'}


def _depth(value):
    if isinstance(value, dict):
        return 1 + max((_depth(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_depth(v) for v in value), default=0)
    return 0


def _base_kit():
    return json.loads((ROOT / 'vendor/kits/web-app/kit.json').read_text(encoding='utf-8'))


def _put(kits_dir, folder, payload):
    path = kits_dir / folder
    path.mkdir(parents=True)
    data = payload if isinstance(payload, str) else json.dumps(payload)
    (path / 'kit.json').write_text(data, encoding='utf-8')


@pytest.fixture
def kits(tmp_path):
    _plugin, _lib, kit_mod = load_from(ROOT, pkg_name=PKG)
    kits_dir = tmp_path / 'kits'
    kits_dir.mkdir()
    shutil.copytree(ROOT / 'vendor/kits/web-app', kits_dir / 'web-app')
    with unittest.mock.patch.object(kit_mod, 'KITS_DIR', kits_dir):
        yield kit_mod, kits_dir, tmp_path


def _list(kit_mod, tmp_path, human=False):
    argv = ['list'] if human else ['list', '--json']
    res = kit_mod.run(argv, cwd=str(tmp_path), home=str(tmp_path))
    assert res['exit'] == 0, res
    assert 'Traceback' not in res['stdout'] + res['stderr']
    return res if human else json.loads(res['stdout'])


def _assert_bounded(data, kit_mod):
    assert _depth(data) <= 4, _depth(data)
    assert set(data) == {'schema', 'ok', 'status', 'error', 'findings', 'kits'}
    assert len(data['kits']) <= kit_mod.MAX_LIST_KITS
    for row in data['kits']:
        assert set(row) == KIT_KEYS, row
        assert isinstance(row['id'], str) and ID_RE.fullmatch(row['id']) and len(row['id']) <= 64, row['id'][:80]
        assert isinstance(row['name'], str) and len(row['name']) <= kit_mod.MAX_NAME_CHARS + 3
        assert isinstance(row['summary'], str) and len(row['summary']) <= kit_mod.MAX_SUMMARY_CHARS + 3
        assert isinstance(row['tags'], list) and len(row['tags']) <= kit_mod.MAX_TAGS
        assert all(isinstance(t, str) and len(t) <= kit_mod.MAX_TAG_CHARS + 3 for t in row['tags'])
        assert type(row['version']) is int
    assert len(data['findings']) <= kit_mod.MAX_FINDINGS + 1
    for finding in data['findings']:
        assert set(finding) == FINDING_KEYS, finding
        assert all(isinstance(v, str) for v in finding.values())
        assert len(finding['detail']) <= 400 and len(finding['fix']) <= 400, finding
        assert '\n' not in finding['detail']


def test_deeply_nested_kit_file_is_one_absent_kit_with_a_notice(kits):
    kit_mod, kits_dir, tmp_path = kits
    _put(kits_dir, 'deep-array', '[' * 200000 + ']' * 200000)
    _put(kits_dir, 'deep-object', '{"a":' * 100000 + '1' + '}' * 100000)
    nested = _base_kit()
    nested['id'] = 'deep-tags'
    nested['tags'] = json.loads('[' * 50 + ']' * 50)
    _put(kits_dir, 'deep-tags', nested)
    data = _list(kit_mod, tmp_path)
    _assert_bounded(data, kit_mod)
    assert [k['id'] for k in data['kits']] == ['web-app']
    assert sorted(f['detail'].split(':')[0] for f in data['findings']) == ['deep-array', 'deep-object', 'deep-tags']
    assert all(f['severity'] == 'warn' for f in data['findings'])


def test_show_of_a_deeply_nested_kit_is_a_plain_unknown_kit(kits):
    kit_mod, kits_dir, tmp_path = kits
    _put(kits_dir, 'deep-array', '[' * 200000 + ']' * 200000)
    res = kit_mod.run(['show', 'deep-array', '--json'], cwd=str(tmp_path), home=str(tmp_path))
    assert res['exit'] == 2, res
    data = json.loads(res['stdout'])
    assert data['error'].startswith("unknown kit 'deep-array'"), data
    assert 'web-app' in data['error']


def test_huge_text_fields_are_capped(kits):
    kit_mod, kits_dir, tmp_path = kits
    big = _base_kit()
    big['id'] = 'big-text'
    big['name'] = 'N' * 3000
    big['summary'] = 'S' * 300000
    big['tags'] = ['t' * 1000] * 200
    _put(kits_dir, 'big-text', big)
    data = _list(kit_mod, tmp_path)
    _assert_bounded(data, kit_mod)
    row = next(k for k in data['kits'] if k['id'] == 'big-text')
    assert row['summary'].endswith('...')
    assert len(json.dumps(data)) < 64 * 1024
    human = _list(kit_mod, tmp_path, human=True)
    assert len(human['stdout']) < 16 * 1024


def test_huge_kit_file_is_skipped_without_reading_it_whole(kits):
    kit_mod, kits_dir, tmp_path = kits
    big = _base_kit()
    big['id'] = 'huge-file'
    big['summary'] = 'x' * (kit_mod.MAX_KIT_FILE_BYTES + 10)
    _put(kits_dir, 'huge-file', big)
    data = _list(kit_mod, tmp_path)
    _assert_bounded(data, kit_mod)
    assert [k['id'] for k in data['kits']] == ['web-app']
    assert [f['detail'].split(':')[0] for f in data['findings']] == ['huge-file']


def test_id_outside_the_rule_is_absent_with_a_notice(kits):
    kit_mod, kits_dir, tmp_path = kits
    long_id = 'a' * 65
    long_kit = _base_kit()
    long_kit['id'] = long_id
    _put(kits_dir, long_id, long_kit)
    ctrl = _base_kit()
    ctrl['id'] = 'web-app\x1b[2J'
    _put(kits_dir, 'ctrl\x1b[31m-' + 'z' * 200, ctrl)
    data = _list(kit_mod, tmp_path)
    _assert_bounded(data, kit_mod)
    assert [k['id'] for k in data['kits']] == ['web-app']
    assert len(data['findings']) == 2
    assert all('\x1b' not in f['detail'] for f in data['findings'])


def test_engine_error_on_one_kit_leaves_the_others(kits):
    kit_mod, kits_dir, tmp_path = kits
    boom = _base_kit()
    boom['id'] = 'boom'
    _put(kits_dir, 'boom', boom)
    real = kit_mod.ENGINE.validate_kit

    def validate(raw):
        if isinstance(raw, dict) and raw.get('id') == 'boom':
            raise RecursionError('maximum recursion depth exceeded')
        return real(raw)

    with unittest.mock.patch.object(kit_mod.ENGINE, 'validate_kit', side_effect=validate):
        data = _list(kit_mod, tmp_path)
    _assert_bounded(data, kit_mod)
    assert [k['id'] for k in data['kits']] == ['web-app']
    assert [f['detail'].split(':')[0] for f in data['findings']] == ['boom']
    assert 'Recursion' not in data['findings'][0]['detail']


def test_kit_and_finding_counts_are_capped(kits):
    kit_mod, kits_dir, tmp_path = kits
    base = _base_kit()
    for index in range(kit_mod.MAX_LIST_KITS + 20):
        row = dict(base, id=f'k{index:04d}')
        _put(kits_dir, f'k{index:04d}', row)
    for index in range(kit_mod.MAX_FINDINGS + 20):
        _put(kits_dir, f'bad{index:04d}', '{not json')
    data = _list(kit_mod, tmp_path)
    _assert_bounded(data, kit_mod)
    assert len(data['kits']) == kit_mod.MAX_LIST_KITS
    assert data['findings'][-1]['id'] == 'kit-list-truncated'
    assert len(json.dumps(data)) < 512 * 1024


def test_shipped_kits_are_listed_unchanged():
    _plugin, _lib, kit_mod = load_from(ROOT, pkg_name=PKG)
    res = kit_mod.run(['list', '--json'], cwd=str(ROOT), home=os.environ['HOME'])
    data = json.loads(res['stdout'])
    assert data['findings'] == []
    expected = json.loads((ROOT / 'tests/contract/list.json').read_text(encoding='utf-8'))
    assert data == expected
