import os
import sys
import time
import glob

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def processo_esta_ativo(pid: int) -> bool:
    """Verifica se um processo com o determinado PID ainda está em execução."""
    if pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            # No Linux, confirma se o PID pertence a uma execução python do bot
            cmdline_path = f"/proc/{pid}/cmdline"
            if os.path.exists(cmdline_path):
                try:
                    with open(cmdline_path, "rb") as f:
                        cmd = f.read().decode("utf-8", errors="ignore")
                        if "main.py" not in cmd and "python" not in cmd and "scheduler.py" not in cmd and "repost_engine.py" not in cmd:
                            return False
                except Exception:
                    pass
            return True
        except (OSError, ProcessLookupError):
            return False

def limpar_todas_travas() -> list[str]:
    """Remove qualquer arquivo de lock remanescente ou preso no workspace."""
    padroes = [
        os.path.join(ROOT_DIR, "*.lock"),
        os.path.join(ROOT_DIR, ".*.lock"),
        os.path.join(ROOT_DIR, "temp", "*.lock")
    ]
    removidos = []
    for p in padroes:
        for f in glob.glob(p):
            try:
                os.remove(f)
                removidos.append(f)
            except Exception:
                pass
    return removidos

class LockSimultaneo:
    """
    Mecanismo de Lock por arquivo com PID para evitar execuções simultâneas.
    Limpa automaticamente travas órfãs (quando o servidor reiniciar ou o processo anterior cair)
    ou abandonadas (> 10 minutos).
    """
    def __init__(self, lock_file: str = None):
        if lock_file is None:
            lock_file = os.path.join(ROOT_DIR, ".umlapso.lock")
        elif not os.path.isabs(lock_file):
            lock_file = os.path.join(ROOT_DIR, lock_file)
        self.lock_file = lock_file
        self.acquired = False

    def __enter__(self):
        if os.path.exists(self.lock_file):
            trava_valida = False
            try:
                # Se a trava tiver mais de 10 minutos (600s), considera abandonada
                idade_segundos = time.time() - os.path.getmtime(self.lock_file)
                if idade_segundos > 600:
                    print(f"[*] Trava antiga detectada em '{os.path.basename(self.lock_file)}' ({int(idade_segundos/60)} min atrás). Limpando automaticamente...")
                    os.remove(self.lock_file)
                else:
                    with open(self.lock_file, "r") as f:
                        conteudo = f.read().strip()
                    if conteudo.isdigit():
                        pid = int(conteudo)
                        if processo_esta_ativo(pid):
                            trava_valida = True
                            raise RuntimeError(f"O processo já está em execução no PID {pid} (trava '{os.path.basename(self.lock_file)}'). Abortando execução concorrente.")
                        else:
                            print(f"[*] Trava órfã detectada (PID anterior: {pid}). Limpando...")
                            os.remove(self.lock_file)
            except RuntimeError:
                raise
            except Exception:
                if not trava_valida and os.path.exists(self.lock_file):
                    try:
                        os.remove(self.lock_file)
                    except Exception:
                        pass

        # Grava o PID atual
        try:
            with open(self.lock_file, "w") as f:
                f.write(str(os.getpid()))
            self.acquired = True
        except Exception as e_write:
            raise RuntimeError(f"Não foi possível gravar o arquivo de lock '{self.lock_file}': {e_write}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.acquired and os.path.exists(self.lock_file):
            try:
                os.remove(self.lock_file)
            except Exception:
                pass
        self.acquired = False

class ProcessLock:
    """
    Gerenciador de lock de processo compatível com 'with ProcessLock(...)'
    ou uso com acquire() / release().
    """
    def __init__(self, lock_filename: str = "umlapso.lock"):
        if not os.path.isabs(lock_filename):
            self.lock_file = os.path.join(ROOT_DIR, lock_filename)
        else:
            self.lock_file = lock_filename
        self.acquired = False

    def acquire(self) -> bool:
        if os.path.exists(self.lock_file):
            try:
                idade_segundos = time.time() - os.path.getmtime(self.lock_file)
                if idade_segundos > 600:
                    try:
                        os.remove(self.lock_file)
                    except Exception:
                        pass
                else:
                    with open(self.lock_file, "r") as f:
                        pid_str = f.read().strip()
                    if pid_str.isdigit():
                        pid = int(pid_str)
                        if processo_esta_ativo(pid):
                            return False
                    try:
                        os.remove(self.lock_file)
                    except Exception:
                        pass
            except Exception:
                pass

        try:
            with open(self.lock_file, "w") as f:
                f.write(str(os.getpid()))
            self.acquired = True
            return True
        except Exception:
            return False

    def release(self) -> None:
        if self.acquired and os.path.exists(self.lock_file):
            try:
                os.remove(self.lock_file)
            except Exception:
                pass
        self.acquired = False

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Não foi possível obter trava de execução para '{self.lock_file}'.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
