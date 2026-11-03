#!/usr/bin/env python3
"""Static CI checks for the Android source tree (no JDK required here).

Verifies the modular structure, all 12 real screen destinations, no hardcoded
secrets, the exact pinned version matrix, and that every Kotlin file is
lexically balanced (brackets, strings, comments). The balance check catches the
class of error that failed the Step 1 build — e.g. an extra closing parenthesis
in a listOf() call, which the Kotlin compiler reports as
"Syntax error: Expecting member declaration" on the following line.

This is NOT a substitute for a Gradle build — android-build.yml performs that on
a runner with the Android SDK.
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
OPENERS = {"(": ")", "[": "]", "{": "}"}
CLOSERS = {")": "(", "]": "[", "}": "{"}


def kotlin_structure_issues(path: Path) -> list[str]:
    """Lexically balance brackets in a Kotlin file.

    Strings (including ``${...}`` templates), raw strings, char literals and
    comments are skipped so that braces inside them never count. Nested block
    comments (Kotlin allows them) and nested string templates are handled.
    """
    text = path.read_text(encoding="utf-8")
    issues: list[str] = []
    # Entries are ("code", opener, line) for brackets or ("template", "", line)
    # for the ${ ... } code region inside a string.
    stack: list[tuple[str, str, int]] = []
    mode = "code"          # code | str | raw
    line = 1
    i, n = 0, len(text)

    while i < n:
        ch = text[i]
        if mode == "code":
            if ch == "\n":
                line += 1
                i += 1
            elif text.startswith("//", i):
                j = text.find("\n", i)
                i = n if j < 0 else j
            elif text.startswith("/*", i):
                depth, start_line, i = 1, line, i + 2
                while i < n and depth:
                    if text.startswith("/*", i):
                        depth += 1
                        i += 2
                    elif text.startswith("*/", i):
                        depth -= 1
                        i += 2
                    else:
                        if text[i] == "\n":
                            line += 1
                        i += 1
                if depth:
                    issues.append(f"line {start_line}: unterminated block comment")
            elif text.startswith('"""', i):
                mode, i = "raw", i + 3
            elif ch == '"':
                mode, i = "str", i + 1
            elif ch == "'":
                j, closed = i + 1, False
                while j < n and j - i <= 6:
                    if text[j] == "\\":
                        j += 2
                        continue
                    if text[j] == "'":
                        closed, j = True, j + 1
                        break
                    if text[j] == "\n":
                        break
                    j += 1
                i = j if closed else i + 1
            else:
                if ch in OPENERS:
                    stack.append(("code", ch, line))
                elif ch in CLOSERS:
                    if not stack:
                        issues.append(f"line {line}: unmatched closing '{ch}'")
                    else:
                        kind, opener, opened_line = stack.pop()
                        if kind == "template":
                            # '}' closes a ${ ... } template -> back into the string
                            mode = "str"
                        elif CLOSERS[ch] != opener:
                            issues.append(
                                f"line {line}: '{ch}' closes '{opener}' opened on line {opened_line}")
                i += 1
        elif mode == "str":
            if ch == "\\":
                i += 2
            elif text.startswith("${", i):
                stack.append(("template", "${", line))
                mode, i = "code", i + 2
            elif ch == '"':
                mode, i = "code", i + 1
            elif ch == "\n":
                issues.append(f"line {line}: newline inside a string literal")
                mode, i = "code", i + 1
                line += 1
            else:
                i += 1
        else:  # raw string
            if text.startswith('"""', i):
                mode, i = "code", i + 3
            elif text.startswith("${", i):
                stack.append(("template", "${", line))
                mode, i = "code", i + 2
            else:
                if ch == "\n":
                    line += 1
                i += 1

    if mode != "code":
        issues.append(f"line {line}: unterminated {mode} string")
    for kind, opener, opened_line in stack:
        if kind == "template":
            issues.append(f"line {opened_line}: unclosed '${{' template")
        else:
            issues.append(f"line {opened_line}: unclosed '{opener}'")
    return issues


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
    kotlin_files = sorted(ANDROID.rglob("*.kt"))
    for path in kotlin_files:
        for issue in kotlin_structure_issues(path):
            failures.append(f"{path.relative_to(ROOT)}: {issue}")
    for f in failures:
        print("FAIL:", f)
    if failures:
        print(f"{len(failures)} Android source check(s) failed")
        return 1
    print(f"Android source checks passed: {len(MODULES)} modules, {len(SCREENS)} screens, "
          f"version matrix pinned, {len(kotlin_files)} Kotlin files balanced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
