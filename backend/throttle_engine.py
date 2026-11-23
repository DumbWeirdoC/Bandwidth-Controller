import subprocess
import json
import os
import sys
import ctypes
from datetime import datetime

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
POLICY_DEFAULT = "GlobalBandwidthLimitDefault"
POLICY_APP = "GlobalBandwidthLimitApp"

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

def load_state():
    """Load the current throttle state from disk."""
    default_state = {
        "enabled": False,
        "limit_kb": 0.0,
        "limit_mb": 0.0,
        "unit": "MB",
        "bps": 0,
        "applied_at": None,
        "is_admin": is_admin(),
        "error": None,
    }
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                saved["is_admin"] = is_admin()
                return saved
        except Exception:
            return default_state
    return default_state

def save_state(state: dict):
    """Save the throttle state to disk."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Error saving state: {e}")

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
        Remove-NetQosPolicy -Name "{POLICY_DEFAULT}" -Confirm:$false -PolicyStore ActiveStore -ErrorAction SilentlyContinue
        Remove-NetQosPolicy -Name "{POLICY_APP}" -Confirm:$false -PolicyStore ActiveStore -ErrorAction SilentlyContinue
        
        New-NetQosPolicy -Name "{POLICY_DEFAULT}" -Default -ThrottleRateActionBitsPerSecond {bps} -PolicyStore ActiveStore -ErrorAction Stop
        New-NetQosPolicy -Name "{POLICY_APP}" -AppPathNameMatchCondition "*" -ThrottleRateActionBitsPerSecond {bps} -PolicyStore ActiveStore -ErrorAction Stop
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
        Remove-NetQosPolicy -Name "{POLICY_DEFAULT}" -Confirm:$false -PolicyStore ActiveStore -ErrorAction SilentlyContinue
        Remove-NetQosPolicy -Name "{POLICY_APP}" -Confirm:$false -PolicyStore ActiveStore -ErrorAction SilentlyContinue
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
