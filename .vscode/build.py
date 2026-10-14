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


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text(
            encoding="utf-8"
        ).lower()
    except OSError:
        return False


def windows_path_to_wsl(path: str) -> Path:
    normalized = path.strip().replace("\\", "/")
    if len(normalized) >= 2 and normalized[1] == ":":
        drive_letter = normalized[0].lower()
        return Path(f"/mnt/{drive_letter}{normalized[2:]}")
    return Path(normalized)


def get_windows_userprofile_from_wsl() -> Path | None:
    try:
        completed = subprocess.run(
            ["cmd.exe", "/c", "echo", "%UserProfile%"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    userprofile = completed.stdout.strip()
    if not userprofile or "%" in userprofile:
        return None

    return windows_path_to_wsl(userprofile)


def get_output_folder():
    for env_name in ("TTSMODMANAGER_SAVE_DIR", "TTS_SAVE_DIR"):
        output_folder_override = os.environ.get(env_name)
        if output_folder_override:
            override_value = output_folder_override.strip()
            if is_wsl() and len(override_value) >= 2 and override_value[1] == ":":
                return windows_path_to_wsl(override_value)
            return Path(override_value).expanduser()

    if PLATFORM == "Windows":
        base_dir = get_windows_documents_dir() / "My Games"
    elif PLATFORM == "Darwin":  # macOS
        base_dir = Path.home() / "Library"
    else:  # Linux
        if is_wsl():
            windows_profile = get_windows_userprofile_from_wsl()
            if windows_profile is None:
                raise RuntimeError(
                    "Unable to detect Windows user profile from WSL. "
                    "Set TTSMODMANAGER_SAVE_DIR to your Tabletop Simulator Saves folder."
                )
            base_dir = windows_profile / "Documents" / "My Games"
        else:
            base_dir = Path.home() / ".local" / "share"

    return base_dir / TTS_SUFFIX


def get_base_command():
    binary_map = {
        "Windows": "TTSModManager.exe",
        "Darwin": "TTSModManager-macOS",
        "Linux": "TTSModManager",
    }
    binary_name = binary_map.get(PLATFORM, "TTSModManager")
    binary_path = Path(__file__).resolve().parent / "bin" / binary_name

    if not FORCE_GO and binary_path.is_file():
        return [str(binary_path)], False
    return ["go", "run", "main.go"], True


def collect_tracked_object_guid_paths(mod_dir: Path) -> dict[str, Path]:
    try:
        output = subprocess.check_output(
            ["git", "-C", str(mod_dir), "ls-files", "--", "objects/"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}

    guid_to_relpath: dict[str, Path] = {}
    for rel_str in output.splitlines():
        if not rel_str.endswith(".json"):
            continue
        rel_path = Path(rel_str)
        abs_path = mod_dir / rel_path
        if not abs_path.is_file():
            continue
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        guid = payload.get("GUID")
        if isinstance(guid, str) and guid and guid not in guid_to_relpath:
            guid_to_relpath[guid] = rel_path
    return guid_to_relpath


def reconcile_object_paths_after_decompose(
    mod_dir: Path, tracked_guid_paths: dict[str, Path]
) -> int:
    objects_root = mod_dir / "objects"
    if not objects_root.is_dir() or not tracked_guid_paths:
        return 0

    rewired_count = 0
    json_files = list(objects_root.rglob("*.json"))

    for current_json in json_files:
        if not current_json.is_file():
            continue
        try:
            with open(current_json, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        guid = payload.get("GUID")
        if not isinstance(guid, str):
            continue

        expected_rel = tracked_guid_paths.get(guid)
        if expected_rel is None:
            continue

        current_rel = current_json.relative_to(mod_dir)
        if current_rel == expected_rel:
            continue

        expected_json = mod_dir / expected_rel
        expected_json.parent.mkdir(parents=True, exist_ok=True)

        current_gmnotes = current_json.with_suffix(".gmnotes")
        expected_gmnotes = expected_json.with_suffix(".gmnotes")
        expected_gmnotes_field = str(expected_gmnotes.relative_to(objects_root)).replace(
            "\\", "/"
        )

        gmnotes_path = payload.get("GMNotes_path")
        if isinstance(gmnotes_path, str) and gmnotes_path != expected_gmnotes_field:
            payload["GMNotes_path"] = expected_gmnotes_field
            with open(current_json, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")

        shutil.move(str(current_json), str(expected_json))
        if current_gmnotes.exists():
            expected_gmnotes.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(current_gmnotes), str(expected_gmnotes))

        rewired_count += 1

    return rewired_count


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
    assets_dir = Path(__file__).resolve().parent
    base_image = assets_dir / f"{GAME_NAME}.png"
    dev_image = assets_dir / f"{GAME_NAME}_dev.png"

    # Use dev image ONLY if on a non-main branch AND the file exists
    if branch and branch != "main" and dev_image.exists():
        source_image = dev_image
    else:
        source_image = base_image

    # Perform the copy (and maybe rename) with a safety check
    if source_image.exists():
        shutil.copy(source_image, output_folder / f"{GAME_NAME}.png")
    else:
        print(f"Note: Icon {source_image} not found, skipping copy.")


# CONFIGURATION
CONFIG = load_config()
GAME_NAME = CONFIG["GAME_NAME"]
HOTKEY = CONFIG["HOTKEY"]
FORCE_GO = CONFIG["FORCE_GO"]
WINDOW_TITLE = "Tabletop Simulator"


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
    parser.add_argument("--moddir", required=True, help="The mod directory.")
    args = parser.parse_args()
    mod_dir = Path(args.moddir).resolve()
    tracked_guid_paths = {}
    if args.action == "decompose":
        tracked_guid_paths = collect_tracked_object_guid_paths(mod_dir)

    # Determine folder and command
    output_folder = get_output_folder()
    cmd, using_go = get_base_command()

    # Set up command-line arguments
    mod_dir_arg = ["-moddir", args.moddir]
    mod_file_arg = ["-modfile", str(output_folder / f"{GAME_NAME}.json")]
    reverse_arg = ["-reverse"] if args.action == "decompose" else []

    # Final command to execute
    full_cmd = cmd + mod_dir_arg + mod_file_arg + reverse_arg

    # Execute the core command
    branch = get_current_git_branch()

    print(f"{start_time_formatted}")
    print(f"Branch:  {branch}")
    print(f"Action:  {args.action}")
    print(f"Command: {' '.join(full_cmd)}")

    if using_go:
        repo_root = Path(__file__).resolve().parent.parent
        full_ttsmodmanager_path = repo_root / "TTSModManager"
        subprocess.run(full_cmd, check=True, cwd=full_ttsmodmanager_path)
    else:
        subprocess.run(full_cmd, check=True)

    # Calculate and print the elapsed time
    elapsed_time = time.time() - start_time
    print(f"Execution took {elapsed_time:.2f} seconds.")

    if args.action == "build":
        copy_preview_image(output_folder, branch)
        load_savegame_in_TTS()
    else:
        rewired_count = reconcile_object_paths_after_decompose(
            mod_dir, tracked_guid_paths
        )
        if rewired_count:
            print(
                f"Reconciled {rewired_count} decomposed object filenames to tracked GUID paths."
            )


if __name__ == "__main__":
    main()
