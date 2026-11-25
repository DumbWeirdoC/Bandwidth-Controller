import asyncio
import ctypes
import json
import os
import struct
import subprocess
import tempfile
import threading
import time
import winreg
from typing import Optional, Tuple

PROXY_PORT = 9483
INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
CONNECTIONS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings\Connections"
INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_OPTION_REFRESH = 37


def _get_backup_path() -> str:
    app_data = os.environ.get("LOCALAPPDATA")
    base = os.path.join(app_data, "BandwidthController") if app_data else tempfile.gettempdir()
    try:
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "proxy_backup.json")
    except Exception:
        return os.path.join(tempfile.gettempdir(), "bandwidth_proxy_backup.json")


class RateLimiter:
    """Thread-safe virtual-clock leaky bucket rate limiter for parallel async streams."""

    def __init__(self, rate_bytes_per_sec: float = 0.0):
        self.rate = rate_bytes_per_sec
        self.next_slot = time.monotonic()
        self.lock = threading.Lock()
        self.total_bytes = 0

    def set_rate(self, rate_bytes_per_sec: float):
        with self.lock:
            self.rate = max(0.0, float(rate_bytes_per_sec))
            self.next_slot = time.monotonic()

    def get_chunk_size(self) -> int:
        r = self.rate
        if r <= 0:
            return 65536
        if r < 100 * 1024:
            return 2048
        if r < 1024 * 1024:
            return 8192
        if r < 10 * 1024 * 1024:
            return 32768
        return 65536

    async def consume(self, amount: int):
        if amount <= 0:
            return
        with self.lock:
            self.total_bytes += amount
            rate = self.rate
            if rate <= 0:
                return
            now = time.monotonic()
            # Allow max 120ms burst credit so speed stays smooth and accurate
            if self.next_slot < now - 0.12:
                self.next_slot = now - 0.12
            self.next_slot += amount / rate
            wait_time = self.next_slot - now

        if wait_time > 0:
            await asyncio.sleep(min(wait_time, 2.0))


download_limiter = RateLimiter(0.0)
upload_limiter = RateLimiter(0.0)
proxy_server_running = False
active_connections = 0
upstream_proxy: Optional[Tuple[str, int]] = None
_proxy_active = False
_backup_lock = threading.Lock()


def build_connection_settings(
    proxy_enabled: bool,
    proxy_server: str = f"127.0.0.1:{PROXY_PORT}",
    bypass: str = "<local>;127.0.0.1;localhost",
) -> bytes:
    """Build a valid Windows DefaultConnectionSettings / SavedLegacySettings binary blob."""
    flags = 0x03 if proxy_enabled else 0x01
    proxy_bytes = proxy_server.encode("ascii") if proxy_enabled else b""
    bypass_bytes = bypass.encode("ascii") if proxy_enabled else b""

    blob = bytearray()
    blob.extend(struct.pack("<I", 0x46))
    blob.extend(struct.pack("<I", 0x05))
    blob.extend(struct.pack("<I", flags))
    blob.extend(struct.pack("<I", len(proxy_bytes)))
    blob.extend(proxy_bytes)
    blob.extend(struct.pack("<I", len(bypass_bytes)))
    blob.extend(bypass_bytes)
    blob.extend(struct.pack("<I", 0))
    blob.extend(b"\x00" * 32)
    return bytes(blob)


def _notify_wininet():
    try:
        ctypes.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        ctypes.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
    except Exception:
        pass


def _detect_upstream_from_backup(backup: dict) -> Optional[Tuple[str, int]]:
    """Detect if the user already had an HTTP proxy (e.g. VPN client) to chain through."""
    if backup.get("ProxyEnable") == 1 and backup.get("ProxyServer"):
        srv = str(backup["ProxyServer"]).strip()
        # Handle 'http=127.0.0.1:10809;https=...' or '127.0.0.1:10809'
        if "=" in srv:
            for part in srv.split(";"):
                if part.lower().startswith("http="):
                    srv = part.split("=", 1)[1].strip()
                    break
        if ":" in srv and f":{PROXY_PORT}" not in srv:
            host, port_s = srv.rsplit(":", 1)
            try:
                return host.strip(), int(port_s.strip())
            except ValueError:
                pass
    return None


def backup_original_proxy_settings():
    """Save original Windows proxy settings before Bandwidth Controller modifies them."""
    global upstream_proxy
    with _backup_lock:
        backup_path = _get_backup_path()
        if os.path.exists(backup_path):
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                upstream_proxy = _detect_upstream_from_backup(existing)
                return
            except Exception:
                pass

        data = {"connections": {}}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_READ) as key:
                for name in ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL"):
                    try:
                        val, _ = winreg.QueryValueEx(key, name)
                        if name == "ProxyServer" and f"127.0.0.1:{PROXY_PORT}" in str(val):
                            # Already ours, don't back up our own address
                            return
                        data[name] = val
                    except FileNotFoundError:
                        pass
        except Exception:
            pass

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONNECTIONS_KEY, 0, winreg.KEY_READ) as conn_key:
                idx = 0
                while True:
                    try:
                        val_name, val_data, val_type = winreg.EnumValue(conn_key, idx)
                        if val_type == winreg.REG_BINARY and isinstance(val_data, bytes):
                            data["connections"][val_name] = val_data.hex()
                        idx += 1
                    except OSError:
                        break
        except Exception:
            pass

        upstream_proxy = _detect_upstream_from_backup(data)
        try:
            with open(backup_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass


def restore_original_proxy_settings():
    """Restore original Windows proxy settings and remove backup file."""
    global upstream_proxy
    with _backup_lock:
        backup_path = _get_backup_path()
        if not os.path.exists(backup_path):
            _set_direct_proxy()
            return

        try:
            with open(backup_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None

        if not data:
            _set_direct_proxy()
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_SET_VALUE) as key:
                    if "ProxyEnable" in data:
                        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, int(data["ProxyEnable"]))
                    else:
                        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)

                    for str_key in ("ProxyServer", "ProxyOverride", "AutoConfigURL"):
                        if str_key in data:
                            winreg.SetValueEx(key, str_key, 0, winreg.REG_SZ, str(data[str_key]))
                        else:
                            try:
                                winreg.DeleteValue(key, str_key)
                            except FileNotFoundError:
                                pass
            except Exception:
                pass

            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONNECTIONS_KEY, 0, winreg.KEY_SET_VALUE) as conn_key:
                    for val_name, hex_str in data.get("connections", {}).items():
                        winreg.SetValueEx(conn_key, val_name, 0, winreg.REG_BINARY, bytes.fromhex(hex_str))
            except Exception:
                pass

            _reset_winhttp_proxy()
            _notify_wininet()

        upstream_proxy = None
        try:
            os.remove(backup_path)
        except Exception:
            pass


def _reset_winhttp_proxy():
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        subprocess.run(["netsh", "winhttp", "reset", "proxy"], startupinfo=si, capture_output=True, timeout=5)
    except Exception:
        pass


def _set_direct_proxy():
    blob = build_connection_settings(False)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
    except Exception:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONNECTIONS_KEY, 0, winreg.KEY_SET_VALUE) as conn_key:
            winreg.SetValueEx(conn_key, "DefaultConnectionSettings", 0, winreg.REG_BINARY, blob)
            winreg.SetValueEx(conn_key, "SavedLegacySettings", 0, winreg.REG_BINARY, blob)
    except Exception:
        pass
    _reset_winhttp_proxy()
    _notify_wininet()


def set_windows_system_proxy(enable: bool, host: str = "127.0.0.1", port: int = PROXY_PORT) -> bool:
    """Enable or restore Windows system-wide HTTP/HTTPS proxy."""
    global _proxy_active
    if not enable:
        _proxy_active = False
        restore_original_proxy_settings()
        return True

    backup_original_proxy_settings()
    proxy_server = f"{host}:{port}"
    bypass = "<local>;127.0.0.1;localhost"
    blob = build_connection_settings(True, proxy_server, bypass)

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy_server)
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, bypass)
            try:
                winreg.DeleteValue(key, "AutoConfigURL")
            except FileNotFoundError:
                pass
    except Exception as e:
        print(f"[-] Registry Internet Settings update error: {e}")

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONNECTIONS_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as conn_key:
            existing_names = ["DefaultConnectionSettings", "SavedLegacySettings"]
            idx = 0
            while True:
                try:
                    v_name, _, v_type = winreg.EnumValue(conn_key, idx)
                    if v_type == winreg.REG_BINARY and v_name not in existing_names:
                        existing_names.append(v_name)
                    idx += 1
                except OSError:
                    break
            for v_name in existing_names:
                winreg.SetValueEx(conn_key, v_name, 0, winreg.REG_BINARY, blob)
    except Exception as e:
        print(f"[-] Connections registry blob update error: {e}")

    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        subprocess.run(
            ["netsh", "winhttp", "set", "proxy", proxy_server, bypass],
            startupinfo=si,
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass

    _notify_wininet()
    _proxy_active = True
    return True


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, limiter: RateLimiter):
    """Stream bytes between endpoints while enforcing the virtual-clock rate limit."""
    try:
        while True:
            chunk_size = limiter.get_chunk_size() if limiter else 32768
            chunk = await reader.read(chunk_size)
            if not chunk:
                break
            if limiter and limiter.rate > 0:
                await limiter.consume(len(chunk))
            writer.write(chunk)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _open_remote(host: str, port: int, is_connect: bool, raw_headers: list) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter, bool]:
    """Open connection to target directly or chained through detected upstream proxy."""
    if upstream_proxy:
        up_host, up_port = upstream_proxy
        r, w = await asyncio.open_connection(up_host, up_port)
        if is_connect:
            w.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode("ascii"))
            await w.drain()
            resp_line = await r.readline()
            while True:
                hl = await r.readline()
                if not hl or hl in (b"\r\n", b"\n"):
                    break
            if b"200" not in resp_line:
                w.close()
                raise ConnectionError(f"Upstream proxy rejected CONNECT: {resp_line!r}")
        else:
            for h in raw_headers:
                w.write(h)
            w.write(b"\r\n")
            await w.drain()
        return r, w, True

    r, w = await asyncio.open_connection(host, port)
    if not is_connect:
        for h in raw_headers:
            w.write(h)
        w.write(b"\r\n")
        await w.drain()
    return r, w, False


async def handle_client(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter):
    """Handle an incoming HTTP or HTTPS CONNECT proxy request."""
    global active_connections
    active_connections += 1
    try:
        line = await client_reader.readline()
        if not line:
            return

        parts = line.split()
        if len(parts) < 2:
            return

        method = parts[0].decode("utf-8", errors="ignore").upper()
        target = parts[1].decode("utf-8", errors="ignore")

        if method == "CONNECT":
            if ":" in target:
                host, port_str = target.rsplit(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    port = 443
            else:
                host, port = target, 443

            while True:
                h_line = await client_reader.readline()
                if not h_line or h_line in (b"\r\n", b"\n"):
                    break

            try:
                remote_reader, remote_writer, _ = await _open_remote(host, port, True, [])
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()
            except Exception:
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await client_writer.drain()
                return

            await asyncio.gather(
                pipe(remote_reader, client_writer, download_limiter),
                pipe(client_reader, remote_writer, upload_limiter),
                return_exceptions=True,
            )
        else:
            headers = [line]
            host = None
            port = 80
            while True:
                h_line = await client_reader.readline()
                if not h_line or h_line in (b"\r\n", b"\n"):
                    break
                headers.append(h_line)
                decoded = h_line.decode("utf-8", errors="ignore")
                if decoded.lower().startswith("host:"):
                    h_val = decoded.split(":", 1)[1].strip()
                    if ":" in h_val:
                        h_h, h_p = h_val.rsplit(":", 1)
                        host = h_h
                        try:
                            port = int(h_p)
                        except ValueError:
                            port = 80
                    else:
                        host = h_val

            if not host:
                return

            try:
                remote_reader, remote_writer, _ = await _open_remote(host, port, False, headers)
            except Exception:
                return

            await asyncio.gather(
                pipe(remote_reader, client_writer, download_limiter),
                pipe(client_reader, remote_writer, upload_limiter),
                return_exceptions=True,
            )
    except Exception:
        pass
    finally:
        active_connections = max(0, active_connections - 1)
        try:
            client_writer.close()
        except Exception:
            pass


async def start_proxy_server():
    """Start the local rate-limiting proxy on 127.0.0.1:9483."""
    global proxy_server_running
    try:
        server = await asyncio.start_server(handle_client, "127.0.0.1", PROXY_PORT)
        proxy_server_running = True
        print(f"[+] Bandwidth Traffic Shaper Proxy active on 127.0.0.1:{PROXY_PORT}")
        async with server:
            await server.serve_forever()
    except Exception as e:
        print(f"[-] Could not start proxy shaper server: {e}")


def update_proxy_throttle(limit_kb: float):
    """Update active bandwidth ceiling on the proxy shaper."""
    limit_bytes = limit_kb * 1024.0
    if limit_kb > 0:
        download_limiter.set_rate(limit_bytes)
        upload_limiter.set_rate(limit_bytes)
        set_windows_system_proxy(True)
    else:
        disable_proxy_throttle()


def disable_proxy_throttle():
    """Disable proxy throttle and restore original Windows proxy settings."""
    download_limiter.set_rate(0.0)
    upload_limiter.set_rate(0.0)
    set_windows_system_proxy(False)
