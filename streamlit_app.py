import json
import os
import pathlib
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

import streamlit as st


BASE = pathlib.Path("/tmp/manimflow_workspace")
BASE.mkdir(parents=True, exist_ok=True)
APP_DIR = pathlib.Path(__file__).resolve().parent
LOCAL_PI = APP_DIR / "node_modules" / ".bin" / "pi"
RUNTIME_DIR = pathlib.Path("/tmp/manimflow_pi_runtime")
NODE_DIR = pathlib.Path("/tmp/manimflow_node")
PI_VERSION = "0.80.6"
_PI_INSTALL_LOCK = threading.Lock()

GITHUB_CLIENT_ID = "8b76dd0df855d8bc7db1"
COPILOT_BASE = "https://api.individual.githubcopilot.com"
COPILOT_MODEL = os.environ.get("COPILOT_MODEL", "gpt-4o")
PI_PROVIDER = "manimflow-copilot"
PI_TOKEN_ENV = "MANIMFLOW_COPILOT_TOKEN"

def request_device_code():
    data = urllib.parse.urlencode(
        {"client_id": GITHUB_CLIENT_ID, "scope": "read:user"}
    ).encode()
    req = urllib.request.Request(
        "https://github.com/login/device/code",
        data=data,
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read())


def deployment_token():
    try:
        value = st.secrets.get("COPILOT_TOKEN", "")
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get("COPILOT_TOKEN", "").strip()


def poll_token(device_code):
    data = urllib.parse.urlencode(
        {
            "client_id": GITHUB_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
    ).encode()
    req = urllib.request.Request(
        "https://github.com/login/oauth/access_token",
        data=data,
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read())


def copilot_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Editor-Version": "vscode/1.85.0",
        "Copilot-Integration-Id": "vscode-chat",
        "Openai-Intent": "conversation-completions",
    }


def copilot_chat(token, messages):
    payload = json.dumps({"model": COPILOT_MODEL, "messages": messages}).encode()
    req = urllib.request.Request(
        f"{COPILOT_BASE}/chat/completions",
        data=payload,
        headers=copilot_headers(token),
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        body = json.loads(response.read())
    return body["choices"][0]["message"]["content"].strip()


def node_major(node_command):
    try:
        version = subprocess.check_output(
            [node_command, "--version"], text=True, timeout=10
        ).strip()
        return int(version.removeprefix("v").split(".", 1)[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def resolve_pi_command(log_queue):
    configured = os.environ.get("PI_COMMAND", "").strip()
    if configured:
        return configured
    if LOCAL_PI.exists():
        return str(LOCAL_PI)
    installed = shutil.which("pi")
    if installed and node_major(shutil.which("node") or "node") >= 22:
        return installed

    runtime_pi = RUNTIME_DIR / "node_modules" / ".bin" / "pi"
    if runtime_pi.exists():
        return str(runtime_pi)

    with _PI_INSTALL_LOCK:
        if runtime_pi.exists():
            return str(runtime_pi)

        node = shutil.which("node")
        npm = shutil.which("npm")
        if not node or node_major(node) < 22 or not npm:
            log_queue.put(("log", "Installing an isolated Node 22 runtime..."))
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "nodeenv",
                    "--node=22.19.0",
                    "--prebuilt",
                    str(NODE_DIR),
                ],
                check=True,
                timeout=300,
            )
            node = str(NODE_DIR / "bin" / "node")
            npm = str(NODE_DIR / "bin" / "npm")

        log_queue.put(("log", f"Installing Pi {PI_VERSION}..."))
        install_env = os.environ.copy()
        install_env["PATH"] = f"{pathlib.Path(node).parent}:{install_env.get('PATH', '')}"
        subprocess.run(
            [
                npm,
                "install",
                "--prefix",
                str(RUNTIME_DIR),
                "--no-audit",
                "--no-fund",
                f"@earendil-works/pi-coding-agent@{PI_VERSION}",
            ],
            check=True,
            env=install_env,
            timeout=300,
        )
        if not runtime_pi.exists():
            raise RuntimeError("Pi installation completed but its executable was not found.")
        return str(runtime_pi)


def write_pi_config(agent_dir):
    agent_dir.mkdir(parents=True, exist_ok=True)
    models = {
        "providers": {
            PI_PROVIDER: {
                "baseUrl": COPILOT_BASE,
                "api": "openai-completions",
                "apiKey": f"${PI_TOKEN_ENV}",
                "authHeader": True,
                "headers": {
                    "Editor-Version": "vscode/1.85.0",
                    "Copilot-Integration-Id": "vscode-chat",
                    "Openai-Intent": "conversation-completions",
                },
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                },
                "models": [
                    {
                        "id": COPILOT_MODEL,
                        "name": f"GitHub Copilot {COPILOT_MODEL}",
                        "reasoning": False,
                        "input": ["text"],
                        "contextWindow": 128000,
                        "maxTokens": 16384,
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                    }
                ],
            }
        }
    }
    (agent_dir / "models.json").write_text(json.dumps(models, indent=2))
    (agent_dir / "settings.json").write_text(
        json.dumps({"telemetry": False, "quietStartup": True}, indent=2)
    )


def write_project_files(workspace, user_prompt, plan):
    render_command = f'"{sys.executable}" -m manim -pql --disable_caching scene.py AnimScene'
    (workspace / "plan.md").write_text(
        f"# Animation Plan\n\n## Original request\n{user_prompt}\n\n## Scenes\n{plan}\n"
    )
    (workspace / "AGENTS.md").write_text(
        "You are an autonomous coding agent building a Manim animation.\n"
        "Read plan.md before editing. Work without asking questions.\n"
        "Create helper modules first and scene.py last.\n"
        "Every Python file must start with: from manim import *\n"
        "scene.py must define AnimScene(Scene).\n"
        "Use MathTex(r'...') for equations and Text() for plain labels.\n"
        "Use Arrow(start=..., end=...) for arrows.\n"
        "Keep every object inside the camera frame and avoid overlaps.\n"
        "Repeatedly run this test and repair every error:\n"
        f"{render_command}\n"
        "Stop only after the command succeeds and produces an MP4.\n"
    )


def newest_video(workspace):
    videos = [
        path
        for path in workspace.rglob("*.mp4")
        if path.is_file() and path.stat().st_size > 50_000
    ]
    return max(videos, key=lambda path: path.stat().st_mtime) if videos else None


def clean_old_runs(max_age_seconds=6 * 60 * 60):
    cutoff = time.time() - max_age_seconds
    for path in BASE.glob("run-*"):
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path)
        except OSError:
            pass


def concise_pi_event(line):
    line = line.strip()
    if not line:
        return None
    # These token-level events contain an increasingly large copy of the whole
    # partial message. Parsing or rendering them can freeze a Streamlit session.
    if line.startswith('{"type":"message_update"'):
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return line[:1000]

    event_type = event.get("type")
    if event_type == "agent_start":
        return "Pi agent started."
    if event_type == "agent_end":
        return "Pi agent finished."
    if event_type != "message_end":
        return None

    message = event.get("message", {})
    role = message.get("role")
    content = message.get("content", [])
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]

    updates = []
    for block in content:
        block_type = block.get("type")
        if block_type == "toolCall":
            name = block.get("name", "tool")
            arguments = block.get("arguments") or {}
            if name in {"write", "edit", "read"}:
                detail = arguments.get("path", "")
            elif name == "bash":
                detail = arguments.get("command", "")
            else:
                detail = ""
            detail = str(detail).replace("\n", " ")[:300]
            updates.append(f"Pi → {name}: {detail}".rstrip())
        elif block_type == "text" and role in {"assistant", "toolResult"}:
            text = str(block.get("text", "")).strip()
            if text:
                prefix = "Result" if role == "toolResult" else "Pi"
                updates.append(f"{prefix}: {text[:1000]}")
    return "\n".join(updates) or None


def run_render(token, user_prompt, log_queue):
    clean_old_runs()
    run_dir = pathlib.Path(tempfile.mkdtemp(prefix="run-", dir=BASE))
    workspace = run_dir / "project"
    agent_dir = run_dir / "pi-agent"
    workspace.mkdir()

    try:
        pi_command = resolve_pi_command(log_queue)

        log_queue.put(("log", "Planning scenes..."))
        plan = copilot_chat(
            token,
            [
                {
                    "role": "system",
                    "content": (
                        "You are a Manim animation planner. Output a plain numbered "
                        "list of visual scenes with no markdown or commentary. Use 3-4 "
                        "scenes for a short request, 5-6 by default, and 8-12 when detailed."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
        )
        log_queue.put(("plan", plan))

        write_project_files(workspace, user_prompt, plan)
        write_pi_config(agent_dir)

        env = os.environ.copy()
        env[PI_TOKEN_ENV] = token
        env["PI_CODING_AGENT_DIR"] = str(agent_dir)
        env["PI_TELEMETRY"] = "0"
        env["PATH"] = f"{pathlib.Path(sys.executable).parent}:{env.get('PATH', '')}"
        if (NODE_DIR / "bin" / "node").exists():
            env["PATH"] = f"{NODE_DIR / 'bin'}:{env.get('PATH', '')}"

        task = (
            "Implement the complete animation described in plan.md. Use your file and "
            "bash tools autonomously. Run the Manim test after edits, diagnose failures, "
            "and keep repairing until it renders successfully. Do not ask questions."
        )
        command = [
            pi_command,
            "--mode",
            "json",
            "--approve",
            "--no-session",
            "--no-extensions",
            "--no-skills",
            "--provider",
            PI_PROVIDER,
            "--model",
            COPILOT_MODEL,
            "--thinking",
            "off",
            "@plan.md",
            task,
        ]

        log_queue.put(("log", "Running Pi (this may take 10-20 minutes)..."))
        process = subprocess.Popen(
            command,
            cwd=str(workspace),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if process.stdout is None:
            raise RuntimeError("Pi output stream is unavailable.")

        raw_lines = queue.Queue()

        def read_pi_output():
            for output_line in process.stdout:
                raw_lines.put(output_line)

        reader = threading.Thread(target=read_pi_output, daemon=True)
        reader.start()
        started_at = time.monotonic()
        last_heartbeat = started_at
        while reader.is_alive() or not raw_lines.empty():
            try:
                output_line = raw_lines.get(timeout=1)
            except queue.Empty:
                output_line = None
            if output_line is not None:
                update = concise_pi_event(output_line)
                if update:
                    log_queue.put(("log", update))
            now = time.monotonic()
            if now - last_heartbeat >= 15:
                elapsed = int(now - started_at)
                log_queue.put(("log", f"Pi is still working... ({elapsed}s elapsed)"))
                last_heartbeat = now
        reader.join(timeout=1)
        return_code = process.wait()
        log_queue.put(("log", f"Pi exit code: {return_code}"))

        video = newest_video(workspace)
        if video is None:
            raise RuntimeError("Pi did not produce a valid MP4 render.")

        destination = run_dir / "animation.mp4"
        shutil.copy2(video, destination)
        log_queue.put(("done", str(destination)))
    except Exception as exc:
        log_queue.put(("error", str(exc)))


st.set_page_config(page_title="ManimFlow", layout="wide")
st.title("ManimFlow")
st.caption("Describe an animation topic → get a rendered MP4")

if "copilot_token" not in st.session_state:
    st.session_state.copilot_token = deployment_token() or None
if "device_flow" not in st.session_state:
    st.session_state.device_flow = None

if not st.session_state.copilot_token:
    st.info("Login with GitHub to use your Copilot subscription for rendering.")
    if st.session_state.device_flow is None:
        if st.button("Login with GitHub Copilot", type="primary"):
            with st.spinner("Requesting device code..."):
                st.session_state.device_flow = request_device_code()
            st.rerun()
    else:
        flow = st.session_state.device_flow
        st.markdown(
            f"""
**Step 1:** Go to **[github.com/login/device](https://github.com/login/device)**

**Step 2:** Enter this code:

### `{flow['user_code']}`

Then continue after authorizing ManimFlow.
"""
        )
        if st.button("I've authorized — continue", type="primary"):
            with st.spinner("Verifying..."):
                result = poll_token(flow["device_code"])
            if "access_token" in result:
                st.session_state.copilot_token = result["access_token"]
                st.session_state.device_flow = None
                st.rerun()
            else:
                st.error(
                    f"Not authorized yet: {result.get('error', 'unknown error')}. Try again."
                )
    st.stop()

with st.sidebar:
    st.success("Connected to GitHub Copilot")
    st.caption(f"Agent: Pi · Model: {COPILOT_MODEL}")
    if deployment_token():
        st.caption("Using the deployment's Streamlit secret.")
    elif st.button("Logout"):
        st.session_state.copilot_token = None
        st.session_state.device_flow = None
        st.rerun()

prompt = st.text_area(
    "Animation prompt",
    placeholder="e.g. Explain how a Fourier series builds up a square wave",
    height=100,
)
generate = st.button("Generate", type="primary")

if generate and prompt.strip():
    log_box = st.empty()
    plan_box = st.empty()
    status = st.empty()
    logs = []
    events = queue.Queue()
    thread = threading.Thread(
        target=run_render,
        args=(st.session_state.copilot_token, prompt.strip(), events),
        daemon=True,
    )
    thread.start()

    video_path = None
    while thread.is_alive() or not events.empty():
        try:
            kind, value = events.get(timeout=0.5)
        except queue.Empty:
            continue
        if kind == "log":
            logs.append(value)
            log_box.code("\n".join(logs[-80:]), language=None)
        elif kind == "plan":
            plan_box.info(f"**Scene plan:**\n\n{value}")
        elif kind == "done":
            video_path = pathlib.Path(value)
            status.success("Render complete!")
        elif kind == "error":
            status.error(value)

    if video_path and video_path.exists():
        video_bytes = video_path.read_bytes()
        st.video(video_bytes)
        st.download_button(
            "Download animation.mp4",
            video_bytes,
            file_name="animation.mp4",
            mime="video/mp4",
        )
