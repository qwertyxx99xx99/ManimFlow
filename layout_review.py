import pathlib
import shutil
import subprocess
import sys

AUDITOR = pathlib.Path(__file__).with_name("layout_audit.py")
BACKUP = ".review_backup"
REVIEW_ROUNDS = 2
REVIEW_FILES = ["@scene.py", "@layout_review.md"]


def audit_layout(workspace, log=print):
    if not (workspace / "scene.py").is_file():
        return ""
    try:
        result = subprocess.run(
            [sys.executable, str(AUDITOR), "scene.py", "AnimScene"],
            cwd=workspace, capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        log("Layout audit timed out; skipping review.")
        return ""
    if result.returncode == 1:
        lines = [l for l in result.stdout.splitlines() if l.startswith(("layout audit", "- "))]
        log("Layout audit flagged issues:\n" + "\n".join(lines))
        (workspace / "layout_review.md").write_text("# Layout audit findings\n\n" + "\n".join(lines) + "\n")
        (workspace / BACKUP).mkdir(exist_ok=True)
        shutil.copy2(workspace / "scene.py", workspace / BACKUP / "scene.py")
        return "\n".join(lines)
    if result.returncode:
        log(f"Layout audit could not run; skipping review: {result.stderr.strip()[-300:]}")
    else:
        log("Layout audit found no issues.")
    return ""


def settle(workspace, log=print):
    scene = workspace / "scene.py"
    videos = [
        p for p in workspace.rglob("AnimScene.mp4")
        if "partial_movie_files" not in p.parts
    ]
    newest = max((p.stat().st_mtime for p in videos), default=0)
    if not scene.is_file() or scene.stat().st_mtime <= newest:
        return
    log("Rendering the revised scene.py...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "manim", "-ql", "--disable_caching", "scene.py", "AnimScene"],
            cwd=workspace, capture_output=True, text=True, timeout=900,
        )
        failed = result.returncode != 0
        detail = (result.stderr or result.stdout).strip()[-500:]
    except subprocess.TimeoutExpired:
        failed, detail = True, "render timed out"
    if failed:
        log(f"Revised scene failed to render; keeping the previous version.\n{detail}")
        backup = workspace / BACKUP / "scene.py"
        if backup.is_file():
            shutil.copy2(backup, scene)


def review_task(report):
    return (
        "The attached layout_review.md lists layout problems that an automated geometry audit found "
        "in the attached scene.py (overlapping objects, crowding, objects outside the frame), most "
        "important first. Your first response must be an edit tool call on scene.py that fixes the "
        "top findings with small changes: adjust spacing, scale, or position, or fade out old objects "
        "before new ones arrive. Do not rewrite the file, do not answer with prose, and do not "
        "apologize. Treat each finding as a real defect unless it is deliberate or a false positive; "
        "for each one you skip, append a line to review_notes.md as: IGNORED: <finding> | <reason>. "
        "Re-rendering is optional because ManimFlow renders scene.py after your edits and keeps the "
        "previous video if the revision fails.\n\n"
        f"Findings:\n{report}"
    )
