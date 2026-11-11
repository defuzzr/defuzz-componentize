# vendored copy of the author's hermes_audit/componentize_survey.py (MIT licence, same as this plugin); do not edit
"""Static script inventory and advisory split proposals; never execute source."""
from __future__ import annotations

import ast
from pathlib import PurePath
from typing import Mapping, Any

__all__ = ["survey_sources"]


def survey_sources(sources: Mapping[str, str]) -> dict[str, Any]:
    """Inventory supplied Python sources and propose review-only component splits."""
    files = []
    splits = []
    findings = []
    for path, source in sorted(sources.items()):
        try:
            tree = ast.parse(source, filename=path)
        except (SyntaxError, ValueError) as exc:
            findings.append({"id": "survey-parse", "severity": "info",
                             "detail": f"{path}: {exc}", "fix": "repair source syntax and survey again"})
            continue
        functions = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        imports = sorted({alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
                         | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module})
        files.append({"path": path, "language": "python", "line_count": len(source.splitlines()),
                      "functions": functions, "imports": imports})
        candidates = [name for name in functions if name != "main"]
        if candidates:
            splits.append({"source": path, "component": f"components/{PurePath(path).stem}.py",
                           "component_functions": candidates,
                           "runner_functions": [name for name in functions if name == "main"],
                           "reason": "Review non-main functions for reusable logic; retain CLI and side effects in the runner.",
                           "requires_review": True})
    error = "one or more scripts could not be parsed" if findings else None
    return {"schema": "componentize.survey/1", "ok": error is None,
            "status": "error" if error else "pass", "error": error, "findings": findings,
            "files": files, "proposed_splits": splits}
