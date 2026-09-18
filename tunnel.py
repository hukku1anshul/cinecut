"""
Makes CineCut on this PC reachable from anywhere, through a tunnel.

    python run.py --lan --port 8080 --no-browser      # CineCut itself, as usual
    python tunnel.py                                  # then this: prints the https address for the phone app

With NGROK_AUTHTOKEN in .env (a free ngrok account) it uses ngrok and the account's permanent address
(NGROK_DOMAIN in .env picks one, e.g. my-name.ngrok-free.app; without it ngrok uses the account's own dev domain).
Without it, a free Cloudflare quick tunnel: no account, but a new address every time.
Everything that comes through either tunnel gets the hosted site's lockdown (backend/app.py, security_guard): only the web
app answers, the studio does not exist. The address is also written to data/tunnel_url.txt. Stop with Ctrl+C.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os                                    # noqa: E402
from backend import config                   # noqa: E402,F401  (loads .env into the environment)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
HOME = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links"


def find(name: str, *places: Path):
    return shutil.which(name) or next((str(p) for p in places if p.exists()), None)


if os.environ.get("NGROK_AUTHTOKEN"):
    exe = find("ngrok", HOME / "ngrok.exe")
    if not exe:
        sys.exit("ngrok is not installed. Install it with: winget install Ngrok.Ngrok")
    cmd = [exe, "http", str(PORT), "--log", "stdout", "--log-format", "logfmt"]
    if os.environ.get("NGROK_DOMAIN"):
        cmd += ["--url", "https://" + re.sub(r"^https?://", "", os.environ["NGROK_DOMAIN"]).strip("/")]
    pattern = re.compile(r"url=(https://[^\s\"]+)")
    kind = "ngrok (permanent address)"
else:
    exe = find("cloudflared", Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
               Path(r"C:\Program Files\cloudflared\cloudflared.exe"), HOME / "cloudflared.exe")
    if not exe:
        sys.exit("cloudflared is not installed. Install it with: winget install Cloudflare.cloudflared")
    cmd = [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"]
    pattern = re.compile(r"(https://[a-z0-9-]+\.trycloudflare\.com)")
    kind = "Cloudflare quick tunnel (new address each time)"

proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
found = False
try:
    for line in proc.stdout:                 # keep reading, so the tunnel never blocks on a full pipe
        m = pattern.search(line)
        if m and not found:
            found = True
            url = m.group(1)
            out = Path(__file__).resolve().parent / "data" / "tunnel_url.txt"
            out.parent.mkdir(exist_ok=True)
            out.write_text(url + "\n", encoding="utf-8")
            print(f"\nCineCut is reachable from anywhere ({kind}) at:\n\n    {url}\n\n"
                  f"Enter it in the phone app, or open {url}/app/ in a browser.", flush=True)
        elif not found and re.search(r"lvl=(eror|crit)|ERR ", line):
            print(line.rstrip()[:300], flush=True)
finally:
    proc.terminate()
