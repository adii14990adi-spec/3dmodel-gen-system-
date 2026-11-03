"""Inspect actions/clips, frame ranges, fps, animated bones and root motion."""
from __future__ import annotations

import sys
from processing.blender._common import finish, guard

guard()
import bpy

source = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
ext = source.rsplit(".", 1)[-1].lower()
if ext == "bvh": bpy.ops.import_anim.bvh(filepath=source)
elif ext == "fbx": bpy.ops.import_scene.fbx(filepath=source, use_anim=True)
elif ext == "dae": bpy.ops.wm.collada_import(filepath=source)
elif ext in ("glb", "gltf"): bpy.ops.import_scene.gltf(filepath=source)
else: raise ValueError(f"unsupported animation source: {ext}")
scene = bpy.context.scene
clips = []
for action in bpy.data.actions:
    start, end = action.frame_range
    clips.append({"name": action.name, "frame_start": start, "frame_end": end,
                  "frame_rate": scene.render.fps / scene.render.fps_base,
                  "duration_seconds": (end - start) / max(1, scene.render.fps),
                  "channel_count": len(action.fcurves) if hasattr(action, "fcurves") else 0})
bones = [{"name": b.name, "parent": b.parent.name if b.parent else None}
         for arm in bpy.data.armatures for b in arm.bones]
finish({"source": source, "clips": clips, "bones": bones, "frame_rate": scene.render.fps})
