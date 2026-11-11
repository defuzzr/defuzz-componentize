"""Commands-only Hermes adapter; registration has no subprocess side effects."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN_NAME = "defuzz-componentize"
VERSION = "0.2.0"

# Commands that read or write receipt state and so need a component library.
LIBRARY_COMMANDS = frozenset({'begin', 'attest', 'check', 'finish', 'status', 'rebaseline'})
VALUE_FLAGS = ('--tools-root', '--state-root')
DEFAULT_LIBRARY = '~/component-library'
FIRST_RUN = (
    'componentize: no component library is set up yet.\n'
    'A component library is one folder that holds your reusable components, their index and the rules agents follow.\n'
    'Pick an existing folder or a new one, then run:\n'
    '  hermes componentize init /absolute/folder\n'
    'To use a different library for one command, pass --tools-root /absolute/folder.\n'
)
NO_CWD = ('componentize: the current folder no longer exists; '
          'change to an existing folder and run the command again\n')


def _home():
    """Absolute home folder, or None when HOME is empty or the user has no home entry."""
    import os
    if os.environ.get('HOME') == '':
        return None
    home = os.path.expanduser('~')
    return home if os.path.isabs(home) else None


def split_argv(argv):
    """Return (subcommand, explicit --tools-root value, positionals after the subcommand)."""
    command, tools_root, rest, skip = None, None, [], False
    for index, token in enumerate(argv):
        if skip:
            skip = False
            continue
        if token in VALUE_FLAGS:
            skip = True
            if token == '--tools-root' and index + 1 < len(argv):
                tools_root = argv[index + 1]
            continue
        if token.startswith('--tools-root='):
            tools_root = token.split('=', 1)[1]
            continue
        if token.startswith('-'):
            continue
        if command is None:
            command = token
        else:
            rest.append(token)
    return command, tools_root, rest


def _has_leading_version(argv):
    skip = False
    for token in argv:
        if skip:
            skip = False
            continue
        if token == '--version':
            return True
        if token in VALUE_FLAGS:
            skip = True
            continue
        if token == '--json' or any(token.startswith(flag + '=') for flag in VALUE_FLAGS):
            continue
        return False
    return False


def _kit_argv(argv):
    out, dropped, skip = [], False, False
    for token in argv:
        if skip:
            skip = False
            continue
        if token in VALUE_FLAGS:
            skip = True
            continue
        if any(token.startswith(flag + '=') for flag in VALUE_FLAGS):
            continue
        if not dropped and token == 'kit':
            dropped = True
            continue
        out.append(token)
    return out


def _strip_tools_root(argv):
    """argv without any --tools-root value; the checked folder is passed separately."""
    out, skip = [], False
    for token in argv:
        if skip:
            skip = False
            continue
        if token == '--tools-root':
            skip = True
            continue
        if token.startswith('--tools-root='):
            continue
        if token in VALUE_FLAGS:
            skip = True
        out.append(token)
    return out


def _pin_folder(raw, *, home, cwd=None, typed=False):
    """Resolve a library folder once and check that resolved value.

    Returns (resolved_path, None), (None, None) when unset, or (None, plain message).
    The library rule decides on the resolved string and on the string it came
    from, so a symlink retargeted after the first look is judged by where it
    points now. Callers must hand the returned string on unchanged.
    """
    from ._kit_runtime import _decide, resolve_home, resolved_target
    from . import library_setup, settings_rule
    if typed:
        # The settings page's rule and sentences, with a relative folder taken from
        # cwd; a ".." segment is refused before anything is joined or normalized.
        norm, err = library_setup.check_typed_folder(raw, cwd=cwd, home=home)
    else:
        # A saved value is judged as it was saved, like the page judges it.
        norm, err = settings_rule.check_library_root(raw, home=home)
    if err is not None:
        return None, err
    if not norm:
        return None, None
    try:
        resolved = resolved_target(norm)
    except (OSError, ValueError):
        return None, 'the folder could not be resolved; check that it exists and try again'
    checked, err = settings_rule.check_library_root(resolved, home=home)
    if err is not None:
        return None, err
    safe_home, home_err = resolve_home(home)
    if home_err:
        return None, home_err
    reason = _decide(norm, checked, safe_home, None)
    if reason:
        return None, settings_rule._refusal(reason)
    return checked, None


LINUX_ONLY_MESSAGE = 'defuzz-componentize supports Linux only.'
PYTHON_TOO_OLD_MESSAGE = 'defuzz-componentize needs Python 3.13 or newer.'


def register(ctx):
    import sys
    if not sys.platform.startswith('linux'):
        print(LINUX_ONLY_MESSAGE, file=sys.stderr)
        return
    if sys.version_info < (3, 13):
        print(PYTHON_TOO_OLD_MESSAGE, file=sys.stderr)
        return
    def configured_library():
        value = ctx.get_config('library_root', '')
        return value if isinstance(value, str) and value.strip() else None

    def library(argv, *, cwd, home, ask=None):
        """Handle init/library inside the plugin; the vendored CLI has no such commands."""
        import json
        import os
        import stat
        from ._kit_runtime import exit_code, format_report, resolved_target
        from . import library_setup
        command, explicit, rest = split_argv(argv)
        chosen = explicit or (rest[0] if rest else None)
        typed = chosen is not None
        if command == 'library':
            chosen = chosen or configured_library()
        elif chosen is None and ask is not None:
            chosen = ask()
            typed = chosen is not None
        if not chosen:
            return {'exit': 2, 'stdout': '', 'stderr': FIRST_RUN}
        # Same rule and sentences as the settings page: init saves this folder as
        # library_root. A folder typed on the command line may be relative to where
        # the command runs; the saved setting must be absolute. Either way a ".."
        # segment is refused before anything is joined or normalized.
        if typed:
            root, dir_error = library_setup.check_typed_folder(chosen, cwd=cwd, home=home)
        else:
            # A saved value is judged as it was saved, and the message names the setting.
            from .settings_rule import M_EMPTY, check_library_root
            root, dir_error = check_library_root(chosen, home=home)
            if dir_error is not None or not root:
                dir_error = f'library_root setting: {dir_error or M_EMPTY}'
        if dir_error is not None or not root:
            return {'exit': 2, 'stdout': '', 'stderr': f'componentize {command}: {dir_error}\n'}
        # init: no second resolution here; library_setup verifies the target once and
        # creates from that verified folder. Only the read-only `library` view resolves.
        if command == 'library' and dir_error is None and root is not None:
            try:
                final_link = os.path.lexists(root) and stat.S_ISLNK(os.lstat(root).st_mode)
            except OSError:
                final_link = False
            if not final_link:
                root = resolved_target(root)
        forbidden = (os.path.normpath(home),) if home else ()
        if command == 'library':
            result = library_setup.inspect_library(root, forbidden=forbidden)
        else:
            result = library_setup.init_library(root, forbidden=forbidden)
            result['saved'] = False
            if result['ok']:
                try:
                    ctx.set_config('library_root', result.get('root') or root)
                    result['saved'] = True
                except (AttributeError, OSError, ValueError) as exc:
                    result['findings'].append({
                        'id': 'library-not-saved', 'severity': 'warn',
                        'detail': f'could not save the library folder in plugin settings ({exc.__class__.__name__})',
                        'fix': f"set library_root to {root} in this plugin's settings, or pass --tools-root {root}"})
        code = exit_code(result['status'])
        if '--json' in argv:
            return {'exit': code, 'stdout': json.dumps(result, indent=2) + '\n', 'stderr': ''}
        return {'exit': code, 'stdout': format_report(f'componentize {command} {root}', result), 'stderr': ''}

    def dispatch(argv, *, ask=None):
        command, explicit, _rest = split_argv(argv)
        if _has_leading_version(argv) or (command == 'version' and not _rest):
            return {'exit': 0, 'stdout': f'componentize {VERSION}\n', 'stderr': ''}
        import os
        from .bridge import run
        try:
            cwd = os.getcwd()
        except OSError:
            cwd = None
        home = _home()
        if cwd is None and command not in ('kit', 'init', 'library'):
            return {'exit': 2, 'stdout': '', 'stderr': NO_CWD}
        if command == 'kit':
            from . import kit_commands
            return kit_commands.run(_kit_argv(argv), cwd=cwd, home=home)
        if command in ('init', 'library'):
            return library(argv, cwd=cwd, home=home, ask=ask)
        from .settings_rule import check_timeout
        default = configured_library()
        if default:
            # A saved (possibly hand-edited) value is checked again before use,
            # resolved once, and that resolved value is what the child gets.
            default, dir_error = _pin_folder(default, home=home)
            if dir_error is not None:
                return {'exit': 2, 'stdout': '', 'stderr': f'componentize: library_root setting: {dir_error}\n'}
        if explicit is not None:
            # A typed --tools-root gets the same rule and the same single resolution.
            pinned, dir_error = _pin_folder(explicit, home=home, cwd=cwd, typed=True)
            if dir_error is not None or not pinned:
                message = dir_error or 'expected a folder'
                return {'exit': 2, 'stdout': '', 'stderr': f'componentize: --tools-root: {message}\n'}
            argv = _strip_tools_root(argv)
            default = pinned
        timeout, timeout_error = check_timeout(ctx.get_config('timeout', 900))
        if timeout_error is not None:
            return {'exit': 2, 'stdout': '', 'stderr': f'componentize: timeout setting: {timeout_error}\n'}
        if command in LIBRARY_COMMANDS and not explicit and not default:
            from .library_setup import has_index
            if not has_index(cwd):
                return {'exit': 2, 'stdout': '', 'stderr': FIRST_RUN}
        return run(argv, cwd=cwd, env=dict(os.environ), timeout=timeout, tools_root=default)

    def setup(parser):
        # Own dest: the host root parser also stores --version in 'version' and
        # prints its own version when that is set, which would shadow this one.
        parser.add_argument('--version', dest='componentize_version', action='store_true',
                            help='print the plugin version (same as the version subcommand)')
        parser.add_argument('--tools-root')
        parser.add_argument('--state-root')
        parser.add_argument('--json', action='store_true')
        parser.add_argument('componentize_argv', nargs=argparse.REMAINDER,
                            help='version|init|library|kit|survey|begin|attest|check|finish|status|rebaseline and arguments')

    def ask_folder():
        """Prompt only on a real terminal; unattended runs get the first-run message instead."""
        import sys
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
        try:
            answer = input(f'Component library folder (existing or new) [{DEFAULT_LIBRARY}]: ').strip()
        except (EOFError, KeyboardInterrupt):
            return None
        return answer or DEFAULT_LIBRARY

    def cli(args):
        import sys
        argv = []
        if getattr(args, 'componentize_version', False):
            argv.append('--version')
        for option in ('tools_root', 'state_root'):
            value = getattr(args, option, None)
            if value is not None:
                argv.extend(['--' + option.replace('_', '-'), value])
        if args.json:
            argv.append('--json')
        argv.extend(args.componentize_argv)
        result = dispatch(argv, ask=ask_folder)
        print(result['stdout'], end='')
        print(result['stderr'], end='', file=sys.stderr)
        return result['exit']

    def slash(args):
        import shlex
        try:
            argv = shlex.split(args)
        except ValueError as exc:
            return f'componentize exit=2: {exc}'
        result = dispatch(argv)
        return f"componentize exit={result['exit']}\n{result['stdout']}{result['stderr']}"

    ctx.register_cli_command(name='componentize', help='Receipt-gated componentization', setup_fn=setup, handler_fn=cli)
    ctx.register_command(name='componentize', handler=slash, description='Receipt-gated componentization', args_hint='<subcommand> [arguments]', argument_mode='text')
    ctx.register_skill(name='componentize', path=ROOT / f'skills/{PLUGIN_NAME}/SKILL.md', description='Explicit componentize workflow')
