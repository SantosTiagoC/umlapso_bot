import os
import time
import requests
from playwright.sync_api import sync_playwright

def obter_projeto_fixo_id(root_dir: str) -> str:
    """Recupera ou define o ID do projeto fixo persistente."""
    caminho_id = os.path.join(root_dir, ".flow_project_id")
    env_id = os.getenv("FLOW_PROJECT_ID")
    if env_id:
        return env_id.strip()
    if os.path.exists(caminho_id):
        with open(caminho_id, "r", encoding="utf-8") as f:
            conteudo = f.read().strip()
            if conteudo:
                return conteudo
    # ID padrão caso nenhum arquivo exista
    return "5240fab7-daab-46f2-85c9-de14684a1c2d"

def gerar_video_veo_flow(prompt: str, output_path: str):
    """
    Automatiza a geração de vídeo no Google Flow (Omni 1.1 Flash) via Playwright.
    - Utiliza SEMPRE o mesmo projeto persistente (sem criar projetos novos a cada cena/vídeo).
    - Configura e garante o modelo Omni 1.1 Flash (menor consumo de créditos: 15 créditos).
    - Parâmetros: Vídeo, 720p, 10s, 9:16, x1.
    - Aplica espera de 1s antes de gerar.
    - Aplica espera de 45s para renderização do vídeo.
    - Abre o player da thumbnail e faz o download em 720p.
    """
    print(f"  -> Iniciando Robô do Google Flow (Omni 1.1 Flash)...")
    
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    perfil_dir = os.path.join(root_dir, "perfil_chrome_bot")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    
    project_id = obter_projeto_fixo_id(root_dir)
    project_url = f"https://flow.google.com/project/{project_id}"
    
    with sync_playwright() as p:
        # Configuração do modo headless (automático para Linux sem display ou via .env)
        headless_env = os.getenv("PLAYWRIGHT_HEADLESS", "").lower()
        if headless_env in ["true", "1", "yes"]:
            modo_headless = True
        elif headless_env in ["false", "0", "no"]:
            modo_headless = False
        else:
            modo_headless = (os.name != "nt" and "DISPLAY" not in os.environ)

        try:
            from core.playwright_utils import garantir_playwright_browsers
            garantir_playwright_browsers()
        except Exception:
            pass

        candidatos_sessao = [
            os.path.join(root_dir, "sessao_google_flow.json"),
            os.path.join(root_dir, "config", "sessao_google_flow.json"),
        ]
        caminho_sessao = None
        for cs in candidatos_sessao:
            if os.path.exists(cs) and os.path.getsize(cs) > 100:
                caminho_sessao = cs
                break
        if not caminho_sessao:
            caminho_sessao = candidatos_sessao[0]

        usa_sessao_json = os.path.exists(caminho_sessao) and os.path.getsize(caminho_sessao) > 100

        browser_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage"
        ]

        if usa_sessao_json:
            print("  [+] Inicializando navegador limpo com sessao_google_flow.json...")
            browser_instance = p.chromium.launch(
                headless=modo_headless,
                args=browser_args
            )
            contexto = browser_instance.new_context(
                storage_state=caminho_sessao,
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            )
            pagina = contexto.new_page()
        else:
            launch_kwargs = {
                "user_data_dir": perfil_dir,
                "headless": modo_headless,
                "args": browser_args,
                "ignore_default_args": ["--enable-automation"],
                "viewport": {"width": 1280, "height": 800}
            }
            try:
                contexto = p.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)
            except Exception:
                contexto = p.chromium.launch_persistent_context(**launch_kwargs)
            browser_instance = contexto
            pagina = contexto.pages[0] if contexto.pages else contexto.new_page()

        print(f"  -> Acessando o projeto fixo: {project_url} ...")
        pagina.goto(project_url, wait_until="domcontentloaded", timeout=60000)
        pagina.wait_for_timeout(3000)

        # 1. VERIFICAÇÃO DE LOGIN / 404 DO PROJETO
        precisa_login = False
        for s in ['button:has-text("Fazer login")', 'button:has-text("Sign in")', 'a:has-text("Fazer login")', 'a:has-text("Sign in")']:
            try:
                if pagina.locator(s).first.is_visible(timeout=1000):
                    precisa_login = True
                    break
            except Exception:
                pass

        if precisa_login:
            print("  [!] Sessão não autenticada. Faça o login manualmente na janela do navegador...")
            for _ in range(60):
                pagina.wait_for_timeout(1000)
                if not any(pagina.locator(s).first.is_visible() for s in ['button:has-text("Fazer login")', 'button:has-text("Sign in")'] if pagina.locator(s).count() > 0):
                    print("  [+] Login detectado!")
                    break

        # Se o projeto fixo retornar 404 (expirou, foi deletado ou não pertence à conta)
        if "404" in pagina.url or "reason=project" in pagina.url or pagina.locator('div[role="textbox"], div.ProseMirror').count() == 0:
            if "404" in pagina.url or "reason=project" in pagina.url:
                print("  [!] Projeto fixo retornou 404. Recuperando via painel principal do Flow...")
                pagina.goto("https://flow.google.com/", wait_until="domcontentloaded", timeout=60000)
                pagina.wait_for_timeout(3000)
                
                # Tenta clicar em "Novo projeto" ou abrir o primeiro projeto existente
                try:
                    btn_novo = pagina.locator('button:has-text("Novo projeto"), a:has-text("Novo projeto"), button:has-text("New project")').first
                    if btn_novo.is_visible(timeout=5000):
                        print("  -> Criando novo projeto automático...")
                        btn_novo.click(force=True)
                        pagina.wait_for_timeout(4000)
                    else:
                        # Tenta clicar no primeiro projeto da lista se existir
                        card_projeto = pagina.locator('a[href*="/project/"], div[role="button"]:has-text("Projeto")').first
                        if card_projeto.is_visible(timeout=3000):
                            card_projeto.click()
                            pagina.wait_for_timeout(4000)
                except Exception as e_rec:
                    print(f"  [*] Aviso na auto-recuperação de projeto: {e_rec}")

                if "/project/" in pagina.url:
                    novo_id = pagina.url.split("/project/")[1].split("?")[0].split("/")[0]
                    if novo_id:
                        with open(os.path.join(root_dir, ".flow_project_id"), "w", encoding="utf-8") as f:
                            f.write(novo_id.strip())
                        print(f"  [+] Novo ID de projeto registrado com sucesso: {novo_id}")

        # 2. SE O PLAYER DE UMA CENA ANTERIOR ESTIVER ABERTO, FECHA-O
        try:
            btn_close = pagina.locator('button:has-text("Concluir"), button:has(mat-icon:has-text("arrow_back"))').first
            if btn_close.is_visible(timeout=1500):
                btn_close.click()
                pagina.wait_for_timeout(1000)
        except Exception:
            pass

        # 3. AGUARDAR CAIXA DE TEXTO (PROSEMIRROR)
        print("  -> Localizando caixa de texto do prompt (ProseMirror)...")
        try:
            caixa_texto = pagina.wait_for_selector('div[role="textbox"], div.ProseMirror', timeout=30000)
        except Exception:
            caminho_debug = os.path.join(root_dir, "logs", "debug_flow_tela.png")
            os.makedirs(os.path.dirname(caminho_debug), exist_ok=True)
            try:
                pagina.screenshot(path=caminho_debug)
                print(f"  [-] Timeout ao aguardar caixa de texto. URL atual: {pagina.url}")
                print(f"  [-] Screenshot de diagnóstico salva em '{caminho_debug}'")
            except Exception:
                pass
            raise

        # 4. CONFIGURAR PARÂMETROS DO MODELO (OMNI 1.1 FLASH, VÍDEO, 9:16, 720p, 10s, x1 - 15 créditos)
        print("  -> Verificando e garantindo configurações: Vídeo · 9:16 · Omni 1.1 Flash · 720p · 10s · x1...")
        try:
            gatilho = pagina.locator("button.settings-trigger-button, button[aria-label='Gatilho de configurações']").first
            if gatilho.is_visible(timeout=5000):
                txt_gatilho = gatilho.inner_text().replace('\n', ' ')
                precisa_configurar = (
                    "Vídeo" not in txt_gatilho or 
                    "Banana" in txt_gatilho or 
                    "9_16" not in txt_gatilho or 
                    "10s" not in txt_gatilho or 
                    "x1" not in txt_gatilho
                )
                if precisa_configurar:
                    print(f"  -> Estado atual: '{txt_gatilho}'. Ajustando para Modo Vídeo 9:16...")
                    gatilho.click(force=True)
                    pagina.wait_for_timeout(600)
                    
                    overlay = pagina.locator(".cdk-overlay-pane flow-prompt-box-settings, flow-prompt-box-settings").first
                    overlay.wait_for(state="visible", timeout=5000)
                    
                    # 1. Seleciona Modo Vídeo dentro da overlay (NUNCA na barra lateral)
                    overlay.locator("mat-button-toggle").filter(has_text="Vídeo").first.click(force=True)
                    pagina.wait_for_timeout(300)
                    
                    # 2. Seleciona Elementos
                    try:
                        overlay.locator("mat-button-toggle").filter(has_text="Elementos").first.click(force=True)
                        pagina.wait_for_timeout(300)
                    except Exception:
                        pass
                        
                    # 3. Proporção 9:16
                    try:
                        overlay.locator("mat-button-toggle").filter(has_text="9:16").first.click(force=True)
                        pagina.wait_for_timeout(300)
                    except Exception:
                        pass
                        
                    # 4. Resolução 720p
                    try:
                        overlay.locator("mat-button-toggle").filter(has_text="720p").first.click(force=True)
                        pagina.wait_for_timeout(300)
                    except Exception:
                        pass
                        
                    # 5. Duração 10s
                    try:
                        overlay.locator("mat-button-toggle").filter(has_text="10s").first.click(force=True)
                        pagina.wait_for_timeout(300)
                    except Exception:
                        pass
                        
                    # 6. Quantidade x1
                    try:
                        overlay.locator("mat-button-toggle").filter(has_text="x1").first.click(force=True)
                        pagina.wait_for_timeout(300)
                    except Exception:
                        pass
                        
                    # Fecha overlay
                    pagina.keyboard.press("Escape")
                    pagina.wait_for_timeout(600)
                    
                    txt_depois = gatilho.inner_text().replace('\n', ' ')
                    print(f"  [+] Configurado com sucesso: {txt_depois}")
                else:
                    print(f"  [+] Configurações já ativas no Flow: {txt_gatilho}")
        except Exception as e_cfg:
            print(f"  [*] Info na configuração do modelo: {e_cfg}")

        # Snapshot dos thumbnails existentes antes da geração
        seletor_thumb = 'img.image, img[src*="/asb/"], img.thumbnail'
        qtd_antes = pagina.locator(seletor_thumb).count()
        srcs_antigos = set()
        try:
            srcs_antigos = set(pagina.locator(seletor_thumb).evaluate_all('imgs => imgs.map(i => i.src)'))
        except Exception:
            pass
        print(f"  [*] Thumbnails existentes antes da geração: {qtd_antes}")

        # 5. INSERIR O PROMPT
        prompt_completo = f"{prompt}, dark cinematic, surrealism, highly detailed"
        print(f"  -> Inserindo novo prompt: {prompt[:60]}...")
        caixa_texto.click()
        pagina.keyboard.press("Control+A")
        pagina.keyboard.press("Backspace")
        pagina.wait_for_timeout(300)
        pagina.keyboard.type(prompt_completo)
        pagina.wait_for_timeout(500)
        print("  [+] Prompt inserido com sucesso!")

        # 6. TEMPO DE ESPERA DE 1 SEGUNDO ANTES DE GERAR
        print("  -> Aguardando 1 segundo antes de disparar a geração...")
        pagina.wait_for_timeout(1000)

        # 7. LOCALIZAR E CLICAR NO BOTÃO GERAR
        print("  -> Disparando geração do vídeo...")
        btn_gerar = pagina.locator('button[aria-label="Iniciar geração"], button.generate-icon-button, button:has-text("arrow_forward"), button:has-text("Gerar")').first
        if btn_gerar.is_visible(timeout=3000):
            btn_gerar.click(force=True)
            print("  [+] Botão 'Gerar' acionado!")
        else:
            caixa_texto.press("Enter")
            print("  [+] Disparo via Enter acionado!")

        # 8. AGUARDAR O NOVO VÍDEO SER RENDERIZADO (NOVO THUMBNAIL APARECER)
        print("  -> Aguardando renderização do novo vídeo (Omni 1.1 Flash)...")
        pagina.wait_for_timeout(35000) # Processamento mínimo do Omni Flash
        
        # Monitora até uma NOVA thumbnail ser adicionada à galeria
        novo_video_detectado = False
        for _ in range(55): # até 90s total
            pagina.wait_for_timeout(1000)
            if pagina.locator(seletor_thumb).count() > qtd_antes:
                novo_video_detectado = True
                print(f"  [+] Novo vídeo gerado detectado no Google Flow! (Total: {pagina.locator(seletor_thumb).count()})")
                pagina.wait_for_timeout(3000)
                break

        # 9. BAIXAR O VÍDEO RECÉM-GERADO (NUNCA O VÍDEO ANTIGO)
        print("  -> Localizando a mídia recém-gerada para download...")
        sucesso_download = False
        
        try:
            todos_thumbs = pagina.locator(seletor_thumb).all()
            thumb = None
            
            # Procura pelo thumbnail recém-gerado (do topo/mais recente para a base)
            for t in todos_thumbs:
                try:
                    src = t.get_attribute("src")
                    if src and src not in srcs_antigos:
                        thumb = t
                        print(f"  [+] Novo thumbnail identificado por src inédito: {src[:45]}...")
                        break
                except Exception:
                    pass
                    
            # Se não diferenciou por src, o item recém-gerado fica sempre no topo (índice 0, menor Y)
            if not thumb:
                thumb = todos_thumbs[0] if todos_thumbs else pagina.locator(seletor_thumb).first
                print("  [+] Selecionado o thumbnail do topo da galeria (mais recente).")
                    
            thumb.scroll_into_view_if_needed()
            thumb.wait_for(state="visible", timeout=20000)
            print("  -> Abrindo player da cena (duplo clique na thumbnail nova)...")
            thumb.dblclick()
            pagina.wait_for_timeout(2000)
            
            # Clica no botão de download no topo do player
            btn_dl = pagina.locator('button[aria-label="Baixar mídia"], button:has(mat-icon:has-text("download"))').first
            btn_dl.wait_for(state="visible", timeout=15000)
            btn_dl.click()
            pagina.wait_for_timeout(1000)
            
            # Clica na opção de resolução 720p
            opt_720 = pagina.locator('button.mat-mdc-menu-item:has-text("720p"), button:has-text("Tamanho original")').first
            print("  -> Acionando download em 720p...")
            
            with pagina.expect_download(timeout=40000) as dl_info:
                opt_720.click()
                
            dl = dl_info.value
            dl.save_as(output_path)
            sucesso_download = True
            print(f"  [+] Download concluído com sucesso!")
            
            # Fecha player para retornar à área de trabalho do mesmo projeto
            try:
                pagina.locator('button:has-text("Concluir"), button:has(mat-icon:has-text("arrow_back"))').first.click()
                pagina.wait_for_timeout(1000)
            except Exception:
                pass
                
        except Exception as e_down:
            print(f"  [-] Erro na tentativa de download: {e_down}")

        # Fallback de segurança se necessário
        if not sucesso_download:
            try:
                for sel in ['button:has-text("Baixar")', 'button:has-text("Download")', 'a[aria-label*="Baixar" i]']:
                    loc = pagina.locator(sel).first
                    if loc.is_visible(timeout=2000):
                        with pagina.expect_download(timeout=20000) as dl_info:
                            loc.click()
                        dl = dl_info.value
                        dl.save_as(output_path)
                        sucesso_download = True
                        break
            except Exception:
                pass

        if os.path.exists(output_path) and os.path.getsize(output_path) > 50000:
            print(f"  [+] Cena salva em: {output_path} ({os.path.getsize(output_path)} bytes)")
        else:
            raise RuntimeError(f"Falha ao obter o vídeo da cena do Google Flow em {output_path}. Arquivo não encontrado ou menor que 50KB.")

        try:
            if usa_sessao_json and os.path.exists(caminho_sessao):
                contexto.storage_state(path=caminho_sessao)
            contexto.close()
            if usa_sessao_json:
                browser_instance.close()
        except Exception:
            pass

def gerar_imagem_com_fallback(prompt: str, output_path: str, preferencia="veo"):
    gerar_video_veo_flow(prompt, output_path)
