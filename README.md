# ManimFlow

Describe an animation topic in a GitHub issue. Get a rendered MP4.

Powered by [Manim](https://www.manim.community/), [Aider](https://aider.chat), and GitHub Copilot (Gemini 2.5 Pro) — no API keys required beyond a GitHub account with Copilot.

---

## Usage

1. Create an issue with your animation topic as the title (add detail in the body if needed)
2. Add the `manim` label
3. A bot comments with live progress updates
4. When done, download the MP4 from the comment link

The workflow handles everything: planning scenes, writing modular Manim code, auto-fixing errors, and rendering.

> Only issues opened by the repo owner trigger the workflow.

## Streamlit app (Pi)

`streamlit_app.py` uses Pi as the autonomous coding and repair agent. Pi reads the
scene plan, writes the Manim project, runs the renderer, and keeps fixing errors
until an MP4 is produced.

For Streamlit Community Cloud:

1. Deploy this repository with `streamlit_app.py` as the entry point.
2. In **App settings → Secrets**, add:

   ```toml
   COPILOT_TOKEN = "your-token"
   GEMINI_API_KEY = "your-gemini-key"
   ```

3. Deploy. The app installs the pinned Pi version and an isolated Node 22 runtime
   when necessary. Manim's system packages come from `packages.txt`.

If `COPILOT_TOKEN` is omitted, the app shows GitHub's device-login flow instead.
The token is kept in Streamlit session state, passed to Pi through its subprocess
environment, and is never placed in the URL or written to Pi's auth file.
Selecting Google Gemini requires `GEMINI_API_KEY`; generation is disabled when it
is missing. Gemini uses `gemini-2.5-flash` for both planning and the Pi agent.

Local development:

```bash
npm ci
streamlit run streamlit_app.py
```

---

## How it works

```
Issue title + body
       │
       ▼
Planner (Gemini 2.5 Pro via Copilot)
       │  numbered scene list
       ▼
Aider --auto-test
       │  writes helpers.py, objects.py, scene.py, ...
       │  runs manim after each edit, feeds errors back
       ▼
animation.mp4  ──►  uploaded as artifact, linked in issue comment
```

Scene count scales to your prompt: short → 3–4 scenes, default → 5–6, detailed → 8–12.

---

## Setup (for forks)

1. Enable GitHub Copilot on your account (free tier works)
2. Get your OAuth token: `gh auth token`
3. Add it as a repo secret named `COPILOT_TOKEN` at `Settings → Secrets → Actions`
4. Create a `manim` label on the repo
5. Open an issue, add the label — done

---

## Stack

| Layer | What |
|---|---|
| LLM | GitHub Copilot (Gemini 2.5 Pro) |
| Agent | [Aider](https://aider.chat) with `--auto-test` |
| Animation | [Manim Community](https://www.manim.community/) |
| CI | GitHub Actions (ubuntu-latest, ~10–20 min per render) |

---

## Notebook

A Colab notebook is also available for interactive use:

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/theabbie/ManimFlow/blob/main/ManimFlow.ipynb)

1. Open in Colab → **Runtime → Run all**
2. Restart runtime when prompted after cell 1
3. Run all cells again — describe your animation and click **Generate**

## CLI

A local CLI is available for running renders from your terminal via a Colab session:

```bash
curl -o ~/bin/manimflow https://raw.githubusercontent.com/theabbie/ManimFlow/main/manimflow
chmod +x ~/bin/manimflow
manimflow "doppler effect visualization and explainer"
```

Requires `colab-cli` (`pip install colab-cli`) and `colab login`.
