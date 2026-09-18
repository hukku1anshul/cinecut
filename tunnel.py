"""
Makes CineCut on this PC reachable from anywhere, through a free Cloudflare quick tunnel (no account needed).

    python run.py --lan --port 8080 --no-browser      # CineCut itself, as usual
    python tunnel.py                                  # then this: prints the https address for the phone app

Everything that comes through the tunnel gets the hosted site's lockdown (backend/app.py, security_guard): only the web
app answers, the studio does not exist. The address is new each time the tunnel starts (it is also written to
data/tunnel_url.txt); a permanent address needs a free Cloudflare or ngrok account. Stop with Ctrl+C.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
PLACES = [Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"), Path(r"C:\Program Files\cloudflared\cloudflared.exe"),
          Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe"]
exe = shutil.which("cloudflared") or next((str(p) for p in PLACES if p.exists()), None)
if not exe:
    sys.exit("cloudflared is not installed. Install it with: winget install Cloudflare.cloudflared")

proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
found = False
try:
    for line in proc.stdout:                 # keep reading, so cloudflared never blocks on a full pipe
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m and not found:
            found = True
            url = m.group(0)
            out = Path(__file__).resolve().parent / "data" / "tunnel_url.txt"
            out.parent.mkdir(exist_ok=True)
            out.write_text(url + "\n", encoding="utf-8")
            print(f"\nCineCut is reachable from anywhere at:\n\n    {url}\n\nEnter it in the phone app (or open {url}/app/ in a browser).", flush=True)
        elif "ERR" in line and not found:
            print(line.rstrip(), flush=True)
finally:
    proc.terminate()
