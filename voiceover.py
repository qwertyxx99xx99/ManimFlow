import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

SPEAK_REPO = "theabbie/AbbieSpeak"
SPEAK_FILE = "speak.py"
SPEAK_DIR = pathlib.Path(
    os.environ.get("MANIMFLOW_SPEAK_DIR", pathlib.Path(tempfile.gettempdir()) / "manimflow_speak")
)
NARRATION_PROMPT = (
    "You write voiceover narration for a short educational animation. Given the scene plan, "
    "write one continuous spoken script that follows the scenes in order, about 2-3 sentences "
    "per scene, in plain conversational English. Output only the words to be spoken: no markdown, "
    "scene labels, stage directions, LaTeX, or symbols. Spell out math in words. Never ask questions."
)


def ensure_speak(log=print):
    SPEAK_DIR.mkdir(parents=True, exist_ok=True)
    script = SPEAK_DIR / SPEAK_FILE
    if not script.is_file():
        log("Downloading AbbieSpeak...")
        hf = shutil.which("hf") or str(pathlib.Path(sys.executable).parent / "hf")
        subprocess.run(
            [hf, "download", SPEAK_REPO, SPEAK_FILE, "--local-dir", str(SPEAK_DIR)],
            check=True, capture_output=True, text=True, timeout=300,
        )
    if not (SPEAK_DIR / ".primed").exists():
        log("Installing voiceover dependencies and model (first run only)...")
        synthesize("Ready.", SPEAK_DIR / "warmup.wav")
        (SPEAK_DIR / ".primed").touch()
    return script


def synthesize(text, wav_path):
    wav_path = pathlib.Path(wav_path).resolve()
    result = subprocess.run(
        ["uv", "run", str(SPEAK_DIR / SPEAK_FILE), text, str(wav_path)],
        capture_output=True, text=True, timeout=1800, cwd=SPEAK_DIR,
    )
    if result.returncode or not pathlib.Path(wav_path).is_file():
        raise RuntimeError(f"speak.py failed: {(result.stderr or result.stdout)[-500:]}")


def clean_narration(text):
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"(?m)^\s*(?:#+|[-*]|\d+[.)])\s*", "", text)
    text = re.sub(r"[*_`]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def write_narration(chat, plan):
    text = clean_narration(chat([
        {"role": "system", "content": NARRATION_PROMPT},
        {"role": "user", "content": plan},
    ]))
    if not text:
        raise RuntimeError("The model returned an empty narration script.")
    return text


def media_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True, timeout=20,
    )
    return float(result.stdout.strip())


DARK_LUMA = 17
MIN_DARK_TAIL = 0.3
FADE_LOOKBACK = 3.0


def frame_brightness(video):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-an",
         "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
         "-f", "null", "-"],
        capture_output=True, text=True, check=True, timeout=300,
    )
    times, levels = [], []
    for line in result.stdout.splitlines():
        if "pts_time:" in line:
            times.append(float(line.split("pts_time:", 1)[1].split()[0]))
        elif "signalstats.YAVG=" in line:
            levels.append(float(line.split("=", 1)[1]))
    count = min(len(times), len(levels))
    return times[:count], levels[:count]


def last_visible_cut(video):
    try:
        times, levels = frame_brightness(video)
    except (subprocess.SubprocessError, ValueError):
        return None
    if len(times) < 3:
        return None
    tail = len(levels)
    while tail > 0 and levels[tail - 1] < DARK_LUMA:
        tail -= 1
    if tail == 0 or tail == len(levels):
        return None
    frame = times[1] - times[0]
    if times[-1] + frame - times[tail] < MIN_DARK_TAIL:
        return None
    window = [i for i in range(tail) if times[i] >= times[tail - 1] - FADE_LOOKBACK]
    peak = max(levels[i] for i in window)
    hold = max(i for i in window if levels[i] >= peak * 0.995)
    return times[hold] + frame


def mux(video, wav, output):
    audio = media_duration(wav)
    length = media_duration(video)
    cut = last_visible_cut(video)
    command = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(wav)]
    if cut:
        pad = max(0.0, audio - cut)
        command += ["-vf", f"trim=end={cut:.3f},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={pad:.3f}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    elif audio - length > 0.05:
        command += ["-vf", f"tpad=stop_mode=clone:stop_duration={audio - length:.3f}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    else:
        command += ["-c:v", "copy"]
    command += ["-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0", str(output)]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=600)


def add_voiceover(chat, plan, video, output, log=print):
    try:
        ensure_speak(log)
        log("Writing narration script...")
        narration = write_narration(chat, plan)
        log(f"Narration: {narration[:300]}")
        wav = pathlib.Path(output).with_suffix(".wav")
        log("Synthesizing voiceover...")
        synthesize(narration, wav)
        mux(video, wav, output)
        log("Voiceover added.")
        return pathlib.Path(output)
    except Exception as error:
        log(f"Voiceover skipped: {error}")
        return pathlib.Path(video)
