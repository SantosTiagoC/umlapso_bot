import os
import sys
import subprocess

def garantir_playwright_browsers() -> bool:
    """
    Verifica se o Chromium do Playwright está presente e funcional no sistema.
    Se estiver ausente, executa automaticamente a instalação completa com dependências
    do sistema (playwright install --with-deps chromium) sem precisar de comandos manuais.
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            # Testa um launch real rápido para garantir que tanto o chromium quanto o headless_shell existem
            browser = p.chromium.launch(headless=True)
            browser.close()
            return True
    except Exception as e_check:
        print("\n" + "=" * 65)
        print("[*] Playwright: Navegador Chromium ausente ou incompleto no sistema.")
        print("[*] Instalando Chromium e dependências automaticamente...")
        print("=" * 65)

        # 1. Tenta instalação completa com dependências de sistema (ideal para Linux Ubuntu/Debian)
        cmd_deps = [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"]
        try:
            res = subprocess.run(cmd_deps, capture_output=True, text=True, check=False)
            if res.returncode == 0:
                print("[+] Chromium e dependências do sistema instalados com sucesso!")
                return True
        except Exception:
            pass

        # 2. Fallback para instalação padrão sem --with-deps (Windows ou usuários sem permissão de root)
        cmd_padrao = [sys.executable, "-m", "playwright", "install", "chromium"]
        try:
            res_padrao = subprocess.run(cmd_padrao, capture_output=True, text=True, check=False)
            if res_padrao.returncode == 0:
                print("[+] Chromium instalado com sucesso!")
                return True
            else:
                print(f"[!] Aviso no download do Playwright: {res_padrao.stderr or res_padrao.stdout}")
        except Exception as e_inst:
            print(f"[!] Erro ao tentar instalar Chromium: {e_inst}")

    # Confirmação final após instalação
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
            return True
    except Exception:
        return False

