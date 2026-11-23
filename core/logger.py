import os
import re
import logging
from logging.handlers import RotatingFileHandler

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOGS_DIR, "umlapso_bot.log")

class SensitiveFilter(logging.Filter):
    """Mascara chaves e tokens de API confidenciais nos logs."""
    def filter(self, record):
        msg = str(record.msg)
        # Mascara tokens comuns como OpenAI, Meta, Google
        msg = re.sub(r'sk-[a-zA-Z0-9_\-]{20,}', 'sk-***[CENSORED]***', msg)
        msg = re.sub(r'EAA[a-zA-Z0-9_\-]{20,}', 'EAA***[CENSORED]***', msg)
        msg = re.sub(r'GOCSPX-[a-zA-Z0-9_\-]{20,}', 'GOCSPX-***[CENSORED]***', msg)
        record.msg = msg
        return True

def configurar_logger(nome: str = "umlapso") -> logging.Logger:
    """Configura e retorna um logger rotativo seguro."""
    logger = logging.getLogger(nome)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.addFilter(SensitiveFilter())

    # Formatador
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 1. Handler para arquivo rotativo (10 MB, até 5 backups)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 2. Handler para console
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger

log = configurar_logger()
