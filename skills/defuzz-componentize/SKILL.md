---
name: defuzz-componentize
description: "Use when asked 'componentize <name>'. Gate component work."
version: 0.2.2
author: defuzzr
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [refactor, components, architecture, receipts, project-kits]
    related_skills: [test-driven-development]
---

# Defuzz Componentize (Hermes adapter)

Bundled Hermes skill ID: `defuzz-componentize:componentize`. Load this ID for the
explicit `componentize <name>` request; the underlying CLI remains
`componentize`, not `defuzz-componentize`.

The procedure itself is harness-neutral and ships with this plugin:
`../../vendor/componentize/PROCEDURE.md` (relative to this SKILL.md) (example spec next to it:
`spec.example.json`). This skill only adds the Hermes-specific way to run it.
**Done = `hermes componentize status <name>` exits 0, line pasted verbatim.**

## When to Use

- The user says **"componentize <name>"**. Only then — never self-trigger, not
  for `simplify-code` runs or general refactors. Project-kit setup (below) may
  also start when the user explicitly asks for a matching project layout.

## First run: the component library

Componentize needs one **component library** folder: reusable components in
`components/`, their index `components/COMPONENTS.md`, runners in
`scripts/`, specs in `specs/`, and an `AGENTS.md` carrying the Architecture
Standard the procedure refers to.

- Check it first: `hermes componentize library` (exit 0 = ready).
- If a command prints `no component library is set up yet`, stop and ask the
  user which folder to use, an existing one or a new one. Never choose for them.
  Then run `hermes componentize init /absolute/folder`.
- `init` creates only what is missing, never overwrites a starter file, keeps one
  managed block in `AGENTS.md`, appends missing `.gitignore` lines, and saves the
  folder as the default. Running it again is safe.
- Do the reuse audit (procedure step 2) against that library's `COMPONENTS.md`.
- A library that already uses `hermes_audit/` keeps that folder name; `library` shows which one is in use.

## Project kits

When a user asks to set up a new project folder of a kind a bundled kit covers:

1. Run `hermes componentize kit list` to see the kits shipped with this version (for example `video-production` and `game-assets`).
2. Show the user the matching kit's tree and rules with `hermes componentize kit show <id>`.
3. Ask the user which folder to use — never choose the folder for them.
4. Run `hermes componentize kit apply <id> /absolute/folder`. Do not hand-create the folders instead.

## Procedure

1. Read the bundled `../../vendor/componentize/PROCEDURE.md` and follow it step
   by step. It is the source of truth; if this skill disagrees, PROCEDURE.md wins.
2. Run the CLI through `terminal`:
   ```
   terminal(command='hermes componentize <subcommand> <name> ...', timeout=900)
   ```
3. Step 7 (independent review): dispatch a review subagent with `delegate_task`.
   Where your setup lets you choose the model for a subagent, run the review on
   a different model from the one that wrote the code; that per-task choice goes
   inside the task entry. If there is no such choice, dispatch the review
   anyway. The reviewer knows nothing of this session, so the plan path, spec
   path and commit under review go in `context`:
   `delegate_task(tasks=[{"goal": "Review the <name> plan", "context": "plan: <path>; spec: <path>; commit: <sha>"}])`
4. Step 8 (implement): more than two tasks -> one task per independent
   deliverable in a worktree, with two-stage review; evaluation material is
   written by a separate agent that has not seen the implementation, and repairs
   to a child's work go to a dispatched fixer on its own branch. Otherwise inline TDD.
5. Finish with `finish`, then `status`; paste the status line in the final message.

## Pitfalls

- A foreground `terminal` call is capped at 600 seconds; `timeout=900` runs the
  command as a background job whose result arrives later as a completion notice.
  Wait for that notice before reading `status`, and do not re-run the command.
- Never rebaseline to make a failing check pass — fix the code.
- Declare every test file and fixture in `inputs`; undeclared files are not in the receipt.

## Verification

- `componentize status <name>: OK  receipt=<12 hex> manifest gen=M baseline gen=B`, exit 0.

## Target and permissions

The target is the library saved by `init`. `--tools-root /absolute/target` overrides it for one command. With no saved library, the working directory is used only if it already holds `components/COMPONENTS.md` (or the older `hermes_audit/COMPONENTS.md`); otherwise the command stops with the first-run message. State defaults to `<tools-root>/.componentize` and is local to that checkout: a receipt does not travel with a clone, worktree or merge, so run `finish` again in each checkout before `status`. `init` gitignores `.componentize/` in the library; for other targets add the ignore rule yourself. A folder typed on the command line may be relative (to where the command runs); a relative saved `library_root` is refused, and `init`, `library`, `kit apply` and `kit check` refuse Hermes homes and system folders (an explicit `--tools-root` gets the same check as the saved setting; an explicit `--state-root` does not). A folder that contains a `..` segment is refused. No prompts: obtain authorization before running manifest-specified golden/test commands. There is no `scaffold` or `pack` subcommand. The bridge timeout defaults to 900 seconds.
