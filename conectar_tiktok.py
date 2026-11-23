import os
import json
import time
import sys
from playwright.sync_api import sync_playwright

# Força codificação UTF-8 no stdout
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    from core.playwright_utils import garantir_playwright_browsers
    garantir_playwright_browsers()
except Exception as e:
    print(f"[*] Aviso Playwright: {e}")

root_dir = os.path.dirname(os.path.abspath(__file__))
sessao_tiktok = os.path.join(root_dir, "sessao_tiktok.json")
config_dir = os.path.join(root_dir, "config")
sessao_tiktok_config = os.path.join(config_dir, "tiktok_session.json")

print("="*65)
print("=== CONECTAR TIKTOK (UMLAPSO BOT) ===")
print("="*65)
print("[*] Abrindo navegador para conexao do TikTok...")
print("[*] Faca o login na sua conta do TikTok (QR Code, Google ou Senha).")
print("="*65)

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--start-maximized"
        ]
    )
    
    context = browser.new_context(
        no_viewport=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )
    
    page = context.new_page()
    page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded")
    
    print("\n[+] Janela do TikTok aberta!")
    print("[*] Aguardando voce realizar o login...")
    print("[*] O robo monitora seus cookies de sessao em tempo real.\n")
    
    logado = False
    
    # Aguarda ate 5 minutos (300 segundos)
    for i in range(300):
        time.sleep(1)
        try:
            # 1. Deteccao direta por cookies de autenticacao do TikTok
            cookies = context.cookies()
            nomes_cookies = {c.get("name") for c in cookies}
            
            # Se qualquer um dos cookies mestres de sessao do TikTok existir
            if any(k in nomes_cookies for k in ["sessionid", "sessionid_ss", "sid_tt", "uid_tt"]):
                logado = True
                print("  [+] Cookie de autenticacao do TikTok detectado!")
                break
                
            # 2. Deteccao por URL do TikTok Studio ou Creator Center
            url_atual = page.url.lower()
            if "tiktokstudio" in url_atual or "creator-center" in url_atual or "creator.tiktok.com" in url_atual:
                logado = True
                print("  [+] URL autenticada do TikTok Studio detectada!")
                break
        except Exception:
            pass
            
    if logado:
        print("\n" + "="*65)
        print("[OK] SUCESSO! LOGIN NO TIKTOK DETECTADO!")
        print("="*65)
        
        # 1. Navega para o TikTok Studio para consolidar todos os cookies de upload
        try:
            print("[*] Consolidando sessão no TikTok Studio...")
            try:
                page.goto("https://www.tiktok.com/tiktokstudio/upload?from=webapp", wait_until="domcontentloaded", timeout=25000)
            except Exception:
                page.goto("https://www.tiktok.com/creator-center/upload", wait_until="domcontentloaded", timeout=25000)
            time.sleep(5)
        except Exception as e_nav:
            print(f"[*] Aviso TikTok Studio: {e_nav}")

        # 2. Obtém o estado de armazenamento
        state = context.storage_state()

        # 3. OTIMIZAÇÃO DE LONGA DURAÇÃO (Padrão QuizBot):
        # Garante que os cookies essenciais de autenticação tenham validade estendida (1 ano)
        # para evitar expiração prematura de sessão no servidor.
        um_ano_a_frente = int(time.time()) + (365 * 24 * 3600)
        cookies_modificados = []
        for c in state.get("cookies", []):
            if c.get("name") in ["sessionid", "sessionid_ss", "sid_tt", "uid_tt", "store-idc", "tt_chain_token"]:
                c["expires"] = um_ano_a_frente
            cookies_modificados.append(c)
        state["cookies"] = cookies_modificados

        # 4. Salva o arquivo de sessão final
        with open(sessao_tiktok, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        print(f"[+] Sessão persistente salva em: {sessao_tiktok}")

        os.makedirs(config_dir, exist_ok=True)
        try:
            with open(sessao_tiktok_config, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            print(f"[+] Sessão espelhada em: {sessao_tiktok_config}")
        except Exception as e_cfg:
            print(f"[*] Aviso ao espelhar config: {e_cfg}")

        print("="*65)
        print("[>>>] SESSÃO PERPÉTUA CONFIGURADA COM SUCESSO!")
        print("A cada publicação, o robô revalida e atualiza automaticamente os cookies.")
        print("="*65)
    else:
        print("\n[!] Tempo limite esgotado. Execute novamente se precisar.")
        
    context.close()
    browser.close()
