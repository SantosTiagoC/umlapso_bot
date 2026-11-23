#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
browser_utils.py - Utilitários para Playwright e navegadores com auto-recuperação.
Garante que binários do Chromium sejam baixados e instalados automaticamente caso ausentes.
"""

import os
import sys
import logging
import subprocess
from typing import Optional, List, Any

logger = logging.getLogger("browser_utils")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

def garantir_playwright_browsers(forcar: bool = False) -> bool:
    """
    Verifica se os binários do Chromium do Playwright estão presentes e executáveis.
    Se estiverem ausentes, executa automaticamente a instalação silenciosa.
    """
    if not forcar:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                exec_path = p.chromium.executable_path
                if exec_path and os.path.exists(exec_path):
                    return True
        except Exception:
            pass

    logger.warning("[*] Playwright: Binários do Chromium ausentes ou corrompidos. Baixando e instalando...")
    try:
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            logger.info("[+] Binários do Chromium instalados com sucesso!")
            return True
        else:
            cmd_deps = [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"]
            res_deps = subprocess.run(cmd_deps, capture_output=True, text=True, check=False)
            if res_deps.returncode == 0:
                logger.info("[+] Chromium com dependências instalado com sucesso!")
                return True
            logger.error(f"[!] Erro ao instalar Playwright: {res_deps.stderr or res_deps.stdout}")
    except Exception as e_inst:
        logger.error(f"[!] Erro ao tentar auto-instalar browsers do Playwright: {e_inst}")

    return False

def lancar_chromium_seguro(
    p: Any,
    headless: bool = True,
    args: Optional[List[str]] = None,
    executable_path: Optional[str] = None,
    **kwargs
) -> Any:
    """
    Lança uma instância do Chromium com auto-recuperação de binários.
    """
    base_args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
    if args:
        for a in args:
            if a not in base_args:
                base_args.append(a)

    launch_kwargs = dict(kwargs)
    launch_kwargs["headless"] = headless
    launch_kwargs["args"] = base_args
    if executable_path and os.path.isfile(executable_path):
        launch_kwargs["executable_path"] = executable_path

    try:
        return p.chromium.launch(**launch_kwargs)
    except Exception as e_launch:
        err_msg = str(e_launch)
        if "Executable doesn't exist" in err_msg or "playwright install" in err_msg:
            logger.warning("[-] Binários do Chromium ausentes. Executando instalação automática...")
            garantir_playwright_browsers(forcar=True)
            if "executable_path" in launch_kwargs:
                del launch_kwargs["executable_path"]
            return p.chromium.launch(**launch_kwargs)
        raise
