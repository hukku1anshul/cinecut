import os
import sys
import subprocess
from pathlib import Path

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def get_windows_desktop():
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, text=True, check=True
        )
        desktop = res.stdout.strip()
        if desktop and os.path.exists(desktop):
            return Path(desktop)
    except Exception:
        pass
    
    # Fallback paths
    candidates = [
        Path.home() / "OneDrive" / "Desktop",
        Path.home() / "Desktop"
    ]
    for c in candidates:
        if c.exists():
            return c
    return Path.home() / "Desktop"

def create_desktop_shortcut():
    base_dir = Path(__file__).resolve().parent
    desktop_dir = get_windows_desktop()

    vbs_path = base_dir / "CineCut_Silent.vbs"
    bat_path = base_dir / "CineCut.bat"
    shortcut_path = desktop_dir / "CineCut AI Studio.lnk"

    # Target the silent VBS launcher
    target_file = vbs_path if vbs_path.exists() else bat_path

    # PowerShell COM shortcut creation
    ps_cmd = (
        f"$WshShell = New-Object -ComObject WScript.Shell; "
        f"$Shortcut = $WshShell.CreateShortcut('{str(shortcut_path)}'); "
        f"$Shortcut.TargetPath = '{str(target_file)}'; "
        f"$Shortcut.WorkingDirectory = '{str(base_dir)}'; "
        f"$Shortcut.Description = 'CineCut AI - Autonomous Movie Editor & Viral Recap Studio'; "
        f"$Shortcut.Save();"
    )

    try:
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        if res.returncode == 0 and shortcut_path.exists():
            print(f"✓ Desktop Shortcut created successfully: {shortcut_path}")
            return True
        else:
            print(f"[!] PowerShell shortcut creation failed: {res.stderr}")
            return False
    except Exception as e:
        print(f"[!] Error creating desktop shortcut: {e}")
        return False

if __name__ == "__main__":
    create_desktop_shortcut()
