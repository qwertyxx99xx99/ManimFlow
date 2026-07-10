import streamlit as st
import subprocess, pathlib, shutil, os, json, re, threading, queue, time, sys
import urllib.request, urllib.parse

BASE = pathlib.Path("/tmp/manimflow_workspace")
BASE.mkdir(parents=True, exist_ok=True)
MANIM_OUTPUT = BASE / "manim_output"

GITHUB_CLIENT_ID = "8b76dd0df855d8bc7db1"
COPILOT_BASE = "https://api.individual.githubcopilot.com"
COPILOT_MODEL = "gpt-4o"
COPILOT_HEADERS = lambda token: {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Editor-Version": "vscode/1.85.0",
    "Copilot-Integration-Id": "vscode-chat",
    "Openai-Intent": "conversation-completions",
}

WAKELOCK_JS = """
<script>
(function() {
  let lock = null;
  async function acquire() {
    try { lock = await navigator.wakeLock.request('screen'); } catch(e) {}
  }
  acquire();
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') acquire();
  });
})();
</script>
"""

# ── GitHub device flow ────────────────────────────────────────────────────────

def request_device_code():
    data = urllib.parse.urlencode({"client_id": GITHUB_CLIENT_ID, "scope": "read:user"}).encode()
    req = urllib.request.Request("https://github.com/login/device/code", data=data,
        headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def poll_token(device_code):
    data = urllib.parse.urlencode({
        "client_id": GITHUB_CLIENT_ID,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }).encode()
    req = urllib.request.Request("https://github.com/login/oauth/access_token", data=data,
        headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

# ── Copilot chat ──────────────────────────────────────────────────────────────

def copilot_chat(token, messages):
    payload = json.dumps({"model": COPILOT_MODEL, "messages": messages}).encode()
    req = urllib.request.Request(f"{COPILOT_BASE}/chat/completions",
        data=payload, headers=COPILOT_HEADERS(token))
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"].strip()

# ── Render pipeline ───────────────────────────────────────────────────────────

def run_render(token, user_prompt, log_queue):
    try:
        log_queue.put(('log', 'Planning scenes...'))
        plan = copilot_chat(token, [
            {"role": "system", "content": (
                "You are a Manim animation planner. Output a plain numbered list of scenes. "
                "Each scene: one short line with the visual idea only, no formatting, no markdown, no bold, no bullets. "
                "Scale count to length: short->3-4, default->5-6, detailed->8-12. "
                "No preamble, no closing remarks."
            )},
            {"role": "user", "content": user_prompt},
        ])
        log_queue.put(('plan', plan))

        if MANIM_OUTPUT.exists():
            shutil.rmtree(MANIM_OUTPUT)
        MANIM_OUTPUT.mkdir(parents=True)

        (MANIM_OUTPUT / 'plan.md').write_text(
            f"# Animation Plan\n\n## Original request\n{user_prompt}\n\n## Scenes\n{plan}\n"
        )
        subprocess.run(['git', 'init'], cwd=str(MANIM_OUTPUT), capture_output=True)
        subprocess.run(['git', 'add', 'plan.md'], cwd=str(MANIM_OUTPUT), capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'init'], cwd=str(MANIM_OUTPUT), capture_output=True)

        task = (
            "Read plan.md and implement it as a Manim animation project.\n\n"
            "Structure:\n"
            "- Helper modules first (objects.py, helpers.py, etc.)\n"
            "- scene.py last, defines AnimScene(Scene), imports from helpers\n\n"
            "scene.py is tested with:\n"
            "  python3 -m manim -pql --disable_caching scene.py AnimScene\n\n"
            "Rules:\n"
            "- Every file must start with: from manim import *\n"
            "- AnimScene(Scene) in scene.py\n"
            "- MathTex(r'...') for all equations\n"
            "- Text() for plain labels\n"
            "- Arrow(start=..., end=...) only\n"
            "- Never use bare names like UP, DOWN, LEFT, RIGHT, ORIGIN without 'from manim import *'\n"
            "- Make it visually complete and polished"
        )

        aider_env = {
            **os.environ,
            "OPENAI_API_KEY": token,
            "GIT_AUTHOR_NAME": "aider", "GIT_AUTHOR_EMAIL": "aider@manimflow",
            "GIT_COMMITTER_NAME": "aider", "GIT_COMMITTER_EMAIL": "aider@manimflow",
        }

        log_queue.put(('log', 'Running aider (10-20 min)...'))
        proc = subprocess.Popen(
            ['aider',
             '--model', f'openai/{COPILOT_MODEL}',
             '--openai-api-base', COPILOT_BASE,
             '--openai-api-key', token,
             '--yes-always', '--no-auto-commits', '--no-pretty',
             '--no-show-model-warnings', '--no-check-update',
             '--map-tokens', '0',
             '--test-cmd', f'{sys.executable} -m manim -pql --disable_caching scene.py AnimScene 2>&1',
             '--auto-test', '--message', task, 'plan.md'],
            cwd=str(MANIM_OUTPUT), text=True, env=aider_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        for line in proc.stdout:
            log_queue.put(('log', line.rstrip()))
        proc.wait()
        log_queue.put(('log', f'aider exit: {proc.returncode}'))

        videos = [v for v in MANIM_OUTPUT.rglob('*.mp4') if v.stat().st_size > 50_000]
        if not videos:
            log_queue.put(('error', 'No valid video rendered.'))
            return

        dest = BASE / 'animation.mp4'
        shutil.copy(sorted(videos, key=lambda p: p.stat().st_mtime)[-1], dest)
        log_queue.put(('done', str(dest)))

    except Exception as e:
        log_queue.put(('error', str(e)))

# ── UI ────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title='ManimFlow', layout='wide')
st.components.v1.html(WAKELOCK_JS, height=0)
st.title('ManimFlow')
st.caption('Describe an animation topic → get a rendered MP4')

if 'copilot_token' not in st.session_state:
    st.session_state.copilot_token = st.query_params.get('token', None) or None
if 'device_flow' not in st.session_state:
    st.session_state.device_flow = None

# ── Login ─────────────────────────────────────────────────────────────────────

if not st.session_state.copilot_token:
    st.info('Login with your GitHub account to use your Copilot subscription for rendering.')

    if st.session_state.device_flow is None:
        if st.button('Login with GitHub Copilot', type='primary'):
            with st.spinner('Requesting device code...'):
                flow = request_device_code()
                st.session_state.device_flow = flow
            st.rerun()
    else:
        flow = st.session_state.device_flow
        st.markdown(f"""
**Step 1:** Go to **[github.com/login/device](https://github.com/login/device)**

**Step 2:** Enter this code:

### `{flow['user_code']}`

Then click the button below once you've authorized.
""")
        if st.button("I've authorized - continue", type='primary'):
            with st.spinner('Verifying...'):
                result = poll_token(flow['device_code'])
            if 'access_token' in result:
                st.session_state.copilot_token = result['access_token']
                st.session_state.device_flow = None
                st.query_params['token'] = result['access_token']
                st.rerun()
            else:
                st.error(f"Not authorized yet: {result.get('error', 'unknown error')}. Try again.")
    st.stop()

# ── Main app (authenticated) ──────────────────────────────────────────────────

with st.sidebar:
    st.success('Connected to GitHub Copilot')
    if st.button('Logout'):
        st.session_state.copilot_token = None
        st.session_state.device_flow = None
        st.query_params.clear()
        st.rerun()

prompt = st.text_area('Animation prompt',
    placeholder='e.g. Explain how a Fourier series builds up a square wave', height=100)
generate = st.button('Generate', type='primary')

if generate and prompt.strip():
    log_box = st.empty()
    plan_box = st.empty()
    status = st.empty()

    logs = []
    q = queue.Queue()

    thread = threading.Thread(
        target=run_render, args=(st.session_state.copilot_token, prompt.strip(), q), daemon=True)
    thread.start()

    video_path = None
    while thread.is_alive() or not q.empty():
        try:
            kind, val = q.get(timeout=0.5)
        except queue.Empty:
            continue

        if kind == 'log':
            logs.append(val)
            log_box.code('\n'.join(logs[-60:]), language=None)
        elif kind == 'plan':
            plan_box.info(f'**Scene plan:**\n\n{val}')
        elif kind == 'done':
            video_path = val
            status.success('Render complete!')
        elif kind == 'error':
            status.error(val)

    if video_path and pathlib.Path(video_path).exists():
        video_bytes = pathlib.Path(video_path).read_bytes()
        st.video(video_bytes)
        st.download_button('Download animation.mp4', video_bytes,
            file_name='animation.mp4', mime='video/mp4')
