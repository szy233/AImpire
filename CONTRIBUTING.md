# Contributing to AImpire

AImpire is designed to be extended. Each "mode" in the chat interface maps to a feature module on the backend. This guide explains how to add one.

---

## How a Feature Works

```
User selects mode in chat UI
        │
        ▼
send() in chat.html checks currentMode.available
        │
        ▼
POST /chat/stream  { message, project_id, session_id }
        │
        ▼
api_server.py → agent.process_message_stream()
        │
        ▼
Claude API (streaming) with feature-specific tools + system prompt
        │
        ▼
Tool calls dispatched in _execute_tool()
        │
        ▼
Streamed response back to mobile client
```

Each feature contributes:
- **Tools** — functions Claude can call (SSH commands, browser actions, file ops, etc.)
- **System prompt** — context that tells Claude what it can do and how to behave
- **Infrastructure** — whatever the tools need (SSH client, browser driver, API client, etc.)

---

## Step-by-Step: Adding a Feature

### 1. Create the feature directory

```bash
mkdir features/<your_feature>
```

### 2. Define your tools

In `core/agent.py`, add your tool schemas to `TOOL_DEFINITIONS`:

```python
{
    "name": "your_tool_name",
    "description": "What this tool does.",
    "input_schema": {
        "type": "object",
        "properties": {
            "param": {"type": "string", "description": "..."},
        },
        "required": ["param"],
    },
},
```

And handle execution in `Agent._execute_tool()`:

```python
elif name == "your_tool_name":
    result = your_implementation(params["param"])
    return json.dumps(result, ensure_ascii=False)
```

### 3. Update the system prompt

Add a section to `SYSTEM_PROMPT` in `core/agent.py` describing the new capability:

```python
SYSTEM_PROMPT = """...existing content...

[Your Feature Name]
- What you can do
- When to use which tools
- Any constraints or warnings
"""
```

### 4. Register the mode in the frontend

In `web/chat.html`, add an entry to the `MODES` array:

```js
{
    id: 'your_feature',
    name: '功能名称',
    icon: '🔧',
    color: '#ff6b6b',           // Accent color for this mode
    desc: '一句话描述这个功能做什么',
    available: true,            // Set false while still in development
},
```

### 5. Write documentation

Create `features/<your_feature>/README.md` following the template below.

---

## Feature README Template

```markdown
# [Feature Name]

> One-line description.

## What it does

Describe what the feature enables the user to do.

## Available Tools

| Tool | Description |
|---|---|
| `tool_name` | What it does |

## Setup

Any infrastructure the feature needs (SSH keys, API keys, browser drivers, etc.)

## Example Conversations

Show 2-3 example user messages and what happens.

## Limitations

Known constraints or things Claude cannot do with this feature.
```

---

## Code Conventions

- **Tool names**: `snake_case`, descriptive verbs (`ssh_run`, `read_file`, `create_experiment`)
- **Tool results**: Always return JSON strings or plain text; keep under 5000 chars
- **Error handling**: Return `"执行失败: {str(e)}"` on exception — never let tools raise
- **System prompt**: Write in Chinese (the target user base), be specific about workflows
- **Colors**: Pick a color distinct from existing modes (gpu=`#00e5a0`, pc=`#5c9dff`, web=`#ff9d5c`, data=`#c97dff`)

---

## Pull Request Checklist

- [ ] New tools added to `TOOL_DEFINITIONS` with clear descriptions
- [ ] Tool execution handled in `_execute_tool()`
- [ ] Mode added to `MODES` array in `chat.html`
- [ ] `features/<name>/README.md` written
- [ ] Tested end-to-end (mobile UI → API → tool → response)
- [ ] Sensitive credentials not hardcoded (use `configs/config.yaml`)
