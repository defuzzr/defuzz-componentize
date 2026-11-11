# vendored copy of the author's componentize/_state.py (MIT licence, same as this plugin); do not edit
"""State layout, manifest path resolution, and JSON helpers for componentize (runner helper module)."""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from typing import Any

_ATTEST_TOP_KEYS = frozenset({"kind_reason", "reuse", "plan", "review", "impl"})
_ATTEST_PHASES = ("plan", "review", "impl")
ATTEST_PHASES = _ATTEST_PHASES
MIN_NOTE_CHARS = 20
_ATTEST_ENTRY_KEYS = frozenset({"text", "at"})
_GEN_PLAIN_RE = re.compile(r"^gen-([1-9][0-9]*)$")
_GEN_JSON_RE = re.compile(r"^gen-([1-9][0-9]*)\.json$")
_CHECKED_EVIDENCE_KEYS = (
    "manifest_sha",
    "manifest_gen",
    "baseline_gen",
    "baseline_shas",
    "file_hashes",
    "checker_digest",
    "runtime",
    "attestations_sha",
    "amend_log_sha",
)


def sha256_text(text: str) -> str:
    """Return SHA-256 hex digest of UTF-8 encoded text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON key: {key!r}")
        out[key] = val
    return out


def _reject_nonfinite_constant(token: str) -> Any:
    raise ValueError(f"non-standard JSON constant not allowed: {token}")


def _check_json_depth(val: Any) -> None:
    if isinstance(val, list):
        for item in val:
            _check_json_depth(item)
    elif isinstance(val, dict):
        for item in val.values():
            _check_json_depth(item)


def parse_json_strict(text: Any) -> tuple[Any, str | None]:
    """Parse JSON strictly, rejecting duplicate keys, NaN/Infinity, huge ints, and deep recursion."""
    if not isinstance(text, str):
        return None, "expected JSON string"
    try:
        data = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
        _check_json_depth(data)
    except (ValueError, RecursionError) as exc:
        return None, str(exc)
    return data, None


def format_json(data: Any) -> str:
    """Format JSON object deterministically with sorted keys and 2-space indent."""
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def job_paths(state_root: str, name: str, gen: int = 1) -> dict[str, str]:
    """Return dictionary of state file and directory paths for a job and generation."""
    job_dir = os.path.join(state_root, name)
    manifest_dir = os.path.join(job_dir, "manifest")
    baseline_dir = os.path.join(job_dir, "baseline", f"gen-{gen}")
    return {
        "job_dir": job_dir,
        "lock": os.path.join(job_dir, "lock"),
        "manifest_dir": manifest_dir,
        "manifest_gen": os.path.join(manifest_dir, f"gen-{gen}.json"),
        "baseline_dir": baseline_dir,
        "baseline_stdout": os.path.join(baseline_dir, "stdout"),
        "baseline_stderr": os.path.join(baseline_dir, "stderr"),
        "baseline_rc": os.path.join(baseline_dir, "rc"),
        "baseline_meta": os.path.join(baseline_dir, "meta.json"),
        "attest": os.path.join(job_dir, "attest.json"),
        "last_check": os.path.join(job_dir, "last_check.json"),
        "receipt": os.path.join(job_dir, "receipt.json"),
    }


def _resolve_field(val: Any, base_dir: str) -> Any:
    if not isinstance(val, str) or not val or "\x00" in val:
        return val
    raw = val if val.startswith("/") else posixpath.join(base_dir, val)
    norm = posixpath.normpath(raw)
    return "/" + norm.lstrip("/") if norm.startswith("//") else norm


def resolve_manifest_paths(manifest: Any, manifest_dir: str) -> Any:
    """Resolve relative path fields in a manifest dict against manifest_dir (argv untouched)."""
    if not isinstance(manifest, dict):
        return manifest
    out = dict(manifest)
    if "original" in out:
        out["original"] = _resolve_field(out["original"], manifest_dir)
    if isinstance(out.get("hermes_plugins"), list):
        out["hermes_plugins"] = [{**item, "path": _resolve_field(item["path"], manifest_dir)} if isinstance(item, dict) and "path" in item else item for item in out["hermes_plugins"]]
    if isinstance(out.get("inputs"), list):
        out["inputs"] = [_resolve_field(item, manifest_dir) for item in out["inputs"]]
    if isinstance(out.get("files"), list):
        resolved_files: list[Any] = []
        for entry in out["files"]:
            if isinstance(entry, dict):
                item = dict(entry)
                if "path" in item:
                    item["path"] = _resolve_field(item["path"], manifest_dir)
                if "vendor_source" in item:
                    item["vendor_source"] = _resolve_field(item["vendor_source"], manifest_dir)
                resolved_files.append(item)
            else:
                resolved_files.append(entry)
        out["files"] = resolved_files
    for section in ("golden", "tests"):
        sec_val = out.get(section)
        if isinstance(sec_val, dict):
            sec_copy = dict(sec_val)
            if "cwd" in sec_copy:
                sec_copy["cwd"] = _resolve_field(sec_copy["cwd"], manifest_dir)
            out[section] = sec_copy
    return out


def build_baseline_meta(golden: dict[str, Any], stdout: str, stderr: str, rc_str: str) -> dict[str, Any]:
    """Construct baseline meta.json dictionary for a captured 2-run golden execution."""
    return {
        "argv": list(golden["argv"]),
        "cwd": golden["cwd"],
        "env_keys": sorted(golden["env"].keys()),
        "compare": list(golden["compare"]),
        "sha256": {
            "stdout": sha256_text(stdout),
            "stderr": sha256_text(stderr),
            "rc": sha256_text(rc_str),
        },
        "runs": 2,
    }


def build_initial_attest(attest_section: dict[str, Any]) -> dict[str, Any]:
    """Construct initial attest.json dictionary from manifest attest section."""
    return {
        "kind_reason": attest_section["kind_reason"],
        "reuse": attest_section["reuse"],
        "plan": [],
        "review": [],
        "impl": [],
    }


def select_current_gen(entries: list[str], *, suffix: str = "") -> int | None:
    """Return the highest positive integer generation N from names matching 'gen-N<suffix>'."""
    if suffix == "":
        pattern = _GEN_PLAIN_RE
    elif suffix == ".json":
        pattern = _GEN_JSON_RE
    else:
        pattern = re.compile(rf"^gen-([1-9][0-9]*){re.escape(suffix)}$")
    gens: list[int] = []
    for item in entries:
        m = pattern.fullmatch(item)
        if m is not None:
            gens.append(int(m.group(1)))
    return max(gens) if gens else None


def _format_golden_drift_error(name: str, b_gen: int, fields: list[str]) -> str:
    fields_str = ", ".join(fields)
    return f"golden changed since baseline gen-{b_gen}: {fields_str}; run componentize rebaseline {name} --reason ..."


def validate_manifest_meta_consistency(
    val_res: dict[str, Any],
    manifest: Any,
    meta: Any,
    *,
    b_gen: int = 1,
    cap_shas: dict[str, str] | None = None,
) -> str | None:
    """Validate that loaded manifest and baseline meta are structurally and golden-consistent."""
    if not val_res.get("ok") or not isinstance(manifest, dict) or not isinstance(meta, dict):
        return "corrupt state: invalid manifest or baseline meta"
    name = str(manifest.get("name", "<name>"))
    golden = manifest.get("golden")
    if not isinstance(golden, dict):
        return "corrupt state: invalid manifest golden"
    if ("waived" in meta) != ("waived" in golden):
        return _format_golden_drift_error(name, b_gen, ["waived"])
    if "waived" in golden:
        if not isinstance(meta.get("waived"), str) or not meta["waived"].strip():
            return "corrupt state: invalid waived baseline meta"
        return None
    fields: list[str] = []
    if meta.get("argv") != golden.get("argv"):
        fields.append("argv")
    if meta.get("cwd") != golden.get("cwd"):
        fields.append("cwd")
    if meta.get("env_keys") != sorted(golden.get("env", {}).keys()):
        fields.append("env_keys")
    if meta.get("compare") != golden.get("compare"):
        fields.append("compare")
    if cap_shas is not None and meta.get("sha256") != cap_shas:
        fields.append("sha256")
    return _format_golden_drift_error(name, b_gen, fields) if fields else None


def _error_finding(fid: str, detail: str, fix: str) -> dict[str, Any]:
    return {"id": fid, "severity": "error", "detail": detail, "fix": fix}


def _format_finding_line(f: dict[str, Any]) -> str:
    return f"{f['id']} {f['detail']} — fix: {f['fix']}"


def _walk_manifest_files_and_inputs(
    files: Any,
    inputs: Any,
    *,
    group_vendors: bool,
) -> tuple[list[str], bool]:
    paths: list[str] = []
    vendors: list[str] = []
    has_component = False
    if isinstance(files, list):
        for entry in files:
            if isinstance(entry, dict):
                p = entry.get("path")
                if isinstance(p, str):
                    paths.append(p)
                vs = entry.get("vendor_source")
                if isinstance(vs, str):
                    (vendors if group_vendors else paths).append(vs)
                if entry.get("role") == "component":
                    has_component = True
    raw = [*paths, *vendors]
    if isinstance(inputs, list):
        raw.extend(item for item in inputs if isinstance(item, str))
    return raw, has_component


def _dedupe_ordered(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def collect_inode_check_paths(manifest: dict[str, Any]) -> list[str]:
    """Return deduplicated ordered list of declared paths (files paths, vendor sources, inputs) plus original."""
    raw, _ = _walk_manifest_files_and_inputs(manifest.get("files"), manifest.get("inputs"), group_vendors=True)
    orig = manifest.get("original")
    if isinstance(orig, str):
        raw.append(orig)
    return _dedupe_ordered(raw)


def find_duplicate_inode_findings(
    path_inodes: list[tuple[str, tuple[int, int] | None]],
) -> list[dict[str, Any]]:
    """Return duplicate-file findings for any two distinct paths sharing (st_dev, st_ino)."""
    seen: dict[tuple[int, int], str] = {}
    seen_paths: set[str] = set()
    findings: list[dict[str, Any]] = []
    for path, dev_ino in path_inodes:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        if dev_ino is None:
            continue
        if dev_ino in seen:
            first = seen[dev_ino]
            findings.append(_error_finding(
                "duplicate-file",
                f"duplicate-file: {first} and {path} are the same file",
                f"ensure {first} and {path} refer to distinct files on disk",
            ))
        else:
            seen[dev_ino] = path
    return findings


def collect_hash_paths(
    files: list[dict[str, Any]],
    index_path: str,
    inputs: list[Any] | None = None,
) -> list[str]:
    """Return deduplicated list of paths to hash for H1/H2 (files, vendor_sources, inputs, index)."""
    raw, has_component = _walk_manifest_files_and_inputs(files, inputs, group_vendors=False)
    if has_component:
        raw.append(index_path)
    return _dedupe_ordered(raw)


def extract_stat_fingerprint(sha_res: dict[str, Any]) -> tuple[int | None, int | None]:
    """Extract (ino, ctime_ns) stat fingerprint from a sha256_file result dict."""
    raw_ino = sha_res.get("ino")
    raw_ctime = sha_res.get("ctime_ns")
    ino = int(raw_ino) if isinstance(raw_ino, int) and not isinstance(raw_ino, bool) else None
    ctime_ns = int(raw_ctime) if isinstance(raw_ctime, int) and not isinstance(raw_ctime, bool) else None
    return (ino, ctime_ns)


def sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate and sort findings deterministically by (id, detail)."""
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in findings:
        key = (
            str(item.get("id", "")),
            str(item.get("detail", "")),
            str(item.get("severity", "")),
            str(item.get("fix", "")),
        )
        if key not in seen:
            seen.add(key)
            unique.append(item)
    unique.sort(key=lambda f: (str(f.get("id", "")), str(f.get("detail", ""))))
    return unique


def build_unreadable_finding(path: str, error_msg: str) -> dict[str, Any]:
    """Construct a check-file-unreadable error finding."""
    return _error_finding(
        "check-file-unreadable",
        f"{path}: {error_msg}",
        f"ensure {path} exists and is a readable regular UTF-8 file",
    )


def build_gate_error_finding(path: str, error_msg: str) -> dict[str, Any]:
    """Construct a check-gate-error finding when audit_module reports status 'error'."""
    return _error_finding(
        "check-gate-error",
        f"{path}: {error_msg}",
        f"fix syntax or gate error in {path}",
    )


def normalize_gate_audit_result(
    path: str, role: str, audit_res: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize an architecture_gate.audit_module result into (gate_entry, findings)."""
    if audit_res.get("status") == "error":
        g_err = build_gate_error_finding(path, str(audit_res.get("error", "")))
        return {"path": path, "role": role, "status": "error", "findings": [g_err]}, [g_err]
    g_findings = list(audit_res.get("findings", []))
    return {"path": path, "role": role, "status": str(audit_res.get("status", "fail")), "findings": g_findings}, g_findings


def build_tests_failed_finding(rc: int, stdout: str, stderr: str) -> dict[str, Any]:
    """Construct a check-tests-failed finding with exit code and last 20 output lines."""
    combined = (stdout or "") + (stderr or "")
    tail = "\n".join(combined.splitlines()[-20:])
    detail = f"rc={rc}: {tail}" if tail else f"rc={rc}"
    return _error_finding(
        "check-tests-failed",
        detail,
        "fix failing tests so the test command exits with code 0",
    )


def detect_truncated_stream(run_res: dict[str, Any]) -> str | None:
    """Return the stream label ('stdout' or 'stderr') when run_argv reported truncated output."""
    if not run_res.get("truncated", False):
        return None
    err_str = str(run_res.get("error") or "")
    for stream in ("stdout", "stderr"):
        if stream in err_str:
            return stream
    out_len = len(str(run_res.get("stdout") or ""))
    err_len = len(str(run_res.get("stderr") or ""))
    return "stderr" if err_len > out_len else "stdout"


def diff_file_hashes(
    h1: dict[str, str | None],
    h2: dict[str, str | None],
    fp1: dict[str, tuple[int | None, int | None]] | None = None,
    fp2: dict[str, tuple[int | None, int | None]] | None = None,
) -> list[str]:
    """Return sorted list of file paths whose SHA-256 or stat fingerprint (ino, ctime_ns) changed."""
    all_paths = sorted(set(h1.keys()) | set(h2.keys()))
    changed: list[str] = []
    for p in all_paths:
        if h1.get(p) != h2.get(p):
            changed.append(p)
        elif fp1 is not None and fp2 is not None and fp1.get(p) != fp2.get(p):
            changed.append(p)
    return changed


def build_check_error_extra(name: str) -> dict[str, Any]:
    """Construct extra fields for JSON exit-2 output on componentize check."""
    return {
        "name": name,
        "status": "error",
        "findings": [],
        "check_result_sha": None,
    }


def build_error_last_check(*, name: str, error: str, at: str) -> dict[str, Any]:
    """Construct minimal error last_check.json record when check exits 2 under the lock."""
    return {
        "schema_version": 1,
        "name": name,
        "status": "error",
        "error": error,
        "findings": [],
        "at": at,
    }


def build_last_check(
    *, name: str, evidence: dict[str, Any],
    gate: list[dict[str, Any]], golden: dict[str, Any], tests: dict[str, Any],
    findings: list[dict[str, Any]], at: str, sha_fn: Any, force_error: bool = False,
    tool_schemas: list[dict[str, Any]] | None = None, plugin_inventory_sha: str | None = None,
) -> dict[str, Any]:
    """Construct last_check.json object and compute check_result_sha over core fields."""
    sorted_f = sort_findings(findings)
    has_err = any(f.get("severity") == "error" for f in sorted_f)
    status = "error" if force_error else ("fail" if has_err else "pass")
    core: dict[str, Any] = {
        "schema_version": 1, "name": name, "status": status, "findings": sorted_f,
        "manifest_gen": evidence["manifest_gen"], "manifest_sha": evidence["manifest_sha"],
        "baseline_gen": evidence["baseline_gen"], "baseline_shas": dict(evidence["baseline_shas"]),
        "file_hashes": dict(evidence["file_hashes"]), "checker_digest": evidence["checker_digest"],
        "runtime": evidence["runtime"], "attestations_sha": evidence["attestations_sha"],
        "amend_log_sha": evidence.get("amend_log_sha"),
        "gate": list(gate), "golden": dict(golden), "tests": dict(tests),
    }
    if tool_schemas is not None:
        core["tool_schemas"] = tool_schemas
        core["plugin_inventory_sha"] = plugin_inventory_sha
    return {**core, "at": at, "check_result_sha": str(sha_fn(core))}


def format_check_text(last_check: dict[str, Any]) -> str:
    """Format human-readable text summary for componentize check."""
    gate_list = last_check["gate"]
    gate_findings = sum(len(g.get("findings", [])) for g in gate_list)
    golden_sec = last_check["golden"]
    if "waived" in golden_sec:
        golden_str = "waived"
    elif golden_sec.get("matched"):
        golden_str = "matched"
    else:
        golden_str = "mismatch"
    tests_sec = last_check["tests"]
    if "waived" in tests_sec:
        tests_str = "waived"
    elif tests_sec.get("rc") == 0:
        tests_str = "pass"
    else:
        tests_str = "fail"
    lines = [
        f"gate: {len(gate_list)} files, {gate_findings} findings",
        f"golden: {golden_str}",
        f"tests: {tests_str}",
    ]
    for f in last_check["findings"]:
        lines.append(_format_finding_line(f))
    verdict = "PASS" if last_check["status"] == "pass" else "FAIL"
    lines.append(f"check {last_check['name']}: {verdict}")
    return "\n".join(lines)


def _validate_attest_entry(phase: str, idx: int, item: Any) -> str | None:
    if not isinstance(item, dict):
        return f"corrupt attest.json: {phase}[{idx}] must be a JSON object"
    if set(item.keys()) != _ATTEST_ENTRY_KEYS:
        return f"corrupt attest.json: {phase}[{idx}] must have exact keys 'text' and 'at'"
    text = item.get("text")
    at = item.get("at")
    if not isinstance(text, str) or len(text.strip()) < MIN_NOTE_CHARS:
        return f"corrupt attest.json: {phase}[{idx}].text must be a string >= {MIN_NOTE_CHARS} chars after trimming"
    if not isinstance(at, str) or not at.strip():
        return f"corrupt attest.json: {phase}[{idx}].at must be a non-empty string"
    return None


def validate_attest_data(attest_data: Any) -> str | None:
    """Validate attest.json against {kind_reason:str, reuse:str, plan|review|impl: list of {text, at}}."""
    if not isinstance(attest_data, dict):
        return "corrupt attest.json: must be a JSON object"
    for phase in _ATTEST_PHASES:
        if not isinstance(attest_data.get(phase), list):
            return f"corrupt attest.json: missing list for phase {phase!r}"
    if set(attest_data.keys()) != _ATTEST_TOP_KEYS:
        return "corrupt attest.json: keys must be exactly kind_reason, reuse, plan, review, impl"
    for key in ("kind_reason", "reuse"):
        if not isinstance(attest_data.get(key), str):
            return f"corrupt attest.json: {key} must be a string"
    for phase in _ATTEST_PHASES:
        for idx, item in enumerate(attest_data[phase]):
            err = _validate_attest_entry(phase, idx, item)
            if err is not None:
                return err
    return None


def missing_required_attestations(kind: str, attest_data: Any) -> tuple[list[str], str | None]:
    """Return (missing_phases, error) for finish attestation validation."""
    err = validate_attest_data(attest_data)
    if err is not None:
        return [], err
    if kind != "component":
        return [], None
    missing = [phase for phase in ("plan", "review") if not attest_data[phase]]
    return missing, None


def assemble_current_evidence(
    *,
    manifest_sha: str,
    manifest_gen: int,
    baseline_gen: int,
    baseline_shas: dict[str, str],
    file_hashes: dict[str, str],
    checker_digest: str,
    runtime: str,
    attestations_sha: str,
    amend_log_sha: str | None = None,
) -> dict[str, Any]:
    """Construct the 9-key current evidence dictionary matching CURRENT_KEYS."""
    return {
        "manifest_sha": manifest_sha,
        "manifest_gen": manifest_gen,
        "baseline_gen": baseline_gen,
        "baseline_shas": dict(baseline_shas),
        "file_hashes": dict(file_hashes),
        "checker_digest": checker_digest,
        "runtime": runtime,
        "attestations_sha": attestations_sha,
        "amend_log_sha": amend_log_sha,
    }


def assemble_checked_receipt_evidence(last_check: dict[str, Any]) -> dict[str, Any]:
    """Build receipt current evidence taking all checked values from last_check."""
    raw: dict[str, Any] = {k: last_check.get(k) if k == "amend_log_sha" else last_check[k] for k in _CHECKED_EVIDENCE_KEYS}
    raw_amend = raw["amend_log_sha"]
    return assemble_current_evidence(
        manifest_sha=str(raw["manifest_sha"]),
        manifest_gen=int(raw["manifest_gen"]),
        baseline_gen=int(raw["baseline_gen"]),
        baseline_shas=dict(raw["baseline_shas"]),
        file_hashes={k: str(v) for k, v in raw["file_hashes"].items()},
        checker_digest=str(raw["checker_digest"]),
        runtime=str(raw["runtime"]),
        attestations_sha=str(raw["attestations_sha"]),
        amend_log_sha=str(raw_amend) if raw_amend is not None else None,
    )


def build_receipt(
    *,
    name: str,
    current: dict[str, Any],
    last_check: dict[str, Any],
    created_at: str,
    digest_fn: Any,
    receipt_keys: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Assemble a schema v1 receipt.json dictionary bound by evidence_digest."""
    schema_version = int(last_check.get("schema_version", 1))
    check_result_sha = str(last_check["check_result_sha"])
    check_status = str(last_check["status"])
    ev_kwargs = {k: current.get(k) if k == "amend_log_sha" else current[k] for k in _CHECKED_EVIDENCE_KEYS}
    digest = str(digest_fn(
        name=name,
        schema_version=schema_version,
        **ev_kwargs,
        check_result_sha=check_result_sha,
        check_status=check_status,
    ))
    raw: dict[str, Any] = {
        "schema_version": schema_version,
        "name": name,
        **current,
        "check_result_sha": check_result_sha,
        "check_status": check_status,
        "created_at": created_at,
        "evidence_digest": digest,
    }
    return {k: raw[k] for k in receipt_keys} if receipt_keys is not None else raw


def extract_rebaseline_reason(baseline_gen: int, meta_data: Any) -> str | None:
    """Extract rebaseline reason when baseline_gen > 1 and meta.json has 'reason'."""
    if baseline_gen <= 1 or not isinstance(meta_data, dict):
        return None
    reason = meta_data.get("reason")
    return reason if isinstance(reason, str) and reason else None


def extract_amend_reason(manifest_gen: int, amend_log_data: Any) -> str | None:
    """Extract amend reason for manifest_gen when manifest_gen > 1 and amend-log.json has an entry."""
    if manifest_gen <= 1 or not isinstance(amend_log_data, list):
        return None
    for item in reversed(amend_log_data):
        if isinstance(item, dict):
            gen_val = item.get("gen", item.get("manifest_gen", item.get("to_gen")))
            reason = item.get("reason")
            if gen_val == manifest_gen and isinstance(reason, str) and reason:
                return reason
    return None


def format_status_ok_line(
    name: str,
    evidence_digest: str,
    manifest_gen: int,
    baseline_gen: int,
    *,
    rebaselined_reason: str | None = None,
    amended_reason: str | None = None,
) -> str:
    """Format the single-line OK output for `componentize status`."""
    line = (
        f"componentize status {name}: OK  receipt={evidence_digest[:12]} "
        f"manifest gen={manifest_gen} baseline gen={baseline_gen}"
    )
    if rebaselined_reason:
        line += f" (rebaselined: {rebaselined_reason})"
    if amended_reason:
        line += f" (amended: {amended_reason})"
    return line


def format_status_stale_text(
    name: str,
    stale: list[str],
    findings: list[dict[str, Any]],
) -> str:
    """Format human-readable STALE output for `componentize status`."""
    items_str = ", ".join(stale) if stale else "receipt"
    lines = [f"componentize status {name}: STALE ({items_str})"]
    for f in findings:
        lines.append(_format_finding_line(f))
    return "\n".join(lines)


def build_status_payload(
    *,
    ok: bool,
    exit_code: int,
    name: str,
    status: str,
    stale: list[str],
    findings: list[dict[str, Any]],
    evidence_digest: str | None,
) -> dict[str, Any]:
    """Construct JSON output dictionary for `componentize status`."""
    return {
        "ok": ok,
        "exit": exit_code,
        "name": name,
        "status": status,
        "stale": list(stale),
        "findings": list(findings),
        "evidence_digest": evidence_digest,
    }


def next_gen_number(current_gen: int) -> int:
    """Return the next sequential generation number (current_gen + 1)."""
    return int(current_gen) + 1


def build_rebaseline_meta(
    golden: dict[str, Any],
    stdout: str = "",
    stderr: str = "",
    rc_str: str = "",
    *,
    reason: str,
    at: str,
    previous_gen: int,
    manifest_gen: int,
) -> dict[str, Any]:
    """Construct baseline meta.json dictionary for a rebaselined generation (captured or waived)."""
    base = {"waived": golden["waived"]} if "waived" in golden else build_baseline_meta(golden, stdout, stderr, rc_str)
    return {
        **base,
        "reason": reason,
        "at": at,
        "previous_gen": previous_gen,
        "manifest_gen": manifest_gen,
    }


def diff_checked_state_evidence(
    last_check: dict[str, Any],
    current: dict[str, Any],
) -> list[str]:
    """Return sorted list of state evidence items that drifted between check and finish."""
    drifted: list[str] = []
    for key in _CHECKED_EVIDENCE_KEYS:
        if last_check.get(key) != current.get(key):
            drifted.append(key)
    return sorted(drifted)


def build_amend_log_entry(
    *, gen: int, reason: str, at: str, previous_gen: int,
) -> dict[str, Any]:
    """Construct an amend-log.json entry for a new manifest generation."""
    return {
        "gen": gen,
        "reason": reason,
        "at": at,
        "previous_gen": previous_gen,
    }


def merge_amend_log_entries(
    existing: list[Any],
    new_entry: dict[str, Any],
    *,
    target_gen_committed: bool,
) -> list[Any]:
    """Append new_entry to amend-log entries, dropping stale uncommitted entries for the same gen."""
    target_gen = new_entry.get("gen")
    if target_gen_committed:
        return [*existing, new_entry]
    filtered = [
        item for item in existing
        if not (isinstance(item, dict) and item.get("gen") == target_gen)
    ]
    return [*filtered, new_entry]


def golden_config_changed(prev_golden: dict[str, Any], new_golden: dict[str, Any]) -> bool:
    """Return True if golden waiver status, argv, cwd, env, or compare differ."""
    if ("waived" in prev_golden) != ("waived" in new_golden):
        return True
    for key in ("argv", "cwd", "env", "compare"):
        if prev_golden.get(key) != new_golden.get(key):
            return True
    return False
