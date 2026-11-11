"""Static checks for the CI workflow shipped with the plugin (no network)."""
from __future__ import annotations

import re
from pathlib import Path

import yaml  # PyYAML; the CI workflow installs it

WORKFLOW = Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'ci.yml'
ALLOWED_ACTIONS = frozenset({'actions/checkout', 'actions/setup-python', 'actions/setup-node'})
_PINNED_RE = re.compile(r'^(?P<name>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@(?P<ref>[0-9a-f]{40})$')


def _text() -> str:
    return WORKFLOW.read_text(encoding='utf-8')


def _doc() -> dict:
    doc = yaml.safe_load(_text())
    assert isinstance(doc, dict)
    return doc


def _triggers(doc: dict) -> dict:
    # PyYAML (YAML 1.1) reads a bare `on:` key as the boolean True.
    on = doc.get('on', doc.get(True))
    assert on is not None, 'workflow has no triggers'
    if isinstance(on, (str, list)):
        on = {name: None for name in ([on] if isinstance(on, str) else on)}
    return on


def _steps(doc: dict):
    for job_id, job in doc['jobs'].items():
        for step in job.get('steps', []):
            yield job_id, step


def test_workflow_parses():
    doc = _doc()
    assert doc['jobs'], 'workflow defines no jobs'
    _triggers(doc)


def test_only_allowed_actions_each_pinned_to_full_sha():
    doc = _doc()
    used = []
    for job_id, step in _steps(doc):
        if 'uses' not in step:
            continue
        match = _PINNED_RE.fullmatch(str(step['uses']))
        assert match, f'{job_id}: {step["uses"]!r} is not pinned to a 40-character commit'
        assert match['name'] in ALLOWED_ACTIONS, f'{job_id}: {match["name"]} is not allowed'
        used.append(match['name'])
    assert set(used) == ALLOWED_ACTIONS
    for line in _text().splitlines():
        if 'uses:' in line:
            assert re.search(r'#\s*v\d+(\.\d+)*\s*$', line), f'no version comment: {line.strip()}'


def test_permissions_are_contents_read_only():
    doc = _doc()
    assert doc.get('permissions') == {'contents': 'read'}
    for job_id, job in doc['jobs'].items():
        assert job.get('permissions', {'contents': 'read'}) == {'contents': 'read'}, job_id


def test_no_sudo_secrets_or_pull_request_target():
    doc = _doc()
    text = _text()
    assert 'pull_request_target' not in _triggers(doc)
    assert 'pull_request_target' not in text
    assert 'secrets.' not in text
    assert not re.search(r'\bsudo\b', text)
    for job_id, step in _steps(doc):
        assert 'sudo' not in str(step.get('run', '')).split(), job_id


def test_every_job_has_a_timeout():
    doc = _doc()
    for job_id, job in doc['jobs'].items():
        timeout = job.get('timeout-minutes')
        assert isinstance(timeout, int) and 0 < timeout <= 60, f'{job_id}: timeout {timeout!r}'


def test_superseded_runs_are_cancelled():
    concurrency = _doc().get('concurrency')
    assert isinstance(concurrency, dict) and concurrency.get('cancel-in-progress') is True


def test_matrix_is_linux_only_on_both_pythons():
    doc = _doc()
    matrices = [job['strategy']['matrix'] for job in doc['jobs'].values() if 'strategy' in job]
    assert any(m.get('os') == ['ubuntu-latest']
               and m.get('python-version') == ['3.13', '3.14'] for m in matrices)
    text = _text()
    assert '3.11' not in text and '3.12' not in text
    for m in matrices:
        assert m.get('os', ['ubuntu-latest']) == ['ubuntu-latest'], m
    for job_id, job in doc['jobs'].items():
        runs_on = job.get('runs-on')
        assert runs_on in ('ubuntu-latest', '${{ matrix.os }}'), (job_id, runs_on)
        assert job.get('continue-on-error', False) is False, job_id


def test_no_macos_or_windows_runner_anywhere():
    text = _text().lower()
    for word in ('macos', 'windows', 'osx'):
        assert word not in text, word


def test_runs_whole_plugin_suite_and_desktop_tests_on_node_24():
    doc = _doc()
    runs = ' \n'.join(str(step.get('run', '')) for _, step in _steps(doc))
    assert 'python -B -m pytest tests -q -p no:cacheprovider' in runs
    assert 'node --import ./desktop/tests/sdk-shim.mjs --test desktop/tests/*.test.mjs' in runs
    node_versions = [str(step.get('with', {}).get('node-version'))
                     for _, step in _steps(doc) if str(step.get('uses', '')).startswith('actions/setup-node@')]
    assert node_versions == ['24']
