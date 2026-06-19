#!/usr/bin/env python3

import sys, os, subprocess, pathlib, shutil, json, re, urllib.request

PROMPT_TITLE = os.environ["ISSUE_TITLE"]
PROMPT_BODY = os.environ.get("ISSUE_BODY", "").strip()
PROMPT = f"{PROMPT_TITLE}\n\n{PROMPT_BODY}" if PROMPT_BODY else PROMPT_TITLE
COMMENT_ID = os.environ["COMMENT_ID"]
GH_TOKEN = os.environ["GH_TOKEN"]
COPILOT_TOKEN = os.environ["COPILOT_TOKEN"]
REPO = os.environ["REPO"]
MANIM_OUTPUT = pathlib.Path("manim_output")
COPILOT_BASE = "https://api.individual.githubcopilot.com"
COPILOT_MODEL = "gemini-2.5-pro"
COPILOT_HEADERS = {
    "Authorization": f"Bearer {COPILOT_TOKEN}",
    "Content-Type": "application/json",
    "Editor-Version": "vscode/1.85.0",
    "Copilot-Integration-Id": "vscode-chat",
}


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


def copilot_chat(messages):
    payload = json.dumps({"model": COPILOT_MODEL, "messages": messages}).encode()
    req = urllib.request.Request(f"{COPILOT_BASE}/chat/completions",
        data=payload, headers=COPILOT_HEADERS)
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"].strip()


# ── Plan ──────────────────────────────────────────────────────────────────────
print("\n=== Planning ===", flush=True)
plan = copilot_chat([
    {"role": "system", "content": (
        "You are a Manim animation planner. Output a plain numbered list of scenes. "
        "Each scene: one short line with the visual idea only, no formatting, no markdown, no bold, no bullets. "
        "Scale count to length: short->3-4, default->5-6, detailed->8-12. "
        "No preamble, no closing remarks."
    )},
    {"role": "user", "content": PROMPT},
])

scene_count = len([l for l in plan.splitlines() if re.match(r'^\d+\.', l.strip())])
print(f"\n=== Scene Plan ({scene_count} scenes) ===\n{plan}\n{'='*40}", flush=True)

update_comment(
    f"🎬 **Render in progress**\n\nPrompt: `{PROMPT_TITLE}`\n\n"
    f"✅ Deps installed\n"
    f"✅ Scene plan ({scene_count} scenes)\n"
    f"_⏳ Writing code & rendering (10-20 min)..._\n\n"
    f"<details><summary>Scene plan</summary>\n\n```\n{plan}\n```\n</details>"
)

# ── Setup workspace ───────────────────────────────────────────────────────────
if MANIM_OUTPUT.exists():
    shutil.rmtree(MANIM_OUTPUT)
MANIM_OUTPUT.mkdir(parents=True)

task_text = (
    f"Implement the following Manim animation plan as a working Python project.\n\n"
    f"Plan:\n{plan}\n\n"
    f"Requirements:\n"
    f"- Create helper modules first (objects.py, helpers.py, etc.) with reusable classes/functions\n"
    f"- Create scene.py last, defining AnimScene(Scene) that imports from helpers\n"
    f"- Test with: python3 -m manim -pql --disable_caching scene.py AnimScene\n"
    f"- Fix any errors until the command succeeds and produces an mp4\n"
    f"- Use MathTex(r'...') for equations, Text() for plain labels\n"
    f"- Arrow(start=..., end=...) — never left=/right= kwargs\n"
    f"- Make it visually complete and polished\n"
    f"Working directory: {MANIM_OUTPUT.resolve()}"
)

# ── OpenHands ─────────────────────────────────────────────────────────────────
WORKSPACE = pathlib.Path(os.environ.get("GITHUB_WORKSPACE", pathlib.Path.cwd()))
OH_PYTHON = WORKSPACE / ".venv-oh" / "bin" / "openhands"
MANIM_PYTHON = WORKSPACE / ".venv-manim" / "bin" / "python3"

oh_env = {
    **os.environ,
    "LLM_MODEL": f"openai/{COPILOT_MODEL}",
    "LLM_API_KEY": COPILOT_TOKEN,
    "LLM_BASE_URL": COPILOT_BASE,
}

# Update task to use the manim venv python explicitly
task_text = task_text.replace(
    "python3 -m manim",
    f"{MANIM_PYTHON} -m manim"
)

print("\n=== OpenHands: autonomous coding loop ===", flush=True)
r = subprocess.run(
    [str(OH_PYTHON), "--headless", "--override-with-envs",
     "--task", task_text],
    text=True, timeout=1800, env=oh_env,
    stderr=subprocess.STDOUT,
)
print(f"openhands exit: {r.returncode}", flush=True)

# ── Check output ──────────────────────────────────────────────────────────────
videos = [v for v in MANIM_OUTPUT.rglob("*.mp4") if v.stat().st_size > 50_000]
if not videos:
    print("FAILED: no valid video rendered", flush=True)
    sys.exit(1)

dest = pathlib.Path("animation.mp4")
shutil.copy(sorted(videos, key=lambda p: p.stat().st_mtime)[-1], dest)
print(f"SUCCESS {dest.stat().st_size:,} bytes", flush=True)
