import os
import sys
import time
import webbrowser
import threading
import subprocess
import ctypes
import atexit
import asyncio
import signal

def is_admin() -> bool:
    """Check if the script is executing with Administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def elevate():
    """Relaunches the application as Administrator via Windows UAC."""
    try:
        if getattr(sys, 'frozen', False):
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
    if getattr(sys, 'frozen', False):
        return
    
    needed = ["fastapi", "uvicorn", "psutil"]
    missing = []
    for pkg in needed:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
            
    if missing:
        print(f"[*] Automatically installing required packages: {', '.join(missing)}...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
            print("[+] All requirements installed successfully!\n")
        except Exception as e:
            print(f"[-] Warning: Failed to install packages: {e}")

def open_browser_delayed(url: str, delay: float = 1.2):
    """Wait for the web server to start, then open the default browser."""
    time.sleep(delay)
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
    from backend.proxy_shaper import disable_proxy_throttle
    disable_proxy_throttle()

def main():
    print_banner()

    # If running directly and not admin, prompt for UAC elevation
    if not is_admin():
        print("[!] Administrator privileges required for Windows network bandwidth control.")
        print("[*] Requesting UAC Administrator permission...")
        elevate()
        return

    # Check and install requirements if missing
    check_requirements()

    # Register exit and signal cleanup
    atexit.register(cleanup)
    try:
        signal.signal(signal.SIGINT, lambda s, f: (cleanup(), sys.exit(0)))
        signal.signal(signal.SIGTERM, lambda s, f: (cleanup(), sys.exit(0)))
    except Exception:
        pass

    # Start background proxy rate shaper thread
    proxy_thread = threading.Thread(target=run_proxy_loop, daemon=True)
    proxy_thread.start()

    from backend.app import app
    import uvicorn

    host = "127.0.0.1"
    port = 9482
    url = f"http://{host}:{port}"

    print(f"[+] Administrator mode verified: Active (Granted)")
    print(f"[+] Local Web Controller on: {url}")
    print(f"[+] Hard Rate-Limiting Engine: Active")
    print("------------------------------------------------------------------------")
    print("Press Ctrl+C in this console window anytime to stop the server.")
    print("------------------------------------------------------------------------")

    # Start browser opener in background thread
    threading.Thread(target=open_browser_delayed, args=(url,), daemon=True).start()

    # Run Uvicorn server
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        cleanup()

if __name__ == "__main__":
    main()
