import asyncio
import time
import winreg
import ctypes
import socket
from typing import Optional

PROXY_PORT = 9483
INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_OPTION_REFRESH = 37

class TokenBucket:
    def __init__(self, rate_bytes_per_sec: float):
        self.rate = rate_bytes_per_sec
        self.tokens = rate_bytes_per_sec
        self.last_update = time.monotonic()
        self.lock = asyncio.Lock()

    def set_rate(self, rate_bytes_per_sec: float):
        self.rate = rate_bytes_per_sec
        self.tokens = rate_bytes_per_sec
        self.last_update = time.monotonic()

    async def consume(self, amount: int):
        if self.rate <= 0:
            return
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens < amount:
                wait_time = (amount - self.tokens) / self.rate
                if wait_time > 0:
                    await asyncio.sleep(min(wait_time, 2.0))
                self.tokens = 0
            else:
                self.tokens -= amount

download_limiter = TokenBucket(0)
upload_limiter = TokenBucket(0)
proxy_server_running = False

def set_windows_system_proxy(enable: bool, host: str = "127.0.0.1", port: int = PROXY_PORT):
    """Enable or disable Windows system-wide HTTP/HTTPS proxy and notify running processes."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"{host}:{port}")
                winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>;127.0.0.1;localhost")
            else:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)

        # Notify WinINet and active applications that proxy settings changed
        ctypes.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        ctypes.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
        return True
    except Exception as e:
        print(f"[-] Could not update Windows system proxy settings: {e}")
        return False

async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, limiter: TokenBucket):
    """Pipe data from reader to writer with token bucket rate limiting."""
    try:
        while True:
            chunk = await reader.read(8192)
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
                port = int(port_str)
            else:
                host, port = target, 443

            # Read and discard remaining request headers
            while True:
                header_line = await client_reader.readline()
                if not header_line or header_line == b"\r\n" or header_line == b"\n":
                    break

            try:
                remote_reader, remote_writer = await asyncio.open_connection(host, port)
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()
            except Exception as e:
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await client_writer.drain()
                client_writer.close()
                return

            # Bidirectional rate-limited tunnel
            await asyncio.gather(
                pipe(client_reader, remote_writer, upload_limiter),
                pipe(remote_reader, client_writer, download_limiter),
                return_exceptions=True
            )
        else:
            # Plain HTTP request
            # Parse Host from headers or target
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
                        host, port_str = h_val.split(":", 1)
                        port = int(port_str)
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
                pipe(client_reader, remote_writer, upload_limiter),
                pipe(remote_reader, client_writer, download_limiter),
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
