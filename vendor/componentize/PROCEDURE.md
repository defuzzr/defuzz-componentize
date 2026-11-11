# componentize — procedure

Turns a working script into a reusable component + thin runner per the
Architecture Standard in `AGENTS.md`, and proves it with the `componentize` CLI.
Works from any agent harness (Hermes, Codex, Claude Code) or by hand.

**Trigger:** only when the user says `componentize <name>`. Never self-start.
**Done:** `componentize status <name>` exits 0 and its output line is pasted
verbatim. Nothing else counts as done.

## Running the CLI

```sh
# Standalone: python3 <path-to>/componentize/componentize.py <subcommand> ...
# Hermes plugin: hermes componentize <subcommand> ... (no separate checkout needed)
componentize status <name> --json      # flags may go before or after the subcommand
```

Exit codes: **0** pass/ok · **1** fail or stale (your work or its inputs are
wrong or changed) · **2** error (usage, state, lock, or the tool could not run).
Every finding prints `id detail — fix: …`; apply the fix, don't weaken the gate.
State lives in `<target>/.componentize/<name>/`, where the target is
`--tools-root` (or the plugin's saved library), not the shell's working directory;
`--state-root` replaces `<target>/.componentize`. Never delete its `lock` file and
never hand-edit anything there. Every subcommand except `survey` writes state
(`status` writes its lock), so each one first resolves the state root once
(symlinks in the folders above it followed) and, judged on that one resolved path,
refuses before creating anything the filesystem root, the home folder or a folder
above it, anything inside a Hermes home (`~/.hermes`, and `HERMES_HOME` when it is
an absolute path), system folders, and a state root held directly by the home
folder, a folder above it or a top-level folder. The home is `HOME` when it is absolute,
otherwise the account's home folder; with neither, these subcommands refuse. Job
names must match `^[a-z0-9][a-z0-9_-]{0,63}$`. The command then opens the folders
of that same resolved path one by one without following symlinks and reads and
writes all job state only through those open folders. A symlink at the state root
or the job folder makes it refuse (exit 2). A folder of the resolved path replaced
by a symlink while the command runs makes it refuse, or, once that folder is
already open, leaves it writing in the folder it checked; a symlinked folder you
gave in the path that is retargeted after the resolution does not move the command
either. Job state is never written elsewhere. Names and paths in messages show
control and other invisible characters as escapes.

**Receipts are local to a checkout.** The state files record absolute paths: the
index is recorded on the resolved `--tools-root` (symlinks followed, like the state
root), so the same checkout opened through a symlinked folder, `..` or a trailing
slash verifies the same receipt; declared files and inputs are recorded in the form
the spec gave at `begin`. Moving or re-mounting the project therefore makes its
receipt stale or unreadable; run `finish` again at the new place (first
`begin --amend` with a spec whose paths point there). When
`begin` creates the state folder inside a git work tree, it writes
`.componentize/.gitignore` containing `*`, so git ignores the folder without an
edit to your own `.gitignore`, and prints one line saying so (it adds the file to
an existing `.componentize/` that lacks one; a differently named existing
`--state-root` is left alone and the line asks you to ignore it). An existing
`.gitignore` there is never rewritten: without a line that is only `*` (or with a
`!` line) `begin` says the folder is NOT ignored by git and what to add; a
symlink or anything but a regular file there makes `begin` refuse before it
creates anything. Because the
state is not committed, a receipt does not travel with a clone, another worktree,
or a merge; `status` in another checkout reports not begun or stale until you run
`finish` there.

| Command | Does | Under `.componentize/<name>/` |
|---|---|---|
| `survey <path> [--json]` | read-only Python script inventory and review-only proposed splits; no job, baseline, or receipt writes | reads nothing, writes nothing |
| `begin <name> --manifest spec.json` | validate spec, run golden twice (must match), save generation 1 | writes `attest.json`, `manifest/gen-1.json`, `baseline/gen-1/` |
| `attest <name> plan\|review\|impl "text"` | record a note (≥20 chars) | writes `attest.json`; deletes `receipt.json` |
| `check <name>` | gate each file by role, rerun golden and compare, run tests; fails if inputs change mid-run | reads manifest, baseline, notes; writes `last_check.json`; deletes `receipt.json` (a pass leaves no receipt) |
| `finish <name>` | check + required notes + full coverage → `receipt.json` | as `check`, then writes a new `receipt.json` only on a pass |
| `status <name>` | re-verify the receipt against current bytes on disk; OK also needs a `last_check.json` that matches the receipt (missing or different = stale); never re-runs anything. Receipts are local to this checkout (see above) | reads `receipt.json`, `last_check.json`, notes, manifest, baseline; never alters receipts or check results, but takes the job lock and writes its PID to `lock` |
| `rebaseline <name> --reason "…"` | new baseline generation from a fresh golden run. The tool refuses when the golden is waived, when the next generation folder already exists, or when the reason is under 20 characters; it does not check why the output changed | deletes `receipt.json`; writes `baseline/gen-N+1/` |
| `begin <name> --amend --manifest spec.json --reason "…"` | new spec generation; reason ≥20 chars; an unchanged spec stops with `no change` | deletes `receipt.json`; writes `manifest/gen-N+1.json`, appends `manifest/amend-log.json` |

Rule for the person, not checked by the tool: use `rebaseline` only when the
golden output changed on purpose; never use it to make a failing check pass.

## Read-only survey

`componentize survey <path> --json` accepts a regular `.py` file or a directory.
It reads source, never imports or executes the target, and writes only stdout/stderr
(including no local bytecode cache). It does not create `.componentize/survey/` or
touch existing jobs. Directory discovery skips symlinks, `.git`, `.componentize`,
`.venv`, `venv`, `__pycache__`, and `node_modules`. Other file types are outside
this Python-only inventory. An explicit symlink or special-file target is refused.

JSON schema identifier: `schema: "componentize.survey/1"`. The envelope contains
`ok`, `status`, `error`, `findings`, `path`, `exit`, `files`, and `proposed_splits`.
File rows include `path`, `language`, `line_count`, `functions`, and `imports`.
Split rows contain `source`, proposed `component` path, `component_functions`,
`runner_functions`, `reason`, and `requires_review: true`. Non-main top-level
functions are candidates, **not proven pure components**; review dependencies,
side effects and naming collisions before extraction. Output is deterministically
sorted by path. Exit **0** means inventory completed (including an empty directory);
exit **2** means usage/read/parse failure, possibly with partial results. No exit 1.
Survey does not authorize extraction and cannot establish completion evidence.

## Plugin manifest policy

| Field / policy | Contract |
|---|---|
| `hermes_plugins` (optional, schema v1/v2) | Ordered list of `{path, python, env?, budget_tokens?, timeout?}`; path resolves relative to spec, python must be absolute, paths unique; env is a non-secret string map; budget defaults to 2,000 (positive integer, not bool), timeout to 60 seconds (positive finite); empty list allowed |
| Trust model | Capture imports and calls `register()` on **trusted, reviewed plugins only**, just like running the spec's tests. Best-effort guards are **not a sandbox**: plugins can still read files or bypass guards. Never put secrets in spec env or `--env`. No ambient environment is merged; HOME, PATH, LC_ALL and PYTHONDONTWRITEBYTECODE are reserved and override spec values |
| Advice, never failure | **Budget and duplication findings are advice, never a failure** in check, finish, status or the standalone runner. Capture failures are unknown, not zero. Integrity/spec failures remain errors |
| Inventory rule | At begin/amend/check/status enumerate root `plugin.yaml` and all `**/*.py`, excluding `.git`, `.venv`, `venv`, `.worktrees`, `node_modules`, `__pycache__`. Every member must already be in `files` or `inputs` (never auto-inserted). Original/vendor_source alone do not satisfy this policy; duplicate declarations remain forbidden. All symlink leaves/directory aliases are refused, including in-root links, preserving no-follow hashing |
| Extra evidence | Declare schema-bearing non-Python resources in inputs. Interpreter-installed packages are outside coverage. Membership/sizes are digested within the hash window and bound in last-check; declared contents are independently hashed |
| Receipts after installation | **Every existing receipt becomes stale once this checker is installed**, even without hermes_plugins. Rerun check, finish, status. Receipt schema stays v1; report lives in authenticated last-check core. Status verifies core against receipt before display, never recaptures, and prints nothing without a report. Missing/corrupt/substituted last-check is stale, not a legacy empty report |

## Plugin schema reporting

`check` and `finish` measure declared `hermes_plugins` entries. The runner injects the bounded process-group executor and create a fresh temporary HOME.
The spec's tests also run with a fresh temporary HOME, created under `TMPDIR` (else `/tmp`) and removed afterwards; set `TMPDIR` to keep it off a disk or out of a folder you do not want written.
Output limits are UTF-8 bytes per stream (4 MiB); child redirected diagnostics are counted with a bounded sink.
The component's standalone fallback truncates **after** subprocess capture allocation and has no process-tree cleanup guarantee.

The estimate is captured **declared schemas under a default recording context**, not the credentialed,
availability-filtered or dynamically overridden model-facing surface, wire size, or billing. It approximates
uncategorized directory-plugin loading; category discovery/registration handles are not emulated.
Tool Search usually defers non-core tools until `tool_describe`; cost applies when described or sent eagerly.
Per-tool rounding need not sum to the once-rounded total. Duplication candidates require an old/new
validation-equivalence test before factoring; no automatic schema rewrite is performed.

## Steps

1. **Classify.** `component` (logic needed twice, or a check/audit) or
   `one_off` (stays one standalone file; runner rules only). Done when you can
   state the kind and a one-sentence reason.
2. **Reuse audit.** Read the library index `components/COMPONENTS.md` (`hermes_audit/COMPONENTS.md` in a library that has no `components/` index) and search the target for prior
   art. Decide import / extend-with-parameter / new. Done when the decision names
   the entries you considered (it goes in `attest.reuse`).
3. **Write `spec.json`** (start from `spec.example.json` next to this file; it
   is an illustration for a library laid out as `components/` + `scripts/` with
   the spec in `specs/`: replace its names, paths and commands with your own and
   create the files first, because `begin` baselines whatever the golden command
   prints, even an error):
   - `schema_version`: 1 remains supported; choose 2 for purpose-bearing specs.
     In v2 only files with role `component` require `purpose`: a string of at
     least 20 characters after trimming. Other roles and v1 do not require it.
   - `files`: every file you will ship, with a role — `component`, `runner`
     (has `main`), `runner_module` (runner-side helper), `vendored`
     (+ `vendor_source`), or `one_off`.
   - `original`: a backup copy of the old script.
   - `golden`: a command that runs the script **at its final path** (old code
     at `begin`, new code at `check`) **on a fixture, never live state**.
     `isolated: true` is your promise of that; the tool does not sandbox.
   - `tests`: a real test command (components cannot waive tests).
   - `inputs`: every test file and fixture the golden/tests commands read
     (required for components) — undeclared inputs are not covered by the receipt.
   - Path fields are absolute or relative to the spec file, in simplest form.
     `argv` entries are passed as-is: relative paths there resolve against
     that command's `cwd`.
4. **`begin`.** Fix every finding. Nondeterministic golden output is refused —
   pin mtimes, `now`, and tools (fake `git` on `PATH` via `golden.env`).
5. **one_off:** conform the file, `check` until 0, then `finish` and `status`.
6. **component — plan.** Public API table (each function returns the §3
   envelope), private helpers, behaviors to preserve, thin runner, vendoring if
   a plugin needs a copy. `attest plan "<path + summary>"`.
7. **component — independent review.** Get the plan reviewed by a separate
   reviewer (another agent session, a review subagent, or a human). Apply the
   findings. The reviewer must not be the implementer and must be given the
   plan, the spec and the exact commit under review. `attest review "<verdict + what changed>"`.
8. **component — implement.** Test-first. Add the `COMPONENTS.md` line for each
   public function in the same commit as the component. Include the file's
   purpose in that rendered line when supplied, e.g.
   `- \`sample.evaluate(data)\` — Purpose: Evaluate fixture data without side effects. — returns \`{ok,status,error,findings}\`.`
   The pure `componentize_evidence.render_component_index_line(entry,
   public_api="sample.evaluate(data)", returns="{ok,status,error,findings}")`
   helper renders this format without writing the index. Declare public exports
   with `__all__`: `component-public-interface` is warning-only when missing,
   not a blocker. Existing index checks still apply; the index is required for a
   component, so with no index at all `check` and `finish` fail with one
   `index-file-missing` line naming the file to create (`components/COMPONENTS.md`).
   Loop `check` until 0.
   If a standalone plugin vendors this component, refresh the vendored copy and
   its checksum manifest with the plugin's vendor refresh script after the
   component changes, and re-run the plugin's own tests.
   `attest impl "<commits>"`.
9. **`finish`, then `status`.** Paste the status line verbatim.

## What the tool enforces, and what it doesn't

- Enforced: role rules on every declared file; golden output unchanged;
  tests pass; nothing declared changed during the run; `status` OK only while
  every declared file, input, spec/baseline generation, the tool's own code,
  the Python runtime and the notes are byte-identical to what was checked.
- Required to exist, not judged: plan and review notes (order and quality are
  on the reviewer). `impl` is optional.
- Receipt schema stays v1; purpose is bound through the existing manifest hash.
  Existing receipt validation is unchanged. As before, a checker-code update
  makes previously captured checker digests stale and requires a fresh finish.
- Not covered: files you didn't declare; a process that ignores the lock and
  edits `.componentize/`; golden/tests commands that detach (`setsid`,
  double-fork) — run them in the foreground. POSIX only.
- `rebaseline` shows forever in `status` as `(rebaselined: …)`. Never use it
  to make a failing check pass (a rule for the person; the tool cannot tell).
