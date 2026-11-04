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

## Animation library (`assets/animation/`)

The uploads live in `assets/animation/Upload/` and are treated as a **read-only
archive**: nothing in the pipeline moves, rewrites or deletes them. The organizer
(`python -m processing.cli organize --apply`) copies each classified source into a
category folder, verifies both sides by SHA-256 and records the mapping in
`docs/animation-library/organization-mapping.json`. Re-running it is idempotent:
identical destinations are reported as `already_organized`, and a destination with
different bytes is an error, never an overwrite.

```
assets/animation/
  Upload/                       # untouched originals (archive, never processed twice)
  locomotion/
    unarmed/{idle,walk,run,sprint,crouch_turn,turn,jump,prone_idle}
    rifle/{idle,walk,run,crouch_walk,crouch_idle,jump}
  combat/
    melee/                      # melee swings, punches
    rifle/{aim,fire,reload}
    reactions/{hit,death}
  other/{interaction,swim,traversal}
  unclassified/
```

Folders are only created when a clip lands in them (`locomotion/unarmed/jump` and
`locomotion/rifle/jump` are separate because a rifle jump carries a weapon and an
unarmed jump does not). `unclassified/` exists exactly because some clips cannot be
proven to belong anywhere else.

### Supported formats

| Format | Detection | Inspection | Runtime GLB |
| --- | --- | --- | --- |
| FBX (binary 7.x) | magic `Kaydara FBX Binary` | native parser (`processing/fbx.py`), no Blender | retargeted with an explicit bone map |
| BVH | `HIERARCHY`/`MOTION` header | native parser (`processing/bvh.py`) | retargeted onto a character |
| GLB / glTF | magic `glTF` | native parser (`processing/glb.py`) | byte copy / repacked |

Detection always reads the bytes, never the extension. A file whose bytes do not
match any supported format is reported as `unsupported` with an explicit error and
is left in `Upload/`; it is never dropped silently.

### Classification rules

Classification is measurement-first (`processing/classify.py`). Every clip is
sampled through forward kinematics and reduced to: root travel and peak speed,
hip height relative to the rest pose, torso tilt, foot lift/alternation, hand
height and loop error. Then:

1. **Measured action** wins: prone/posture, swim, death, jump, gait (walk/run/sprint
   from peak speed), in-place gait (stride amplitude), turn, aim, fire, reload.
2. **File-name hints are candidates, not facts.** A hint is only used when the
   measurements are consistent with it (e.g. `walk crouching forward` is accepted
   because the measured action is `walk`). A hint that contradicts the motion
   produces `FILENAME_MEASUREMENT_MISMATCH` and the motion wins.
3. If a hint names a semantically unmeasurable action (`reload`, `grenade`, `hit`)
   *and* nothing contradicts it, the category comes from the name with
   `CLASSIFICATION_FROM_FILENAME` and a confidence of 0.62.
4. Anything else becomes `unclassified/` with a `UNCLASSIFIED_*` warning naming the
   measurements — never a guess.
5. Weapon (`rifle` vs `unarmed`) comes from the measured hand pose (both arms
   forward => two-handed weapon), not from the name.

### Skeleton compatibility rules

`processing/compatibility.py` groups skeletons by *fingerprint* (bone names +
parent links + normalised rest translations), not by bone count. Two rigs with 21
bones can land in different groups — and they do: the 74 uploads share all 65 bone
names and the whole hierarchy but split into two proportional variants
(`skel_226dc3817c`, 19 files, and `skel_534eaa02b3`, 55 files; the `LeftFoot`
offset is 0.4453 m in one and 0.3824 m in the other). Bone count equality would
have hidden that. Compatibility against a game character needs:

* every role in `config/bone_mappings/humanoid.json` resolved unambiguously,
* at least `humanoid_min_role_coverage` (0.75) of the required roles,
* at least 12 mapped bones, and
* matching hierarchy and spatial (bind-pose) checks.

The uploaded Mixamo rig is ambiguous for `chest`, `hand` and `toe` roles (Spine /
Spine1 / Spine2, finger chains, toe tips). `config/bone_mappings/mixamo_to_game.json`
resolves that ambiguity explicitly. The map is only applied to a skeleton whose
bone names actually match it; otherwise the generic matcher runs and reports the
ambiguity honestly.

### Retargeting limitations

* Retargeting uses an explicit role map. Without one, the pipeline reports
  `manual_bone_map_required` and exports nothing (it never guesses a pairing).
* Bone lengths are kept from the target rig; only rotations and the hips
  translation are transferred. Root translation is scaled by the hip-height ratio.
* Fingers, toes tips and eye bones that the target does not have are dropped and
  listed in `metadata.retarget.dropped_roles`.
* The result is validated behaviourally (`RETARGET_VALIDATED`): the exported GLB is
  re-parsed and re-measured, and the hip-height profile, vertical oscillation,
  torso orientation, loop closure and peak pose change must survive the transfer,
  with unit quaternions and strictly increasing key times. Khronos validation runs
  afterwards on the same file.
* **No playback claim is made.** The validator proves the file is well-formed and
  that the motion profile survived; rendering it in Filament/Godot is a separate,
  manual step.

### Upload procedure

1. Drop new files into `assets/animation/Upload/` (any supported format, any name,
   Unicode and spaces are fine).
2. Run `python -m processing.cli inventory` — the report lists `unsupported`,
   `corrupted` and `duplicate_*` entries with reasons.
3. Run `python -m processing.cli organize --apply` (dry run without `--apply`).
   Originals stay where they are; `docs/animation-library/` is refreshed.
4. Commit the new category copies plus `docs/animation-library/`; the
   `animation-processing.yml` workflow then inventories, organizes, processes,
   retargets, validates and verifies the archive hashes.

### Which files the pipeline processes

`processing.cli` processes the **canonical copy** of each content hash, not every
file that looks like an animation:

* `assets/animation/Upload/` is the archive and is never processed (its contents
  are covered by the organised copies);
* a source the organiser re-homed is skipped in favour of its copy in the category
  tree, and a source skipped as `duplicate_content` is skipped in favour of the
  canonical twin — so the same bytes can never be published under two asset ids;
* a file the organiser has never seen (dropped in without running `organize`) is
  still processed, so nothing goes silent.

### Processing workflow (`.github/workflows/animation-processing.yml`)

`inventory` → `organize --apply` → resolve changed sources → `process-animations`
→ `verify_upload_integrity` → summaries → `build-index` → artifact upload. The
existing Blender step remains the fallback for formats the native parsers do not
cover; the FBX path does not need Blender.

### Validation report format

Per asset: `reports/animations/<asset_id>.json` + `.md`, summarised in the manifest
(`validation.status` = `PASS` / `WARNING` / `ERROR`). Checks carry
`{severity, code, description, asset, location, suggested_fix}`; classification
warnings, compatibility records, retarget metadata and the retarget validation
block are all in the same report. The library-wide inventory is
`docs/animation-library/inventory.json`, and the per-file mapping with SHA-256 is
`docs/animation-library/organization-mapping.json`.
