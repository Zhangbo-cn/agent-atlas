"""Scripted OpenAI-compatible endpoint used to drive real pi runs.

Every request is logged to /tmp/pi-lab/log/req-NNN.json so the trace shows
exactly what pi sent (including the compaction summarization prompt).
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = "/tmp/pi-lab"
LOG = os.path.join(ROOT, "log")
STATE = {"n": 0, "scenario": "threshold", "turn": 0, "overflow_fired": False}


def log_request(body, meta):
    STATE["n"] += 1
    n = STATE["n"]
    with open(os.path.join(LOG, f"req-{n:03d}.json"), "w") as f:
        json.dump({"n": n, "meta": meta, "body": body}, f, indent=1)
    msgs = body.get("messages", [])
    chars = len(json.dumps(msgs))
    line = {
        "n": n,
        "scenario": STATE["scenario"],
        "messages": len(msgs),
        "roles": [m.get("role") for m in msgs],
        "body_chars": chars,
        "est_tokens_chars_div_4": chars // 4,
        "has_tool_schema": bool(body.get("tools")),
        "stream": body.get("stream"),
    }
    with open(os.path.join(ROOT, "requests.log"), "a") as f:
        f.write(json.dumps(line) + "\n")
    return n


def is_summarization(body):
    msgs = body.get("messages", [])
    text = json.dumps(msgs)
    if "<conversation>" not in text:
        return False
    return any(
        marker in text
        for marker in (
            "context checkpoint summary",
            "PREFIX of a turn",
            "Update the existing structured summary",
            "<previous-summary>",
        )
    )


SUMMARY_TEXT = """## Goal
Finish the multi-step diagnostic sweep requested by the user.

## Constraints & Preferences
- Use the bash tool for each step.
- (none) beyond that

## Progress
### Done
- [x] Step 1: enumerated the synthetic log file
- [x] Step 2: measured padded line output
- [x] Step 3: measured a single oversized line

### In Progress
- [ ] Step 4: summarize findings

### Blocked
- (none)

## Key Decisions
- **Keep steps small**: each bash call prints a bounded amount so output stays inspectable.

## Next Steps
1. Run the remaining diagnostic step.
2. Report the totals.

## Critical Context
- Working directory: /tmp/pi-lab/work
- Commands used: seq, awk padding, tr on /dev/zero
"""

TOOL_STEPS = [
    {"command": "seq 1 3000", "description": "Print 3000 numbered lines"},
    {"command": "seq 1 5000 | awk '{print $0\" padded-padded-padded-padded-padded\"}'", "description": "Print 5000 padded lines"},
    {"command": "seq 1 200", "description": "Print 200 numbered lines"},
]


def sse(handler, payload_chunks, usage=None, finish="stop"):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "close")
    handler.end_headers()
    cid = "chatcmpl-lab"
    created = int(time.time())
    for delta, fr in payload_chunks:
        chunk = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": "lab-tiny",
            "choices": [{"index": 0, "delta": delta, "finish_reason": fr}],
        }
        handler.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        handler.wfile.flush()
    if usage:
        chunk = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": "lab-tiny",
            "choices": [],
            "usage": usage,
        }
        handler.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
    handler.wfile.write(b"data: [DONE]\n\n")
    handler.wfile.flush()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/reset"):
            scenario = "threshold"
            if "scenario=" in self.path:
                scenario = self.path.split("scenario=")[1].split("&")[0]
            STATE["scenario"] = scenario
            STATE["n"] = 0
            STATE["turn"] = 0
            STATE["overflow_fired"] = False
            open(os.path.join(ROOT, "requests.log"), "w").close()
            body = json.dumps({"scenario": scenario}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length)
        body = json.loads(raw)
        meta = {"path": self.path, "auth": self.headers.get("authorization")}
        is_summary = is_summarization(body)
        meta["kind"] = "summarization" if is_summary else "agent"
        n = log_request(body, meta)

        if is_summary:
            usage = {"prompt_tokens": len(json.dumps(body)) // 4, "completion_tokens": 220, "total_tokens": 0}
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
            sse(self, [({"role": "assistant", "content": SUMMARY_TEXT}, None), ({}, "stop")], usage)
            return

        msgs = body.get("messages", [])
        turn = STATE["turn"]
        STATE["turn"] += 1
        usage = {
            "prompt_tokens": len(json.dumps(msgs)) // 4,
            "completion_tokens": 60,
            "total_tokens": 0,
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

        if STATE["scenario"] == "overflow" and not STATE["overflow_fired"] and turn >= 2:
            payload = json.dumps(
                {
                    "error": {
                        "message": "Input length (265330) exceeds model's maximum context length (262144).",
                        "type": "invalid_request_error",
                        "code": "context_length_exceeded",
                    }
                }
            ).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            STATE["overflow_fired"] = True
            STATE["turn"] -= 1
            return

        steps = TOOL_STEPS
        if STATE["scenario"] == "silent":
            steps = [
                {"command": "seq 1 3000", "description": "Print 3000 numbered lines"},
                {"command": "seq 1 5000 | awk '{print $0\" padded-padded-padded-padded-padded\"}'", "description": "Print 5000 padded lines"},
                {"command": "seq 1 5000 | awk '{print $0\" padded-padded-padded-padded-padded\"}'", "description": "Print 5000 padded lines again"},
            ]
        if turn < len(steps):
            step = steps[turn]
            args = json.dumps(step)
            chunks = [
                ({"role": "assistant", "content": f"Running step {turn + 1}."}, None),
                (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": f"call_step_{turn + 1}",
                                "type": "function",
                                "function": {"name": "bash", "arguments": args},
                            }
                        ]
                    },
                    None,
                ),
                ({}, "tool_calls"),
            ]
            sse(self, chunks, usage)
            return

        text = "All diagnostic steps finished. Totals: 5 bash calls, 2 oversized outputs truncated."
        sse(self, [({"role": "assistant", "content": text}, None), ({}, "stop")], usage)


if __name__ == "__main__":
    os.makedirs(LOG, exist_ok=True)
    os.makedirs(os.path.join(ROOT, "work"), exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 4873), Handler)
    print("lab server on 4873", flush=True)
    server.serve_forever()
