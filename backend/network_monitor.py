import time
import psutil
from typing import Dict, Any

class NetworkMonitor:
    def __init__(self):
        self.last_time = time.time()
        initial_io = psutil.net_io_counters()
        self.last_bytes_recv = initial_io.bytes_recv
        self.last_bytes_sent = initial_io.bytes_sent
        self.download_speed_kb = 0.0
        self.upload_speed_kb = 0.0
        self.download_speed_mb = 0.0
        self.upload_speed_mb = 0.0

    def sample(self) -> Dict[str, Any]:
        """Calculates current download & upload speeds based on elapsed time."""
        current_time = time.time()
        elapsed = current_time - self.last_time

        io = psutil.net_io_counters()
        bytes_recv = io.bytes_recv
        bytes_sent = io.bytes_sent

        if elapsed > 0.05:
            delta_recv = bytes_recv - self.last_bytes_recv
            delta_sent = bytes_sent - self.last_bytes_sent

            # Protect against counter overflow/reset
            if delta_recv < 0:
                delta_recv = 0
            if delta_sent < 0:
                delta_sent = 0

            self.download_speed_kb = round((delta_recv / elapsed) / 1024.0, 2)
            self.upload_speed_kb = round((delta_sent / elapsed) / 1024.0, 2)

            self.download_speed_mb = round(self.download_speed_kb / 1024.0, 3)
            self.upload_speed_mb = round(self.upload_speed_kb / 1024.0, 3)

            self.last_time = current_time
            self.last_bytes_recv = bytes_recv
            self.last_bytes_sent = bytes_sent

        # Gather interface names
        interfaces = []
        try:
            stats = psutil.net_if_stats()
            for iface, stat in stats.items():
                if stat.isup and not iface.startswith("Loopback"):
                    interfaces.append({
                        "name": iface,
                        "speed": stat.speed,
                        "mtu": stat.mtu
                    })
        except Exception:
            pass

        return {
            "download_kb": self.download_speed_kb,
            "upload_kb": self.upload_speed_kb,
            "download_mb": self.download_speed_mb,
            "upload_mb": self.upload_speed_mb,
            "total_bytes_recv": bytes_recv,
            "total_bytes_sent": bytes_sent,
            "total_gb_recv": round(bytes_recv / (1024 ** 3), 2),
            "total_gb_sent": round(bytes_sent / (1024 ** 3), 2),
            "interfaces": interfaces,
            "timestamp": current_time
        }

monitor = NetworkMonitor()

def get_stats() -> Dict[str, Any]:
    return monitor.sample()
