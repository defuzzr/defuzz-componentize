"""Read-only status, settings validation and bundled kit preview routes for defuzz-componentize.

Mounted by Hermes at `/api/plugins/defuzz-componentize/*` when the plugin is
enabled. Never writes files, never runs the checker or golden commands, and
never starts a subprocess. The settings page saves through Hermes's own
`plugins.manage settings` call; it must first POST the draft to
`/settings/validate`, which applies settings_rule (the CLI's rule) and returns
the normalized values to save. Saved values are re-checked at every use.
"""
from __future__ import annotations

from collections.abc import Mapping
import importlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import sys
import types
from typing import Any

from fastapi import APIRouter, Body
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE_NAME = f"{__name__.replace('-', '_')}__pkg"

PLUGIN_NAME = "defuzz-componentize"
CHECKER_VERSION = "1.3.0"
REQUIRES_HERMES = ">=0.23.0"
DEFAULT_TIMEOUT = 900.0
MAX_KIT_ID_CHARS = 64
MAX_BODY_BYTES = 16 * 1024
_BAD_REQUEST = {"error": "Invalid request."}
_TOO_LARGE = {"error": "Invalid request: the request body is too large."}
_KIT_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _ensure_plugin_package() -> types.ModuleType:
    """Load a private plugin package namespace without mutating sys.path or hermes_plugins."""
    init_file = _PLUGIN_ROOT / "__init__.py"
    existing = sys.modules.get(_PACKAGE_NAME)
    if existing is not None and hasattr(existing, "__path__"):
        existing_file = getattr(existing, "__file__", None)
        if existing_file is not None:
            try:
                if Path(existing_file).resolve() == init_file.resolve():
                    return existing
            except OSError:
                pass

    prefix = f"{_PACKAGE_NAME}."
    for key in list(sys.modules):
        if key == _PACKAGE_NAME or key.startswith(prefix):
            sys.modules.pop(key, None)

    spec = importlib.util.spec_from_file_location(
        _PACKAGE_NAME,
        init_file,
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot create module spec for defuzz-componentize")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = _PACKAGE_NAME
    module.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
    sys.modules[_PACKAGE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        for key in list(sys.modules):
            if key == _PACKAGE_NAME or key.startswith(prefix):
                sys.modules.pop(key, None)
        raise
    return module


def _load(stem: str) -> Any:
    pkg = _ensure_plugin_package()
    full_name = f"{pkg.__name__}.{stem}"
    existing = sys.modules.get(full_name)
    if existing is not None:
        return existing
    return importlib.import_module(f".{stem}", pkg.__name__)


class _ReadOnlyRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            if request.method not in ("GET", "HEAD"):
                declared = request.headers.get("content-length", "")
                if declared and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
                    return JSONResponse(status_code=400, content=_TOO_LARGE)
                # Read (and cache) the body once with a bound, also for chunked uploads.
                size = 0
                chunks = []
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_BODY_BYTES:
                        return JSONResponse(status_code=400, content=_TOO_LARGE)
                    chunks.append(chunk)
                request._body = b"".join(chunks)
            try:
                return await handler(request)
            except (RequestValidationError, RecursionError, UnicodeError):
                # Malformed, deeply nested or undecodable JSON is a client error, not a 500.
                return JSONResponse(status_code=400, content=_BAD_REQUEST)
            except StarletteHTTPException as exc:
                # FastAPI's own body-parsing refusal (e.g. bytes that are not UTF-8): same shape.
                if exc.status_code == 400:
                    return JSONResponse(status_code=400, content=_BAD_REQUEST)
                raise

        return handle


router = APIRouter(route_class=_ReadOnlyRoute)


def _read_profile_settings() -> tuple[dict[str, Any], str, tuple[str, ...], tuple[str, ...]]:
    """Read active profile settings and protected directory roots via public Hermes APIs."""
    raw_home = os.path.expanduser("~")
    home = os.path.normpath(raw_home) if raw_home and os.path.isabs(raw_home) else ""
    forbidden = (home,) if home else ()
    subtrees: list[str] = []
    if home:
        subtrees.append(os.path.normpath(os.path.join(home, ".hermes")))
    env_hermes = os.environ.get("HERMES_HOME", "").strip()
    if env_hermes and os.path.isabs(env_hermes):
        subtrees.append(os.path.normpath(env_hermes))

    settings: dict[str, Any] = {}
    try:
        from hermes_cli.config import load_config_readonly
        from hermes_constants import get_default_hermes_root, get_hermes_home
    except ImportError:
        return settings, home, forbidden, tuple(dict.fromkeys(subtrees))

    try:
        for candidate in (get_hermes_home(), get_default_hermes_root()):
            cand_str = str(candidate)
            if cand_str and os.path.isabs(cand_str):
                subtrees.append(os.path.normpath(cand_str))
        cfg = load_config_readonly() or {}
        plugins_cfg = cfg.get("plugins") if isinstance(cfg, Mapping) else None
        entries = plugins_cfg.get("entries") if isinstance(plugins_cfg, Mapping) else None
        entry = entries.get(PLUGIN_NAME) if isinstance(entries, Mapping) else None
        raw_settings = entry.get("settings") if isinstance(entry, Mapping) else None
        if isinstance(raw_settings, Mapping):
            settings = dict(raw_settings)
    except Exception:
        logging.getLogger(__name__).warning("Failed to read profile settings for %s", PLUGIN_NAME, exc_info=True)
    return settings, home, forbidden, tuple(dict.fromkeys(subtrees))


def _expand_configured_library(raw_value: Any, home: str) -> tuple[str, str]:
    """Return (sanitized_display_value, inspection_candidate) without joining relative paths to cwd."""
    if not isinstance(raw_value, str):
        return "", ""
    cleaned = raw_value.strip()
    if not cleaned:
        return "", ""
    if "\x00" in cleaned or "\n" in cleaned or "\r" in cleaned:
        return "", cleaned
    if cleaned == "~":
        return cleaned, (home if home else cleaned)
    if cleaned.startswith("~/"):
        return cleaned, ((home.rstrip(os.path.sep) + cleaned[1:]) if home else cleaned)
    return cleaned, cleaned


def _bundled_kits_envelope() -> dict[str, Any]:
    kit_commands = _load("kit_commands")
    res = kit_commands.run(["list", "--json"], cwd="/", home="/")
    if res.get("exit") != 0:
        raise RuntimeError("kit list failed")
    payload = json.loads(res["stdout"])
    if not isinstance(payload, dict) or not isinstance(payload.get("kits"), list):
        raise RuntimeError("invalid kit list payload")
    return payload


@router.get("/status")
def get_status() -> Any:
    """Return read-only plugin version, bounded library index status, and kit count."""
    try:
        pkg = _ensure_plugin_package()
        lib_setup = _load("library_setup")
        settings, home, forbidden, forbidden_subtrees = _read_profile_settings()
        rule = _load("settings_rule")
        display_root, candidate_root = _expand_configured_library(settings.get("library_root", ""), home)
        # The same timeout rule as the validate route and the CLI (settings_rule.check_timeout).
        checked_timeout, timeout_error = rule.check_timeout(settings.get("timeout"))
        timeout_valid = timeout_error is None
        timeout_val = checked_timeout if timeout_valid else DEFAULT_TIMEOUT

        # A hand-edited config value gets the same refusal as the page and the CLI.
        checked_root, root_error = rule.check_library_root(
            settings.get("library_root", ""), home=home, hermes_homes=forbidden_subtrees)
        if root_error in rule.INVALID_PATH_MESSAGES:
            display_root = ""  # never echo an unusable (possibly non-UTF-8) value
        if root_error is not None:
            lib_info = {
                "ok": False,
                "state": "invalid_path" if root_error in rule.INVALID_PATH_MESSAGES else "unsafe_path",
                "count": None,
                "index_rel": None,
                "message": root_error,
            }
        elif not checked_root:
            lib_info = lib_setup.count_indexed_components("", forbidden=forbidden, forbidden_subtrees=forbidden_subtrees)
        else:
            lib_info = lib_setup.count_indexed_components(
                checked_root,
                forbidden=forbidden,
                forbidden_subtrees=forbidden_subtrees,
            )

        kits_payload = _bundled_kits_envelope()
        return {
            "ok": True,
            "plugin": PLUGIN_NAME,
            "version": str(getattr(pkg, "VERSION", "0.2.0")),
            "checker_version": CHECKER_VERSION,
            "requires_hermes": REQUIRES_HERMES,
            "kits_count": len(kits_payload["kits"]),
            "kits_schema": kits_payload.get("schema", "componentize.kit/1"),
            "settings": {
                "library_root": display_root,
                "timeout": timeout_val,
                "timeout_valid": timeout_valid,
                "timeout_message": timeout_error or "",
            },
            "library": {
                "configured": bool(candidate_root),
                "ok": bool(lib_info["ok"]),
                "state": str(lib_info["state"]),
                "count": lib_info["count"],
                "index_rel": lib_info["index_rel"],
                "message": str(lib_info["message"]),
            },
            "caps": {
                "max_library_root_chars": lib_setup.MAX_LIBRARY_ROOT_CHARS,
                "max_dir_entries": lib_setup.MAX_DIR_ENTRIES,
                "max_index_bytes": lib_setup.MAX_INDEX_BYTES,
                "max_index_lines": lib_setup.MAX_INDEX_LINES,
                "max_kit_id_chars": MAX_KIT_ID_CHARS,
            },
        }
    except Exception:
        logging.getLogger(__name__).exception("defuzz-componentize status route failed")
        return JSONResponse(status_code=500, content={"error": "Componentize status is unavailable."})


@router.post("/settings/validate")
def validate_settings(payload: Any = Body(default=None)) -> Any:
    """Check a settings draft with the CLI's rule; never saves. Returns normalized values or per-field messages."""
    if not isinstance(payload, Mapping):
        return JSONResponse(status_code=400, content=_BAD_REQUEST)
    try:
        rule = _load("settings_rule")
        _settings, home, _forbidden, hermes_homes = _read_profile_settings()
        values, errors, request_error = rule.check_settings_payload(dict(payload), home=home, hermes_homes=hermes_homes)
    except Exception:
        logging.getLogger(__name__).exception("defuzz-componentize settings validation failed")
        return JSONResponse(status_code=500, content={"error": "Settings could not be checked."})
    if request_error is not None:
        return JSONResponse(status_code=400, content={"error": request_error})
    if errors:
        return {"ok": False, "errors": errors}
    return {"ok": True, "values": values}


@router.get("/kits")
def get_kits() -> Any:
    """Return summaries for all 14 bundled project kits."""
    try:
        payload = _bundled_kits_envelope()
        return {
            "schema": payload.get("schema", "componentize.kit/1"),
            "ok": True,
            "kits": payload["kits"],
        }
    except Exception:
        logging.getLogger(__name__).exception("defuzz-componentize kits route failed")
        return JSONResponse(status_code=500, content={"error": "Bundled project kits could not be read."})


@router.get("/kits/{kit_id:path}")
def get_kit(kit_id: str) -> Any:
    """Return read-only preview data for one validated bundled kit."""
    if (
        not isinstance(kit_id, str)
        or not kit_id
        or len(kit_id) > MAX_KIT_ID_CHARS
        or "\x00" in kit_id
        or "/" in kit_id
        or "\\" in kit_id
        or ".." in kit_id
        or not _KIT_ID_RE.fullmatch(kit_id)
    ):
        return JSONResponse(status_code=400, content={"error": "Invalid kit identifier."})

    try:
        kits_payload = _bundled_kits_envelope()
        bundled_ids = {row["id"] for row in kits_payload["kits"] if isinstance(row, Mapping) and "id" in row}
        if kit_id not in bundled_ids:
            return JSONResponse(status_code=404, content={"error": "Unknown project kit."})

        kit_commands = _load("kit_commands")
        res = kit_commands.run(["show", kit_id, "--json"], cwd="/", home="/")
        if res.get("exit") != 0:
            return JSONResponse(status_code=500, content={"error": "Project kit preview is unavailable."})
        data = json.loads(res["stdout"])
        return {
            "schema": data.get("schema", "componentize.kit/1"),
            "ok": True,
            "kit": data["kit"],
            "declared_dirs": data["declared_dirs"],
            "placement_rules": data["placement_rules"],
            "tree": data["tree"],
            "agents_block": data["agents_block"],
        }
    except Exception:
        logging.getLogger(__name__).exception("defuzz-componentize kit preview route failed")
        return JSONResponse(status_code=500, content={"error": "Project kit preview is unavailable."})
