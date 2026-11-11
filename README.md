# defuzz-componentize

An optional companion to Defuzz that guides turning existing Python scripts into tested, reusable components and checks them against saved baseline output.
It also ships project kits: folder layouts, starter files and written rules, not working applications.

## Requirements

- Linux; Python 3.13 or newer; Hermes meeting `requires_hermes` in [plugin.yaml](plugin.yaml).

## Install

```sh
hermes plugins install defuzzr/defuzz-componentize
hermes plugins enable defuzz-componentize
```

## Use

- `hermes componentize init <folder>`: create a component library and try to save its path in settings.
- `hermes componentize survey <path>`: read Python source and suggest splits for review.
- `hermes componentize kit list`: list kits before choosing an explicit ID for `kit show`, `kit apply` or `kit check`.
- `hermes componentize --help`: command help; see the [procedure](vendor/componentize/PROCEDURE.md) and [example spec](vendor/componentize/spec.example.json) for the checked-component workflow.

Kits are also listed in Desktop under **Settings → Plugins → Componentize**; their definitions live in [`vendor/kits/`](vendor/kits/).

## What it does on your machine

- **Reads:** selected project/library source, indexes, specs, declared files and inputs, saved job evidence and optional plugin trees; Desktop reads active-profile settings through Hermes and inspects the library.
- **Writes:** kits create missing folders/files, update their `AGENTS.md` block and append ignore/LFS rules; conflicts can leave partial setup. `init` and Desktop save settings through Hermes, whose config reads may create directories/backups. Job evidence and locks live in `<library>/.componentize/<name>/` (indexed current folder fallback; `--state-root` overrides). Keep state outside Hermes homes. Tests/plugin measurement create temporary homes; interruption can leave them behind.
- **Runs:** isolated Python workflow children; spec-supplied baseline/test commands; optional Python imports and `register()` calls for plugin measurement. These have host permissions, no filesystem/network sandbox and no additional plugin approval prompt: review them first. Libc lookup can run `ldconfig`, `gcc`/`cc`, `objdump` or `ld` outside workflow limits, with fallbacks inheriting the host environment. Manual maintenance scripts run Git; kit text is data.
- **Network:** the adapter has no outbound service integration or setup-time binary downloads; Desktop talks to Hermes. Executed commands and imported plugins can contact third parties and send data.
- **Background processes:** no installed persistent service; outer cancellation can leave nested/detached commands running without their supervising CLI enforcing deadlines.
- **Credentials and telemetry:** the adapter has no credential store or telemetry integration; executed commands/plugins can access host files and have their own credential use/reporting. Keep secrets out of specs.

## Uninstall

Run `hermes plugins remove defuzz-componentize`.
Project/library files and job evidence remain; review and remove them separately.

- **Licence:** [MIT](LICENSE).
- **Reporting:** contact the plugin distributor with the command, `hermes componentize version` output and a redacted error.
