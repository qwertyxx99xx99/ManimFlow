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


def copilot_chat(messages, max_tokens=2048):
    payload = json.dumps({
        "model": COPILOT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(
        f"{COPILOT_BASE}/chat/completions",
        data=payload, headers=COPILOT_HEADERS,
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"].strip()


# ── Plan ──────────────────────────────────────────────────────────────────────
print("\n=== Planning ===", flush=True)
raw = copilot_chat([
    {"role": "system", "content": (
        "You are a Manim animation planner. Output ONLY a numbered scene plan — "
        "no questions, no clarifications, no preamble, no code blocks. "
        "Scale scenes: short→3-4, default→5-6, long/detailed→8-12. "
        "Plain English per scene: shapes/colors, motion, equations/labels. Never ask anything."
    )},
    {"role": "user", "content": PROMPT},
])

plan = raw
scene_count = len([l for l in plan.splitlines() if re.match(r'^\d+\.', l.strip())])
print(f"\n=== Scene Plan ({scene_count} scenes) ===\n{plan}\n{'='*40}", flush=True)

update_comment(
    f"🎬 **Render in progress**\n\nPrompt: `{PROMPT_TITLE}`\n\n"
    f"✅ Deps installed\n"
    f"✅ Scene plan ({scene_count} scenes)\n"
    f"_⏳ Writing code & rendering (10–20 min)..._\n\n"
    f"<details><summary>Scene plan</summary>\n\n```\n{plan}\n```\n</details>"
)

# ── Setup workspace ───────────────────────────────────────────────────────────
if MANIM_OUTPUT.exists():
    shutil.rmtree(MANIM_OUTPUT)
MANIM_OUTPUT.mkdir(parents=True)

(MANIM_OUTPUT / "plan.md").write_text(
    f"# Animation Plan\n\n## Original request\n{PROMPT}\n\n## Scenes\n{plan}\n"
)
subprocess.run(["git", "init"], cwd=str(MANIM_OUTPUT), capture_output=True)
subprocess.run(["git", "add", "plan.md"], cwd=str(MANIM_OUTPUT), capture_output=True)
subprocess.run(["git", "commit", "-m", "init"], cwd=str(MANIM_OUTPUT), capture_output=True)

# ── Aider ─────────────────────────────────────────────────────────────────────
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

aider_env = {
    **os.environ,
    "OPENAI_API_KEY": COPILOT_TOKEN,
    "GIT_AUTHOR_NAME": "aider", "GIT_AUTHOR_EMAIL": "aider@ci",
    "GIT_COMMITTER_NAME": "aider", "GIT_COMMITTER_EMAIL": "aider@ci",
}

print("\n=== Fetching Manim docs ===", flush=True)
docs_dir = pathlib.Path("manim_docs")
if not docs_dir.exists():
    subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", "--depth=1",
                    "https://github.com/ManimCommunity/manim.git", str(docs_dir)], check=True)
    subprocess.run(["git", "sparse-checkout", "set", "docs/source/reference_index",
                    "docs/source/tutorials"], cwd=str(docs_dir), check=True)
    subprocess.run(["git", "checkout"], cwd=str(docs_dir), check=True)

doc_files = list(docs_dir.rglob("*.rst"))
read_args = [arg for f in doc_files for arg in ["--read", str(f)]]

print("\n=== Aider: coding + auto-fix loop ===", flush=True)
r = subprocess.run(
    ["aider",
     "--model", f"openai/{COPILOT_MODEL}",
     "--openai-api-base", COPILOT_BASE,
     "--openai-api-key", COPILOT_TOKEN,
     "--yes-always", "--no-auto-commits", "--no-pretty",
     "--no-show-model-warnings", "--no-check-update",
     "--test-cmd", "python3 -m manim -pql --disable_caching scene.py AnimScene 2>&1",
     "--auto-test", "--message", task,
     *read_args,
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
