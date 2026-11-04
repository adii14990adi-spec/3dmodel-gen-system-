"""Battle Royale animation taxonomy and a measurement-first classifier.

Design rules
------------
1. **Measurements decide.** Every action verdict comes from motion features
   computed by :mod:`processing.motion` (root travel, speed, hip height relative
   to the rest floor, foot contact, torso tilt, loop closure, pose change, hand
   geometry). A file name never overrides a measurement.
2. **Names are hints, and hints are recorded.** A file name may only *name* an
   action the measurements cannot separate (e.g. "reload" vs "equip"). When a
   hint is used, ``evidence`` says so, ``confidence`` drops, and the report
   carries a warning explaining exactly what was inferred from the name.
3. **Contradictions are surfaced.** When a hint disagrees with the measurements
   the measurement wins and ``FILENAME_MEASUREMENT_MISMATCH`` is reported.
4. **Low confidence goes to ``unclassified/``.** Below ``MIN_CONFIDENCE`` the
   asset is not filed under a guess; the uncertainty is explained instead.

Threshold calibration
---------------------
Thresholds are derived from the measured distribution of the uploaded pack (see
``docs/animation-library.md``): walk cycles peak at <= 2.0 m/s, runs at
2.4-5.2 m/s, sprints at >= 6.2 m/s; standing clips keep the hips at >= 0.78 of
the rest hip height, crouches at 0.35-0.78 and prone clips below 0.35; falls
end with the torso more than 55 deg away from vertical and a large loop error,
while looping clips close within a few degrees.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# Taxonomy
# --------------------------------------------------------------------------- #
#: Battle Royale animation library roots. Only non-empty branches are created.
TAXONOMY_ROOTS = ("locomotion", "combat", "other", "unclassified")

#: Weapon/parent branches per root.
TAXONOMY_BRANCHES = {
    "locomotion": ("unarmed", "rifle", "pistol"),
    "combat": ("rifle", "pistol", "melee", "reactions"),
    "other": ("grenade", "healing", "interaction", "emotes", "traversal", "swim"),
    "unclassified": (),  # flat: unclassified/<file>
}

#: Actions that a measurement alone can decide.
MEASURED_ACTIONS = ("idle", "walk", "run", "sprint", "jump", "turn", "death",
                    "hit", "roll", "swim", "prone", "crouch")

#: Actions that require a name hint plus consistent measurements.
SEMANTIC_ACTIONS = ("aim", "fire", "reload", "equip", "unequip", "grenade",
                    "healing", "interaction", "emote", "recoil", "traversal", "melee")

#: Gait thresholds (metres/second, measured peak root speed).
WALK_MAX_PEAK = 2.6
RUN_MAX_PEAK = 5.4

#: Posture thresholds (mean hip height / rest hip height).
PRONE_MAX_RATIO = 0.35
CROUCH_MAX_RATIO = 0.78

#: Fall/tilt thresholds (degrees between the hip->chest axis and world up).
DEATH_END_TILT = 55.0
ROLL_MAX_TILT = 150.0

#: Below this confidence an asset is filed under ``unclassified/``.
MIN_CONFIDENCE = 0.55

#: Hint token -> candidate action. Longest match wins.
HINT_ACTIONS: dict[str, str] = {
    "aiming": "aim", "aim": "aim",
    "firing": "fire", "fire": "fire", "shoot": "fire", "shooting": "fire",
    "reload": "reload", "reloading": "reload",
    "grenade": "grenade", "toss": "grenade",
    "hit": "hit", "reaction": "hit",
    "death": "death", "dying": "death", "dead": "death", "headshot": "death",
    "grab": "interaction", "put back": "interaction", "interact": "interaction",
    "equip": "equip", "unequip": "unequip",
    "heal": "healing", "healing": "healing", "medkit": "healing",
    "emote": "emote", "dance": "emote", "wave": "emote",
    "attack": "melee", "melee": "melee", "punch": "melee", "swing": "melee",
    "walk": "walk", "walking": "walk",
    "run": "run", "running": "run",
    "sprint": "sprint", "sprinting": "sprint",
    "idle": "idle",
    "jump": "jump",
    "crouch": "crouch", "crouching": "crouch",
    "prone": "prone",
    "turn": "turn", "turning": "turn",
    "strafe": "strafe",
    "swim": "swim", "swimming": "swim",
    "roll": "roll", "dive": "roll",
    "skate": "traversal", "skateboarding": "traversal", "vault": "traversal",
    "climb": "traversal",
}

HINT_WEAPONS = {"rifle": "rifle", "pistol": "pistol", "melee": "melee",
                "sword": "melee", "knife": "melee"}

HINT_DIRECTIONS = {"backward": "backward", "backwards": "backward", "back": "backward",
                   "forward": "forward", "left": "left", "right": "right"}
HINT_SUFFIXES = {"left": "left", "right": "right"}


def normalise_text(value: str) -> str:
    """Lowercase, ASCII-folded text used for hint matching only."""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()


def filename_hint(stem: str) -> dict[str, Any]:
    """Extract action/weapon/direction hints from a file name (evidence, not truth)."""
    text = normalise_text(stem)
    actions: list[str] = []
    for token, action in sorted(HINT_ACTIONS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(token)}\b", text) and action not in actions:
            actions.append(action)
    weapons = [weapon for token, weapon in HINT_WEAPONS.items()
               if re.search(rf"\b{re.escape(token)}\b", text)]
    directions = [direction for token, direction in HINT_DIRECTIONS.items()
                  if re.search(rf"\b{re.escape(token)}\b", text)]
    return {"text": text, "actions": actions, "weapons": weapons,
            "directions": sorted(set(directions)),
            "source": "file_name"}


# --------------------------------------------------------------------------- #
# Result model
# --------------------------------------------------------------------------- #
@dataclass
class Classification:
    category: str
    action: str
    posture: str
    gait: str
    weapon: str
    confidence: float
    style: str = ""
    evidence: list[str] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    measured: dict[str, Any] = field(default_factory=dict)
    hint: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "action": self.action,
            "posture": self.posture,
            "gait": self.gait,
            "weapon": self.weapon,
            "style": self.style,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
            "warnings": self.warnings,
            "measured": self.measured,
            "filename_hint": self.hint,
        }


def _warn(code: str, description: str, fix: str = "") -> dict[str, str]:
    return {"severity": "WARNING", "code": code, "description": description,
            "suggested_fix": fix or "Review the clip and adjust the classification rule if needed."}


# --------------------------------------------------------------------------- #
# Measurement helpers
# --------------------------------------------------------------------------- #
def posture_of(features: dict[str, Any]) -> tuple[str, float]:
    rest = (features.get("rest") or {}).get("hip_height_m") or 0.0
    mean = (features.get("hip_height") or {}).get("mean_m")
    if not rest or mean is None:
        return "unknown", 0.0
    ratio = mean / rest
    if ratio < PRONE_MAX_RATIO:
        return "prone", ratio
    if ratio < CROUCH_MAX_RATIO:
        return "crouch", ratio
    return "standing", ratio


def hands_ready(features: dict[str, Any]) -> dict[str, Any]:
    """Weapon posture from hand geometry relative to the hips.

    A two-handed forward grip (hands close together, forward, at chest height)
    is the rifle-ready posture; a single extended arm reads as a pistol; free
    hands read as unarmed. There is no weapon geometry in the uploads, so this
    is a posture heuristic and is reported as such.
    """
    hands = features.get("hands") or {}
    left, right = hands.get("left") or {}, hands.get("right") or {}
    if not left or not right:
        return {"weapon": "unknown", "two_handed": False, "confidence": 0.0,
                "forward": None, "separation": None, "height": None}
    forward_l, forward_r = left.get("forward_mean_m", 0.0), right.get("forward_mean_m", 0.0)
    height = min(left.get("above_hips_mean_m", 0.0), right.get("above_hips_mean_m", 0.0))
    separation = hands.get("separation_mean_m")
    # A two-handed carry keeps the hands together and at chest height; the
    # support hand reaches forward while the trigger hand stays near the chest,
    # so the *maximum* forward reach is the signal, not the minimum.
    forward = max(forward_l, forward_r)
    two_handed = bool(separation is not None and separation <= 0.45
                      and forward >= 0.30 and height >= 0.10)
    one_handed = bool(not two_handed and max(forward_l, forward_r) >= 0.40
                      and separation is not None and separation >= 0.55)
    weapon = "rifle" if two_handed else ("pistol" if one_handed else "unarmed")
    return {"weapon": weapon, "two_handed": two_handed,
            "confidence": 0.7 if two_handed else (0.55 if one_handed else 0.5),
            "forward": round(forward, 4), "separation": separation,
            "height": round(height, 4)}


def measured_action(features: dict[str, Any]) -> dict[str, Any]:
    """Return the measurement-decided action plus the numbers that decided it."""
    root = features.get("root_motion") or {}
    torso = features.get("torso") or {}
    motion = features.get("motion") or {}
    feet = features.get("feet") or {}
    duration = features.get("duration_seconds") or 0.0
    net = root.get("net_horizontal_m") or 0.0
    mean_speed = root.get("speed_mps") or 0.0
    peak_speed = root.get("peak_speed_mps") or 0.0
    in_place = net < 0.08
    tilt_end = torso.get("tilt_deg_end") or 0.0
    tilt_max = torso.get("tilt_deg_max") or 0.0
    tilt_mean = torso.get("tilt_deg_mean") or 0.0
    loop_error = motion.get("loop_error_deg") or 0.0
    step = motion.get("mean_step_deg") or 0.0
    airborne = features.get("airborne_fraction") or 0.0
    yaw = abs(features.get("yaw_change_deg") or 0.0)
    posture, ratio = posture_of(features)
    hip_range = (features.get("hip_height") or {})
    hip_drop = (hip_range.get("max_m", 0.0) - hip_range.get("min_m", 0.0))
    measured = {"in_place": in_place, "net_m": round(net, 4),
                "mean_speed_mps": mean_speed, "peak_speed_mps": peak_speed,
                "hip_ratio": round(ratio, 4), "tilt_end_deg": tilt_end,
                "tilt_max_deg": tilt_max, "tilt_mean_deg": tilt_mean,
                "loop_error_deg": loop_error, "mean_step_deg": step,
                "airborne_fraction": airborne, "yaw_change_deg": yaw,
                "hip_travel_m": round(hip_drop, 4),
                "alternation_count": feet.get("alternation_count", 0)}

    base = {**measured}

    if tilt_max >= ROLL_MAX_TILT and loop_error <= 60.0:
        return {**base, "action": "roll", "posture": posture,
                "reason": f"torso rotates {tilt_max:.0f} deg (full roll)"}
    if tilt_end >= DEATH_END_TILT and (loop_error >= 40.0 or hip_drop >= 0.30):
        return {**base, "action": "death", "posture": "fallen",
                "reason": f"torso ends {tilt_end:.0f} deg from vertical with loop error {loop_error:.0f} deg"}
    if tilt_mean >= 35.0 and airborne >= 0.30 and loop_error <= 40.0:
        return {**base, "action": "swim", "posture": "horizontal",
                "reason": f"body horizontal (mean tilt {tilt_mean:.0f} deg) with the feet off the floor"}
    if airborne >= 0.10 and net <= 1.0 and peak_speed <= 2.5 and duration <= 3.0 and tilt_max < 60.0:
        return {**base, "action": "jump", "posture": posture,
                "reason": f"{airborne * 100:.0f}% of the clip is airborne with no travel"}
    if in_place and yaw >= 40.0:
        return {**base, "action": "turn", "posture": posture,
                "reason": f"{yaw:.0f} deg of yaw with no net translation"}
    if net >= 0.5 or (net >= 0.15 and peak_speed >= 0.8):
        gait = "sprint" if peak_speed >= RUN_MAX_PEAK else ("run" if peak_speed >= WALK_MAX_PEAK else "walk")
        return {**base, "action": gait, "posture": posture, "gait": gait,
                "reason": f"root travels {net:.2f} m at peak {peak_speed:.2f} m/s"}
    if (in_place and int(feet.get("alternation_count", 0)) >= 2 and step >= 0.6
            and (feet.get("stride_amplitude_m") or 0.0) >= 0.30):
        # In-place gait: feet alternate but the root does not travel. Speed is not
        # measurable, so the gait is named from stride amplitude instead.
        stride = feet.get("stride_amplitude_m") or 0.0
        gait = "run" if stride >= 0.55 else "walk"
        return {**base, "action": gait, "posture": posture, "gait": gait,
                "reason": f"in-place gait: {feet.get('alternation_count')} foot changes, "
                          f"stride {stride:.2f} m"}
    if posture == "prone":
        return {**base, "action": "prone", "posture": posture,
                "reason": f"hips sit at {ratio:.2f} of rest height with no root travel"}
    if step < 0.35:
        return {**base, "action": "idle", "posture": posture,
                "reason": f"no root travel and only {step:.2f} deg mean pose change per sample"}
    return {**base, "action": "action", "posture": posture,
            "reason": f"in-place one-shot: {step:.2f} deg mean pose change per sample"}


#: Consistency predicates: what a hinted action must look like in the measurements.
def hint_is_consistent(hint_action: str, features: dict[str, Any],
                       measured: dict[str, Any], hands: dict[str, Any]) -> tuple[bool, str]:
    root = features.get("root_motion") or {}
    torso = features.get("torso") or {}
    motion = features.get("motion") or {}
    duration = features.get("duration_seconds") or 0.0
    in_place = measured.get("in_place")
    peak = measured.get("peak_speed_mps") or 0.0
    step = measured.get("mean_step_deg") or 0.0
    tilt_end = measured.get("tilt_end_deg") or 0.0
    ready = hands.get("two_handed") or hands.get("weapon") == "pistol"

    if hint_action == "walk":
        return peak < WALK_MAX_PEAK + 0.6, f"peak speed {peak:.2f} m/s"
    if hint_action == "run":
        return WALK_MAX_PEAK - 0.4 <= peak < RUN_MAX_PEAK + 0.6, f"peak speed {peak:.2f} m/s"
    if hint_action == "sprint":
        return peak >= RUN_MAX_PEAK - 0.6, f"peak speed {peak:.2f} m/s"
    if hint_action == "idle":
        return bool(in_place) and step < 1.5, f"in-place={in_place}, step {step:.2f} deg"
    if hint_action in ("fire", "reload", "equip", "unequip", "interaction"):
        return bool(in_place), f"in-place={in_place}, step {step:.2f} deg"
    if hint_action == "aim":
        return bool(in_place) and step < 0.4, f"in-place={in_place}, step {step:.2f} deg"
    if hint_action == "fire":
        return duration <= 1.2, f"duration {duration:.2f} s"
    if hint_action == "reload":
        return duration >= 1.2, f"duration {duration:.2f} s"
    if hint_action == "hit":
        return duration <= 1.6 and tilt_end < 40.0, f"duration {duration:.2f} s, end tilt {tilt_end:.0f} deg"
    if hint_action == "death":
        return tilt_end >= 40.0, f"end tilt {tilt_end:.0f} deg"
    if hint_action == "jump":
        return (features.get("airborne_fraction") or 0.0) >= 0.02 or duration <= 2.5, "airborne or short clip"
    if hint_action == "crouch":
        return (measured.get("hip_ratio") or 1.0) < 0.9, f"hip ratio {measured.get('hip_ratio')}"
    if hint_action == "prone":
        return (measured.get("hip_ratio") or 1.0) < 0.5, f"hip ratio {measured.get('hip_ratio')}"
    if hint_action == "turn":
        return (measured.get("yaw_change_deg") or 0.0) >= 20.0, \
            f"yaw change {measured.get('yaw_change_deg', 0.0):.1f} deg"
    if hint_action == "grenade":
        return bool(in_place), f"in-place={in_place}, step {step:.2f} deg"
    if hint_action == "swim":
        return (measured.get("tilt_mean_deg") or 0.0) >= 35.0, f"mean tilt {measured.get('tilt_mean_deg')}"
    if hint_action == "roll":
        return (measured.get("tilt_max_deg") or 0.0) >= 90.0, f"max tilt {measured.get('tilt_max_deg')}"
    if hint_action == "melee":
        return bool(in_place), f"in-place={in_place}"
    if hint_action == "traversal":
        return True, "no measurement distinguishes traversal modes"
    if hint_action == "emote":
        return bool(in_place), f"in-place={in_place}"
    if hint_action == "healing":
        return bool(in_place) and duration >= 1.0, f"duration {duration:.2f} s"
    if hint_action == "strafe":
        direction = (root.get("direction_deg") or 0.0)
        return 60.0 <= abs(direction) <= 120.0, f"travel direction {direction:.0f} deg from +Z"
    return False, "no consistency rule"


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
def _category_for(action: str, posture: str, weapon: str) -> str:
    """Map an action+posture+weapon to a library path (no trailing slash)."""
    weapon = weapon if weapon in ("rifle", "pistol") else "unarmed"
    if action in ("idle", "prone"):
        base = "prone_idle" if (action == "prone" or posture == "prone") else "idle"
        if posture == "crouch" and action == "idle":
            base = "crouch_idle"
        return f"locomotion/{weapon}/{base}"
    if action in ("walk", "run", "sprint"):
        prefix = "crouch_" if posture == "crouch" else ""
        return f"locomotion/{weapon}/{prefix}{action}"
    if action == "turn":
        prefix = "crouch_" if posture == "crouch" else ""
        return f"locomotion/{weapon}/{prefix}turn"
    if action == "jump":
        return f"locomotion/{weapon}/jump"
    if action == "strafe":
        return f"locomotion/{weapon}/strafe"
    if action == "death":
        return "combat/reactions/death"
    if action == "hit":
        return "combat/reactions/hit"
    if action in ("aim", "fire", "reload", "recoil", "equip", "unequip"):
        return f"combat/{weapon}/{action}"
    if action == "melee":
        return "combat/melee"
    if action == "grenade":
        return "other/grenade"
    if action == "healing":
        return "other/healing"
    if action == "interaction":
        return "other/interaction"
    if action == "emote":
        return "other/emotes"
    if action == "swim":
        return "other/swim"
    if action in ("roll", "traversal"):
        return "other/traversal"
    return "unclassified"


def classify(features: dict[str, Any], stem: str) -> Classification:
    """Classify one clip from measured features plus (recorded) file-name hints."""
    hint = filename_hint(stem)
    measured = measured_action(features)
    posture, ratio = posture_of(features)
    hands = hands_ready(features)
    weapon = hands.get("weapon", "unknown")
    if weapon not in ("rifle", "pistol", "unarmed"):
        weapon = "unarmed"
    action = measured["action"]
    evidence: list[str] = [measured.get("reason", "")]
    warnings: list[dict[str, str]] = []
    confidence = 0.85

    # Evaluate every hint token against the measurements; the first hint that the
    # data actually supports is the one we may use.
    known = MEASURED_ACTIONS + SEMANTIC_ACTIONS
    candidates: list[tuple[str, bool, str]] = []
    for candidate in hint["actions"]:
        if candidate in known:
            consistent, why = hint_is_consistent(candidate, features, measured, hands)
            candidates.append((candidate, consistent, why))
    consistent_hints = [(name, why) for name, ok, why in candidates if ok]
    # Prefer the hint that names the measured action, so "walk crouching" (hints
    # crouch + walk) confirms a measured walk instead of contradicting it.
    supported = next(((n, w) for n, w in consistent_hints if n == action),
                     consistent_hints[0] if consistent_hints else None)

    # The feet never alternate, so there is no gait cycle: a traversal hint is
    # accepted for a clip that translates without stepping (skateboard, vault).
    if (any(name == "traversal" for name, ok, _ in candidates if ok)
            and action in ("walk", "run", "sprint")
            and int((features.get("feet") or {}).get("alternation_count", 0)) <= 1):
        action = "traversal"
        confidence = 0.68
        evidence.append("translation without a gait cycle supports the traversal hint")
        warnings.append(_warn(
            "CLASSIFICATION_FROM_FILENAME",
            "Filed as traversal because the character translates without alternating feet and the file "
            "name says so; the exact traversal type is not measured.",
            "Confirm the traversal type visually."))
        supported = ("traversal", "translation without a gait cycle")

    if action == "action":
        if supported:
            action = supported[0]
            confidence = 0.62
            evidence.append(f"file-name hint '{action}' supported by {supported[1]}")
            warnings.append(_warn(
                "CLASSIFICATION_FROM_FILENAME",
                f"Action '{action}' comes from the file name; the motion alone cannot separate it from "
                f"other in-place actions ({supported[1]}), and the measurements are consistent with it.",
                "Confirm the action visually and extend the classifier rules if it is wrong."))
        else:
            action = "unclassified"
            confidence = 0.3
            if candidates:
                detail = "; ".join(f"{name} ({why})" for name, _ok, why in candidates)
                warnings.append(_warn(
                    "FILENAME_MEASUREMENT_MISMATCH",
                    f"File-name hint(s) {detail} disagree with the measured motion "
                    f"({measured['reason']}).", "Verify the source clip; the name is unreliable."))
            else:
                warnings.append(_warn(
                    "UNCLASSIFIED_AMBIGUOUS_MOTION",
                    "In-place one-shot motion with no decisive measurement and no usable file-name hint.",
                    "Inspect the clip and add a classification rule or a manual override."))
    elif not candidates:
        evidence.append("no file-name hint; decided from measurements")
    elif supported and supported[0] == action:
        evidence.append(f"file-name hint '{supported[0]}' agrees with the measurement")
        confidence = min(0.95, max(confidence, 0.9))
    elif supported:
        warnings.append(_warn(
            "FILENAME_MEASUREMENT_MISMATCH",
            f"File name suggests '{supported[0]}' but the measured motion is '{action}' "
            f"({measured['reason']}). The measurement wins.",
            "Rename the asset or verify the export."))
        confidence = min(confidence, 0.6)
    else:
        detail = "; ".join(f"{name} ({why})" for name, _ok, why in candidates)
        warnings.append(_warn(
            "FILENAME_MEASUREMENT_MISMATCH",
            f"File-name hint(s) {detail} are not supported by the measurements; measured motion is "
            f"'{action}' ({measured['reason']}). The measurement wins.",
            "Rename the asset or verify the export."))
        confidence = min(confidence, 0.6)

    # Aiming idle vs relaxed idle: both are static; a two-handed ready posture
    # separates them, and the hint decides the remaining ambiguity.
    if action == "idle" and any(name == "aim" for name, ok, _ in candidates if ok) and hands.get("two_handed"):
        action = "aim"
        confidence = 0.68
        evidence.append("two-handed ready posture with no movement supports the aiming hint")
        warnings.append(_warn(
            "CLASSIFICATION_FROM_FILENAME",
            "Separated an aiming idle from a relaxed idle using the file-name hint plus a measured "
            "two-handed ready posture.",
            "Confirm visually; tune `hands_ready` thresholds for your rig."))

    category = "unclassified" if action == "unclassified" else _category_for(action, posture, weapon)
    if confidence < MIN_CONFIDENCE and not category.startswith("unclassified"):
        warnings.append(_warn(
            "LOW_CONFIDENCE_CLASSIFICATION",
            f"Confidence {confidence:.2f} is below the {MIN_CONFIDENCE:.2f} threshold.",
            "Verify manually or extend the classifier."))

    # Direction/style modifier (strafe, forward, backward) from the hint plus the
    # measured world-space travel direction. Facing cannot be verified on this
    # rig (see docs/limitations.md), so the hint is recorded as the source.
    style = ""
    if "strafe" in hint["actions"] and hint_is_consistent("strafe", features, measured, hands)[0]:
        style = "strafe"
    elif hint["directions"]:
        style = hint["directions"][0]

    evidence = [item for item in dict.fromkeys(evidence) if item]
    return Classification(category=category, action=action, posture=posture,
                          gait=measured.get("gait", ""), weapon=weapon, style=style,
                          confidence=confidence, evidence=evidence, warnings=warnings,
                          measured=measured, hint=hint)


def category_root(category: str) -> str:
    return category.split("/", 1)[0]


def validate_category(category: str) -> bool:
    """True when a category is inside the declared taxonomy."""
    parts = category.split("/")
    root = parts[0]
    if root not in TAXONOMY_ROOTS:
        return False
    if root == "unclassified":
        return len(parts) == 1
    if len(parts) < 2:
        return False
    branch = parts[1]
    allowed = TAXONOMY_BRANCHES[root]
    if root == "combat" and branch == "reactions":
        return len(parts) == 3 and parts[2] in ("hit", "death")
    if root == "other":
        return len(parts) == 2 and branch in allowed
    if root == "locomotion":
        return branch in ("unarmed", "rifle", "pistol") and len(parts) == 3
    return len(parts) <= 3
