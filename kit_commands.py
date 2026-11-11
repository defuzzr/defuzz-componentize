"""List, preview, apply and check bundled project kits. Standard library only."""
import json
import os
from pathlib import Path

from ._kit_runtime import (
    ENGINE, _hermes_homes, containment_report, create_from_verified, exit_code,
    expand_dir, format_report, resolve_home, show_text, verify_target,
)


ROOT = Path(__file__).resolve().parent
KITS_DIR = ROOT / 'vendor/kits'
SCHEMA = 'componentize.kit/1'
SUBCOMMANDS = ('list', 'show', 'apply', 'check')

# Bounds for what `kit list` / `kit show` emit; another plugin reads this output.
MAX_LIST_KITS = 200
MAX_FINDINGS = 50
MAX_ID_CHARS = 64
MAX_NAME_CHARS = 120
MAX_SUMMARY_CHARS = 240
MAX_TAGS = 32
MAX_TAG_CHARS = 64
MAX_KIT_FILE_BYTES = 1024 * 1024
_NOTICE_CHARS = 300


def _one_line(text):
    return show_text(text, limit=0)


def _cap(text, limit):
    return text if len(text) <= limit else text[:limit] + '...'


def _kit_summary(kit):
    """One flat, bounded row: strings are capped, tags are a short list of short strings."""
    return {
        'id': kit['id'],
        'name': _cap(kit['name'], MAX_NAME_CHARS),
        'summary': _cap(kit['summary'], MAX_SUMMARY_CHARS),
        'tags': [_cap(tag, MAX_TAG_CHARS) for tag in list(kit.get('tags', []))[:MAX_TAGS]],
        'version': kit['version'],
        'availability': kit.get('availability', 'available'),
    }


def _notice(fid, name, detail):
    return {
        'id': fid,
        'severity': 'warn',
        'detail': show_text(f'{name}: {detail}', limit=_NOTICE_CHARS),
        'fix': show_text(f'fix or remove vendor/kits/{name}/kit.json', limit=_NOTICE_CHARS),
    }


def _read_kit_file(kit_file):
    """(parsed JSON, None) or (None, plain reason). Reads at most MAX_KIT_FILE_BYTES + 1."""
    try:
        with open(kit_file, 'rb') as stream:
            data = stream.read(MAX_KIT_FILE_BYTES + 1)
    except OSError as exc:
        return None, f'cannot read kit.json ({exc.__class__.__name__})'
    if len(data) > MAX_KIT_FILE_BYTES:
        return None, f'kit.json is larger than {MAX_KIT_FILE_BYTES // 1024} KiB'
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return None, 'kit.json is not valid UTF-8 text'
    try:
        return json.loads(text), None
    except (ValueError, RecursionError):
        return None, 'kit.json is not valid JSON or is nested too deeply'


def _kit_problem(raw, name):
    """Why this kit definition cannot be listed, or None. Never raises."""
    try:
        checked = ENGINE.validate_kit(raw)
    except Exception:  # one broken definition must not break the whole list
        return 'the kit definition could not be checked'
    if not checked['ok']:
        return checked['error'] or (checked['findings'][0]['detail'] if checked['findings'] else 'invalid kit')
    if raw.get('id') != name:
        return f"kit id {raw.get('id')!r} does not match folder {name!r}"
    if len(name) > MAX_ID_CHARS:
        return f'kit id is longer than {MAX_ID_CHARS} characters'
    return None


def _scan_kits():
    kits, findings = [], []
    try:
        entries = sorted(os.listdir(KITS_DIR))
    except OSError as exc:
        findings.append({
            'id': 'kit-dir-unreadable',
            'severity': 'warn',
            'detail': show_text(f'cannot list {KITS_DIR}: {exc}', limit=_NOTICE_CHARS),
            'fix': 'verify the plugin vendor/kits directory is intact',
        })
        return kits, findings
    hidden_kits = hidden_notices = 0
    for index, name in enumerate(entries):
        if len(kits) >= MAX_LIST_KITS:
            hidden_kits = sum(1 for rest in entries[index:] if (KITS_DIR / rest).is_dir())
            break
        folder = KITS_DIR / name
        if not folder.is_dir():
            continue
        raw, read_err = _read_kit_file(folder / 'kit.json')
        problem = None if read_err else _kit_problem(raw, name)
        if read_err or problem:
            if len(findings) >= MAX_FINDINGS:
                hidden_notices += 1
            elif read_err:
                findings.append(_notice('kit-unreadable', name, read_err))
            else:
                findings.append(_notice('kit-invalid', name, problem))
            continue
        kits.append(raw)
    if hidden_kits or hidden_notices:
        findings.append({
            'id': 'kit-list-truncated',
            'severity': 'warn',
            'detail': f'{hidden_kits} more kit folders and {hidden_notices} more kit notices are not shown',
            'fix': 'remove kit folders you do not use from vendor/kits',
        })
    return kits, findings


def _load_kit(kit_id):
    if isinstance(kit_id, str) and ENGINE._ID_RE.fullmatch(kit_id):
        kit_file = KITS_DIR / kit_id / 'kit.json'
        if kit_file.is_file():
            raw, read_err = _read_kit_file(kit_file)
            if read_err is None and _kit_problem(raw, kit_id) is None:
                return raw, None
    valid_kits, _ = _scan_kits()
    available = ', '.join(k['id'] for k in valid_kits) or 'none'
    return None, _one_line(f'unknown kit {kit_id!r}; available kits: {available}')


def _parse_argv(argv):
    want_json = '--json' in argv
    variables, positionals = {}, []
    unknown_option = None
    usage_error = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == '--json':
            index += 1
            continue
        if token == '--set':
            if index + 1 >= len(argv):
                if usage_error is None:
                    usage_error = '--set requires name=value'
                index += 1
                continue
            pair = argv[index + 1]
            name, sep, value = pair.partition('=')
            if not sep or not name:
                if usage_error is None:
                    usage_error = _one_line(f'malformed --set {pair!r}: expected name=value')
            else:
                variables[name] = value
            index += 2
            continue
        if token.startswith('-'):
            if unknown_option is None:
                unknown_option = _one_line(f'unknown option {token!r}')
            index += 1
            continue
        positionals.append(token)
        index += 1
    return want_json, variables, positionals, unknown_option, usage_error


def _unsafe_set_value(value):
    """True when a --set value is '.', '..', absolute, or contains those segments."""
    if not isinstance(value, str) or not value or value.startswith(('/', '~')) or os.path.isabs(value):
        return True
    return any(part in ('', '.', '..') for part in value.split('/'))


def reject_unsafe_set(kit, variables):
    """Refuse '..', '.' segments, and absolute --set values before substitution.

    The vendored engine only rejects them after they land in a path, so
    ``project=../escape`` is accepted. Refuse them on every --set value.
    """
    if not variables:
        return None
    for name, value in variables.items():
        if _unsafe_set_value(value):
            return show_text(f"variable {name} must not contain '..' or an absolute path, got {value}")
    return None


def _json_reply(envelope):
    return {'exit': exit_code(envelope['status']), 'stdout': json.dumps(envelope, indent=2) + '\n', 'stderr': ''}


def _usage_reply(message, *, want_json, cmd=None, kit_id=None, root=None):
    msg = _one_line(message)
    if want_json:
        if cmd in ('apply', 'check'):
            return _json_reply(_apply_or_check_envelope({'error': msg}, kit_id=kit_id, root=root))
        return _json_reply({
            'schema': SCHEMA,
            'ok': False,
            'status': 'error',
            'error': msg,
            'findings': [],
        })
    return {'exit': 2, 'stdout': '', 'stderr': f'componentize kit: {msg}\n'}


def _apply_or_check_envelope(raw, *, kit_id, root):
    return {
        'schema': SCHEMA,
        'ok': bool(raw.get('ok', False)),
        'status': raw.get('status', 'error'),
        'error': _one_line(raw['error']) if raw.get('error') else None,
        'findings': list(raw.get('findings', [])),
        'root': raw.get('root', root),
        'kit': raw.get('kit', kit_id),
        'items': list(raw.get('items', [])),
        'declared_dirs': list(raw.get('declared_dirs', [])),
        'placement_rules': list(raw.get('placement_rules', [])),
    }


def _format_apply_or_check(cmd, kit_id, root, envelope):
    safe_id = _one_line(kit_id) if kit_id is not None else ''
    title = f'componentize kit {cmd} {safe_id} {root}'
    return {'exit': exit_code(envelope['status']), 'stdout': format_report(title, envelope), 'stderr': ''}


def run(argv, *, cwd, home):
    """Execute `kit <subcommand> ...` and return {'exit', 'stdout', 'stderr'}."""
    want_json, variables, positionals, unknown_option, usage_error = _parse_argv(list(argv))
    cmd = positionals[0] if positionals else None
    kit_id = positionals[1] if len(positionals) >= 2 else None
    raw_dir = positionals[2] if len(positionals) >= 3 else None

    if cmd is None:
        return _usage_reply('expected subcommand: list, show, apply or check', want_json=want_json)
    if cmd not in SUBCOMMANDS:
        return _usage_reply(f'unknown subcommand {cmd!r}; expected list, show, apply or check', want_json=want_json)
    if unknown_option is not None:
        return _usage_reply(unknown_option, want_json=want_json, cmd=cmd, kit_id=None, root=None)

    resolved_home, home_err = resolve_home(home)
    if cmd in ('apply', 'check') and home_err:
        return _usage_reply(home_err, want_json=want_json, cmd=cmd, kit_id=kit_id, root=None)
    # ~ expansion uses the home the caller passed. Do not expand ~ against the
    # account home when HOME is empty: that would write under a folder the
    # user did not name. Protection still uses the resolved home below.
    root, dir_error = expand_dir(raw_dir, cwd=cwd, home=home, setting='target folder', typed=True) if raw_dir else (None, None)
    if usage_error is not None:
        return _usage_reply(usage_error, want_json=want_json, cmd=cmd, kit_id=kit_id, root=root)
    if dir_error is not None:
        return _usage_reply(dir_error, want_json=want_json, cmd=cmd, kit_id=kit_id, root=None)

    if cmd == 'list':
        if len(positionals) != 1:
            return _usage_reply('list takes no positional arguments', want_json=want_json, cmd=cmd)
        if variables:
            return _usage_reply('list does not accept --set', want_json=want_json, cmd=cmd)
        kits, findings = _scan_kits()
        summaries = [_kit_summary(k) for k in kits]
        envelope = {
            'schema': SCHEMA,
            'ok': True,
            'status': 'pass',
            'error': None,
            'findings': findings,
            'kits': summaries,
        }
        if want_json:
            return _json_reply(envelope)
        lines = [f"{show_text(row['id']):<20} {'[coming soon] ' if row['availability'] == 'coming_soon' else ''}{show_text(row['summary'])}" for row in summaries]
        return {'exit': 0, 'stdout': ('\n'.join(lines) + '\n') if lines else '', 'stderr': ''}

    if cmd == 'show':
        if len(positionals) < 2 or not kit_id:
            return _usage_reply('show requires ID', want_json=want_json, cmd=cmd)
        if len(positionals) > 2:
            return _usage_reply('show takes only ID', want_json=want_json, cmd=cmd)
        kit, err = _load_kit(kit_id)
        if kit is None:
            return _usage_reply(err, want_json=want_json, cmd=cmd)
        set_err = reject_unsafe_set(kit, variables)
        if set_err is not None:
            return _usage_reply(set_err, want_json=want_json, cmd=cmd)
        values, var_err = ENGINE.resolve_variables(kit, '/project', variables)
        if var_err or values is None:
            return _usage_reply(var_err or 'invalid variables', want_json=want_json, cmd=cmd)
        tree = ENGINE.kit_tree(kit, values)
        block = ENGINE.render_agents_block(kit, values)
        envelope = {
            'schema': SCHEMA,
            'ok': True,
            'status': 'pass',
            'error': None,
            'findings': [],
            'kit': _kit_summary(kit),
            'declared_dirs': ENGINE.declared_dirs(kit, values),
            'placement_rules': ENGINE.placement_rules(kit, values),
            'tree': tree,
            'agents_block': block,
        }
        if want_json:
            return _json_reply(envelope)
        lines = [kit['name'], kit['summary'], '', *tree, '', block.rstrip('\n')]
        return {'exit': 0, 'stdout': '\n'.join(lines) + '\n', 'stderr': ''}

    # apply or check
    if len(positionals) < 2 or not kit_id:
        return _usage_reply(f'{cmd} requires ID and DIR', want_json=want_json, cmd=cmd, kit_id=None, root=None)
    if len(positionals) < 3 or not raw_dir:
        return _usage_reply(f'{cmd} requires DIR', want_json=want_json, cmd=cmd, kit_id=kit_id, root=None)
    if len(positionals) > 3:
        return _usage_reply(f'{cmd} takes only ID and DIR', want_json=want_json, cmd=cmd, kit_id=kit_id, root=root)

    kit, err = _load_kit(kit_id)
    if kit is None:
        return _usage_reply(err, want_json=want_json, cmd=cmd, kit_id=kit_id, root=root)

    if kit.get('availability', 'available') == 'coming_soon':
        return {'exit': 2, 'stdout': '',
                'stderr': f'{kit_id} is coming soon and cannot be applied yet.\n'}

    verified, safety_err = verify_target(root, home=resolved_home or home)
    if safety_err is not None or verified is None:
        envelope = _apply_or_check_envelope(
            {'error': safety_err or 'project folder must be an absolute normalized path'},
            kit_id=kit_id, root=root,
        )
        return _json_reply(envelope) if want_json else _format_apply_or_check(cmd, kit_id, root, envelope)

    set_err = reject_unsafe_set(kit, variables)
    if set_err is not None:
        verified.close()
        envelope = _apply_or_check_envelope({'error': set_err}, kit_id=kit_id, root=root)
        return _json_reply(envelope) if want_json else _format_apply_or_check(cmd, kit_id, root, envelope)

    safety_home = resolved_home or home
    forbidden = (os.path.normpath(safety_home),) if isinstance(safety_home, str) and os.path.isabs(safety_home) else ()
    homes = tuple(_hermes_homes(safety_home, None))
    engine_fn = ENGINE.apply_kit if cmd == 'apply' else ENGINE.inspect_kit
    operate = verified.path
    fd = None
    # Creation uses the verified directory fd, and apply hands that same fd to
    # the engine, which then writes only relative to it and never opens the path.
    # containment_report says which reported writes could not be opened from it.
    try:
        if cmd == 'apply':
            fd, prep_err = create_from_verified(verified)
            if prep_err is not None or fd is None:
                envelope = _apply_or_check_envelope(
                    {'error': prep_err or 'could not prepare the target folder'},
                    kit_id=kit_id, root=root,
                )
                return _json_reply(envelope) if want_json else _format_apply_or_check(cmd, kit_id, root, envelope)
            held = os.fstat(fd)
            root_dev, root_ino = held.st_dev, held.st_ino
        raw_result = engine_fn(kit, operate, variables=variables, forbidden=forbidden, hermes_homes=homes,
                               **({'root_fd': fd} if fd is not None else {}))
        if fd is not None:
            escape = containment_report(fd, root_dev, root_ino, operate, raw_result)
            if escape:
                raw_result = dict(raw_result)
                raw_result['error'] = escape
                raw_result['ok'] = False
                raw_result['status'] = 'error'
    except OSError as exc:
        detail = show_text(_one_line(exc))
        if show_text(operate) not in detail:
            detail = f'{detail} ({show_text(operate)})' if detail else show_text(operate)
        raw_result = {'error': f'{exc.__class__.__name__}: {detail}'}
    finally:
        verified.close()
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    envelope = _apply_or_check_envelope(raw_result, kit_id=kit_id, root=operate)
    return _json_reply(envelope) if want_json else _format_apply_or_check(cmd, kit_id, operate, envelope)
