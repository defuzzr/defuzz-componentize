"""Run the bundled CLI without importing its packages into Hermes."""
import importlib.util
import math
from pathlib import Path
import sys

from ._kit_runtime import reduced_child_env

ROOT = Path(__file__).resolve().parent


def run(argv, *, cwd, env, timeout=900, tools_root=None):
    """Preserve argv tokens and 0/1/2; operational bridge errors are exit 2.

    tools_root is the library folder the caller already resolved and checked.
    It is passed to the child unchanged, never resolved again here, so the
    child works in the folder that was checked. When it is omitted the current
    folder is resolved once and used.
    """
    try:
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be positive and finite')
    except (ValueError, TypeError) as exc:
        return {'exit': 2, 'stdout': '', 'stderr': f'componentize: {exc}\n'}
    spec = importlib.util.spec_from_file_location('_componentize_bridge_io', ROOT / 'vendor/componentize/_io.py')
    assert spec is not None and spec.loader is not None
    io = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(io)
    # -I ignores PYTHONPATH/user site; vendor CLI adds only its own package root.
    command = [sys.executable, '-I', '-B', str(ROOT / 'vendor/componentize/componentize.py'),
               '--tools-root', str(tools_root) if tools_root else str(Path(cwd).resolve()), *argv]
    try:
        child_env = reduced_child_env(env)
    except ValueError as exc:
        return {'exit': 2, 'stdout': '', 'stderr': f'componentize: {exc}\n'}
    result = io.run_argv(command, cwd=str(cwd), env=child_env, timeout=timeout)
    failed = result['error'] or result['timed_out']
    code = result['rc'] if not failed and result['rc'] in (0, 1, 2) else 2
    stderr = result['stderr']
    if failed:
        stderr += f"\ncomponentize bridge: {result['error']}\n"
    return {'exit': code, 'stdout': result['stdout'], 'stderr': stderr}
