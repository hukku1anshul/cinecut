import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def find_available_port(start_port: int, host: str) -> int:
    for port in range(start_port, start_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    return start_port


def _module_ok(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def check_dependencies() -> None:
    print("🎬 CineCut AI Studio 6.0")
    print("=" * 55)
    for tool in ("ffmpeg", "ffprobe"):
        print(f"  [{'✓' if shutil.which(tool) else '✗'}] {tool}" + ("" if shutil.which(tool) else "  <- install FFmpeg and add it to PATH"))
    ytdlp = shutil.which("yt-dlp")
    print(f"  [{'✓' if ytdlp else '!'}] yt-dlp (YouTube / URL downloads)")
    if ytdlp and not shutil.which("deno"):
        print("  [!] Deno not found: YouTube downloads may fail. Install with: winget install DenoLand.Deno")
    print(f"  [{'✓' if _module_ok('edge_tts') else '!'}] edge-tts narrator (needs internet; personal use)")
    print(f"  [{'✓' if _module_ok('cv2') else 'i'}] OpenCV face tracking for 9:16 shorts")
    print(f"  [{'✓' if _module_ok('faster_whisper') or _module_ok('whisper') else 'i'}] Whisper offline transcription (optional)")

    from backend.video_engine.probe import check_nvenc_support, get_gpu_name
    if check_nvenc_support():
        print(f"  [✓] GPU encoding: {get_gpu_name() or 'NVIDIA'} (NVENC)")
    else:
        print("  [i] GPU encoding unavailable: using CPU (libx264)")
    print("=" * 55)


def main() -> None:
    parser = argparse.ArgumentParser(description="CineCut AI Studio")
    parser.add_argument("--port", type=int, default=8080, help="first port to try (default 8080)")
    parser.add_argument("--lan", action="store_true",
                        help="share the TV player (/tv) with other devices on your network; the editor stays local-only")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    args = parser.parse_args()

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    port = find_available_port(args.port, host)
    os.environ["CINECUT_PORT"] = str(port)
    os.environ["CINECUT_LAN"] = "1" if args.lan else "0"

    check_dependencies()
    url = f"http://localhost:{port}"
    print(f"\n🚀 CineCut Web Studio: {url}")
    if args.lan:
        print(f"📺 TV player is shared on your network at port {port}/tv (Windows may ask to allow network access).")
    print("   Press Ctrl+C to stop the server.\n")

    if not args.no_browser:
        def open_browser():
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn
    uvicorn.run("backend.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
