import os
import json
import time
import sys
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    from core.playwright_utils import garantir_playwright_browsers
    garantir_playwright_browsers()
except Exception as e:
    print(f"[*] Aviso Playwright: {e}")

root_dir = os.path.dirname(os.path.abspath(__file__))
sessao_json = os.path.join(root_dir, "sessao_google_flow.json")
flow_id_file = os.path.join(root_dir, ".flow_project_id")

print("="*65)
print("=== CONECTAR GOOGLE FLOW (UMLAPSO BOT) ===")
print("="*65)
print("[*] Abrindo navegador para conexão...")
print("[*] Faça o login na sua conta Google e abra seu projeto no Flow.")
print("="*65)

with sync_playwright() as p:
    # Lança o Chromium nativo do Playwright sem conflito com o Chrome já aberto no PC
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
    page.goto("https://flow.google.com/", wait_until="domcontentloaded")
    
    print("\n[+] Janela aberta!")
    print("[*] Aguardando você fazer login e entrar no projeto...")
    print("[*] O script salvará tudo sozinho assim que a tela de geração do Flow carregar.\n")
    
    conectado = False
    
    # Aguarda até 10 minutos
    for i in range(600):
        time.sleep(1)
        try:
            # Detecta se a caixa de prompt do Flow está visível
            if page.locator('div[role="textbox"], div.ProseMirror').count() > 0 and "/project/" in page.url:
                conectado = True
                break
        except Exception:
            pass
            
    if conectado:
        print("\n" + "="*65)
        print("[✔] SUCESSO! LOGIN E PROJETO DETECTADOS COM SUCESSO!")
        
        # Extrai o ID do projeto atual
        try:
            proj_id = page.url.split("/project/")[1].split("?")[0].split("/")[0]
            with open(flow_id_file, "w", encoding="utf-8") as f:
                f.write(proj_id.strip())
            print(f"[+] Projeto ativo salvo: {proj_id}")
        except Exception as e:
            print(f"[*] Aviso ao extrair ID: {e}")
            
        time.sleep(2)
        # Salva o arquivo de sessão completo
        context.storage_state(path=sessao_json)
        print(f"[+] Sessão exportada com sucesso para '{os.path.basename(sessao_json)}'!")
        print("="*65)
        print("\n[>>>] PRONTO! AGORA:")
        print("1. Envie 'sessao_google_flow.json' e 'core/vision_veo.py' para o servidor.")
        print("2. No servidor, apague o cache antigo: rm -rf /home/umlapso_bot/perfil_chrome_bot")
        print("3. Execute no servidor: xvfb-run -a ./venv/bin/python main.py")
        print("="*65)
        time.sleep(4)
    else:
        print("\n[!] Tempo esgotado (10 min). Execute novamente quando estiver pronto.")
        
    context.close()
    browser.close()
