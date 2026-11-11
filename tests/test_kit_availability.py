"""Unavailable kits remain discoverable but never touch a target."""
import importlib.util
import json
from pathlib import Path

import pytest
from test_kit_commands import ROOT, load_from


@pytest.mark.parametrize('value,valid', [('available', True), ('coming_soon', True), ('soon', False), ('', False), (None, False), ([], False), (1, False)])
def test_availability_validator(value, valid, future_kit):
    # Exercise the original before refreshing the pinned vendor copy.
    source = ROOT.parents[1] / 'hermes_audit/project_kit.py'
    if not source.exists():
        source = ROOT / 'vendor/hermes_audit/project_kit.py'
    spec = importlib.util.spec_from_file_location('availability_engine', source)
    assert spec is not None and spec.loader is not None
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    kit = json.loads((future_kit.KITS_DIR / 'future-kit/kit.json').read_text())
    kit['availability'] = value
    result = engine.validate_kit(kit)
    assert result['ok'] is valid
    if not valid:
        assert result['findings'][0]['detail'] == 'availability: must be available or coming_soon'


@pytest.fixture
def future_kit(tmp_path, monkeypatch):
    _, _, kit = load_from(ROOT)
    folder = tmp_path / 'kits' / 'future-kit'
    folder.mkdir(parents=True)
    manifest = {
        'schema_version': 1, 'id': 'future-kit', 'version': 1,
        'name': 'Future kit', 'summary': 'Planned synthetic kit.',
        'availability': 'coming_soon',
        'dirs': [{'path': 'src', 'summary': 'Synthetic sources.'}],
        'agents': {'purpose': ['Synthetic future project.']},
    }
    (folder / 'kit.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(kit, 'KITS_DIR', folder.parent)
    return kit


def test_future_discovery(tmp_path, future_kit):
    kit = future_kit
    args = dict(cwd=str(tmp_path), home=str(tmp_path / 'home'))
    rows = json.loads(kit.run(['list', '--json'], **args)['stdout'])['kits']
    row = next(r for r in rows if r['id'] == 'future-kit')
    assert row['availability'] == 'coming_soon'
    assert 'future-kit' in kit.run(['list'], **args)['stdout']
    assert '[coming soon]' in kit.run(['list'], **args)['stdout']
    shown = kit.run(['show', 'future-kit'], **args)
    assert shown['exit'] == 0
    assert 'Planned synthetic kit.' in shown['stdout']
    assert 'Synthetic future project.' in shown['stdout']


@pytest.mark.parametrize('command', ['apply', 'check'])
@pytest.mark.parametrize('flags', [[], ['--json']])
def test_future_refused_without_writes(tmp_path, future_kit, command, flags):
    kit = future_kit
    target = tmp_path / 'missing' / 'project'
    result = kit.run([command, 'future-kit', str(target), *flags], cwd=str(tmp_path), home=str(tmp_path / 'home'))
    assert result['exit'] != 0
    assert result['stdout'] + result['stderr'] == 'future-kit is coming soon and cannot be applied yet.\n'
    assert not target.parent.exists()
    assert not (tmp_path / 'home').exists()
