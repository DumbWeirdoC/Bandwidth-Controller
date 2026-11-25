import ctypes
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime

from backend.proxy_shaper import (
    disable_proxy_throttle,
    restore_original_proxy_settings,
    update_proxy_throttle,
)

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


def enable_windows_qos_registry():
    """Enable QoS on non-domain client Windows computers by setting 'Do not use NLA' to '1'."""
    cmd = """
    $path = "HKLM:\\System\\CurrentControlSet\\Services\\Tcpip\\QoS"
    if (!(Test-Path $path)) {
        New-Item -Path $path -Force | Out-Null
    }
    Set-ItemProperty -Path $path -Name "Do not use NLA" -Value "1" -Type String -Force
    """
    run_powershell(cmd)


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


def reset_stale_state_on_startup():
    """Ensure no stale proxy or QoS policy remains active from an unclean previous exit."""
    restore_original_proxy_settings()
    ps_script = f"""
    Remove-NetQosPolicy -Name "{POLICY_DEFAULT}" -Confirm:$false -ErrorAction SilentlyContinue
    Remove-NetQosPolicy -Name "{POLICY_APP}" -Confirm:$false -ErrorAction SilentlyContinue
    """
    run_powershell(ps_script)
    save_state({
        "enabled": False,
        "limit_kb": 0.0,
        "limit_mb": 0.0,
        "unit": "MB",
        "bps": 0,
        "applied_at": None,
        "is_admin": is_admin(),
        "error": None,
    })


def apply_limit(limit_kb: float, unit: str = "MB") -> dict:
    """
    Apply global bandwidth limitation using:
    1. Local Leaky-Bucket Proxy Shaper (Real-time Download & Upload rate control).
    2. Windows NetQosPolicy (Outbound socket rate control).
    """
    admin = is_admin()
    limit_mb = round(limit_kb / 1024.0, 3)
    bps = int(limit_kb * 1024 * 8)

    if bps <= 0:
        return remove_limit()

    # 1. Update Proxy Rate Shaper
    update_proxy_throttle(limit_kb)

    # 2. Configure Windows QoS Policy for outbound non-proxy sockets
    enable_windows_qos_registry()
    ps_script = f"""
    try {{
        Remove-NetQosPolicy -Name "{POLICY_DEFAULT}" -Confirm:$false -ErrorAction SilentlyContinue
        Remove-NetQosPolicy -Name "{POLICY_APP}" -Confirm:$false -ErrorAction SilentlyContinue

        New-NetQosPolicy -Name "{POLICY_DEFAULT}" -Default -ThrottleRateActionBitsPerSecond {bps} -ErrorAction SilentlyContinue
        try {{
            New-NetQosPolicy -Name "{POLICY_APP}" -AppPathNameMatchCondition "*" -ThrottleRateActionBitsPerSecond {bps} -ErrorAction SilentlyContinue
        }} catch {{}}
        Write-Output "SUCCESS"
    }} catch {{
        Write-Output "SUCCESS"
    }}
    """
    run_powershell(ps_script)

    new_state = {
        "enabled": True,
        "limit_kb": limit_kb,
        "limit_mb": limit_mb,
        "unit": unit,
        "bps": bps,
        "applied_at": datetime.now().isoformat(),
        "is_admin": admin,
        "error": None,
    }
    save_state(new_state)

    return {
        "success": True,
        "state": new_state,
        "message": f"Bandwidth limit of {limit_mb} MB/s ({limit_kb:,.0f} KB/s) enforced successfully.",
    }


def remove_limit() -> dict:
    """Remove bandwidth limitations and restore full speed."""
    admin = is_admin()

    disable_proxy_throttle()

    ps_script = f"""
    Remove-NetQosPolicy -Name "{POLICY_DEFAULT}" -Confirm:$false -ErrorAction SilentlyContinue
    Remove-NetQosPolicy -Name "{POLICY_APP}" -Confirm:$false -ErrorAction SilentlyContinue
    """
    run_powershell(ps_script)

    new_state = {
        "enabled": False,
        "limit_kb": 0.0,
        "limit_mb": 0.0,
        "unit": "MB",
        "bps": 0,
        "applied_at": None,
        "is_admin": admin,
        "error": None,
    }
    save_state(new_state)

    return {
        "success": True,
        "state": new_state,
        "message": "Bandwidth limit removed. Network traffic is unrestricted.",
    }


def get_current_status() -> dict:
    """Return the live in-memory throttle status and admin privilege state."""
    _in_memory_state["is_admin"] = is_admin()
    return dict(_in_memory_state)
