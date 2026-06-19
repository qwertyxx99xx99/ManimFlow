#!/usr/bin/env python3

import sys, os, subprocess, pathlib, shutil, json, re, threading, urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

_title = os.environ["ISSUE_TITLE"]
_body = os.environ.get("ISSUE_BODY", "").strip()
PROMPT = f"{_title}\n\n{_body}" if _body else _title
COMMENT_ID = os.environ["COMMENT_ID"]
GH_TOKEN = os.environ["GH_TOKEN"]
REPO = os.environ["REPO"]
LOCAL_PORT = 18642
MANIM_OUTPUT = pathlib.Path("manim_output")
EXA_URL = "https://demos.exa.ai/chatbot-demo/api/chat/stream"


def update_comment(body):
    payload = json.dumps({"body": body}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/issues/comments/{COMMENT_ID}",
        data=payload, method="PATCH",
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f"[warn] comment update failed: {e}", flush=True)


def _strip(t):
    i = t.find("\n\n```followups")
    if i >= 0:
        t = t[:i]
    return re.sub(r"\n\[.*?\]\s*$", "", t, flags=re.DOTALL).rstrip()


def _exa(messages):
    non_sys = [m for m in messages if m["role"] != "system"]
    sys_msg = next((m for m in messages if m["role"] == "system"), None)
    last = non_sys[-1]
    user_content = (
        "IMPORTANT: use plain ``` for ALL code blocks, never ```python or ```bash.\n\n"
        + (sys_msg["content"] if sys_msg else "")
        + "\n\n" + last["content"]
    )
    payload = json.dumps({
        "message": user_content,
        "history": [{"role": m["role"], "content": m["content"]} for m in non_sys[:-1]],
        "exaEnabled": False,
        "model": "google/gemini-2.5-flash",
        "searchType": "instant",
    }).encode()
    req = urllib.request.Request(EXA_URL, data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    full = ""; evt = None
    with urllib.request.urlopen(req, timeout=180) as resp:
        buf = b""
        for raw in resp:
            buf += raw
            lines = buf.split(b"\n"); buf = lines.pop()
            for line in lines:
                t = line.decode("utf-8", errors="replace").strip()
                if not t: continue
                if t.startswith("event:"): evt = t[6:].strip()
                elif t.startswith("data:") and evt == "content":
                    try: full += json.loads(t[5:].strip()).get("content", "")
                    except: pass
    return _strip(full)


class _TS(ThreadingMixIn, HTTPServer): daemon_threads = True
class _H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/v1/models":
            b = json.dumps({"object": "list", "data": [{"id": "manimator", "object": "model", "created": 0, "owned_by": "exa"}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        if self.path != "/v1/chat/completions": self.send_response(404); self.end_headers(); return
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        try:
            content = _exa(body.get("messages", []))
        except Exception as e:
            self.send_response(500); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode()); return
        rb = json.dumps({
            "id": "chatcmpl-local", "object": "chat.completion", "model": "manimator",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(rb))); self.end_headers(); self.wfile.write(rb)


srv = _TS(("127.0.0.1", LOCAL_PORT), _H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
print(f"LLM proxy on :{LOCAL_PORT}", flush=True)

# ── Plan ──────────────────────────────────────────────────────────────────────
print("\n=== Planning ===", flush=True)
payload = json.dumps({"model": "manimator", "messages": [
    {"role": "system", "content": (
        "You are a Manim animation planner. Output ONLY a numbered scene plan — "
        "no questions, no clarifications, no preamble, no code blocks. "
        "Scale scenes: short→3-4, default→5-6, long/detailed→8-12. "
        "Plain English per scene: shapes/colors, motion, equations/labels. Never ask anything."
    )},
    {"role": "user", "content": PROMPT},
], "max_tokens": 2048}).encode()
req = urllib.request.Request(
    f"http://127.0.0.1:{LOCAL_PORT}/v1/chat/completions",
    data=payload, headers={"Content-Type": "application/json", "Authorization": "Bearer dummy"},
)
with urllib.request.urlopen(req, timeout=180) as resp:
    raw = json.loads(resp.read())["choices"][0]["message"]["content"].strip()

raw = re.sub(r"```.*?```", "", raw, flags=re.DOTALL).strip()
for marker in ["Would you like", "Do you want", "Should I", "Could you clarify",
               "Can you provide", "What specific", "How long", "Any specific", "Let me know"]:
    for sep in ("\n\n", "\n"):
        idx = raw.find(sep + marker)
        if idx >= 0:
            raw = raw[:idx]
plan = raw.strip()
scene_count = len([l for l in plan.splitlines() if re.match(r'^\d+\.', l.strip())])
print(f"\n=== Scene Plan ({scene_count} scenes) ===\n{plan}\n{'='*40}", flush=True)

update_comment(
    f"🎬 **Render in progress**\n\nPrompt: `{PROMPT}`\n\n"
    f"✅ Deps installed\n"
    f"✅ Scene plan ({scene_count} scenes)\n"
    f"_⏳ Writing code & rendering (this takes 10–20 min)..._\n\n"
    f"<details><summary>Scene plan</summary>\n\n```\n{plan}\n```\n</details>"
)

# ── Setup ─────────────────────────────────────────────────────────────────────
if MANIM_OUTPUT.exists():
    shutil.rmtree(MANIM_OUTPUT)
MANIM_OUTPUT.mkdir(parents=True)

(MANIM_OUTPUT / "plan.md").write_text(
    f"# Animation Plan\n\n## Original request\n{PROMPT}\n\n## Scenes\n{plan}\n"
)
subprocess.run(["git", "init"], cwd=str(MANIM_OUTPUT), capture_output=True)
subprocess.run(["git", "add", "plan.md"], cwd=str(MANIM_OUTPUT), capture_output=True)
subprocess.run(["git", "commit", "-m", "init"], cwd=str(MANIM_OUTPUT), capture_output=True)

aider_env = {
    **os.environ,
    "OPENAI_API_KEY": "dummy",
    "GIT_AUTHOR_NAME": "aider", "GIT_AUTHOR_EMAIL": "aider@ci",
    "GIT_COMMITTER_NAME": "aider", "GIT_COMMITTER_EMAIL": "aider@ci",
}

task = (
    "Read plan.md and implement the animation as a Manim project.\n\n"
    "Write files in this order:\n"
    "1. All helper modules first (objects.py, helpers.py, etc.) with all reusable classes/functions\n"
    "2. scene.py last — imports from helpers, defines AnimScene(Scene) with construct()\n\n"
    "Do not leave any file empty. scene.py runs with:\n"
    "  python3 -m manim -pql --disable_caching scene.py AnimScene\n\n"
    "Rules:\n"
    "- AnimScene(Scene) in scene.py\n"
    "- MathTex(r'...') for all equations and math symbols\n"
    "- Text() only for plain prose labels\n"
    "- Arrow(start=..., end=...) — never left=/right= kwargs\n"
    "- Make it visually complete and polished"
)

# ── Aider ─────────────────────────────────────────────────────────────────────
print("\n=== Aider: coding + auto-fix loop ===", flush=True)
r = subprocess.run(
    ["aider", "--model", "openai/manimator",
     "--openai-api-base", f"http://127.0.0.1:{LOCAL_PORT}/v1",
     "--openai-api-key", "dummy",
     "--yes-always", "--no-auto-commits", "--no-pretty",
     "--no-show-model-warnings", "--no-check-update",
     "--test-cmd", "python3 -m manim -pql --disable_caching scene.py AnimScene 2>&1",
     "--auto-test", "--message", task,
     "plan.md"],
    cwd=str(MANIM_OUTPUT), text=True, timeout=1800, env=aider_env,
    stderr=subprocess.STDOUT,
)
print(f"aider exit: {r.returncode}", flush=True)

# ── Check output ──────────────────────────────────────────────────────────────
videos = [v for v in MANIM_OUTPUT.rglob("*.mp4") if v.stat().st_size > 50_000]
if not videos:
    print("FAILED: no valid video rendered", flush=True)
    sys.exit(1)

dest = pathlib.Path("animation.mp4")
shutil.copy(sorted(videos, key=lambda p: p.stat().st_mtime)[-1], dest)
print(f"SUCCESS {dest.stat().st_size:,} bytes", flush=True)
