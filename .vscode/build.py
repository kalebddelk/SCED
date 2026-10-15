# pyright: reportUndefinedVariable=false
# Standard Library
import argparse
import datetime
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

# Third-Party Libraries
try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    import pygetwindow
except ImportError:
    pygetwindow = None

PLATFORM = platform.system()
TTS_SUFFIX = Path("Tabletop Simulator") / "Saves"

# Helper Functions


def load_config():
    """Loads configuration from build_config.json with defaults."""
    config_path = Path(__file__).parent / "build_config.json"
    data = {"GAME_NAME": "ArkhamSCE", "HOTKEY": "f13", "FORCE_GO": False}  # Defaults

    if config_path.is_file():
        try:
            with open(config_path, "r") as f:
                user_config = json.load(f)
                data.update(user_config)
        except Exception as e:
            print(f"Warning: Could not read build_config.json ({e}). Using defaults.")
    return data


def get_current_git_branch():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def get_windows_documents_dir() -> Path:
    """Helper to safely retrieve the Windows Documents folder via registry."""
    try:
        import winreg

        sub_key = (
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        )
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key) as key:
            # 'Personal' is the registry key for the Documents folder
            doc_path_str, _ = winreg.QueryValueEx(key, "Personal")

            # Expand environment variables like %USERPROFILE% if present
            return Path(os.path.expandvars(doc_path_str))
    except Exception:
        # Fallback to standard guess if registry lookup fails
        return Path.home() / "Documents"


def get_output_folder():
    if PLATFORM == "Windows":
        base_dir = get_windows_documents_dir() / "My Games"
    elif PLATFORM == "Darwin":  # macOS
        base_dir = Path.home() / "Library"
    else:  # Linux
        base_dir = Path.home() / ".local" / "share"

    return base_dir / TTS_SUFFIX


def get_script_dir() -> Path:
    return Path(__file__).resolve().parent


def get_repo_root() -> Path:
    return get_script_dir().parent


def get_base_command():
    binary_map = {
        "Windows": "TTSModManager.exe",
        "Darwin": "TTSModManager-macOS",
        "Linux": "TTSModManager",
    }
    binary_name = binary_map.get(PLATFORM, "TTSModManager")
    binary_path = get_script_dir() / "bin" / binary_name

    if not FORCE_GO and binary_path.is_file():
        return [str(binary_path)], False
    return ["go", "run", "main.go"], True


def get_watch_roots(repo_root: Path):
    watch_dirs = [
        repo_root / "src",
        repo_root / "xml",
        repo_root / "locales",
        repo_root / "modsettings",
    ]
    watch_files = [
        repo_root / "config.json",
        repo_root / ".vscode" / "build_config.json",
    ]
    roots = []
    for entry in watch_dirs + watch_files:
        if entry.exists():
            roots.append(entry)
    return roots


def snapshot_watch_files(roots):
    state = {}
    for root in roots:
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file() and ".git" not in path.parts:
                    try:
                        state[str(path.resolve())] = path.stat().st_mtime_ns
                    except OSError:
                        pass
        elif root.is_file():
            try:
                state[str(root.resolve())] = root.stat().st_mtime_ns
            except OSError:
                pass
    return state


def watch_for_changes(full_cmd, repo_root: Path, poll_interval: float = 1.0):
    watch_roots = get_watch_roots(repo_root)
    if not watch_roots:
        print("No watchable source files found; exiting watch mode.")
        return

    state = snapshot_watch_files(watch_roots)
    print("Watching for source changes. Press Ctrl+C to stop.")
    print("Monitoring:")
    for root in watch_roots:
        print(f"  - {root}")

    while True:
        time.sleep(poll_interval)
        new_state = snapshot_watch_files(watch_roots)
        changed = [
            path for path, mtime in new_state.items() if state.get(path) != mtime
        ]
        if changed:
            print("Change detected, rebuilding...")
            for path in sorted(changed)[:10]:
                print(f"  - {os.path.relpath(path, repo_root)}")
            subprocess.run(full_cmd, check=True)
            state = new_state


def load_savegame_in_TTS():
    """Attempts to focus TTS and send the hotkey for loading."""
    if PLATFORM == "Windows":
        # Check if the required Windows libraries are available
        if pygetwindow is None or pyautogui is None:
            print(
                "Info: 'pygetwindow' or 'pyautogui' not installed. Cannot automatically load the savegame in TTS."
            )
            return

        for window in pygetwindow.getWindowsWithTitle(WINDOW_TITLE):
            # Check if the title is an EXACT match
            if window.title == WINDOW_TITLE:
                try:
                    window.activate()
                except pygetwindow.PyGetWindowException:
                    # If direct activation fails, toggle the window state
                    # This bypasses the Windows focus restriction
                    window.minimize()
                    window.restore()

                time.sleep(0.5)  # Give the OS time to switch focus

                # Requires setup in TTS (Example Autoexec.cfg: bind f13 load ArkhamSCE)
                pyautogui.hotkey(HOTKEY)
                break  # Found the exact window, so stop searching

    elif PLATFORM == "Darwin":
        # TODO
        return

    elif PLATFORM == "Linux":
        # TODO
        return


def copy_preview_image(output_folder, branch):
    script_dir = get_script_dir()
    base_image = Path(f"{GAME_NAME}.png")
    dev_image = Path(f"{GAME_NAME}_dev.png")

    # Prefer dev icon on feature branches when available.
    if branch and branch != "main" and (script_dir / dev_image).exists():
        source_image = script_dir / dev_image
    elif (script_dir / base_image).exists():
        source_image = script_dir / base_image
    else:
        source_image = None

    if source_image is not None:
        shutil.copy(source_image, output_folder / base_image)
    else:
        print(f"Note: Icon {base_image.name} not found in {script_dir}, skipping copy.")


# CONFIGURATION
CONFIG = load_config()
GAME_NAME = CONFIG["GAME_NAME"]
HOTKEY = CONFIG["HOTKEY"]
FORCE_GO = CONFIG["FORCE_GO"]
WINDOW_TITLE = "Tabletop Simulator"

GENERATED_NOISE_PATHS = ("config.json", "modsettings", "objects", "xml")


def should_keep_generated_file(rel_path: str, keep_paths: list[str]) -> bool:
    normalized = Path(rel_path).as_posix()
    for keep in keep_paths:
        keep_norm = Path(keep).as_posix().strip("/")
        if not keep_norm:
            continue
        if normalized == keep_norm or normalized.startswith(f"{keep_norm}/"):
            return True
    return False


def clean_generated_diff(repo_root: Path, keep_paths: list[str] | None = None) -> list[str]:
    """Revert known TTS-generated directories so only authored source changes remain."""
    keep_paths = keep_paths or []
    candidate_paths: list[str] = []

    try:
        changed = subprocess.check_output(
            ["git", "-C", str(repo_root), "diff", "--name-only"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    for rel_path in changed.splitlines():
        if not rel_path.strip():
            continue
        if should_keep_generated_file(rel_path, keep_paths):
            continue
        if rel_path == "config.json":
            candidate_paths.append(rel_path)
            continue
        if any(rel_path == base or rel_path.startswith(f"{base}/") for base in GENERATED_NOISE_PATHS):
            candidate_paths.append(rel_path)

    if not candidate_paths:
        return []

    subprocess.run(
        ["git", "-C", str(repo_root), "restore", "--worktree", "--source=HEAD", "--", *candidate_paths],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return candidate_paths


# Main Logic
def main():
    # Start the timer and get the current time
    start_time = time.time()
    start_time_formatted = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Parse arguments
    parser = argparse.ArgumentParser(
        description=f"VS Code build script for {GAME_NAME}"
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=["build", "decompose"],
        help="Action to perform: build or decompose.",
    )
    parser.add_argument(
        "--moddir",
        default=str(get_repo_root()),
        help="The mod directory. Defaults to the repo root.",
    )
    parser.add_argument(
        "--keep",
        action="append",
        default=[],
        help="Path to preserve when cleaning generated TTS noise after decompose. Repeat to keep multiple paths.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch source files and rebuild automatically whenever a source file changes.",
    )
    args = parser.parse_args()

    # Determine folder and command
    output_folder = get_output_folder()
    cmd, using_go = get_base_command()

    mod_dir = Path(args.moddir).expanduser()
    if not mod_dir.is_absolute():
        mod_dir = (Path.cwd() / mod_dir).resolve()

    # Set up command-line arguments
    mod_dir_arg = ["-moddir", str(mod_dir)]
    mod_file_arg = ["-modfile", str(output_folder / f"{GAME_NAME}.json")]
    reverse_arg = ["-reverse"] if args.action == "decompose" else []
    # TTS includes default object properties during reverse/decompose operations.
    # Writing bundled Lua/XML back to src/ makes the source tree the canonical
    # diff and cuts down on noisy generated metadata changes.
    write_src_arg = ["-writesrc"] if args.action == "decompose" else []

    # Final command to execute
    full_cmd = cmd + mod_dir_arg + mod_file_arg + reverse_arg + write_src_arg

    # Execute the core command
    branch = get_current_git_branch()

    print(f"{start_time_formatted}")
    print(f"Branch:  {branch}")
    print(f"Action:  {args.action}")
    print(f"Command: {' '.join(full_cmd)}")

    if args.watch:
        if using_go:
            repo_root = get_repo_root()
            full_ttsmodmanager_path = repo_root / "TTSModManager"
            build_command = full_cmd
            print("Starting watch mode. The build will run once immediately and then react to file saves.")
            subprocess.run(build_command, check=True, cwd=str(full_ttsmodmanager_path))
            watch_for_changes(build_command, mod_dir)
        else:
            subprocess.run(full_cmd, check=True)
            watch_for_changes(full_cmd, mod_dir)
        return

    if using_go:
        repo_root = get_repo_root()
        full_ttsmodmanager_path = repo_root / "TTSModManager"
        subprocess.run(full_cmd, check=True, cwd=str(full_ttsmodmanager_path))
    else:
        subprocess.run(full_cmd, check=True)

    if args.action == "decompose":
        restored = clean_generated_diff(mod_dir, args.keep)
        if restored:
            print("Restored generated TTS noise from the repo diff:")
            for path in restored:
                print(f"  - {path}")

    # Calculate and print the elapsed time
    elapsed_time = time.time() - start_time
    print(f"Execution took {elapsed_time:.2f} seconds.")

    if args.action == "build":
        copy_preview_image(output_folder, branch)
        load_savegame_in_TTS()


if __name__ == "__main__":
    main()
