import time
import psutil
from typing import Dict, Any, Tuple
from backend import proxy_shaper


def _is_virtual_or_loopback(name: str) -> bool:
    low = name.lower()
    return (
        low.startswith("loopback")
        or low.startswith("vethernet")
        or low.startswith("local area connection*")
        or "tun" in low
        or "tap" in low
        or "isatap" in low
        or "teredo" in low
    )


class NetworkMonitor:
    def __init__(self):
        self.last_time = time.time()
        recv, sent = self._read_primary_counters()
        self.last_bytes_recv = recv
        self.last_bytes_sent = sent
        self.download_speed_kb = 0.0
        self.upload_speed_kb = 0.0
        self.download_speed_mb = 0.0
        self.upload_speed_mb = 0.0

    def _read_primary_counters(self) -> Tuple[int, int]:
        """Sum bytes on active physical network adapters without double-counting loopback/TUN."""
        per_nic = psutil.net_io_counters(pernic=True)
        stats = psutil.net_if_stats()

        physical_recv = 0
        physical_sent = 0
        found_physical = False

        for name, io in per_nic.items():
            st = stats.get(name)
            if st and st.isup and not _is_virtual_or_loopback(name):
                physical_recv += io.bytes_recv
                physical_sent += io.bytes_sent
                found_physical = True

        if found_physical:
            return physical_recv, physical_sent

        total = psutil.net_io_counters()
        return total.bytes_recv, total.bytes_sent

    def sample(self) -> Dict[str, Any]:
        """Calculate current download & upload speeds based on elapsed time."""
        current_time = time.time()
        elapsed = current_time - self.last_time

        bytes_recv, bytes_sent = self._read_primary_counters()

        if elapsed > 0.1:
            delta_recv = max(0, bytes_recv - self.last_bytes_recv)
            delta_sent = max(0, bytes_sent - self.last_bytes_sent)

            self.download_speed_kb = round((delta_recv / elapsed) / 1024.0, 2)
            self.upload_speed_kb = round((delta_sent / elapsed) / 1024.0, 2)

            self.download_speed_mb = round(self.download_speed_kb / 1024.0, 3)
            self.upload_speed_mb = round(self.upload_speed_kb / 1024.0, 3)

            self.last_time = current_time
            self.last_bytes_recv = bytes_recv
            self.last_bytes_sent = bytes_sent

        interfaces = []
        try:
            stats = psutil.net_if_stats()
            for iface, stat in stats.items():
                if stat.isup and not _is_virtual_or_loopback(iface):
                    interfaces.append({
                        "name": iface,
                        "speed": stat.speed,
                        "mtu": stat.mtu,
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
            "shaper_connections": proxy_shaper.active_connections,
            "timestamp": current_time,
        }


monitor = NetworkMonitor()


def get_stats() -> Dict[str, Any]:
    return monitor.sample()
