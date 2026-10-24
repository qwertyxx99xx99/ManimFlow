import sys
from importlib.util import module_from_spec, spec_from_file_location
from itertools import combinations
from pathlib import Path

import numpy as np
from manim import DOWN, LEFT, RIGHT, UP, CoordinateSystem, Line, Mobject, ParametricFunction, Scene, VMobject, config, tempconfig

MIN_GAP = 0.15
FRAME_MARGIN = 0.25
MAX_FINDINGS = 12
TOUCH_DISTANCE = 0.05
BACKGROUND_AREA = 0.5
CIRCULAR = ("circle", "dot", "ellipse", "annulus", "sector", "arc")


def label_of(mob, index):
    text = getattr(mob, "text", None) or getattr(mob, "tex_string", None)
    name = type(mob).__name__
    return f"{name}[{index}]('{str(text)[:30]}')" if text else f"{name}[{index}]"


def bounds_of(mob):
    return (
        float(mob.get_critical_point(LEFT)[0]),
        float(mob.get_critical_point(RIGHT)[0]),
        float(mob.get_critical_point(DOWN)[1]),
        float(mob.get_critical_point(UP)[1]),
    )


def is_thin(mob):
    if isinstance(mob, (ParametricFunction, Line)):
        return True
    return type(mob) is VMobject and mob.get_fill_opacity() == 0


def point_in_bounds(mob, bounds):
    points = mob.get_all_points()[::3]
    inside = (
        (points[:, 0] >= bounds[0]) & (points[:, 0] <= bounds[1])
        & (points[:, 1] >= bounds[2]) & (points[:, 1] <= bounds[3])
    )
    return bool(inside.any())


def contains(outer, inner):
    return outer[0] <= inner[0] and outer[1] >= inner[1] and outer[2] <= inner[2] and outer[3] >= inner[3]


def circular_clear(a, b):
    names = [type(m).__name__.lower() for m in (a, b)]
    if not all(any(k in n for k in CIRCULAR) for n in names):
        return False
    radius = [float(np.linalg.norm(m.get_boundary_point(RIGHT) - m.get_center())) for m in (a, b)]
    return float(np.linalg.norm(a.get_center() - b.get_center())) >= sum(radius)


def audit_scene(scene):
    frame_x, frame_y = float(config["frame_x_radius"]), float(config["frame_y_radius"])
    frame_area = 4 * frame_x * frame_y
    items = [
        (label_of(mob, index), mob, bounds_of(mob))
        for index, mob in enumerate(scene.mobjects, start=1)
        if len(mob.get_points_defining_boundary()) > 0
    ]
    items = [
        item for item in items
        if (item[2][1] - item[2][0]) * (item[2][3] - item[2][2]) < BACKGROUND_AREA * frame_area
    ]
    findings = []
    for name, _, (left, right, bottom, top) in items:
        parts = []
        if left < -frame_x + FRAME_MARGIN:
            parts.append(f"left by {-frame_x + FRAME_MARGIN - left:.2f}")
        if right > frame_x - FRAME_MARGIN:
            parts.append(f"right by {right - frame_x + FRAME_MARGIN:.2f}")
        if bottom < -frame_y + FRAME_MARGIN:
            parts.append(f"bottom by {-frame_y + FRAME_MARGIN - bottom:.2f}")
        if top > frame_y - FRAME_MARGIN:
            parts.append(f"top by {top - frame_y + FRAME_MARGIN:.2f}")
        if parts:
            findings.append(f"{name} is outside the safe frame: " + ", ".join(parts))
    for (name_a, mob_a, a), (name_b, mob_b, b) in combinations(items, 2):
        if isinstance(mob_a, CoordinateSystem) or isinstance(mob_b, CoordinateSystem):
            continue
        thin_a, thin_b = is_thin(mob_a), is_thin(mob_b)
        if thin_a and thin_b:
            continue
        overlap_x = min(a[1], b[1]) - max(a[0], b[0])
        overlap_y = min(a[3], b[3]) - max(a[2], b[2])
        if overlap_x > 0 and overlap_y > 0:
            if contains(a, b) or contains(b, a) or circular_clear(mob_a, mob_b):
                continue
            if thin_a != thin_b and not point_in_bounds(mob_a if thin_a else mob_b, b if thin_a else a):
                continue
            findings.append(f"{name_a} overlaps {name_b} (x={overlap_x:.2f}, y={overlap_y:.2f})")
            continue
        if thin_a or thin_b:
            continue
        gap_x = max(a[0] - b[1], b[0] - a[1], 0.0)
        gap_y = max(a[2] - b[3], b[2] - a[3], 0.0)
        if overlap_y > 0 and 0 < gap_x < MIN_GAP:
            findings.append(f"{name_a} and {name_b} are crowded horizontally (gap={gap_x:.2f})")
        if overlap_x > 0 and 0 < gap_y < MIN_GAP:
            findings.append(f"{name_a} and {name_b} are crowded vertically (gap={gap_y:.2f})")
    return findings


def run(scene_file, scene_class):
    path = Path(scene_file).resolve()
    sys.path.insert(0, str(path.parent))
    spec = spec_from_file_location("audited_scene", path)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    scene_cls = getattr(module, scene_class)
    if not (isinstance(scene_cls, type) and issubclass(scene_cls, Scene)):
        raise TypeError(f"{scene_class} is not a Scene subclass")
    seen = {}
    plays = [0]
    with tempconfig({"dry_run": True, "write_to_movie": False, "disable_caching": True, "format": "png"}):
        scene = scene_cls()
        original_play = scene.play

        def audited_play(*args, **kwargs):
            result = original_play(*args, **kwargs)
            plays[0] += 1
            for finding in audit_scene(scene):
                seen.setdefault(finding, plays[0])
            return result

        scene.play = audited_play
        scene.render()
        for finding in audit_scene(scene):
            seen.setdefault(finding, plays[0])
    return plays[0], seen


def main():
    if len(sys.argv) != 3:
        print("usage: layout_audit.py scene.py SceneClass", file=sys.stderr)
        return 2
    try:
        plays, seen = run(sys.argv[1], sys.argv[2])
    except Exception as error:
        print(f"layout audit failed: {error}", file=sys.stderr)
        return 2
    if not seen:
        print(f"layout audit: no issues across {plays} animation steps")
        return 0
    def priority(item):
        finding = item[0]
        if "outside the safe frame" in finding:
            return 0
        return 1 if "overlaps" in finding else 2

    ordered = sorted(seen.items(), key=priority)
    print(f"layout audit: {len(seen)} issues across {plays} animation steps")
    for finding, step in ordered[:MAX_FINDINGS]:
        print(f"- after play() #{step}: {finding}")
    if len(ordered) > MAX_FINDINGS:
        print(f"- {len(ordered) - MAX_FINDINGS} lower-priority findings not shown")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
