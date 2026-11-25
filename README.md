# Bandwidth Controller

A modern, local web-based network bandwidth limiter and real-time traffic monitor for Windows 10 and Windows 11.

## Features

- **System-Wide Rate Control (0 – 200 MB/s):** Limit network throughput from 0 up to 200 MB/s (204,800 KB/s) with immediate effect.
- **Dual-Unit Control Switch (KB/s & MB/s):** Toggle between Kilobytes per second (`KB/s`) and Megabytes per second (`MB/s`) using the unit selector right next to the control bar, plus direct numeric input and quick preset chips.
- **Real-Time Traffic Shaping:** Uses a virtual-clock leaky bucket proxy shaper combined with Windows QoS policies to pace download and upload streams accurately across parallel connections.
- **Live Network Telemetry & Waveform:** Displays real-time download/upload speeds (`MB/s` and `KB/s`), total data transferred, active physical adapter, and a live 60-point canvas waveform chart.
- **Safe Backup & Restore:** Automatically backs up existing Windows proxy settings and restores them cleanly on Disable or application exit.
- **Standalone Executable:** Packaged as `dist/BandwidthController.exe` with Administrator UAC elevation, bundled dependencies, and automatic browser launch.

## Local Web Address

The local control panel runs on port **9482**:

```text
http://127.0.0.1:9482
```

## Quick Start

### Option 1: Standalone Executable (Windows 10 / 11)

1. Double-click `dist/BandwidthController.exe`.
2. Accept the Windows UAC Administrator prompt.
3. Your default web browser will automatically open `http://127.0.0.1:9482`.
4. Choose your bandwidth limit in `KB/s` or `MB/s` and click **Apply Limit**.
5. Click **Disable (Unlimited)** or close the console window to restore full unrestricted speed.

### Option 2: Run from Source

```powershell
pip install -r requirements.txt
python main.py
```

Or double-click `install_and_run.bat` to install dependencies and start the server as Administrator.

## Project Structure

- `main.py` — Application entry point, UAC elevation, console close handler, proxy shaper thread, and browser launcher
- `backend/app.py` — FastAPI server and REST API endpoints (`/api/status`, `/api/apply`, `/api/disable`, `/api/stats`)
- `backend/proxy_shaper.py` — Async HTTP/HTTPS CONNECT rate-limiting shaper with Windows registry & WinHTTP integration
- `backend/throttle_engine.py` — Coordinated throttle state and Windows `NetQosPolicy` engine
- `backend/network_monitor.py` — Physical NIC telemetry monitor using `psutil`
- `frontend/` — Dark glassmorphism web interface (`index.html`, `style.css`, `app.js`)
