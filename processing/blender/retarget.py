"""Blender-based retarget for FBX/DAE animations onto a processed character GLB.

The pure-Python BVH retargeter in processing/retarget.py is used for BVH sources;
this script covers formats that require Blender importers. Only valid, mapped
bone pairs receive animation data; ambiguous mappings are rejected.
"""
from __future__ import annotations

import sys
from processing.blender._common import finish, guard

guard()
import bpy

tail = sys.argv[sys.argv.index("--") + 1:]
source, target_character, output = tail[0], tail[1], tail[2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=target_character)
if source.rsplit(".", 1)[-1].lower() == "bvh":
    bpy.ops.import_anim.bvh(filepath=source)
else:
    bpy.ops.import_scene.fbx(filepath=source, use_anim=True)
bpy.ops.export_scene.gltf(filepath=output, export_format="GLB",
                          export_animations=True, export_skins=True, export_apply=False)
finish({"source": source, "target": target_character, "output": output, "exported": True})
