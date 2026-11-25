import asyncio
import atexit
import ctypes
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser

HOST = "127.0.0.1"
PORT = 9482

# Keep reference to ctypes callback so it isn't garbage collected
_console_handler_ref = None


def is_admin() -> bool:
    """Check if the script is executing with Administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def elevate():
    """Relaunches the application as Administrator via Windows UAC."""
    try:
        if getattr(sys, "frozen", False):
            exe = sys.executable
            args = ""
        else:
            exe = sys.executable
            args = f'"{os.path.abspath(__file__)}"'

        ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 1)
        sys.exit(0)
    except Exception as e:
        print(f"[-] Administrator elevation request failed: {e}")


def check_requirements():
    """Ensure all required Python packages are installed (only when running from source)."""
    if getattr(sys, "frozen", False):
        return

    needed = ["fastapi", "uvicorn", "psutil", "pydantic"]
    missing = []
    for pkg in needed:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"[*] Installing required packages: {', '.join(missing)}...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
            print("[+] All requirements installed successfully!\n")
        except Exception as e:
            print(f"[-] Warning: Failed to install packages: {e}")


def wait_and_open_browser(host: str, port: int):
    """Wait until the local server socket is accepting connections, then open browser."""
    url = f"http://{host}:{port}"
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                break
        except OSError:
            time.sleep(0.15)

    print(f"[+] Launching Bandwidth Controller UI: {url}")
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"[-] Could not launch browser automatically: {e}")


def print_banner():
    banner = r"""
========================================================================
   ____                  __          _     ____  __  __
  / __ )____ _____  ____/ /_      __(_)___/ / /_/ /_/ /_
 / __  / __ `/ __ \/ __  / | /| / / / __  / __/ __/ __ \
/ /_/ / /_/ / / / / /_/ /| |/ |/ / / /_/ / /_/ /_/ / / /
\____/\__,_/_/ /_/\__,_/ |__/|__/_/\__,_/\__/\__/_/ /_/  v1.0

     Bandwidth Controller - Smart Local Bandwidth Limiter
========================================================================
    """
    print(banner)


def run_proxy_loop():
    """Run the asyncio event loop for the proxy rate shaper server."""
    from backend.proxy_shaper import start_proxy_server

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(start_proxy_server())
    except Exception as e:
        print(f"[-] Proxy shaper loop error: {e}")


def cleanup():
    """Restore original proxy settings and remove QoS policies on exit."""
    try:
        from backend.throttle_engine import remove_limit

        remove_limit()
    except Exception:
        pass


def install_windows_console_handler():
    """Register Windows console control handler so clicking [X] restores proxy settings."""
    global _console_handler_ref
    try:
        HANDLER_ROUTINE = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

        def _handler(ctrl_type: int) -> bool:
            # 0=CTRL_C, 1=CTRL_BREAK, 2=CTRL_CLOSE, 5=CTRL_LOGOFF, 6=CTRL_SHUTDOWN
            if ctrl_type in (0, 1, 2, 5, 6):
                cleanup()
                return False
            return False

        _console_handler_ref = HANDLER_ROUTINE(_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler_ref, True)
    except Exception:
        pass


def main():
    print_banner()

    if not is_admin():
        print("[!] Administrator privileges required for Windows network bandwidth control.")
        print("[*] Requesting UAC Administrator permission...")
        elevate()
        return

    check_requirements()

    from backend.app import app
    from backend.throttle_engine import reset_stale_state_on_startup
    import uvicorn

    # Clean any stale settings from a previous unclean termination
    reset_stale_state_on_startup()

    # Register exit & console close handlers
    atexit.register(cleanup)
    install_windows_console_handler()
    try:
        signal.signal(signal.SIGINT, lambda s, f: (cleanup(), sys.exit(0)))
        signal.signal(signal.SIGTERM, lambda s, f: (cleanup(), sys.exit(0)))
    except Exception:
        pass

    # Start background proxy rate shaper thread
    proxy_thread = threading.Thread(target=run_proxy_loop, daemon=True)
    proxy_thread.start()

    url = f"http://{HOST}:{PORT}"
    print("[+] Administrator mode verified: Active (Granted)")
    print(f"[+] Local Web Controller on: {url}")
    print("[+] Real-Time Rate-Limiting Engine: Ready")
    print("------------------------------------------------------------------------")
    print("Press Ctrl+C or close this window anytime to restore normal speed & exit.")
    print("------------------------------------------------------------------------")

    threading.Thread(target=wait_and_open_browser, args=(HOST, PORT), daemon=True).start()

    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
