import json
import time
from datetime import datetime, timezone
from pathlib import Path

import bleach
import mistune
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from markupsafe import Markup

app = Flask(__name__)

PROJECTS_ROOT = Path.home() / ".claude" / "projects"

MARKDOWN_TAGS = [
    "p", "br", "hr", "strong", "em", "del", "code", "pre", "blockquote",
    "ul", "ol", "li", "a", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "th", "td", "span", "img",
]
MARKDOWN_ATTRS = {
    "a": ["href", "title"],
    "img": ["src", "alt"],
    "code": ["class"],
    "span": ["class"],
}


def render_markdown(text: str) -> Markup:
    html = mistune.markdown(text, escape=True, hard_wrap=True)
    clean = bleach.clean(html, tags=MARKDOWN_TAGS, attributes=MARKDOWN_ATTRS, strip=True)
    return Markup(clean)


def id_from_path(path: Path) -> str:
    """The short id a transcript's own filename encodes (strips the "agent-" prefix
    subagent transcripts use), for display back to the user in place of a pasted path."""
    stem = path.stem
    if stem.startswith("agent-"):
        return stem[len("agent-"):]
    return stem


def find_transcript(id_: str):
    """Locate a session or subagent transcript .jsonl file by id (or accept a raw path)."""
    id_ = (id_ or "").strip()
    if not id_:
        return None

    direct = Path(id_).expanduser()
    if direct.is_file():
        return direct

    if not PROJECTS_ROOT.exists():
        return None

    candidates = list(PROJECTS_ROOT.rglob(f"{id_}.jsonl"))
    candidates += list(PROJECTS_ROOT.rglob(f"agent-{id_}.jsonl"))
    if not candidates:
        candidates = list(PROJECTS_ROOT.rglob(f"*{id_}*.jsonl"))

    if not candidates:
        return None

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def load_entries(path: Path):
    entries = []
    with path.open("r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def build_tool_result_map(entries):
    results = {}
    for e in entries:
        content = (e.get("message") or {}).get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    results[block.get("tool_use_id")] = block
    return results


def relative_time(ts: str) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts

    secs = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins, s = divmod(rem, 60)

    units = [(days, "d"), (hours, "h"), (mins, "m"), (s, "s")]
    parts = [f"{v}{u}" for v, u in units if v]
    if not parts:
        parts = ["0s"]
    return "".join(parts[:2]) + " ago"


TOOL_SUMMARY_KEYS = ("description", "file_path", "path", "pattern", "query", "url", "prompt")


def summarize_tool_input(input_dict) -> str:
    if not isinstance(input_dict, dict):
        return ""
    for key in TOOL_SUMMARY_KEYS:
        val = input_dict.get(key)
        if val:
            return str(val)
    for val in input_dict.values():
        if isinstance(val, (str, int, float)) and str(val):
            return str(val)
    return ""


def truncate(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def stringify_tool_output(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    parts.append(c.get("text", ""))
                elif c.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(json.dumps(c, ensure_ascii=False)[:2000])
            else:
                parts.append(str(c))
        return "\n".join(parts)
    return str(content)


def render_entries(entries):
    tool_results = build_tool_result_map(entries)
    turns = []

    for idx, e in enumerate(entries):
        msg = e.get("message") or {}
        role = msg.get("role") or e.get("type") or "unknown"
        content = msg.get("content")
        blocks = []

        if isinstance(content, str):
            if content.strip():
                blocks.append({
                    "kind": "text",
                    "id": f"text-{idx}",
                    "preview": truncate(content, 240),
                    "html": render_markdown(content),
                })
        elif isinstance(content, list):
            for bi, b in enumerate(content):
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "text":
                    text = b.get("text") or ""
                    if text.strip():
                        blocks.append({
                            "kind": "text",
                            "id": f"text-{idx}-{bi}",
                            "preview": truncate(text, 240),
                            "html": render_markdown(text),
                        })
                elif btype == "thinking":
                    text = b.get("thinking", "")
                    if text.strip():
                        blocks.append({
                            "kind": "thinking",
                            "id": f"think-{idx}-{bi}",
                            "preview": truncate(text, 240),
                            "html": render_markdown(text),
                        })
                elif btype == "tool_use":
                    result = tool_results.get(b.get("id"))
                    tool_input = b.get("input", {}) if isinstance(b.get("input"), dict) else {}
                    name = b.get("name", "tool")
                    is_bash = name.lower() == "bash"
                    blocks.append({
                        "kind": "tool",
                        "id": b.get("id") or f"tool-{idx}-{bi}",
                        "name": name,
                        "short": truncate(summarize_tool_input(tool_input)),
                        "is_bash": is_bash,
                        "command": tool_input.get("command", "") if is_bash else None,
                        "input": json.dumps(tool_input, indent=2, ensure_ascii=False),
                        "output": stringify_tool_output(result.get("content")) if result else None,
                        "is_error": bool(result and result.get("is_error")),
                        "pending": result is None,
                    })
                elif btype == "tool_result":
                    continue  # rendered inline with its matching tool_use block
                else:
                    dump = json.dumps(b, indent=2, ensure_ascii=False)[:2000]
                    blocks.append({
                        "kind": "text",
                        "id": f"text-{idx}-{bi}",
                        "preview": truncate(dump, 240),
                        "html": render_markdown(f"```\n{dump}\n```"),
                    })

        if not blocks:
            continue

        turns.append({
            "key": e.get("uuid") or f"line-{idx}",
            "role": role,
            "timestamp": e.get("timestamp", ""),
            "rel_time": relative_time(e.get("timestamp", "")),
            "blocks": blocks,
        })

    return turns


def is_real_user_message(content) -> bool:
    """True for an actual user-authored turn, false for a tool-result-only message
    (tool results are sent back as role "user" messages in the transcript)."""
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip()
            for b in content
        )
    return False


def compute_stats(entries):
    tool_calls = 0
    assistant_messages = 0
    user_messages = 0

    # Cumulative, for reference only: these double/triple-count context, since every
    # call resends the whole conversation-so-far as input (cache_read or cache_creation),
    # and a cache-expiry ("cache miss") re-write inflates cache_creation further.
    cumulative = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}

    # Each message's own (input + cache_read + cache_creation) is the FULL context size
    # at that point, not incremental — so "unique" input tokens is the size of context
    # at its largest point, not a sum across turns.
    peak_context_tokens = 0
    saw_usage = False

    for e in entries:
        msg = e.get("message") or {}
        role = msg.get("role")
        content = msg.get("content")
        if role == "assistant":
            assistant_messages += 1
        elif role == "user" and is_real_user_message(content):
            user_messages += 1

        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool_calls += 1

        usage = msg.get("usage")
        if isinstance(usage, dict):
            saw_usage = True
            inp = usage.get("input_tokens") or 0
            out = usage.get("output_tokens") or 0
            cw = usage.get("cache_creation_input_tokens") or 0
            cr = usage.get("cache_read_input_tokens") or 0

            cumulative["input"] += inp
            cumulative["output"] += out
            cumulative["cache_creation"] += cw
            cumulative["cache_read"] += cr

            peak_context_tokens = max(peak_context_tokens, inp + cw + cr)

    unique_input_tokens = peak_context_tokens
    unique_output_tokens = cumulative["output"]

    return {
        "tool_calls": tool_calls,
        "assistant_messages": assistant_messages,
        "user_messages": user_messages,
        "unique_input_tokens": unique_input_tokens,
        "unique_output_tokens": unique_output_tokens,
        "total_tokens": unique_input_tokens + unique_output_tokens,
        "cumulative_tokens": cumulative,
        "has_usage_data": saw_usage,
    }


@app.route("/api/stats/<path:id_>")
def api_stats(id_):
    path = find_transcript(id_)
    if not path:
        return jsonify({"error": "not found"}), 404
    return jsonify(compute_stats(load_entries(path)))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/find")
def api_find():
    path = find_transcript(request.args.get("id", ""))
    if not path:
        return jsonify({"found": False}), 404
    return jsonify({
        "found": True,
        "id": id_from_path(path),
        "path": str(path),
        "size": path.stat().st_size,
    })


@app.route("/render/<path:id_>")
def render_fragment(id_):
    path = find_transcript(id_)
    if not path:
        return "<div class='empty'>Transcript not found.</div>", 404
    turns = render_entries(load_entries(path))
    return render_template("_transcript.html", turns=turns)


@app.route("/stream/<path:id_>")
def stream(id_):
    path = find_transcript(id_)
    if not path:
        return jsonify({"error": "not found"}), 404

    def gen():
        last_sig = None
        yield "event: update\ndata: init\n\n"
        while True:
            try:
                st = path.stat()
                sig = (st.st_mtime_ns, st.st_size)
            except FileNotFoundError:
                sig = None
            if sig != last_sig:
                last_sig = sig
                yield "event: update\ndata: changed\n\n"
            else:
                yield "event: ping\ndata: keepalive\n\n"
            time.sleep(1)

    return Response(
        stream_with_context(gen()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(debug=True, port=5091)
