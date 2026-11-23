#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flow_client.py - Cliente unificado para geração de imagens no Google Flow (9:16 vertical).
Suporta:
1. Playwright Chromium com auto-instalação de binários e isolamento via ProcessLock.
2. Anexo automático de imagens de referência ('assets/characters/leo_ref.png' e 'bip_ref.png').
3. Geração de folha de referência oficial dos personagens via 'main.py --gerar-referencias'.
4. Enquadramento seguro 9:16 (medium chest-up framing, headroom generoso, sem cortar rosto).
5. Definição estrita do Bip (drone esférico sem braços e sem pernas).
6. Retry automático de até 2 tentativas por cena antes de qualquer fallback.
"""

import os
import re
import sys
import time
import subprocess
import logging
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

logger = logging.getLogger("flow_client")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from core.lock import ProcessLock

REF_CHAR_DIR = os.path.join(ROOT_DIR, "assets", "characters")
LEO_REF_PATH = os.path.join(REF_CHAR_DIR, "leo_ref.png")
BIP_REF_PATH = os.path.join(REF_CHAR_DIR, "bip_ref.png")

def obter_projeto_flow_id(root_dir: str = ROOT_DIR) -> str:
    """Recupera o ID do projeto no Google Flow (.flow_project_id ou .env)."""
    env_id = os.getenv("FLOW_PROJECT_ID", "").strip()
    if env_id:
        if "/project/" in env_id:
            return env_id.split("/project/")[1].split("?")[0].split("/")[0]
        return env_id

    caminho_id = os.path.join(root_dir, ".flow_project_id")
    if os.path.exists(caminho_id):
        try:
            with open(caminho_id, "r", encoding="utf-8") as f:
                conteudo = f.read().strip()
                if conteudo:
                    if "/project/" in conteudo:
                        return conteudo.split("/project/")[1].split("?")[0].split("/")[0]
                    return conteudo
        except Exception:
            pass

    return "ad9e40ce-b9d5-4aa8-9af4-b44634ee6324"

from core.browser_utils import garantir_playwright_browsers, lancar_chromium_seguro

def sanitizar_prompt_flow(prompt: str) -> str:
    """
    Higieniza e blinda o prompt para enquadramento 9:16 seguro.
    Se a cena for de Leo, garante âncora visual e enquadramento seguro.
    Se a cena for de cenário/ambiente ('no leo'), NÃO adiciona âncora do Leo nem enquadramento de rosto.
    """
    s = prompt or ""
    # Substitui enquadramentos arriscados que cortam a cabeça ou rosto
    s = re.sub(r"\b(extreme\s+close-?up|macro\s+shot|tight\s+crop|cropped\s+face|extreme\s+portrait)\b", "medium chest-up framing", s, flags=re.IGNORECASE)
    # Remove marcas d'água, menções a texto
    s = re.sub(r"\b(text|letters|watermark|logo|subtitles|caption|words|writing)\b", "", s, flags=re.IGNORECASE)
    s = s.strip(" .,")

    tem_leo = "leo" in s.lower() and "no leo" not in s.lower()
    tem_bip = "bip" in s.lower() and "no bip" not in s.lower()

    ancora_leo = "Leo, 24-year-old stylized 3D Pixar-style man with messy dark brown hair, wearing ONLY a matte black bomber jacket with glowing electric cyan trim, no glasses, medium chest-up framing, character fully centered inside vertical 9:16 frame with generous headroom above hair, face completely visible and uncropped"
    bip_desc = "Bip, a small spherical floating metallic drone with a single glowing cyan digital screen face, NO arms, NO legs, floating in air beside Leo"
    regras_negativas = "no random animals, no unrelated disasters, strictly matching the news topic, vertical 9:16 aspect ratio, 8k resolution, completely devoid of any text, letters, subtitles, watermarks, symbols or borders"

    if tem_leo:
        if "leo, 24-year-old" not in s.lower():
            s = f"{ancora_leo}, {s}"
        if "medium chest-up" not in s.lower() and "wide" not in s.lower() and "shot" not in s.lower():
            s = f"{s}, medium chest-up framing, generous headroom above hair, face completely visible and uncropped"
    else:
        # Cenário 3D cinematográfico puro sem personagem humano
        if "no leo" not in s.lower():
            s = f"{s}, NO human close-up, NO Leo, wide environmental 3D scene"

    if tem_bip and "no arms" not in s.lower():
        s = f"{s}, {bip_desc}"

    if "pixar" not in s.lower() and "blender" not in s.lower():
        prompt_final = f"{s}, 3D Pixar and Blender animation render style, volumetric lighting, {regras_negativas}"
    else:
        prompt_final = f"{s}, {regras_negativas}"

    prompt_final = re.sub(r',\s*,+', ',', prompt_final)
    prompt_final = re.sub(r'\s+', ' ', prompt_final).strip(' ,')
    return prompt_final


class FlowClient:
    """Cliente robusto para interagir com o Google Flow via Playwright com fallback e imagens de referência."""

    def __init__(self, session_path: Optional[str] = None, headless: Optional[bool] = None):
        self.root_dir = ROOT_DIR
        self.session_path = session_path or os.path.join(ROOT_DIR, "sessao_google_flow.json")
        self.project_id = obter_projeto_flow_id(self.root_dir)

        env_headless = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() in ("true", "1", "yes")
        self.headless = env_headless if headless is None else headless

    def sessao_valida(self) -> bool:
        """Verifica se o arquivo de sessão existe e tem tamanho plausível."""
        return os.path.exists(self.session_path) and os.path.getsize(self.session_path) > 100

    def obter_imagens_referencia(self, prompt: Optional[str] = None) -> List[str]:
        """
        Retorna caminhos existentes de folhas de referência dos personagens.
        SÓ anexa leo_ref.png se 'leo' estiver explicitamente no prompt e não for 'no leo'.
        SÓ anexa bip_ref.png se 'bip' estiver no prompt e não for 'no bip'.
        """
        refs = []
        p_low = (prompt or "").lower()

        quer_leo = True if prompt is None else ("leo" in p_low and "no leo" not in p_low)
        quer_bip = True if prompt is None else ("bip" in p_low and "no bip" not in p_low)

        if quer_leo and os.path.exists(LEO_REF_PATH) and os.path.getsize(LEO_REF_PATH) > 10000:
            refs.append(LEO_REF_PATH)
        if quer_bip and os.path.exists(BIP_REF_PATH) and os.path.getsize(BIP_REF_PATH) > 10000:
            refs.append(BIP_REF_PATH)
        return refs

    def gerar_folha_referencia_personagens(self, force: bool = False) -> Dict[str, str]:
        """
        Gera e salva automaticamente a folha de referência oficial de Léo e Bip em assets/characters/
        caso ainda não existam. Chamado via 'python main.py --gerar-referencias'.
        """
        os.makedirs(REF_CHAR_DIR, exist_ok=True)
        resultados = {}

        if not self.sessao_valida():
            logger.warning("[!] Sessão do Google Flow não conectada. Conecte com 'python conectar_google.py'.")
            return resultados

        print("\n" + "=" * 65)
        print("🎨 GERANDO FOLHAS DE REFERÊNCIA OFICIAIS DOS PERSONAGENS (FATOR X)")
        print("=" * 65)

        # 1. Referência Oficial de Léo
        if force or not (os.path.exists(LEO_REF_PATH) and os.path.getsize(LEO_REF_PATH) > 20000):
            print("[*] Renderizando imagem de referência oficial de Léo no Google Flow...")
            prompt_leo = (
                "Character reference sheet and official model turn-around of Leo, a 24-year-old stylized 3D Pixar and Blender animation style young man, "
                "messy dark brown hair, expressive warm brown eyes, wearing ONLY a signature matte black bomber jacket with glowing electric cyan trim, no glasses. "
                "Medium chest-up framing, character fully centered inside vertical 9:16 frame with generous headroom above hair, face completely visible and uncropped, "
                "clean studio lighting, 3D Pixar and Blender animation render, volumetric cinematic lighting, vertical 9:16 framing, ultra sharp 8k, "
                "neutral dark studio background, completely devoid of any text, letters, subtitles, watermarks, symbols or borders"
            )
            res_leo = self.gerar_imagem(prompt_leo, LEO_REF_PATH, retries=3)
            if res_leo and os.path.exists(res_leo):
                print(f"  [✔] Referência de Léo salva: {LEO_REF_PATH}")
                resultados["leo"] = LEO_REF_PATH
            else:
                print("  [!] Falha ao gerar referência de Léo no Flow.")
        else:
            print(f"  [✓] Referência de Léo já existe: {LEO_REF_PATH}")
            resultados["leo"] = LEO_REF_PATH

        # 2. Referência Oficial do Drone Bip
        if force or not (os.path.exists(BIP_REF_PATH) and os.path.getsize(BIP_REF_PATH) > 20000):
            print("[*] Renderizando imagem de referência oficial de Bip no Google Flow...")
            prompt_bip = (
                "Character reference sheet of Bip, a cute small spherical floating 3D metallic drone with a single glowing cyan digital eye screen face, "
                "NO arms, NO legs, clean floating hover pose, futuristic companion, 3D Pixar style render, ultra sharp 8k, volumetric lighting, "
                "vertical 9:16 framing, neutral dark studio background, completely devoid of any text, letters, subtitles, watermarks, symbols or borders"
            )
            res_bip = self.gerar_imagem(prompt_bip, BIP_REF_PATH, retries=3)
            if res_bip and os.path.exists(res_bip):
                print(f"  [✔] Referência de Bip salva: {BIP_REF_PATH}")
                resultados["bip"] = BIP_REF_PATH
            else:
                print("  [!] Falha ao gerar referência de Bip no Flow.")
        else:
            print(f"  [✓] Referência de Bip já existe: {BIP_REF_PATH}")
            resultados["bip"] = BIP_REF_PATH

        print("=" * 65 + "\n")
        return resultados

    def gerar_imagem(self, prompt: str, output_path: str, timeout_segundos: int = 75, retries: int = 2) -> Optional[str]:
        """
        Gera uma imagem no Google Flow com retentativa automática (retries=2) antes de qualquer fallback.
        Garante que cada cena tenha tentativa prioritária de renderizar imagem inédita.
        """
        if not self.sessao_valida():
            logger.info("  [!] Sessão do Google Flow ausente ou vazia. Utilizando fallback de fundo escuro.")
            return None

        prompt_sanitizado = sanitizar_prompt_flow(prompt)
        for tentativa in range(1, retries + 1):
            res = self._tentar_geracao_flow_uma_vez(prompt_sanitizado, output_path, timeout_segundos)
            if res and os.path.exists(res) and os.path.getsize(res) > 25000:
                return res
            if tentativa < retries:
                logger.info(f"  [*] Tentativa {tentativa}/{retries} no Flow falhou. Retentando cena única em 2s...")
                time.sleep(2)
        return None

    def _tentar_geracao_flow_uma_vez(self, prompt_sanitizado: str, output_path: str, timeout_segundos: int = 75) -> Optional[str]:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        project_url = f"https://flow.google.com/project/{self.project_id}"

        # Usa lock para garantir isolamento de processos
        with ProcessLock("flow_generation.lock"):
            try:
                from playwright.sync_api import sync_playwright
            except ImportError:
                logger.warning("  [!] Playwright não importável. Usando fallback.")
                return None

            browser_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage"
            ]

            user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
                if os.name == "nt"
                else "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            )

            try:
                with sync_playwright() as p:
                    browser = lancar_chromium_seguro(p, headless=self.headless, args=browser_args)

                    context = browser.new_context(
                        storage_state=self.session_path,
                        viewport={"width": 1280, "height": 800},
                        user_agent=user_agent
                    )
                    page = context.new_page()
                    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

                    try:
                        page.goto(project_url, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(2500)

                        # 1. Verifica se precisa de login
                        for s in ['button:has-text("Fazer login")', 'button:has-text("Sign in")', 'a:has-text("Fazer login")']:
                            if page.locator(s).count() > 0 and page.locator(s).first.is_visible(timeout=800):
                                logger.warning("  [!] Sessão do Google Flow expirada. Use 'python conectar_google.py'.")
                                return None

                        # 2. Recuperação de 404
                        if "404" in page.url or "reason=project" in page.url:
                            logger.info("  [*] Projeto 404. Tentando recuperar na raiz do Flow...")
                            page.goto("https://flow.google.com/", wait_until="domcontentloaded", timeout=45000)
                            page.wait_for_timeout(2500)
                            btn_novo = page.locator('button:has-text("Novo projeto"), a:has-text("Novo projeto")').first
                            if btn_novo.is_visible(timeout=3000):
                                btn_novo.click(force=True)
                                page.wait_for_timeout(3500)
                            if "/project/" in page.url:
                                novo_id = page.url.split("/project/")[1].split("?")[0].split("/")[0]
                                self.project_id = novo_id
                                with open(os.path.join(self.root_dir, ".flow_project_id"), "w", encoding="utf-8") as f_id:
                                    f_id.write(novo_id.strip())

                        # 3. Fecha visualizador anterior se aberto
                        try:
                            btn_fechar = page.locator('button:has-text("Concluir"), button:has(mat-icon:has-text("arrow_back"))').first
                            if btn_fechar.is_visible(timeout=1000):
                                btn_fechar.click()
                                page.wait_for_timeout(800)
                        except Exception:
                            pass

                        # 4. Modo Imagens
                        try:
                            btn_img = page.locator("button:has-text('Imagens'), button:has(mat-icon:has-text('image'))").first
                            if btn_img.is_visible(timeout=2000):
                                btn_img.click()
                                page.wait_for_timeout(800)
                        except Exception:
                            pass

                        # 5. Mapeia miniaturas pré-existentes
                        seletor_thumb = "img.image, img[src*='/asb/']"

                        def eh_thumb_valido(src: str, cls: str = "") -> bool:
                            if not src:
                                return False
                            s_l, c_l = src.lower(), cls.lower()
                            if any(ign in s_l for ign in ["lh3.googleusercontent.com", "ogw", "gstatic.com", "pr_32px", "cleardot", "avatar"]):
                                return False
                            if "gb_" in c_l:
                                return False
                            return "/asb/" in s_l or "image" in c_l or "googleusercontent.com" in s_l

                        def obter_thumbs():
                            try:
                                return set(page.locator(seletor_thumb).evaluate_all(
                                    "imgs => imgs.map(i => i.src).filter(Boolean)"
                                ))
                            except Exception:
                                return set()

                        srcs_antigos = obter_thumbs()
                        qtd_antes = len(srcs_antigos)

                        # 6. Anexa imagens de referência oficiais (Ingredientes / Subject Reference) se existirem para esta cena
                        refs_existentes = self.obter_imagens_referencia(prompt_sanitizado)
                        if refs_existentes:
                            try:
                                file_input = page.locator('input[type="file"]').first
                                if file_input.count() > 0:
                                    file_input.set_input_files(refs_existentes)
                                    page.wait_for_timeout(1000)
                                    logger.info(f"  [+] {len(refs_existentes)} imagem(ns) de referência anexadas no Flow!")
                            except Exception as e_ref:
                                logger.debug(f"Aviso no anexo de referência: {e_ref}")

                        # 7. Digita o prompt na caixa ProseMirror
                        caixa = page.wait_for_selector('div[role="textbox"], div.ProseMirror', timeout=20000)
                        caixa.click()
                        page.keyboard.press("Control+A")
                        page.keyboard.press("Backspace")
                        page.wait_for_timeout(200)

                        logger.info(f"  -> Inserindo prompt no Flow: {prompt_sanitizado[:75]}...")
                        page.keyboard.type(prompt_sanitizado)
                        page.wait_for_timeout(400)

                        # Dispara geração
                        btn_gerar = page.locator('button[aria-label="Iniciar geração"], button:has-text("arrow_forward")').first
                        if btn_gerar.is_visible(timeout=2000):
                            btn_gerar.click(force=True)
                        else:
                            page.keyboard.press("Enter")

                        # 8. Aguarda nova miniatura renderizar
                        nova_detectada = False
                        for _ in range(timeout_segundos):
                            page.wait_for_timeout(1000)
                            srcs_atuais = obter_thumbs()
                            if len(srcs_atuais) > qtd_antes or (srcs_atuais - srcs_antigos):
                                nova_detectada = True
                                break

                        if not nova_detectada:
                            logger.warning("  [-] Timeout aguardando renderização no Google Flow.")
                            return None

                        page.wait_for_timeout(2000)

                        # 9. Localiza e clica na nova miniatura
                        thumbs = page.locator(seletor_thumb).all()
                        thumb_alvo = None
                        for t in thumbs:
                            try:
                                src = t.get_attribute("src") or ""
                                cls = t.get_attribute("class") or ""
                                if eh_thumb_valido(src, cls) and src not in srcs_antigos:
                                    thumb_alvo = t
                                    break
                            except Exception:
                                pass

                        if not thumb_alvo:
                            logger.warning("  [-] Miniatura da nova imagem não localizada.")
                            return None

                        # Pré-captura URL em alta resolução (Fife 2048px)
                        src_fife = None
                        try:
                            src_attr = thumb_alvo.get_attribute("src")
                            if src_attr and "googleusercontent.com" in src_attr:
                                src_fife = src_attr.split("=")[0] + "=s2048"
                        except Exception:
                            pass

                        thumb_alvo.scroll_into_view_if_needed()
                        thumb_alvo.click(force=True)
                        page.wait_for_timeout(2000)

                        salvo = False
                        # Método A: Download oficial via menu
                        try:
                            btn_dl = page.locator("button[aria-label*='Baixar' i], button:has(mat-icon:has-text('download'))").first
                            if btn_dl.is_visible(timeout=4000):
                                btn_dl.click()
                                page.wait_for_timeout(800)
                                opt = page.locator("button.mat-mdc-menu-item:has-text('Tamanho original'), button.mat-mdc-menu-item").first
                                if opt.is_visible(timeout=4000):
                                    with page.expect_download(timeout=25000) as dl_info:
                                        opt.click(force=True)
                                    dl = dl_info.value
                                    dl.save_as(output_path)
                                    salvo = True
                        except Exception:
                            pass

                        # Método B: Download direto via URL Fife 2048px
                        if not salvo and src_fife:
                            try:
                                resp = page.request.get(src_fife)
                                if resp.status == 200 and len(resp.body()) > 25000:
                                    with open(output_path, "wb") as f_out:
                                        f_out.write(resp.body())
                                    salvo = True
                            except Exception:
                                pass

                        # Fecha modal
                        try:
                            page.locator('button:has-text("Concluir"), button:has(mat-icon:has-text("arrow_back"))').first.click()
                        except Exception:
                            pass

                        if salvo and os.path.exists(output_path) and os.path.getsize(output_path) > 25000:
                            logger.info(f"  [+] Imagem Flow salva: {os.path.basename(output_path)} ({os.path.getsize(output_path)} bytes)")
                            return output_path
                        return None

                    finally:
                        context.close()
                        browser.close()

            except Exception as e:
                logger.warning(f"  [!] Aviso ao gerar no Google Flow: {e}")
                return None

    def gerar_imagens_para_roteiro(
        self,
        cenas: List[Dict[str, Any]],
        output_dir: str,
        max_imagens: Optional[int] = None,
        desativar_imagens: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Gera 1 imagem 3D única por cena para manter ritmo ágil de desenho animado (corte a cada 3.5-4s).
        Se desativar_imagens=True ou se o Flow falhar em alguma cena, faz fallback para a última imagem válida
        ou para o fundo dinâmico escuro, sem interromper o pipeline.
        """
        if desativar_imagens or not self.sessao_valida():
            logger.info("[*] Geração de imagens do Flow desativada ou sem sessão. Usando fundo dinâmico.")
            for c in cenas:
                c["bg_image"] = None
            return cenas

        os.makedirs(output_dir, exist_ok=True)
        total_cenas = len(cenas)
        limite_imgs = max_imagens if (max_imagens is not None and max_imagens > 0) else total_cenas

        logger.info(f"[*] Gerando ilustrações 3D Pixar/Blender no Google Flow (1 imagem única por cena, até {limite_imgs})...")

        imagem_atual = None
        ultima_imagem_valida = None

        for i, cena in enumerate(cenas):
            c_id = cena.get("id", i + 1)
            img_path = os.path.join(output_dir, f"cena_{c_id:02d}_flow.jpg")
            prompt = cena.get("prompt_imagem_en") or f"3D Pixar character acting, scene for: {cena.get('texto_fala', '')}"

            if i < limite_imgs:
                # Se a imagem já foi gerada com sucesso anteriormente nesta pasta, reaproveita
                if os.path.exists(img_path) and os.path.getsize(img_path) > 25000:
                    logger.info(f"  [✓] Imagem existente encontrada para cena {c_id:02d}: {os.path.basename(img_path)}")
                    imagem_atual = os.path.abspath(img_path)
                    ultima_imagem_valida = imagem_atual
                else:
                    logger.info(f"  -> Solicitando imagem 3D única para cena {c_id:02d}/{total_cenas}...")
                    gerou = self.gerar_imagem(prompt, img_path, retries=2)
                    if gerou and os.path.exists(gerou) and os.path.getsize(gerou) > 25000:
                        imagem_atual = os.path.abspath(gerou)
                        ultima_imagem_valida = imagem_atual
                    else:
                        if ultima_imagem_valida:
                            logger.info(f"  [*] Fallback da cena {c_id:02d}: utilizando imagem anterior válida.")
                            imagem_atual = ultima_imagem_valida
                        else:
                            logger.info(f"  [*] Sem imagem ainda para cena {c_id:02d}; utilizando fundo dinâmico.")
                            imagem_atual = None
            else:
                imagem_atual = ultima_imagem_valida or imagem_atual

            cena["bg_image"] = imagem_atual

        return cenas
