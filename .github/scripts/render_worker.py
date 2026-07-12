#!/usr/bin/env python3

import json
import os
import pathlib
import subprocess
import sys
import urllib.request


prompt_title = os.environ["ISSUE_TITLE"]
prompt_body = os.environ.get("ISSUE_BODY", "").strip()
prompt = f"{prompt_title}\n\n{prompt_body}" if prompt_body else prompt_title
comment_id = os.environ["COMMENT_ID"]
github_token = os.environ["GH_TOKEN"]
repository = os.environ["REPO"]


def update_comment(body):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/issues/comments/{comment_id}",
        data=json.dumps({"body": body}).encode(),
        method="PATCH",
        headers={
            "Authorization": f"Bearer {github_token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        urllib.request.urlopen(request, timeout=15)
    except Exception as error:
        print(f"[warn] comment update failed: {error}", flush=True)


update_comment(
    f"Render in progress\n\nPrompt: `{prompt_title}`\n\n"
    "Dependencies ready\n"
    "Pi is planning, consulting Manim documentation, coding, and rendering autonomously."
)

command = [
    sys.executable,
    "manimflow_pi.py",
    "--provider",
    "copilot",
    "--prompt",
    prompt,
    "--workspace",
    "manim_output",
    "--output",
    "animation.mp4",
    "--pi",
    str(pathlib.Path("node_modules/.bin/pi").resolve()),
    "--attempts",
    "6",
]
result = subprocess.run(command, text=True, timeout=3300)
if result.returncode:
    raise SystemExit(result.returncode)
if not pathlib.Path("animation.mp4").is_file():
    raise RuntimeError("Pi exited without producing animation.mp4")
