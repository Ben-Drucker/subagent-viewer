# subagent-viewer

A tiny Flask app for reading Claude Code session and subagent transcripts (`.jsonl` files
under `~/.claude/projects`) as a readable, live-updating chat view instead of raw JSON.

## Features

- Paste a session id, subagent id, or a full path to a transcript file — pasted paths
  resolve down to their id automatically.
- Live-streams new turns as the transcript file grows (via SSE), so you can watch an
  in-progress agent run.
- Renders assistant/user text as Markdown, with tool calls and thinking blocks in
  collapsible cards.
- Independent show/hide toggles for model output, model reasoning, user input, and tool
  calls, so long-running transcripts stay scannable.
- A stats bar with tool-call counts, turn counts, and token usage pulled directly from
  the API's own recorded `usage` data (not re-tokenized).

## Usage

```bash
pip install flask mistune bleach
python3 app.py
```

Then open `http://127.0.0.1:5091/?id=<session-or-subagent-id>`, or paste an id into the
box on the page.
