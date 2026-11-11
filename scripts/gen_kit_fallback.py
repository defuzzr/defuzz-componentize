#!/usr/bin/env python3
"""Regenerate BUNDLED_KITS_FALLBACK in desktop/plugin.js from vendor/kits/*/kit.json.

Usage: gen_kit_fallback.py [--check | --write]
  (default) print the generated list
  --check   exit 1 naming the kits whose fallback entry differs
  --write   rewrite the list in desktop/plugin.js in place
Standard library only.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_JS = ROOT / "desktop" / "plugin.js"
KITS = ROOT / "vendor" / "kits"
START = "export const BUNDLED_KITS_FALLBACK = ["
BLOCK = re.compile(r"export const BUNDLED_KITS_FALLBACK = \[\n.*?\n\]\n", re.S)


def js_str(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def entry(k):
    tags = ", ".join(js_str(t) for t in k["tags"])
    return (
        "  {\n"
        f"    id: {js_str(k['id'])},\n"
        f"    name: {js_str(k['name'])},\n"
        f"    summary: {js_str(k['summary'])},\n"
        f"    tags: [{tags}],\n"
        f"    availability: {js_str(k.get('availability', 'available'))},\n"
        f"    version: {int(k['version'])}\n"
        "  }"
    )


def load():
    kits = []
    for d in sorted(p for p in KITS.iterdir() if (p / "kit.json").is_file()):
        raw = json.loads((d / "kit.json").read_text(encoding="utf-8"))
        raw["id"] = d.name
        kits.append(raw)
    return kits


def main(argv):
    kits = load()
    block = START + "\n" + ",\n".join(entry(k) for k in kits) + "\n]\n"
    src = PLUGIN_JS.read_text(encoding="utf-8")
    m = BLOCK.search(src)
    if not m:
        print("gen_kit_fallback: BUNDLED_KITS_FALLBACK block not found in desktop/plugin.js")
        return 2
    if "--check" in argv:
        if m.group(0) == block:
            return 0
        bad = [k["id"] for k in kits if entry(k) not in m.group(0)]
        print("gen_kit_fallback: fallback differs from vendor kits: "
              + (", ".join(bad) or "ordering/format"))
        return 1
    if "--write" in argv:
        PLUGIN_JS.write_text(src[:m.start()] + block + src[m.end():], encoding="utf-8")
        return 0
    sys.stdout.write(block)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
