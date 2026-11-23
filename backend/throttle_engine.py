import subprocess
import json
import os
import sys
import ctypes
import tempfile
from datetime import datetime

POLICY_DEFAULT = "GlobalBandwidthLimitDefault"
POLICY_APP = "GlobalBandwidthLimitApp"

def get_state_file_path() -> str:
    """Return a guaranteed safe, writable path for state.json."""
    candidates = []
    app_data = os.environ.get("LOCALAPPDATA")
    if app_data:
        candidates.append(os.path.join(app_data, "BandwidthController", "state.json"))
    home = os.path.expanduser("~")
    if home:
        candidates.append(os.path.join(home, ".bandwidth_controller", "state.json"))
    candidates.append(os.path.join(tempfile.gettempdir(), "bandwidth_state.json"))

    for path in candidates:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            test_file = path + ".tmp"
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(test_file)
            return path
        except Exception:
            continue
    return os.path.join(tempfile.gettempdir(), "bandwidth_state.json")

# In-memory state cache
_in_memory_state = {
    "enabled": False,
    "limit_kb": 0.0,
    "limit_mb": 0.0,
    "unit": "MB",
    "bps": 0,
    "applied_at": None,
    "is_admin": False,
    "error": None,
}

def is_admin() -> bool:
    """Check if the current process has Administrator privileges on Windows."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def elevate_if_needed():
    """Relaunch the current process as Administrator using UAC if not already admin."""
    if not is_admin():
        params = " ".join([f'"{arg}"' for arg in sys.argv])
        try:
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, params, None, 1
            )
            sys.exit(0)
        except Exception as e:
            print(f"Elevation failed: {e}")

def run_powershell(command: str):
    """Run a PowerShell command silently and return success, stdout, and stderr."""
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            capture_output=True,
            text=True,
            startupinfo=startupinfo,
            timeout=15,
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def load_state() -> dict:
    """Load the current throttle state from disk or memory."""
    global _in_memory_state
    state_file = get_state_file_path()
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
                saved["is_admin"] = is_admin()
                _in_memory_state.update(saved)
                return _in_memory_state
        except Exception:
            pass
            
    _in_memory_state["is_admin"] = is_admin()
    return _in_memory_state

def save_state(state: dict):
    """Save the throttle state to disk and memory."""
    global _in_memory_state
    _in_memory_state.update(state)
    try:
        state_file = get_state_file_path()
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Warning: Could not persist state to file: {e}")

def apply_limit(limit_kb: float, unit: str = "MB") -> dict:
    """
    Apply global bandwidth limitation using Windows NetQosPolicy.
    limit_kb: Bandwidth limit in Kilobytes per second (KB/s).
    """
    admin = is_admin()
    limit_mb = round(limit_kb / 1024.0, 3)
    # Calculate bits per second (1 KB = 1024 bytes, 1 byte = 8 bits)
    bps = int(limit_kb * 1024 * 8)

    if bps <= 0:
        return remove_limit()

    ps_script = f"""
    try {{
        Remove-NetQosPolicy -Name "{POLICY_DEFAULT}" -Confirm:$false -ErrorAction SilentlyContinue
        Remove-NetQosPolicy -Name "{POLICY_APP}" -Confirm:$false -ErrorAction SilentlyContinue
        
        New-NetQosPolicy -Name "{POLICY_DEFAULT}" -Default -ThrottleRateActionBitsPerSecond {bps} -ErrorAction Stop
        try {{
            New-NetQosPolicy -Name "{POLICY_APP}" -AppPathNameMatchCondition "*" -ThrottleRateActionBitsPerSecond {bps} -ErrorAction SilentlyContinue
        }} catch {{}}
        Write-Output "SUCCESS"
    }} catch {{
        Write-Error $_.Exception.Message
        exit 1
    }}
    """

    success, stdout, stderr = run_powershell(ps_script)

    new_state = {
        "enabled": success,
        "limit_kb": limit_kb,
        "limit_mb": limit_mb,
        "unit": unit,
        "bps": bps,
        "applied_at": datetime.now().isoformat() if success else None,
        "is_admin": admin,
        "error": None if success else (stderr or stdout or "Permission denied or CIM error"),
    }
    save_state(new_state)

    return {
        "success": success,
        "state": new_state,
        "message": (
            f"Bandwidth limit of {limit_mb} MB/s ({limit_kb:,.0f} KB/s) applied successfully."
            if success
            else f"Failed to apply policy: {new_state['error']}. Make sure the application is running as Administrator."
        ),
    }

def remove_limit() -> dict:
    """Remove any active NetQosPolicy limitation."""
    admin = is_admin()
    ps_script = f"""
    try {{
        Remove-NetQosPolicy -Name "{POLICY_DEFAULT}" -Confirm:$false -ErrorAction SilentlyContinue
        Remove-NetQosPolicy -Name "{POLICY_APP}" -Confirm:$false -ErrorAction SilentlyContinue
        Write-Output "SUCCESS"
    }} catch {{
        Write-Error $_.Exception.Message
        exit 1
    }}
    """
    success, stdout, stderr = run_powershell(ps_script)

    new_state = {
        "enabled": False,
        "limit_kb": 0.0,
        "limit_mb": 0.0,
        "unit": "MB",
        "bps": 0,
        "applied_at": None,
        "is_admin": admin,
        "error": None if success else stderr,
    }
    save_state(new_state)

    return {
        "success": success,
        "state": new_state,
        "message": "Bandwidth limit removed. Network traffic is unrestricted.",
    }

def get_current_status() -> dict:
    """Return the current throttle status and admin privilege state."""
    state = load_state()
    state["is_admin"] = is_admin()
    return state
