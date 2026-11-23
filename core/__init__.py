import os
import sys
import shutil

def garantir_ffmpeg_path() -> bool:
    """Garante que o caminho do FFmpeg/FFprobe seja detectado no sistema mesmo sem reiniciar a sessão."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return True
    if sys.platform == "win32":
        import winreg
        paths = []
        for root in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
            try:
                sub = r"Environment" if root == winreg.HKEY_CURRENT_USER else r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
                with winreg.OpenKey(root, sub) as key:
                    val, _ = winreg.QueryValueEx(key, "Path")
                    paths.extend(val.split(";"))
            except Exception:
                pass
        for p in paths:
            if p and os.path.isdir(p) and p not in os.environ.get("PATH", ""):
                os.environ["PATH"] += os.pathsep + p
        if shutil.which("ffmpeg") and shutil.which("ffprobe"):
            return True
    return False

garantir_ffmpeg_path()
