import pathlib
import shutil
import subprocess
import sys

AUDITOR = pathlib.Path(__file__).with_name("layout_audit.py")
REVIEW_ROUNDS = 2


def audit_layout(workspace, log=print):
    if not (workspace / "scene.py").is_file():
        return ""
    shutil.copy2(AUDITOR, workspace / AUDITOR.name)
    try:
        result = subprocess.run(
            [sys.executable, AUDITOR.name, "scene.py", "AnimScene"],
            cwd=workspace, capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        log("Layout audit timed out; skipping review.")
        return ""
    if result.returncode == 1:
        lines = [l for l in result.stdout.splitlines() if l.startswith(("layout audit", "- after"))]
        log("Layout audit flagged issues:\n" + "\n".join(lines))
        return "\n".join(lines)
    if result.returncode:
        log(f"Layout audit could not run; skipping review: {result.stderr.strip()[-300:]}")
    else:
        log("Layout audit found no issues.")
    return ""


def review_task(report):
    return (
        "ManimFlow ran a geometry audit on the finished scene.py. It reports overlapping objects, "
        "crowding, and objects outside the frame after each play() call. Treat each finding as a real "
        "defect by default and fix it with spacing, scaling, repositioning, or by fading out old "
        "objects before new ones arrive. You may ignore a finding only when it is deliberate or a "
        "false positive; for each one you ignore, append a line to review_notes.md as: "
        "IGNORED: <finding> | <reason>. The audit is advisory, so never break the animation to "
        "satisfy it. After editing, run the exact render command from AGENTS.md and confirm a valid "
        "MP4 still renders. If an edit breaks rendering, undo it.\n\n"
        f"{report}"
    )
