#!/usr/bin/env python3
"""Check the vendored copies against the author's upstream source tree, or refresh them from a pinned commit."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
# Recorded in VENDOR.json and stated in every vendored .py header.
ORIGIN = "the author's upstream source tree (componentize engine and hermes_audit modules); not published separately"
LICENSE = 'MIT'


def _fail(msg: str) -> NoReturn:
    print(f'refresh_vendor.py: error: {msg}', file=sys.stderr)
    raise SystemExit(2)


INDEX_REL = 'hermes_audit/COMPONENTS.md'
_ENTRY = re.compile(r'^- `([A-Za-z_][A-Za-z0-9_]*)\.')


def vendored_modules(rels) -> list[str]:
    """Names of the `hermes_audit/<module>.py` files this plugin vendors."""
    return sorted(r[len('hermes_audit/'):-3] for r in rels
                  if r.startswith('hermes_audit/') and r.endswith('.py') and r.count('/') == 1 and not r.endswith('/__init__.py'))


def filtered_index(data: bytes, modules) -> bytes:
    """Keep only index entries for vendored modules, with a note under the title.

    Entry lines are `- `<module>.<function>(...)`; all other lines are kept, and
    runs of blank lines left by removed entries collapse to one.
    """
    keep = set(modules)
    lines = data.decode('utf-8').split('\n')
    out: list[str] = []
    for line in lines:
        match = _ENTRY.match(line)
        if match and match.group(1) not in keep:
            continue
        if line == '' and out and out[-1] == '':
            continue
        out.append(line)
    note = ('> Filtered copy for this plugin: it lists only the modules vendored here ('
            + ', '.join(f'`{m}`' for m in sorted(keep)) + '). The upstream index lists more modules, which are not shipped.')
    title = 1 if out and out[0].startswith('#') else 0
    out[title:title] = ['', note] if title else [note, '']
    return '\n'.join(out).encode('utf-8')


def vendored_bytes(rel: str, data: bytes, modules=None) -> bytes:
    """Return the vendored copy of one source file: `.py` files get the header line.

    The component index is filtered to `modules` (the vendored module names).

    The header is line 1, or line 2 when the source starts with a `#!` line, so
    the shebang stays first. `hermes_audit/` modules are checked by the
    architecture gate, which reads the header from line 1 only: they must not
    start with `#!`.
    """
    if rel == INDEX_REL:
        if modules is None:
            _fail(f'{rel} needs the vendored module list')
        return filtered_index(data, modules)
    if not rel.endswith('.py'):
        return data
    header = f"# vendored copy of the author's {rel} ({LICENSE} licence, same as this plugin); do not edit\n".encode()
    if data.startswith(b'#!'):
        if rel.startswith('hermes_audit/'):
            _fail(f'{rel} starts with #!; the architecture gate reads a vendored header from line 1 only')
        shebang, sep, rest = data.partition(b'\n')
        if not sep:
            return data + b'\n' + header
        return shebang + sep + header + rest
    return header + data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help="Root of the author's upstream source tree")
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--commit', help='Required full 40-character source commit for refresh')
    args = parser.parse_args(argv)
    if args.source is None:
        _fail('--source SOURCE_ROOT is required (the upstream source tree is not shipped with this plugin)')
    manifest = json.loads((ROOT / 'VENDOR.json').read_text())
    source = args.source.resolve()
    if not all((source / d).is_dir() for d in ('hermes_audit', 'componentize', 'kits')):
        _fail(f'upstream source directories (hermes_audit, componentize, kits) not found under {source}')
    if not args.check:
        if not args.commit or len(args.commit) != 40 or any(c not in '0123456789abcdef' for c in args.commit):
            _fail('refresh requires --commit with a full lowercase SHA')
        resolved = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=source, capture_output=True, text=True, errors='replace', check=False, timeout=30)
        if resolved.returncode or resolved.stdout.strip() != args.commit:
            _fail('--commit must match source HEAD')
    updated = {'origin': ORIGIN, 'license': LICENSE, 'source_commit': args.commit or manifest['source_commit'], 'files': {}}
    drift = []
    pending = {}
    modules = vendored_modules(manifest['files'])
    for rel, expected in manifest['files'].items():
        src_path = source / rel
        if not src_path.is_file():
            _fail(f'missing source file: {rel}')
        data = src_path.read_bytes()
        if not args.check:
            pinned = subprocess.run(['git', 'show', f'{args.commit}:{rel}'], cwd=source, capture_output=True, check=False, timeout=30)
            if pinned.returncode or pinned.stdout != data:
                _fail(f'current source differs from pinned commit: {rel}')
        vendor = vendored_bytes(rel, data, modules)
        hashes = {'source_sha256': hashlib.sha256(data).hexdigest(), 'vendor_sha256': hashlib.sha256(vendor).hexdigest()}
        if hashes != expected or (ROOT / 'vendor' / rel).read_bytes() != vendor:
            drift.append(rel)
        pending[rel] = vendor
        updated['files'][rel] = hashes
    if args.check:
        print('\n'.join(drift) if drift else 'vendor matches current upstream source')
        return int(bool(drift))
    for rel, data in pending.items():
        (ROOT / 'vendor' / rel).write_bytes(data)
    (ROOT / 'VENDOR.json').write_text(json.dumps(updated, indent=2) + '\n')
    print(f"refreshed {len(pending)} files from {args.commit}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
