"""The one rule for this plugin's saved settings (library_root, timeout).

Used by the settings page's validate route, the status route, and the CLI
before it uses a saved value, so a hand-edited config value gets the same
plain refusal as a value typed on the page. The system and Hermes-home
decision is delegated to _kit_runtime.check_safe_target (the CLI's check).
"""
from decimal import Decimal, InvalidOperation
import math
import os
import re

from ._kit_runtime import check_safe_target

MAX_LIBRARY_ROOT_CHARS = 1024
DEFAULT_TIMEOUT = 900.0
MAX_TIMEOUT = 86400.0

M_NUL = 'Invalid path: remove NUL characters and line breaks.'
M_LONG = f'Path is too long: use at most {MAX_LIBRARY_ROOT_CHARS} characters.'
M_ABS = 'Enter an absolute path starting with /, such as /home/user/components.'
M_DOTDOT = 'Remove ".." segments: enter the full path of the folder.'
M_ROOT = 'Refused: pick a dedicated folder, not the filesystem root (/).'
M_HOME = 'Refused: pick a dedicated folder inside your home folder, not the home folder itself.'
M_SYSTEM = 'Refused: pick a dedicated folder, not a system folder such as /etc or /usr.'
M_HERMES = 'Refused: pick a dedicated folder outside Hermes home (such as ~/.hermes).'
T_BAD = 'Invalid timeout: enter a finite number greater than 0.'
T_MAX = 'Invalid timeout: enter at most 86400 seconds (24 hours).'
M_CTRL = 'Invalid path: remove control characters.'
M_ENCODING = 'Invalid path: use only valid UTF-8 text.'
M_EMPTY = 'Enter the folder for the component library, such as ~/components.'
U_KEY = 'Invalid request: only library_root and timeout can be set.'
U_BODY = 'Invalid request: send one JSON object.'

SETTING_KEYS = ('library_root', 'timeout')
# A plain decimal number: digits, optional fraction, optional exponent. No sign
# other than an optional '+', no '_', no hex, no 'nan'/'inf', no garbage.
_DECIMAL_RE = re.compile(r'\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,6})?')
_MAX_TIMEOUT_TEXT = 1024

# Messages for a value that is not a usable path at all (vs. a refused folder).
INVALID_PATH_MESSAGES = frozenset({M_NUL, M_LONG, M_ABS, M_DOTDOT, M_CTRL, M_ENCODING})


def _refusal(reason):
    return M_HERMES if 'Hermes home' in reason else M_SYSTEM


def check_library_root(raw, *, home, env=None, hermes_homes=(), cwd=None):
    """Return (normalized_absolute_path, None), ('', None) when unset, or (None, plain message).

    The settings page passes no cwd: a relative value is refused. The init
    command passes the folder it runs in as cwd, so a typed relative folder
    means that folder plus the typed text. Either way a ".." segment is refused
    before anything is joined or normalized, so the command never accepts a
    value the page refuses for "..".
    """
    if raw is None:
        return '', None
    if not isinstance(raw, str):
        return None, M_ABS
    if len(raw) > MAX_LIBRARY_ROOT_CHARS * 4:
        return None, M_LONG
    value = raw.strip()
    if not value:
        return '', None
    if any(ch in value for ch in '\x00\r\n'):
        return None, M_NUL
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value):
        return None, M_CTRL
    try:
        value.encode('utf-8')
    except UnicodeError:
        return None, M_ENCODING
    if len(value) > MAX_LIBRARY_ROOT_CHARS:
        return None, M_LONG
    if '..' in value.replace('\\', '/').split('/'):
        return None, M_DOTDOT
    home_ok = isinstance(home, str) and os.path.isabs(home)
    if value == '~' or value.startswith('~/'):
        if not home_ok:
            return None, M_ABS
        value = home.rstrip('/') + value[1:]
    elif not value.startswith('~') and not os.path.isabs(value) and isinstance(cwd, str) and os.path.isabs(cwd):
        value = os.path.join(cwd, value)
        if len(value) > MAX_LIBRARY_ROOT_CHARS:
            return None, M_LONG
    if not os.path.isabs(value):
        return None, M_ABS
    norm = '/' + os.path.normpath(value).lstrip('/')
    try:
        # Python 3.11 raises OSError (EINVAL) here when a link is swapped during the
        # call; that path cannot be resolved, so it gets the unresolvable-path refusal.
        real = os.path.realpath(norm)
    except OSError:
        return None, M_SYSTEM
    if norm == '/' or real == '/':
        return None, M_ROOT
    if home_ok:
        norm_home = os.path.normpath(home)
        try:
            real_home = os.path.realpath(norm_home)
        except OSError:
            return None, M_SYSTEM
        if norm in (norm_home, real_home) or real in (norm_home, real_home):
            return None, M_HOME
    if norm == '/home' or real == '/home':
        return None, M_SYSTEM
    reason = check_safe_target(norm, home=home if home_ok else None, env=env)
    if reason:
        return None, _refusal(reason)
    # Hermes homes reported by the running Hermes (profile homes) that the
    # environment may not name: ask the same check with each as HERMES_HOME.
    for hermes_home in hermes_homes:
        if isinstance(hermes_home, str) and os.path.isabs(hermes_home):
            reason = check_safe_target(norm, home=None, env={'HERMES_HOME': hermes_home})
            if reason:
                return None, M_HERMES
    return norm, None


def check_timeout(raw):
    """Return (seconds, None) or (None, plain message). Missing means the default.

    The one timeout rule: the validate route, the status route and the CLI's
    saved-config consumer all call it. Accepts an int, a float or a plain
    decimal string; never raises (huge integers included).
    """
    if raw is None:
        return DEFAULT_TIMEOUT, None
    if isinstance(raw, bool):
        return None, T_BAD
    if isinstance(raw, str):
        text = raw.strip(' \t')
        if len(text) > _MAX_TIMEOUT_TEXT or not _DECIMAL_RE.fullmatch(text):
            return None, T_BAD
        try:
            exact = Decimal(text)
        except (InvalidOperation, ValueError):
            return None, T_BAD
    elif isinstance(raw, int):
        exact = Decimal(raw)
    elif isinstance(raw, float):
        if not math.isfinite(raw):
            return None, T_BAD
        exact = Decimal(raw)
    else:
        return None, T_BAD
    if exact <= 0:
        return None, T_BAD
    if exact > Decimal(int(MAX_TIMEOUT)):
        return None, T_MAX
    try:
        value = float(exact)
    except (OverflowError, ValueError):
        return None, T_BAD
    if not math.isfinite(value) or value <= 0:
        return None, T_BAD
    return value, None


def check_settings_payload(payload, *, home, env=None, hermes_homes=()):
    """Check a whole settings draft. Return (values, None, None), (None, field_errors, None) or (None, None, request_error)."""
    if not isinstance(payload, dict):
        return None, None, U_BODY
    if any(key not in SETTING_KEYS for key in payload):
        return None, None, U_KEY
    root, root_error = check_library_root(payload.get('library_root', ''), home=home, env=env, hermes_homes=hermes_homes)
    timeout, timeout_error = check_timeout(payload.get('timeout'))
    errors = {}
    if root_error is not None:
        errors['library_root'] = root_error
    if timeout_error is not None:
        errors['timeout'] = timeout_error
    if errors:
        return None, errors, None
    return {'library_root': root, 'timeout': timeout}, None, None
