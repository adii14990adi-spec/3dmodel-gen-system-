#!/usr/bin/env python3
"""Static CI checks for the Android source tree (no JDK required here).

Verifies the modular structure, all 12 real screen destinations, no hardcoded
secrets and the exact pinned version matrix. This is NOT a substitute for a
Gradle build — android-build.yml performs that on a runner with the Android SDK.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANDROID = ROOT / "android"
SCREENS = ["HomeScreen", "AssetLibraryScreen", "ModelBrowserScreen", "ViewportScreen",
           "SkeletonInspectorScreen", "AnimationLibraryScreen", "AnimationPlayerScreen",
           "MaterialInspectorScreen", "GeometryInspectorScreen", "ValidationReportScreen",
           "DownloadsScreen", "SettingsScreen"]
MODULES = ["app", "core", "data", "asset-library", "renderer", "skeleton", "animation", "inspector"]
SECRET_PATTERNS = [r"AKIA[0-9A-Z]{16}", r"ghp_[A-Za-z0-9]{20,}",
                   r"-----BEGIN (RSA |EC )?PRIVATE KEY-----"]


def main() -> int:
    failures: list[str] = []
    for mod in MODULES:
        if not (ANDROID / mod / "build.gradle.kts").is_file():
            failures.append(f"missing build.gradle.kts for :{mod}")
    sources = "\n".join(p.read_text(encoding="utf-8") for p in ANDROID.rglob("*.kt"))
    for screen in SCREENS:
        if f"fun {screen}(" not in sources:
            failures.append(f"missing screen composable {screen}")
    nav = (ANDROID / "app/src/main/java/com/assetstudio/app/MainActivity.kt").read_text()
    for route in ["home", "library", "models", "viewport", "skeleton", "animations",
                  "player", "materials", "geometry", "validation", "downloads", "settings"]:
        if f'composable(Routes.{route.upper()})' not in nav:
            failures.append(f"missing navigation destination {route}")
    for pattern in SECRET_PATTERNS:
        if re.search(pattern, sources):
            failures.append(f"possible hardcoded secret matching {pattern}")
    versions = (ANDROID / "gradle/libs.versions.toml").read_text()
    for expected in ['agp = "8.7.3"', 'kotlin = "2.0.21"', 'filament = "1.75.1"',
                     'composeBom = "2024.12.01"']:
        if expected not in versions:
            failures.append(f"version pin missing: {expected}")
    wrapper = (ANDROID / "gradle/wrapper/gradle-wrapper.properties").read_text()
    if "gradle-8.9-bin.zip" not in wrapper:
        failures.append("Gradle wrapper is not pinned to 8.9")
    if not (ANDROID / "gradle/wrapper/gradle-wrapper.jar").is_file():
        failures.append("gradle-wrapper.jar missing")
    for f in failures:
        print("FAIL:", f)
    if failures:
        print(f"{len(failures)} Android source check(s) failed")
        return 1
    print(f"Android source checks passed: {len(MODULES)} modules, {len(SCREENS)} screens, version matrix pinned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
