# ManimFlow

Describe an animation in plain English. Get a video.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/theabbie/ManimFlow/blob/main/ManimFlow.ipynb)

---

## How it works

1. **You** type a prompt — e.g. *"Show how a Fourier series builds a square wave"*
2. **Planner LLM** turns that into a structured visual scene breakdown
3. **SWE-agent** reads the plan, writes a Manim script, renders it, reads errors, and self-corrects until it succeeds
4. **Video** plays inline in the notebook

No API keys. No local setup. Just open in Colab and run.

## Stack

| Layer | What |
|---|---|
| LLM | [Exa demo](https://demos.exa.ai) via a hosted [Cloudflare Worker](worker/) shim |
| Agent | [SWE-agent v1.1.0](https://github.com/SWE-agent/SWE-agent) with `thought_action` parser |
| Animation | [Manim Community](https://www.manim.community/) |

## Usage

Click **Open in Colab**, then **Runtime → Run all**. A text box appears in the last cell — describe your animation and click **Generate**.

The agent writes, renders, and self-corrects until a clean MP4 lands at `/content/animation.mp4` and plays inline.

## Worker

The `worker/` folder is a Cloudflare Worker that wraps the Exa demo API with an OpenAI-compatible interface. Deployed at `https://manimator.abbie.workers.dev` — no auth required.
