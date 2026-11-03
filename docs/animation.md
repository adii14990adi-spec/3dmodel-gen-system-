# Animation pipeline, skeleton mapping and retargeting

## Inspection

`.bvh` files are parsed by `processing/bvh.py`: hierarchy, offsets, channels, frame count, `Frame Time`, fps, duration, animated bones and root position channels. glTF/GLB sources are inspected by `processing/glb.py` and expose real clip names, durations, frame ranges, channel counts, interpolation, animated nodes and root motion. FBX/DAE are inspected in CI by `processing/blender/inspect_animation.py`.

## Compatibility is conservative

`config/bone_mappings/humanoid.json` defines 21 canonical roles (hips, spine, chest, neck, head, shoulders, arms, hands, legs, feet, toes) with vendor aliases for Mixamo (`mixamorig:`), Biped (`Bip01_`) and plain names. `processing/compatibility.py` applies:

1. **Name/role mapping** — exact alias (confidence 1.0) or explicit side-token heuristic (0.6).
2. **Hierarchy checks** — e.g. a lower arm must be parented under its upper arm; a head under a neck.
3. **Spatial checks** — implausible rest offsets (unit mismatches) are flagged.
4. **Ambiguity detection** — two bones mapping to the same role produce `ambiguous_roles` and an explicit `manual_map_required` reason. The pipeline never silently chooses.
5. **Coverage thresholds** — `humanoid_min_role_coverage: 0.75`, `humanoid_min_mapped_bones: 12`, all `required_roles` present.

An incompatible result carries real reasons (missing roles, low coverage, ambiguity, hierarchy problems, no skeleton). The deliberately included quadruped BVH is rejected with role-coverage and missing-role reasons.

Similar names alone are never proof: a `"Spine"`/`"Spine1"` pair is ambiguous (spine vs chest) unless the configured aliases disambiguate it. Edit `config/bone_mappings/humanoid.json` or pass a manual map to resolve real production skeletons.

## Retargeting (`processing/retarget.py`)

For BVH sources the pipeline copies the **target character GLB** (never the original), maps BVH joints to target skin joint node indices through canonical roles, and writes one glTF animation with:

- `time = frame * frame_time` (real frame timing)
- Euler XYZ (degrees) → normalised quaternions, expressed relative to the target's rest rotation (rest/orientation differences)
- optional root translation channel from BVH position channels, scaled by `scale_factor=0.01` (cm→m) with an optional Z-up correction
- LINEAR interpolation, per-bone rotation channels, exactly one clip named `Retarget_<source>`
- existing animations removed so the output contains only the exported clip

Extreme rotations are normalised; limb direction and hierarchy come from the real rest matrices. Foot sliding is not solved by IK — it is documented in `docs/limitations.md`. Weight normalisation is never applied to the original; weights in the target processed copy are left intact.

For glTF sources with embedded clips, the clip is passed through only when the source and target skeleton names are identical. Otherwise `manual_bone_map_required` is recorded; no unverified playback claim is made.

## Playability policy

`GET /api/v1/models/{id}/compatible-animations` reports `compatible` (skeleton checks passed) and `playable` (compatible **and** `retarget_status ∈ {completed, passthrough_identical_skeleton}` **and** `target_character_id == model_id` **and** a validated runtime GLB exists). This prevents a merely compatible skeleton from appearing playable in the Android player.

**First-compatible-target policy:** each animation is exported for the first compatible target by sorted manifest id. Other compatible characters appear in `compatibility[]` with `status: compatible` but have no exported GLB and are therefore not playable. To export for every compatible humanoid, run `process-animations` with the target selection changed in `processing/cli.py::process_animation` (the retargeter already accepts any target path). This choice is deliberate and documented rather than hidden.
