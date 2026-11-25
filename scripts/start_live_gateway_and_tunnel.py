"""Start Live Gateway on port 8008 and open ngrok tunnel."""
import os
import subprocess
import time
import httpx
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
ngrok_exe = r"C:\Users\Kushagra\AppData\Local\Programs\Python\Python313\Scripts\ngrok.exe"

print("[1] Starting uvicorn live_gateway on port 8008...")
srv_proc = subprocess.Popen(
    [str(venv_python), "-m", "uvicorn", "repoagent.api.live_gateway:app", "--port", "8008", "--host", "127.0.0.1"],
    cwd=str(ROOT),
)

time.sleep(2.5)

print("[2] Starting ngrok tunnel to port 8008...")
ngrok_proc = subprocess.Popen(
    [ngrok_exe, "http", "8008", "--log=stdout"],
)

time.sleep(3.5)

# Discover public tunnel URL
public_url = None
for _ in range(10):
    try:
        r = httpx.get("http://127.0.0.1:4040/api/tunnels", timeout=2.0)
        if r.status_code == 200:
            data = r.json()
            for t in data.get("tunnels", []):
                if t.get("proto") == "https":
                    public_url = t.get("public_url")
                    break
                elif not public_url:
                    public_url = t.get("public_url")
            if public_url:
                break
    except Exception:
        pass
    time.sleep(1.0)

print(f"[3] Public Tunnel URL: {public_url}")

# Verify gateway health via public tunnel
if public_url:
    try:
        h_resp = httpx.get(f"{public_url}/health", headers={"ngrok-skip-browser-warning": "1"}, timeout=10.0)
        print(f"[4] Health Check via Public Tunnel: HTTP {h_resp.status_code} - {h_resp.json()}")
    except Exception as e:
        print(f"[4] Health Check Error: {e}")

# Keep processes running
try:
    while True:
        time.sleep(1.0)
except KeyboardInterrupt:
    srv_proc.terminate()
    ngrok_proc.terminate()
