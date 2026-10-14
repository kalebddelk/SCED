#!/usr/bin/env python3

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


POS_RE = re.compile(r'^\s*"[ ]*pos[XYZ]"\s*:\s*', re.IGNORECASE)


@dataclass
class ChangeInfo:
    path: str
    added: int
    removed: int
    pos_plus: int
    pos_minus: int
    lua_script_touches: int
    xmlui_touches: int
    is_untracked: bool

    @property
    def churn(self) -> int:
        return self.added + self.removed

    @property
    def pos_changed(self) -> bool:
        return self.pos_plus > 0 and self.pos_minus > 0


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=cwd,
        text=True,
        stderr=subprocess.DEVNULL,
    )


def list_modified_files(repo_root: Path) -> list[str]:
    output = run_git(["diff", "--name-only", "HEAD", "--"], repo_root)
    return [line for line in output.splitlines() if line.strip()]


def list_untracked_files(repo_root: Path) -> list[str]:
    output = run_git(["ls-files", "--others", "--exclude-standard"], repo_root)
    return [line for line in output.splitlines() if line.strip()]


def analyze_modified_file(repo_root: Path, rel_path: str) -> ChangeInfo:
    patch = run_git(["diff", "-U0", "--", rel_path], repo_root)
    added = 0
    removed = 0
    pos_plus = 0
    pos_minus = 0
    lua_script_touches = 0
    xmlui_touches = 0

    for line in patch.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("+"):
            added += 1
            payload = line[1:]
            if POS_RE.match(payload):
                pos_plus += 1
            if '"LuaScript"' in payload or '"LuaScriptState"' in payload:
                lua_script_touches += 1
            if '"XmlUI"' in payload:
                xmlui_touches += 1
        elif line.startswith("-"):
            removed += 1
            payload = line[1:]
            if POS_RE.match(payload):
                pos_minus += 1
            if '"LuaScript"' in payload or '"LuaScriptState"' in payload:
                lua_script_touches += 1
            if '"XmlUI"' in payload:
                xmlui_touches += 1

    return ChangeInfo(
        path=rel_path,
        added=added,
        removed=removed,
        pos_plus=pos_plus,
        pos_minus=pos_minus,
        lua_script_touches=lua_script_touches,
        xmlui_touches=xmlui_touches,
        is_untracked=False,
    )


def analyze_untracked_file(rel_path: str) -> ChangeInfo:
    return ChangeInfo(
        path=rel_path,
        added=0,
        removed=0,
        pos_plus=0,
        pos_minus=0,
        lua_script_touches=0,
        xmlui_touches=0,
        is_untracked=True,
    )


def include_for_profile(change: ChangeInfo, profile: str) -> bool:
    path = change.path
    if profile == "move":
        return change.pos_changed

    if profile == "script":
        return (
            path.startswith("src/")
            or path.endswith(".ttslua")
            or change.lua_script_touches > 0
        )

    if profile == "ui":
        return (
            path.startswith("xml/")
            or path == "modsettings/CustomUIAssets.json"
            or change.xmlui_touches > 0
        )

    return (
        change.pos_changed
        or change.lua_script_touches > 0
        or change.xmlui_touches > 0
        or path.startswith("src/")
        or path.startswith("xml/")
        or path == "modsettings/CustomUIAssets.json"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report-only triage for noisy SCED decompose diffs."
    )
    parser.add_argument(
        "--profile",
        choices=["move", "script", "ui", "all"],
        default="all",
        help="Intent profile for filtering likely relevant files.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=40,
        help="Maximum number of candidate files to print.",
    )
    parser.add_argument(
        "--moddir",
        default=".",
        help="Repository root path (defaults to current directory).",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    repo_root = Path(args.moddir).resolve()

    modified = list_modified_files(repo_root)
    untracked = list_untracked_files(repo_root)

    analyzed = [analyze_modified_file(repo_root, rel) for rel in modified]
    analyzed.extend(analyze_untracked_file(rel) for rel in untracked)

    candidates = [
        c for c in analyzed if not c.is_untracked and include_for_profile(c, args.profile)
    ]
    candidates.sort(key=lambda c: (c.churn, c.path))
    candidates = candidates[: max(args.limit, 1)]

    print(f"SCED triage profile: {args.profile}")
    print(f"Modified files: {len(modified)} | Untracked files: {len(untracked)}")
    print(f"Candidate files: {len(candidates)}")
    print()

    if not candidates:
        print("No candidates found for this profile.")
        return 0

    print("Candidates (lowest churn first):")
    for c in candidates:
        signals = []
        if c.pos_changed:
            signals.append("pos-change")
        if c.lua_script_touches:
            signals.append("lua-script-touch")
        if c.xmlui_touches:
            signals.append("xmlui-touch")
        if c.path.startswith("src/"):
            signals.append("src")
        if c.path.startswith("xml/"):
            signals.append("xml")
        signal_label = ",".join(signals) if signals else "path-match"
        print(
            f"- {c.path}  (churn={c.churn}, +{c.added}/-{c.removed}, signal={signal_label})"
        )

    print()
    print(
        "Tip: stage intended files first, then drop unstaged noise with "
        "`git restore --worktree -- .` and untracked noise with `git clean -fd`."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
