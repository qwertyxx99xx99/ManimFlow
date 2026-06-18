# ManimFlow

Describe an animation in plain English. Get a video.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/theabbie/ManimFlow/blob/main/ManimFlow.ipynb)

---

## How it works

1. **You** type a prompt — e.g. *"Explain how blockchain achieves tamper-proof consensus"*
2. **Planner LLM** turns that into a structured scene-by-scene visual breakdown (plain English, no code)
3. **Aider** reads the plan and writes a modular Manim project across multiple files (`helpers.py`, `objects.py`, `scene.py`, etc.)
4. **Auto-fix loop** — Manim renders the output; if it fails, the error is fed back to Aider which fixes and retries automatically
5. **Video** plays inline in the notebook (or downloads locally via the CLI)

No API keys. No local setup required.

---

## CLI — `manimflow`

Run from your terminal. Creates a Colab session, renders the video, downloads it, and cleans up — zero interaction.

### Requirements

- [Colab CLI](https://github.com/google-colab/colab-cli): `pip install colab-cli`
- Logged in: `colab login`

### Install

```bash
curl -o ~/bin/manimflow https://raw.githubusercontent.com/theabbie/ManimFlow/main/manimflow
chmod +x ~/bin/manimflow
# ensure ~/bin is in PATH
```

### Usage

```bash
manimflow "doppler effect visualization and explainer"
```

All render logs stream to stdout. When done, prints the local path to the downloaded MP4:

```
/Users/you/ManimFlow_videos/doppler_effect_visualization_and_explainer.mp4
```

Videos are saved to `~/ManimFlow_videos/`.

---

## Notebook

1. Open in Colab → **Runtime → Run all**
2. Cell 1 installs system deps (~60s) and Python packages — **restart the runtime when prompted**
3. Run all cells again — a text box appears at the bottom
4. Describe your animation and click **Generate**

Scene count scales to your prompt: short → 3–4 scenes, default → 5–6, long/detailed → 8–12. Aider writes modular code, Manim renders it, errors loop back automatically until a clean MP4 is produced.

---

## Stack

| Layer | What |
|---|---|
| LLM | [Exa demo](https://demos.exa.ai) proxied locally via an OpenAI-compatible HTTP server |
| Model | Google Gemini 2.5 Flash (via Exa) |
| Agent | [Aider](https://aider.chat) with `--auto-test` and a local git repo for change tracking |
| Animation | [Manim Community](https://www.manim.community/) |
| LaTeX | Minimal texlive: `latex-base`, `latex-extra`, `fonts-recommended`, `plain-generic`, `dvisvgm` |

## Architecture

```
Prompt
  │
  ▼
Planner  ──►  Exa/Gemini 2.5 Flash  ──►  plain-English scene plan
  │
  ▼
Aider --auto-test --test-cmd "manim scene.py AnimScene"
  │  writes helpers.py, objects.py, scene.py, ...
  │  runs manim after each edit
  │  feeds errors back and self-corrects
  │
  ▼
animation.mp4
```

## Notes

- Aider splits code across multiple files — `scene.py` imports from helper modules, keeping each file within the LLM's context window for complex animations.
- A local proxy server (port 18642) translates OpenAI-format requests from Aider into Exa SSE streams.
- `numpy==1.26.4` and `scipy==1.13.1` are pinned — Colab ships numpy 2.x which breaks Manim's scipy dependency.
- The runtime restart after cell 1 is required to flush the in-memory numpy 2.x import.
- `git init` in `manim_output/` is required for Aider's change tracking (`--auto-test` only fires when Aider detects edited files via git diff).
