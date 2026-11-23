import asyncio
import time
import winreg
import ctypes
import socket
import struct
import subprocess
import threading
from typing import Optional

PROXY_PORT = 9483
INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
CONNECTIONS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings\Connections"
INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_OPTION_REFRESH = 37

class TokenBucket:
    def __init__(self, rate_bytes_per_sec: float):
        self.rate = rate_bytes_per_sec
        self.capacity = max(rate_bytes_per_sec, 65536.0)
        self.tokens = self.capacity
        self.last_update = time.monotonic()
        self.lock = threading.Lock()

    def set_rate(self, rate_bytes_per_sec: float):
        with self.lock:
            self.rate = rate_bytes_per_sec
            self.capacity = max(rate_bytes_per_sec, 65536.0)
            self.tokens = self.capacity
            self.last_update = time.monotonic()

    async def consume(self, amount: int):
        if self.rate <= 0:
            return

        wait_time = 0.0
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens < amount:
                deficit = amount - self.tokens
                wait_time = deficit / self.rate
                self.tokens = 0.0
            else:
                self.tokens -= amount

        if wait_time > 0:
            await asyncio.sleep(min(wait_time, 1.0))

download_limiter = TokenBucket(0)
upload_limiter = TokenBucket(0)
proxy_server_running = False

def build_connection_settings(proxy_enabled: bool, proxy_server: str = f"127.0.0.1:{PROXY_PORT}", bypass: str = "<local>;127.0.0.1;localhost") -> bytes:
    """Construct a full Windows DefaultConnectionSettings binary blob."""
    flags = 0x03 if proxy_enabled else 0x01
    proxy_bytes = proxy_server.encode('ascii') if proxy_enabled else b""
    bypass_bytes = bypass.encode('ascii') if proxy_enabled else b""

    blob = bytearray()
    blob.extend(struct.pack('<I', 0x46))  # Header size: 70
    blob.extend(struct.pack('<I', 0x02))  # Counter
    blob.extend(struct.pack('<I', flags)) # Byte 8: Flags (0x03 = proxy active, 0x01 = direct)
    blob.extend(struct.pack('<I', len(proxy_bytes)))
    blob.extend(proxy_bytes)
    blob.extend(struct.pack('<I', len(bypass_bytes)))
    blob.extend(bypass_bytes)
    blob.extend(struct.pack('<I', 0))     # Auto-config PAC length: 0
    blob.extend(b"\x00" * 32)             # Padding
    return bytes(blob)

def set_windows_system_proxy(enable: bool, host: str = "127.0.0.1", port: int = PROXY_PORT):
    """
    Enforce or disable Windows system-wide HTTP/HTTPS proxy across:
    1. WinINet Internet Settings
    2. Connections\\DefaultConnectionSettings (used by Chrome, Edge, Brave)
    3. Connections\\SavedLegacySettings
    4. WinHTTP system proxy (netsh winhttp)
    """
    proxy_server = f"{host}:{port}"
    bypass = "<local>;127.0.0.1;localhost"
    blob = build_connection_settings(enable, proxy_server, bypass)

    # 1. Update WinINet Internet Settings
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy_server)
                winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, bypass)
            else:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
    except Exception as e:
        print(f"[-] Registry Internet Settings update error: {e}")

    # 2. Update Connections binary blobs (Crucial for Chromium & Edge)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONNECTIONS_KEY, 0, winreg.KEY_SET_VALUE) as conn_key:
            winreg.SetValueEx(conn_key, "DefaultConnectionSettings", 0, winreg.REG_BINARY, blob)
            winreg.SetValueEx(conn_key, "SavedLegacySettings", 0, winreg.REG_BINARY, blob)
    except Exception as e:
        print(f"[-] Connections registry blob update error: {e}")

    # 3. Update WinHTTP system proxy via netsh
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        if enable:
            subprocess.run(["netsh", "winhttp", "set", "proxy", proxy_server, bypass], startupinfo=si, capture_output=True, timeout=5)
        else:
            subprocess.run(["netsh", "winhttp", "reset", "proxy"], startupinfo=si, capture_output=True, timeout=5)
    except Exception:
        pass

    # 4. Notify Windows system that settings changed immediately
    try:
        ctypes.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        ctypes.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
    except Exception:
        pass

    return True

async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, limiter: TokenBucket):
    """Pipe streamed data between client and remote server with precise token bucket rate limiting."""
    try:
        while True:
            chunk = await reader.read(16384)
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
            await writer.wait_closed()
        except Exception:
            pass

async def handle_client(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter):
    """Handle an incoming proxy connection (HTTP or HTTPS CONNECT tunnel)."""
    try:
        line = await client_reader.readline()
        if not line:
            client_writer.close()
            return

        parts = line.split()
        if len(parts) < 2:
            client_writer.close()
            return

        method = parts[0].decode('utf-8', errors='ignore').upper()
        target = parts[1].decode('utf-8', errors='ignore')

        if method == "CONNECT":
            # HTTPS Tunneling
            if ":" in target:
                host, port_str = target.split(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    port = 443
            else:
                host, port = target, 443

            # Read all request headers until CRLF CRLF
            while True:
                h_line = await client_reader.readline()
                if not h_line or h_line == b"\r\n" or h_line == b"\n":
                    break

            try:
                remote_reader, remote_writer = await asyncio.open_connection(host, port)
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()
            except Exception:
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await client_writer.drain()
                client_writer.close()
                return

            # Active rate-limited tunnel:
            # - remote -> client = DOWNLOAD (controlled by download_limiter)
            # - client -> remote = UPLOAD (controlled by upload_limiter)
            await asyncio.gather(
                pipe(remote_reader, client_writer, download_limiter),
                pipe(client_reader, remote_writer, upload_limiter),
                return_exceptions=True
            )
        else:
            # Plain HTTP request
            headers = [line]
            host = None
            port = 80
            while True:
                h_line = await client_reader.readline()
                if not h_line or h_line == b"\r\n" or h_line == b"\n":
                    break
                headers.append(h_line)
                decoded = h_line.decode('utf-8', errors='ignore')
                if decoded.lower().startswith("host:"):
                    h_val = decoded.split(":", 1)[1].strip()
                    if ":" in h_val:
                        h_h, h_p = h_val.split(":", 1)
                        host = h_h
                        try:
                            port = int(h_p)
                        except ValueError:
                            port = 80
                    else:
                        host = h_val

            if not host:
                client_writer.close()
                return

            try:
                remote_reader, remote_writer = await asyncio.open_connection(host, port)
                for h in headers:
                    remote_writer.write(h)
                remote_writer.write(b"\r\n")
                await remote_writer.drain()
            except Exception:
                client_writer.close()
                return

            await asyncio.gather(
                pipe(remote_reader, client_writer, download_limiter),
                pipe(client_reader, remote_writer, upload_limiter),
                return_exceptions=True
            )
    except Exception:
        pass
    finally:
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
        download_limiter.set_rate(0)
        upload_limiter.set_rate(0)
        set_windows_system_proxy(False)

def disable_proxy_throttle():
    """Disable proxy throttle and reset Windows system proxy."""
    download_limiter.set_rate(0)
    upload_limiter.set_rate(0)
    set_windows_system_proxy(False)
