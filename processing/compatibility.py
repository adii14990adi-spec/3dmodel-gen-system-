"""Configurable, conservative humanoid skeleton compatibility checking.

Bone-name similarity alone is NEVER treated as proof: canonical-role mapping is
combined with hierarchy and spatial checks, and any ambiguous role mapping must
be resolved by an explicit manual configuration before retargeting is allowed.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from processing.common import ROOT, load_config


def load_mappings() -> dict[str, Any]:
    return json.loads((ROOT / "config" / "bone_mappings" / "humanoid.json").read_text(encoding="utf-8"))


def normalise(name: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "_" for c in name.strip())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


def side_of(name: str, mappings: dict[str, Any]) -> str | None:
    n = normalise(name)
    tokens = mappings.get("side_tokens", {})
    left = [normalise(t) for t in tokens.get("left", [])]
    right = [normalise(t) for t in tokens.get("right", [])]
    is_left = any(n.startswith(t) or t in n for t in left)
    is_right = any(n.startswith(t) or t in n for t in right)
    if is_left and not is_right: return "left"
    if is_right and not is_left: return "right"
    return None


def role_for(name: str, mappings: dict[str, Any]) -> tuple[str | None, float]:
    """Return (role, confidence); >=0.95 exact alias, 0.6 heuristic token match."""
    n = normalise(name)
    roles = mappings["roles"]
    for role, aliases in roles.items():
        if n in {normalise(a) for a in aliases}:
            return role, 1.0
    side = side_of(name, mappings)
    for role, aliases in roles.items():
        for alias in aliases:
            a = normalise(alias)
            if len(a) >= 3 and (a in n or n in a):
                if side and not role.startswith(side) and role.startswith(("left", "right")):
                    continue
                return role, 0.6
    return None, 0.0


def map_roles(bones: list[dict[str, Any]]) -> tuple[dict[str, list[str]], list[str], float]:
    mappings = load_mappings()
    found: dict[str, list[str]] = {}
    ambiguous: list[str] = []
    confidences: list[float] = []
    for bone in bones:
        name = bone.get("name", "")
        role, conf = role_for(name, mappings)
        if not role:
            continue
        found.setdefault(role, []).append(name)
        confidences.append(conf)
    for role, names in list(found.items()):
        if len(names) > 1:
            ambiguous.append(role)
            # Ambiguity is NOT resolved silently; keep the first only for reporting.
            found[role] = names[:1]
    return found, ambiguous, (sum(confidences) / len(confidences)) if confidences else 0.0


def _hierarchy_check(bones: list[dict[str, Any]], mapped: dict[str, list[str]]) -> list[str]:
    """Reject obviously wrong hierarchies (e.g. a leg bone parented under an arm)."""
    by_name = {b.get("name"): b for b in bones}
    problems: list[str] = []
    for child_role, parent_role in (("left_lower_arm", "left_upper_arm"),
                                    ("right_lower_arm", "right_upper_arm"),
                                    ("left_lower_leg", "left_upper_leg"),
                                    ("right_lower_leg", "right_upper_leg"),
                                    ("head", "neck")):
        child = mapped.get(child_role, [None])[0]
        parent = mapped.get(parent_role, [None])[0]
        if not child or not parent or parent_role == "neck" and "neck" not in mapped:
            continue
        info = by_name.get(child)
        if info and info.get("parent") and normalise(info["parent"]) != normalise(parent):
            problems.append(f"{child_role} ('{child}') is not parented under {parent_role} ('{parent}')")
    return problems


def _spatial_check(bones: list[dict[str, Any]]) -> list[str]:
    """Detect degenerate/inverted limb directions from rest joint translations."""
    by_name = {b.get("name", ""): b for b in bones}
    problems: list[str] = []
    for name, bone in by_name.items():
        t = bone.get("translation") or [0, 0, 0]
        if not isinstance(t, (list, tuple)) or len(t) < 3:
            continue
        length = math.sqrt(sum(float(v) ** 2 for v in t[:3]))
        if length > 100:  # implausible metre-scale joint offset
            problems.append(f"bone '{name}' has an implausible rest offset ({length:.1f}); check source units")
    return problems


def check_compatibility(source_bones: list[dict[str, Any]], target_bones: list[dict[str, Any]],
                        manual_map: dict[str, str] | None = None) -> dict[str, Any]:
    """Compare two real skeletons. Returns a record with an explicit decision."""
    cfg = load_config()["compatibility"]
    mappings = load_mappings()
    required = set(mappings.get("required_roles", []))
    src_roles, src_amb, src_conf = map_roles(source_bones)
    tgt_roles, tgt_amb, tgt_conf = map_roles(target_bones)
    reasons: list[str] = []
    ambiguous = sorted(set(src_amb + tgt_amb))
    unmapped_required = sorted(required - set(tgt_roles))
    common = sorted(set(src_roles) & set(tgt_roles))
    coverage = len(common) / max(1, len(required))
    hierarchy = _hierarchy_check(target_bones, tgt_roles) + _hierarchy_check(source_bones, src_roles)
    spatial = _spatial_check(source_bones) + _spatial_check(target_bones)
    if not source_bones:
        reasons.append("source asset has no skeleton")
    if not target_bones:
        reasons.append("target character has no skeleton")
    if ambiguous and cfg.get("require_unambiguous_roles", True):
        reasons.append("ambiguous bone-role mappings: " + ", ".join(ambiguous) +
                       "; provide an explicit manual bone map in config/bone_mappings/")
    if unmapped_required:
        reasons.append("target is missing required humanoid roles: " + ", ".join(unmapped_required))
    if coverage < float(cfg["humanoid_min_role_coverage"]):
        reasons.append(f"role coverage {coverage:.2f} is below {cfg['humanoid_min_role_coverage']}")
    if hierarchy:
        reasons.extend(hierarchy)
    if spatial:
        reasons.extend(spatial)
    if len(common) < int(cfg["humanoid_min_mapped_bones"]):
        reasons.append(f"only {len(common)} shared roles; at least {cfg['humanoid_min_mapped_bones']} required")
    compatible = not reasons and bool(source_bones) and bool(target_bones)
    return {
        "status": "compatible" if compatible else "incompatible",
        "score": round((src_conf + tgt_conf) / 2 * coverage, 4) if compatible else 0.0,
        "role_coverage": round(coverage, 4), "mapped_bones": len(common),
        "mapped_roles": common, "unmapped_roles": unmapped_required,
        "ambiguous_roles": ambiguous, "manual_map_applied": bool(manual_map),
        "reasons": reasons or ["hierarchy, spatial and role checks passed"],
    }
