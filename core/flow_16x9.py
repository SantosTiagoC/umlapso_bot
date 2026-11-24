#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flow_16x9.py - Cliente Google Flow dedicado para vídeos horizontais 16:9 (Widescreen) para @um.lapso.
- Gera imagens 16:9 cinematográficas para documentários horizontais (YouTube Widescreen).
- Gera e otimiza thumbnail oficial de 1280x720 (< 2MB) no formato JPEG.
- Snapshot estrito antes de submeter o prompt e verificação SHA-256 anti-duplicação.
- Lock de processo compartilhado com o bot de Shorts para impedir colisões de browser.
- Seletor flexível com espera de até 35s e diagnóstico de URL/Título em caso de timeout.
- Trava de segurança (circuit breaker) interrompendo o loop após 3 falhas consecutivas.
"""

import os
import re
import sys
import time
import hashlib
import subprocess
import logging
from typing import Optional, List, Dict, Any, Set, Tuple
from pathlib import Path
from dotenv import load_dotenv
from PIL import Image

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

logger = logging.getLogger("flow_16x9")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from core.lock import ProcessLock
from core.browser_utils import garantir_playwright_browsers, lancar_chromium_seguro

def calcular_sha256(caminho_arquivo: str) -> str:
    """Calcula o hash SHA-256 de um arquivo."""
    h = hashlib.sha256()
    with open(caminho_arquivo, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def obter_projeto_flow_id(root_dir: str = ROOT_DIR) -> str:
    """Recupera o ID do projeto no Google Flow."""
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

    return "5240fab7-daab-46f2-85c9-de14684a1c2d"

def sanitizar_prompt_16x9(prompt: str) -> str:
    """Higieniza e formata o prompt para composição horizontal widescreen 16:9."""
    s = prompt or ""
    s = re.sub(r"\b(text|letters|watermark|logo|subtitles|caption|words|writing)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(vertical|9:16|portrait|mobile|shorts)\b", "", s, flags=re.IGNORECASE)
    s = s.strip(" .,")

    estilo_16x9 = (
        "widescreen 16:9 horizontal landscape composition, cinematic dark surrealism, "
        "atmospheric cosmic sci-fi, volumetric lighting, photorealistic 8k, Unreal Engine 5 render, "
        "deep cinematic shadows, high contrast, eerie and intriguing mood, "
        "completely devoid of any text, letters, subtitles, watermarks, symbols or borders"
    )

    prompt_final = f"{s}, {estilo_16x9}"
    prompt_final = re.sub(r',\s*,+', ',', prompt_final)
    prompt_final = re.sub(r'\s+', ' ', prompt_final).strip(' ,')
    return prompt_final

class Flow16x9Client:
    """Cliente especializado para geração de imagens horizontais 16:9 no Google Flow."""

    def __init__(self, session_path: Optional[str] = None, headless: Optional[bool] = None):
        self.root_dir = ROOT_DIR
        self.session_path = session_path or os.path.join(ROOT_DIR, "sessao_google_flow.json")
        self.project_id = obter_projeto_flow_id(self.root_dir)

        env_headless = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() in ("true", "1", "yes")
        self.headless = env_headless if headless is None else headless

        self.urls_baixadas_sessao: Set[str] = set()
        self.hashes_baixados_sessao: Set[str] = set()

    def sessao_valida(self) -> bool:
        """Verifica se há sessão Google Flow utilizável."""
        return os.path.exists(self.session_path) and os.path.getsize(self.session_path) > 100

    def gerar_imagem(self, prompt: str, output_path: str, timeout_segundos: int = 80, retries: int = 2) -> Optional[str]:
        """Gera imagem 16:9 com garantia de unicidade por SHA-256."""
        if not self.sessao_valida():
            logger.warning("[!] Sessão do Google Flow ausente. Verifique sessao_google_flow.json.")
            return None

        prompt_sanitizado = sanitizar_prompt_16x9(prompt)
        for tentativa in range(1, retries + 1):
            res = self._tentar_geracao_flow_uma_vez(prompt_sanitizado, output_path, timeout_segundos)
            if res and os.path.exists(res) and os.path.getsize(res) > 25000:
                return res
            if tentativa < retries:
                logger.info(f"  [*] Tentativa {tentativa}/{retries} falhou ou gerou duplicado. Retentando em 2s...")
                time.sleep(2)
        return None

    def _tentar_geracao_flow_uma_vez(self, prompt_sanitizado: str, output_path: str, timeout_segundos: int = 80) -> Optional[str]:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        project_url = f"https://flow.google.com/project/{self.project_id}"

        with ProcessLock("flow_generation.lock"):
            try:
                from playwright.sync_api import sync_playwright
            except ImportError:
                logger.warning("[!] Playwright não importável.")
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

                        # 1. Verifica login
                        for s in ['button:has-text("Fazer login")', 'button:has-text("Sign in")', 'a:has-text("Fazer login")']:
                            if page.locator(s).count() > 0 and page.locator(s).first.is_visible(timeout=800):
                                logger.warning("[!] Sessão do Google Flow expirada. Rode 'python conectar_google.py'.")
                                return None

                        # 2. Recuperação de projeto 404 / redirecionamento
                        if "404" in page.url or "reason=project" in page.url:
                            logger.info("  [*] Projeto 404 detectado. Recuperando na raiz do Flow...")
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
                                logger.info(f"  [+] Novo ID de projeto recuperado e salvo: {novo_id}")

                        # 3. Fecha modal anterior se aberto
                        try:
                            btn_fechar = page.locator('button:has-text("Concluir"), button:has(mat-icon:has-text("arrow_back"))').first
                            if btn_fechar.is_visible(timeout=1000):
                                btn_fechar.click()
                                page.wait_for_timeout(800)
                        except Exception:
                            pass

                        # 4. Aba de Imagens
                        try:
                            btn_img = page.locator("button:has-text('Imagens'), button:has(mat-icon:has-text('image'))").first
                            if btn_img.is_visible(timeout=2000):
                                btn_img.click()
                                page.wait_for_timeout(1000)
                        except Exception:
                            pass

                        # 5. Tenta selecionar proporção 16:9 / Paisagem na interface
                        try:
                            for sel_ratio in [
                                'button:has-text("16:9")',
                                'button[aria-label*="16:9"]',
                                'button[aria-label*="Paisagem" i]',
                                'button:has-text("Paisagem")',
                                'button:has-text("Landscape")',
                                'button[aria-label*="Landscape" i]'
                            ]:
                                btn_r = page.locator(sel_ratio).first
                                if btn_r.is_visible(timeout=1200):
                                    btn_r.click(force=True)
                                    page.wait_for_timeout(500)
                                    break
                        except Exception:
                            pass

                        # Aguarda estabilização da galeria
                        page.wait_for_timeout(3000)

                        # 6. Snapshot rigoroso de todos os thumbnails existentes
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

                        snapshot_srcs = obter_thumbs() | self.urls_baixadas_sessao

                        # 7. Aguarda e digita o prompt 16:9 com timeout ampliado e diagnóstico
                        seletor_prompt = 'div[role="textbox"], div.ProseMirror, div[contenteditable="true"], textarea'
                        try:
                            caixa = page.wait_for_selector(seletor_prompt, timeout=35000)
                        except Exception as e_sel:
                            url_atual = page.url
                            titulo_atual = page.title()
                            logger.error(
                                f"[-] Timeout (35000ms) aguardando campo de prompt no Google Flow! "
                                f"URL atual: {url_atual} | Título: {titulo_atual} | Detalhes: {e_sel}"
                            )
                            return None

                        if not caixa:
                            logger.error(f"[-] Campo de prompt não localizado. URL: {page.url} | Título: {page.title()}")
                            return None

                        caixa.click()
                        page.keyboard.press("Control+A")
                        page.keyboard.press("Backspace")
                        page.wait_for_timeout(200)

                        logger.info(f"  -> Inserindo prompt 16:9 no Flow: {prompt_sanitizado[:75]}...")
                        page.keyboard.type(prompt_sanitizado)
                        page.wait_for_timeout(400)

                        btn_gerar = page.locator('button[aria-label="Iniciar geração"], button:has-text("arrow_forward")').first
                        if btn_gerar.is_visible(timeout=2000):
                            btn_gerar.click(force=True)
                        else:
                            page.keyboard.press("Enter")

                        # 8. Aguarda miniatura estritamente nova
                        nova_detectada = False
                        thumb_alvo = None
                        src_alvo = None

                        for _ in range(timeout_segundos):
                            page.wait_for_timeout(1000)
                            srcs_atuais = obter_thumbs()
                            novos_srcs = (srcs_atuais - snapshot_srcs) - self.urls_baixadas_sessao
                            if novos_srcs:
                                thumbs = page.locator(seletor_thumb).all()
                                for t in thumbs:
                                    try:
                                        src = t.get_attribute("src") or ""
                                        cls = t.get_attribute("class") or ""
                                        if eh_thumb_valido(src, cls) and src in novos_srcs:
                                            thumb_alvo = t
                                            src_alvo = src
                                            nova_detectada = True
                                            break
                                    except Exception:
                                        pass
                                if nova_detectada:
                                    break

                        if not nova_detectada or not thumb_alvo:
                            logger.warning("  [-] Nenhuma imagem 16:9 estritamente nova detectada no Flow.")
                            return None

                        page.wait_for_timeout(2000)

                        # URL em alta resolução (Fife 2048px)
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
                        # Download oficial
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

                        # Download direto Fife 2048px
                        if not salvo and src_fife:
                            try:
                                resp = page.request.get(src_fife)
                                if resp.status == 200 and len(resp.body()) > 25000:
                                    with open(output_path, "wb") as f_out:
                                        f_out.write(resp.body())
                                    salvo = True
                            except Exception:
                                pass

                        try:
                            page.locator('button:has-text("Concluir"), button:has(mat-icon:has-text("arrow_back"))').first.click()
                        except Exception:
                            pass

                        # Validação SHA-256 anti-duplicação
                        if salvo and os.path.exists(output_path) and os.path.getsize(output_path) > 25000:
                            sha = calcular_sha256(output_path)
                            if sha in self.hashes_baixados_sessao:
                                logger.warning(f"  [!] Alerta: Imagem repetida detectada (SHA-256 {sha[:8]}). Rejeitando.")
                                try:
                                    os.remove(output_path)
                                except Exception:
                                    pass
                                return None

                            self.hashes_baixados_sessao.add(sha)
                            if src_alvo:
                                self.urls_baixadas_sessao.add(src_alvo)

                            tamanho_kb = os.path.getsize(output_path) / 1024
                            logger.info(f"  [+] Imagem 16:9 salva: {os.path.basename(output_path)} | {tamanho_kb:.1f} KB | SHA-256: {sha[:8]}")
                            return output_path

                        return None

                    finally:
                        context.close()
                        browser.close()

            except Exception as e:
                logger.warning(f"  [!] Erro no Flow 16:9: {e}")
                return None

    def gerar_thumbnail_youtube(self, prompt_thumbnail: str, output_jpg: str) -> Optional[str]:
        """
        Gera e redimensiona a thumbnail oficial do YouTube em 1280x720 (< 2MB JPEG).
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_jpg)), exist_ok=True)
        raw_thumb_path = output_jpg + ".raw.png"

        logger.info("[*] Gerando capa de alto impacto (Thumbnail 16:9) no Google Flow...")
        res_raw = self.gerar_imagem(prompt_thumbnail, raw_thumb_path, retries=2)
        if not res_raw or not os.path.exists(res_raw):
            logger.warning("[!] Falha ao gerar thumbnail exclusiva no Flow.")
            return None

        try:
            with Image.open(res_raw) as img:
                img_rgb = img.convert("RGB")
                img_resized = img_rgb.resize((1280, 720), Image.Resampling.LANCZOS)
                img_resized.save(output_jpg, "JPEG", quality=92, optimize=True)

            if os.path.exists(raw_thumb_path):
                os.remove(raw_thumb_path)

            tam_mb = os.path.getsize(output_jpg) / (1024 * 1024)
            logger.info(f"[+] Thumbnail 1280x720 criada com sucesso: {output_jpg} ({tam_mb:.2f} MB)")
            return output_jpg
        except Exception as e:
            logger.warning(f"[!] Erro ao formatar thumbnail com Pillow: {e}")
            return res_raw

    def gerar_imagens_para_roteiro_longo(
        self,
        cenas: List[Dict[str, Any]],
        output_dir: str,
        prompt_thumbnail: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Gera 1 imagem 16:9 inédita para cada cena (22 a 32 cenas) e a thumbnail 1280x720.
        Possui trava de falhas consecutivas (circuit breaker) abortando após 3 falhas seguidas.
        Retorna (cenas_atualizadas_com_uri, caminho_thumbnail_jpg).
        """
        os.makedirs(output_dir, exist_ok=True)
        total_cenas = len(cenas)
        logger.info(f"[*] Gerando ilustrações 16:9 no Google Flow ({total_cenas} cenas para vídeo longo)...")

        falhas_consecutivas = 0
        LIMITE_FALHAS_CONSECUTIVAS = 3

        # 1. Gera Thumbnail do YouTube primeiro
        thumbnail_path = os.path.join(output_dir, "thumbnail.jpg")
        if prompt_thumbnail:
            res_thumb = self.gerar_thumbnail_youtube(prompt_thumbnail, thumbnail_path)
            if not res_thumb:
                falhas_consecutivas += 1
                logger.warning(f"  [-] Falha ao gerar thumbnail no Flow ({falhas_consecutivas}/{LIMITE_FALHAS_CONSECUTIVAS} falhas consecutivas).")
        else:
            thumbnail_path = None

        # 2. Gera imagem para cada cena
        imagem_atual = None
        ultima_imagem_valida = None

        for i, cena in enumerate(cenas):
            c_id = cena.get("id", i + 1)
            img_path = os.path.join(output_dir, f"cena_{c_id:02d}_flow16x9.jpg")
            prompt = cena.get("prompt_imagem_en") or f"widescreen 16:9 cinematic horizontal scene: {cena.get('fala_pt', '')}"

            if os.path.exists(img_path):
                try:
                    os.remove(img_path)
                except Exception:
                    pass

            logger.info(f"  -> Solicitando imagem 16:9 para cena {c_id:02d}/{total_cenas}...")
            gerou = self.gerar_imagem(prompt, img_path, retries=2)

            if gerou and os.path.exists(gerou) and os.path.getsize(gerou) > 25000:
                falhas_consecutivas = 0
                sha = calcular_sha256(gerou)
                tamanho_kb = os.path.getsize(gerou) / 1024
                logger.info(f"  [✔] Cena {c_id:02d} OK: {os.path.basename(gerou)} | {tamanho_kb:.1f} KB | SHA-256: {sha[:8]}")
                imagem_atual = Path(os.path.abspath(gerou)).resolve().as_uri()
                ultima_imagem_valida = imagem_atual
            else:
                falhas_consecutivas += 1
                logger.warning(
                    f"  [-] Falha na imagem da cena {c_id:02d} "
                    f"({falhas_consecutivas}/{LIMITE_FALHAS_CONSECUTIVAS} falhas consecutivas)."
                )

                if falhas_consecutivas >= LIMITE_FALHAS_CONSECUTIVAS:
                    logger.error(
                        f"[!!!] CIRCUIT BREAKER ATIVADO: {falhas_consecutivas} falhas consecutivas no seletor/sessão do Google Flow. "
                        f"Interrompendo loop de geração para evitar bloqueio e timeouts prolongados."
                    )
                    # Preenche a cena atual e as restantes com fallback ou None
                    for j in range(i, total_cenas):
                        cenas[j]["bg_image"] = ultima_imagem_valida
                    break

                if ultima_imagem_valida:
                    logger.warning(f"  [!] Fallback cena {c_id:02d}: utilizando imagem anterior válida.")
                    imagem_atual = ultima_imagem_valida
                else:
                    imagem_atual = None

            cena["bg_image"] = imagem_atual

        return cenas, thumbnail_path
