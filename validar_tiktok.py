import os
import sys
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright
from core.playwright_utils import garantir_playwright_browsers

ROOT_DIR = Path(__file__).resolve().parent

candidatos_sessao = [
    ROOT_DIR / "sessao_tiktok.json",
    ROOT_DIR / "config" / "tiktok_session.json",
    ROOT_DIR / "config" / "sessao_tiktok.json",
    ROOT_DIR / "tiktok_session.json",
]

SESSION_FILE = None
for c in candidatos_sessao:
    if c.exists() and c.stat().st_size > 100:
        SESSION_FILE = c
        break

if not SESSION_FILE:
    SESSION_FILE = candidatos_sessao[0]

LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOT_FILE = LOGS_DIR / "teste_sessao_tiktok.png"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def validar_sessao_tiktok(headless: bool = True) -> bool:
    print("=" * 65)
    print("=== VALIDAÇÃO DE SESSÃO DO TIKTOK — UMLAPSO BOT 🎵 ===")
    print("=" * 65)

    if not SESSION_FILE.exists():
        print(f"[!] ERRO: Arquivo de sessão não encontrado em: {SESSION_FILE}")
        print("[!] Execute 'python conectar_tiktok.py' para gerar sua sessão.")
        return False

    print(f"[*] Carregando sessão de: {SESSION_FILE}")
    print(f"[*] Modo de teste: {'Headless (Invisível)' if headless else 'Visível'}")

    garantir_playwright_browsers()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized"
                ]
            )
            context = browser.new_context(
                storage_state=str(SESSION_FILE),
                user_agent=USER_AGENT
            )
            page = context.new_page()

            print("[*] Acessando TikTok Studio / Creator Center...")
            target_url = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                page.goto("https://www.tiktok.com/creator-center/upload", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            # Salva screenshot para auditoria visual
            erro_screenshot = LOGS_DIR / "debug_tiktok_erro.png"
            try:
                page.screenshot(path=str(SCREENSHOT_FILE))
                print(f"[+] Screenshot de verificação salvo em: {SCREENSHOT_FILE}")
            except Exception:
                pass

            url_final = page.url.lower()

            # Checagem 1: Redirecionamento para login ou Captcha = Sessão Expirada
            is_login = "login" in url_final or page.locator("form#loginContainer, input[type='password']").count() > 0
            is_captcha = page.locator('div[class*="captcha"], #captcha_container, div[id*="secsdk"]').count() > 0

            if is_login or is_captcha:
                try:
                    page.screenshot(path=str(erro_screenshot))
                    print(f"[+] Screenshot de erro salvo em: {erro_screenshot}")
                except Exception:
                    pass
                print("\n[❌] RESULTADO: SESSÃO EXPIRADA OU BLOQUEADA POR CAPTCHA!")
                print("     URL atual: " + page.url)
                print("     -> Sessão expirada — rode python conectar_tiktok.py")
                return False

            # Checagem 2: Localizador de upload ativo = Sessão Válida
            tem_input_file = page.locator("input[type='file']").count() > 0
            tem_upload_box = page.locator("text='Select video', text='Carregar vídeo', text='Selecionar vídeo'").count() > 0

            if tem_input_file or tem_upload_box or "creator-center" in url_final or "tiktokstudio" in url_final:
                print("\n[✔] SUCESSO ABSOLUTO! SESSÃO DO TIKTOK ATIVA E AUTENTICADA!")
                print("    O Umlapso Bot está pronto para realizar uploads automáticos no TikTok.")
                print("=" * 65)

                # Preserva cookies frescos atualizados
                try:
                    context.storage_state(path=str(SESSION_FILE))
                except Exception:
                    pass

                return True
            else:
                print(f"\n[?] AVISO: Página carregada, mas botão de upload não localizado de imediato.")
                print(f"    URL atual: {page.url}")
                print(f"    Verifique a captura de tela salva em: {SCREENSHOT_FILE}")
                return False

    except Exception as e:
        print(f"\n[!] Falha ao executar validação do TikTok: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validador de Sessão do TikTok para Umlapso Bot")
    parser.add_argument("--headful", action="store_true", help="Executa com navegador visível")
    args = parser.parse_args()

    sucesso = validar_sessao_tiktok(headless=not args.headful)
    sys.exit(0 if sucesso else 1)

