"""The library folder handed to the receipt CLI is resolved once, checked, and passed on as is.

A saved ``library_root`` (or a typed ``--tools-root``) is resolved one time. The plugin's
library rule decides on that resolved string together with the string it came from, and
the bridge gives the child exactly that string. A symlink retargeted at any moment, or
``--tools-root ~``, therefore either gets one plain refusal line with nothing written, or
the job state lands in the folder that was checked. The conftest pins HOME, HERMES_HOME
and the temp folder inside pytest's tmp_path, so the "home" here is a throwaway folder.
"""
import importlib
import json
import os
import sys
import threading
from pathlib import Path

import pytest

from test_kit_commands import ROOT, Context, load_from

PKG = 'componentize_plugin_tools_root_test'


def _flip(link, target):
    tmp = str(link) + '.flip'
    os.symlink(target, tmp)
    os.replace(tmp, link)


def _entries(path):
    out = []
    for dirpath, dirnames, filenames in os.walk(path):
        for name in dirnames + filenames:
            out.append(os.path.relpath(os.path.join(dirpath, name), path))
    return sorted(out)


@pytest.fixture
def layout(tmp_path, pinned_home_and_tmp):
    home = Path(pinned_home_and_tmp['HOME'])
    (home / 'sub').mkdir()
    work = tmp_path / 'w'
    safe = work / 'safe'
    (safe / 'sub').mkdir(parents=True)
    lnk = work / 'lnk'
    os.symlink(safe, lnk)
    job = tmp_path / 'job'
    job.mkdir()
    (job / 'comp.py').write_text('def f():\n    return 1\n')
    (job / 'run.py').write_text('import comp\nprint(comp.f())\n')
    (job / 'old.py').write_text('print(1)\n')
    (job / 't.py').write_text('pass\n')
    manifest = {
        'schema_version': 1, 'name': 'toy_job', 'kind': 'component',
        'attest': {'kind_reason': 'Splitting legacy script into pure component and thin runner.',
                   'reuse': 'Audited the index and added comp.f to it for reuse.'},
        'files': [{'path': str(job / 'comp.py'), 'role': 'component'},
                  {'path': str(job / 'run.py'), 'role': 'runner'}],
        'inputs': [str(job / 't.py')],
        'original': str(job / 'old.py'),
        'golden': {'argv': [sys.executable, str(job / 'run.py')], 'cwd': str(job),
                   'env': {'PATH': os.environ.get('PATH', '')}, 'compare': ['stdout', 'stderr', 'rc'],
                   'isolated': True},
        'tests': {'argv': [sys.executable, str(job / 't.py')], 'cwd': str(job)},
    }
    (job / 'm.json').write_text(json.dumps(manifest))
    plugin, _lib, _kit = load_from(ROOT, pkg_name=PKG)
    rule = importlib.import_module(PKG + '.settings_rule')
    bridge = importlib.import_module(PKG + '.bridge')
    return {'home': home, 'work': work, 'safe': safe, 'lnk': lnk, 'job': job,
            'plugin': plugin, 'rule': rule, 'bridge': bridge}


def _slash(env, settings, line):
    ctx = Context(settings=settings)
    env['plugin'].register(ctx)
    cwd = os.getcwd()
    os.chdir(env['job'])
    try:
        return ctx.calls['slash']['handler'](line)
    finally:
        os.chdir(cwd)


def _begin(env, settings, extra=''):
    return _slash(env, settings, f"{extra}begin toy_job --manifest {env['job'] / 'm.json'}")


def _assert_plain_refusal(out):
    head, _, body = out.partition('\n')
    assert head == 'componentize exit=2', out
    lines = body.strip().splitlines()
    assert len(lines) == 1, out
    assert lines[0].startswith('componentize: '), out
    assert 'Traceback' not in out and 'Errno' not in out, out


def test_control_writes_job_state_in_the_checked_folder(layout):
    out = _begin(layout, {'library_root': str(layout['lnk'] / 'sub')})
    assert out.startswith('componentize exit=0\n'), out
    assert (layout['safe'] / 'sub/.componentize/toy_job/lock').exists()
    assert _entries(layout['home']) == ['.hermes', 'sub']


@pytest.mark.parametrize('spelling', ['~', '{home}', '{home}/', '--tools-root={home}'])
def test_typed_tools_root_home_is_refused_and_writes_nothing(layout, spelling):
    home = str(layout['home'])
    value = spelling.format(home=home)
    extra = f'{value} ' if value.startswith('--') else f'--tools-root {value} '
    out = _begin(layout, {'library_root': str(layout['safe'] / 'sub')}, extra=extra)
    _assert_plain_refusal(out)
    assert _entries(layout['home']) == ['.hermes', 'sub']
    assert not (layout['safe'] / 'sub/.componentize').exists()


def test_typed_tools_root_through_a_link_into_home_is_refused(layout):
    _flip(layout['lnk'], layout['home'])
    out = _begin(layout, {}, extra=f"--tools-root {layout['lnk'] / 'sub'} ")
    _assert_plain_refusal(out)
    assert _entries(layout['home']) == ['.hermes', 'sub']


@pytest.mark.parametrize('after_call', [1, 2, 3])
def test_link_retargeted_after_each_check_never_writes_into_home(layout, after_call):
    """The race from the review: the link moves into home right after a check returns."""
    rule = layout['rule']
    real = rule.check_library_root
    calls = {'n': 0}

    def wrapped(*args, **kwargs):
        result = real(*args, **kwargs)
        calls['n'] += 1
        if calls['n'] == after_call:
            _flip(layout['lnk'], layout['home'])
        return result

    seen = []
    real_run = layout['bridge'].run

    def run(argv, **kwargs):
        seen.append(kwargs.get('tools_root'))
        return real_run(argv, **kwargs)

    rule.check_library_root = wrapped
    layout['bridge'].run = run
    try:
        out = _begin(layout, {'library_root': str(layout['lnk'] / 'sub')})
    finally:
        rule.check_library_root = real
        layout['bridge'].run = real_run
    assert _entries(layout['home']) == ['.hermes', 'sub'], out
    if out.startswith('componentize exit=2\n'):
        _assert_plain_refusal(out)
        assert not seen, out
    else:
        assert out.startswith('componentize exit=0\n'), out
        assert (layout['safe'] / 'sub/.componentize/toy_job/lock').exists(), out
        # The child got the resolved folder, not the link.
        assert seen == [str(layout['safe'] / 'sub')], seen


def test_swap_loop_never_puts_job_state_in_home(layout):
    stop = threading.Event()
    targets = (layout['safe'], layout['home'])

    def racer():
        index = 0
        while not stop.is_set():
            _flip(layout['lnk'], targets[index % 2])
            index += 1

    thread = threading.Thread(target=racer, daemon=True)
    thread.start()
    outcomes = {'ok': 0, 'refused': 0}
    try:
        for _ in range(12):
            job_state = layout['safe'] / 'sub/.componentize'
            if job_state.exists():
                import shutil
                shutil.rmtree(job_state)
            out = _begin(layout, {'library_root': str(layout['lnk'] / 'sub')})
            assert _entries(layout['home']) == ['.hermes', 'sub'], out
            if out.startswith('componentize exit=0\n'):
                outcomes['ok'] += 1
                assert (job_state / 'toy_job/lock').exists(), out
            else:
                outcomes['refused'] += 1
                _assert_plain_refusal(out)
    finally:
        stop.set()
        thread.join()
    assert sum(outcomes.values()) == 12, outcomes
