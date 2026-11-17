"""E2E: the agent-lights plugin on a real Hermes checkout.

Copies the plugin into a temp HERMES_HOME, enables it with the real
``hermes plugins enable``, then runs one real agent turn on the CLI platform
against a local fake model that calls ``clarify`` and then answers. Passes
when the session's slot file moves working -> human_intervention -> working
-> final_answer. Nothing outside <work_dir> is touched.

Run with that checkout's venv python, from the checkout:
    .venv/bin/python -I <this file> <plugin_dir> <empty_work_dir>
Set E2E_CLARIFY_STYLE=single for releases whose clarify tool takes one
question (v2026.8.31 and earlier).
"""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

plugin_dir, work = Path(sys.argv[1]), Path(sys.argv[2])
hermes_home = work / "hermes_home"
lights_home = work / "lights"
hermes_home.mkdir(parents=True, exist_ok=True)
os.environ.update({
    "HERMES_HOME": str(hermes_home),
    "HERMES_AGENT_LIGHTS_HOME": str(lights_home),
    "HERMES_AGENT_LIGHTS_AUTO_LAUNCH": "0",
})
for key in ("HERMES_SLOT", "HERMES_KANBAN_TASK"):
    os.environ.pop(key, None)
slot_file = lights_home / "agent-lights" / "v2" / "slots" / "1.json"


def slot_state():
    try:
        return json.loads(slot_file.read_text())["state"]
    except FileNotFoundError:
        return None


observed = []  # (moment, slot state)

# -- install (copy, as install.sh does) + enable through the real CLI --------------------------------
hermes = str(Path(sys.executable).with_name("hermes"))
import shutil  # noqa: E402
shutil.copytree(plugin_dir, hermes_home / "plugins" / "agent-lights",
                ignore=shutil.ignore_patterns("__pycache__"))
for argv in ([hermes, "plugins", "enable", "agent-lights"],):
    done = subprocess.run(argv, capture_output=True, text=True, input="y\n", timeout=120)
    print("$", " ".join(argv[1:]), "->", done.returncode)
    print((done.stdout + done.stderr).strip()[-600:])
    if done.returncode != 0:
        sys.exit(1)

# -- fake OpenAI-compatible model ------------------------------------------
requests_seen = []
CLARIFY_ARGS = (
    {"questions": [{"question": "Which color?", "choices": ["red", "blue"]}]}
    if os.environ.get("E2E_CLARIFY_STYLE") != "single"
    else {"question": "Which color?", "choices": ["red", "blue"]}
)


class Model(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests_seen.append(body)
        observed.append((f"model request {len(requests_seen)}", slot_state()))
        has_tool_result = any(m.get("role") == "tool" for m in body.get("messages", []))
        if not has_tool_result:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "clarify", "arguments": json.dumps(
                    CLARIFY_ARGS)},
            }]}
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": "Blue it is."}
            finish = "stop"
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            delta = dict(message)
            if delta.get("tool_calls"):
                delta["tool_calls"] = [{**delta["tool_calls"][0], "index": 0}]
            for chunk in (
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
            ):
                chunk.update({"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "fake"})
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        payload = {"id": "x", "object": "chat.completion", "created": 0, "model": "fake",
                   "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        data = json.dumps({"data": [{"id": "fake", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
threading.Thread(target=server.serve_forever, daemon=True).start()
base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"

# -- a real agent turn on the CLI platform ---------------------------------
import model_tools  # noqa: E402,F401  (triggers plugin discovery)
from hermes_cli.plugins import get_plugin_manager  # noqa: E402
from run_agent import AIAgent  # noqa: E402

manager = get_plugin_manager()
loaded = {name: p.enabled for name, p in manager._plugins.items() if "agent-lights" in name}
print("plugin loaded/enabled:", loaded)


def clarify_callback(questions, *rest, **kwargs):
    observed.append(("inside clarify prompt", slot_state()))
    if isinstance(questions, list):  # current batch API
        return {"answers": {questions[0]["qid"]: "blue"}, "outcome": "answered"}
    return "blue"  # older releases: callback(question, choices)


agent = AIAgent(
    base_url=base_url, api_key="sk-fake", provider="custom", api_mode="chat_completions",
    model="fake", platform="cli", quiet_mode=True, skip_context_files=True, skip_memory=True,
    enabled_toolsets=["clarify"], clarify_callback=clarify_callback,
)
result = agent.run_conversation("Pick a color for me.")
observed.append(("after turn", slot_state()))
print("final response:", result.get("final_response"))
print("observed:")
for moment, state in observed:
    print(f"  {moment:24} {state}")

# Hermes may make auxiliary model calls before the turn (no slot yet), so
# compare the sequence of distinct states the session moved through.
states = []
for _, state in observed:
    if state is not None and (not states or states[-1] != state):
        states.append(state)
expected = ["working", "human_intervention", "working", "final_answer"]
ok = states == expected and observed[-1] == ("after turn", "final_answer")
print("states:", states)
print("E2E", "PASS" if ok else f"FAIL (expected {expected})")
sys.exit(0 if ok else 1)
