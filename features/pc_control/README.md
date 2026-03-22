# PC Control

> Control your local PC through natural language — automate workflows, manage files, run scripts.

**Status**: 🔜 Coming soon
**Mode color**: `#5c9dff`
**Mode icon**: 💻

---

## Planned Capabilities

- Launch and control desktop applications
- File system operations (create, move, delete, search)
- Run local scripts and shell commands
- Manage clipboard, screenshots, window focus
- Automate repetitive desktop workflows

## Planned Tools

| Tool | Description |
|---|---|
| `run_local_command` | Execute a shell command on the local PC |
| `open_application` | Launch an application by name |
| `manage_files` | Create, move, copy, delete files and directories |
| `take_screenshot` | Capture the current screen |
| `type_text` | Type text into the focused application |
| `clipboard` | Read or write clipboard contents |

## Design Notes

Will use a local agent bridge process on the PC that communicates with the cloud server via WebSocket or polling. The bridge handles command execution and result streaming without exposing a public port.

## Contributing

Interested in building this? See [CONTRIBUTING.md](../../CONTRIBUTING.md) for the feature development guide.
