#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tiktok_publisher.py - Módulo blindado de publicação e agendamento no TikTok via Playwright.
Baseado na arquitetura de produção validada do tiktok-viral-engine.
"""

import os
import sys
import json
import time
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

logger = logging.getLogger("tiktok_publisher")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

LOGS_DIR = os.path.join(ROOT_DIR, "logs")

from core.playwright_utils import garantir_playwright_browsers
from core.lock import ProcessLock
from core.anti_duplicidade import verificar_duplicidade, registrar_publicacao_sucesso

class TikTokPublisher:
    """Módulo autônomo e blindado de publicação no TikTok via Playwright."""

    def __init__(self, session_path: Optional[str] = None, headless: Optional[bool] = None):
        caminho_especificado = session_path or os.getenv("TIKTOK_SESSION_PATH")
        if caminho_especificado and os.path.exists(caminho_especificado):
            self.session_path = Path(caminho_especificado)
        else:
            candidatos = [
                Path(ROOT_DIR) / "sessao_tiktok.json",
                Path(ROOT_DIR) / "config" / "tiktok_session.json",
                Path(ROOT_DIR) / "config" / "sessao_tiktok.json",
                Path(ROOT_DIR) / "tiktok_session.json",
            ]
            self.session_path = candidatos[0]
            for c in candidatos:
                if c.exists() and c.stat().st_size > 100:
                    self.session_path = c
                    break

        env_headless = os.getenv("PLAYWRIGHT_HEADLESS", "").lower()
        if headless is not None:
            self.headless = headless
        elif env_headless in ("false", "0", "no"):
            self.headless = False
        else:
            self.headless = True

        os.makedirs(LOGS_DIR, exist_ok=True)

    def sessao_existe(self) -> bool:
        return self.session_path.exists() and self.session_path.stat().st_size > 100

    def _obter_caminho_ffmpeg(self) -> str:
        import shutil
        f = shutil.which("ffmpeg")
        if f:
            return f
        for c in [
            os.path.join(ROOT_DIR, "ffmpeg.exe"),
            os.path.join(ROOT_DIR, "ffmpeg"),
            os.path.join(ROOT_DIR, "core", "ffmpeg"),
        ]:
            if os.path.isfile(c):
                return c
        return "ffmpeg"

    def _obter_duracao_video_segundos(self, video_path: str) -> float:
        """Mede a duração exata do arquivo de vídeo em segundos via ffprobe."""
        if not video_path or not os.path.exists(video_path):
            return 0.0
        
        # Tenta ffprobe
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(res.stdout.strip())
        except Exception:
            pass

        # Fallback OpenCV se instalado
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS)
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            cap.release()
            if fps > 0:
                return float(frames / fps)
        except Exception:
            pass

        return 0.0

    def _tratar_modal_confirmacao_post(self, page) -> bool:
        """
        Clica automaticamente em botões de confirmação quando o TikTok Studio exibe modais como:
        'Continue to post? We're still checking your video for potential issues. [Post now]'
        """
        seletores_confirmar = [
            'div[role="dialog"] button:has-text("Post now")',
            'div[role="dialog"] button:has-text("Publicar agora")',
            'div[role="dialog"] button:has-text("Post anyway")',
            'div[role="dialog"] button:has-text("Publicar mesmo assim")',
            'div[role="dialog"] button:has-text("Post")',
            'div[role="dialog"] button:has-text("Publicar")',
            'button:has-text("Post now")',
            'button:has-text("Publicar agora")',
            'button:has-text("Post anyway")',
            'button:has-text("Publicar mesmo assim")'
        ]
        for sel in seletores_confirmar:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible(timeout=500):
                    txt = loc.first.inner_text().strip()
                    if "cancel" not in txt.lower() and "não" not in txt.lower():
                        logger.info(f"[TikTok] Modal de confirmação detectado ('{txt}'). Confirmando publicação...")
                        loc.first.click()
                        page.wait_for_timeout(1000)
                        return True
            except Exception:
                pass
        return False

    def _tratar_popups_estudio(self, page):
        """Trata e fecha pop-ups e modais bloqueantes no TikTok Studio (PT-BR e EN)."""
        # Trata pop-up de 'Deseja cancelar o envio?'
        try:
            modal_cancel = page.locator('text="Sure you want to cancel", text="cancel your upload", text="cancelar o envio"')
            if modal_cancel.count() > 0 and modal_cancel.first.is_visible(timeout=400):
                btn_no = page.locator('button:has-text("No"), button:has-text("Não")').first
                if btn_no.is_visible(timeout=400):
                    btn_no.click()
                    page.wait_for_timeout(300)
        except Exception:
            pass

        seletores_popup = [
            'button:has-text("Turn on")',
            'button:has-text("Ativar")',
            'button:has-text("Entendi")',
            'button:has-text("Got it")',
            'button:has-text("OK")',
            'button:has-text("Ok")',
            'button:has-text("Continuar")',
            'button:has-text("Continue")',
            'button:has-text("Fechar")',
            'button:has-text("Close")',
            'button:has-text("Agora não")',
            'button:has-text("Not now")',
            'button[aria-label="Close"]',
            'button[aria-label="Fechar"]'
        ]

        for sel in seletores_popup:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible(timeout=400):
                    loc.first.click()
                    page.wait_for_timeout(300)
            except Exception:
                pass

    def verificar_sessao(self, timeout_ms: int = 30000) -> Dict[str, Any]:
        """Verifica se os cookies do TikTok ainda estão autenticados no TikTok Studio."""
        if not self.sessao_existe():
            return {
                "autenticado": False,
                "motivo": f"Arquivo de sessão não encontrado: {self.session_path}",
                "url": None
            }

        garantir_playwright_browsers()
        from playwright.sync_api import sync_playwright

        logger.info(f"[*] Verificando autenticação da sessão TikTok ({self.session_path.name})...")
        try:
            with sync_playwright() as p:
                args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
                browser = p.chromium.launch(headless=self.headless, args=args)
                context = browser.new_context(
                    storage_state=str(self.session_path),
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
                )
                page = context.new_page()
                target_url = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception:
                    page.goto("https://www.tiktok.com/creator-center/upload", wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(3500)

                url_final = page.url
                is_login = "login" in url_final.lower() or page.locator("form#loginContainer, input[type='password']").count() > 0
                tem_input_file = page.locator("input[type='file']").count() > 0

                browser.close()

                if is_login:
                    return {
                        "autenticado": False,
                        "motivo": "Sessão expirada — rode python conectar_tiktok.py",
                        "url": url_final
                    }

                return {
                    "autenticado": True,
                    "motivo": "Sessão válida e pronta para upload.",
                    "url": url_final,
                    "pronto_para_upload": tem_input_file
                }
        except Exception as e:
            logger.error(f"[!] Erro ao verificar sessão do TikTok: {e}")
            return {
                "autenticado": False,
                "motivo": f"Falha na checagem: {e}",
                "url": None
            }

    def publicar(
        self,
        video_path: str,
        legenda: str,
        agendar: Optional[str] = None,
        timeout_upload_segundos: int = 120,
        callback_status: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Publica um vídeo pronto no TikTok com arquitetura blindada.
        """
        inicio_ts = time.time()
        video_path_obj = Path(video_path)

        def _notificar(msg: str):
            if callback_status and callable(callback_status):
                try:
                    callback_status(msg)
                except Exception:
                    pass

        # 1. Validação do arquivo
        if not video_path_obj.exists() or video_path_obj.stat().st_size < 30000:
            return {
                "sucesso": False,
                "status": "erro",
                "mensagem": f"Arquivo de vídeo inválido ou inexistente: {video_path}"
            }

        # 2. Trava anti-duplicidade estrita
        is_dup, motivo_dup = verificar_duplicidade(str(video_path_obj), "tiktok")
        if is_dup:
            msg_skip = f"[SKIP] Vídeo já publicado no TikTok: {video_path_obj.name} ({motivo_dup})"
            logger.warning(msg_skip)
            print(msg_skip)
            return {
                "sucesso": True,
                "status": "SKIPPED",
                "mensagem": msg_skip,
                "video": video_path_obj.name
            }

        # 3. Duração mínima do vídeo via ffprobe
        duracao_segundos = self._obter_duracao_video_segundos(str(video_path_obj.resolve()))
        if duracao_segundos > 0 and duracao_segundos < 3.0:
            logger.error(f"[TikTok] Publicação abortada: vídeo com apenas {duracao_segundos:.1f}s (< 3.0s mínimo).")
            return {
                "sucesso": False,
                "status": "video_muito_curto",
                "mensagem": f"Vídeo com duração insuficiente ({duracao_segundos:.1f}s < 3.0s). Publicação abortada por segurança."
            }

        # 4. Checagem do arquivo de sessão
        if not self.sessao_existe():
            return {
                "sucesso": False,
                "status": "sessao_inexistente",
                "mensagem": f"Arquivo de sessão não encontrado em {self.session_path}. Sessão expirada — rode python conectar_tiktok.py"
            }

        garantir_playwright_browsers()

        with ProcessLock("tiktok_upload.lock"):
            from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

            logger.info(f"[TikTok] Iniciando upload automatizado: {video_path_obj.name}")
            _notificar("🌐 [1/4] Acessando o TikTok Studio...")

            screenshot_erro = os.path.join(LOGS_DIR, "debug_tiktok_erro.png")
            url_falha: str = ""

            try:
                with sync_playwright() as p:
                    args = [
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage"
                    ]
                    browser = p.chromium.launch(headless=self.headless, args=args)
                    context = browser.new_context(
                        storage_state=str(self.session_path),
                        viewport={"width": 1280, "height": 850},
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
                    )
                    page = context.new_page()

                    def _salvar_screenshot_diagnostico(motivo: str):
                        nonlocal url_falha
                        url_falha = page.url
                        try:
                            page.screenshot(path=screenshot_erro)
                            logger.warning(f"[TikTok] 📸 Screenshot de diagnóstico salvo: {screenshot_erro} | URL: {url_falha} | Motivo: {motivo}")
                        except Exception as e_sc:
                            logger.debug(f"Falha ao tirar screenshot: {e_sc}")

                    try:
                        # 1. Navega para o TikTok Studio
                        target_url = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
                        logger.info(f"[*] Navegando para: {target_url}")
                        try:
                            page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                        except Exception:
                            logger.info("[*] Fallback para /creator-center/upload...")
                            page.goto("https://www.tiktok.com/creator-center/upload", wait_until="domcontentloaded", timeout=45000)

                        page.wait_for_timeout(4000)
                        self._tratar_popups_estudio(page)

                        # Detecção de sessão expirada ou captcha
                        url_atual = page.url.lower()
                        tem_login = "login" in url_atual or page.locator("form#loginContainer, input[type='password']").count() > 0
                        tem_captcha = page.locator('div[class*="captcha"], #captcha_container, div[id*="secsdk"]').count() > 0

                        if tem_login or tem_captcha:
                            _salvar_screenshot_diagnostico("Sessao expirada ou captcha")
                            browser.close()
                            raise RuntimeError("Sessão expirada — rode python conectar_tiktok.py")

                        # 2. Localiza input de arquivo
                        input_file = None
                        if page.locator("input[type='file']").count() > 0:
                            input_file = page.locator("input[type='file']").first
                        else:
                            for frame in page.frames:
                                if frame.locator("input[type='file']").count() > 0:
                                    input_file = frame.locator("input[type='file']").first
                                    break

                        if not input_file:
                            _salvar_screenshot_diagnostico("Input de arquivo ausente")
                            browser.close()
                            raise RuntimeError(f"Campo de seleção de arquivo não encontrado na página (URL: {page.url}).")

                        _notificar("📤 [2/4] Enviando arquivo de vídeo...")
                        input_file.set_input_files(str(video_path_obj.resolve()))
                        page.wait_for_timeout(3000)
                        self._tratar_popups_estudio(page)

                        # 3. Preenchimento da legenda SEO + hashtags
                        _notificar("✍️ [3/4] Inserindo legenda e hashtags...")
                        legenda_box = None
                        seletores_legenda = [
                            '.public-DraftEditor-content',
                            'div[contenteditable="true"]',
                            'div[role="combobox"]',
                            'textarea'
                        ]
                        for s_leg in seletores_legenda:
                            try:
                                loc = page.locator(s_leg).first
                                if loc.is_visible(timeout=3000):
                                    legenda_box = loc
                                    break
                            except Exception:
                                pass

                        if legenda_box:
                            try:
                                legenda_box.click(timeout=3000)
                                page.wait_for_timeout(200)
                                page.keyboard.press("Control+A")
                                page.keyboard.press("Backspace")
                                page.wait_for_timeout(200)
                                page.keyboard.insert_text(legenda.strip()[:2000])
                                page.wait_for_timeout(300)
                                page.keyboard.press("Escape")
                                page.wait_for_timeout(400)
                            except Exception as e_leg:
                                logger.warning(f"Aviso ao preencher legenda: {e_leg}")
                        else:
                            logger.warning("  [-] Caixa de legenda não localizada com precisão.")

                        self._tratar_popups_estudio(page)

                        # 4. Aguarda upload 100% concluído
                        _notificar("⏳ [4/4] Aguardando processamento do vídeo pelo TikTok Studio...")
                        upload_concluido = False
                        tempo_limite_upload = time.time() + float(timeout_upload_segundos)

                        seletor_btn_pub = 'button[data-e2e="post_video_button"], button:has-text("Publicar"), button:has-text("Post"), div[role="button"]:has-text("Publicar"), div[role="button"]:has-text("Post")'

                        while time.time() < tempo_limite_upload:
                            self._tratar_popups_estudio(page)

                            tem_preview = page.locator('video, .preview-video, .player-container, div[class*="preview"], div[class*="player"]').count() > 0
                            tem_texto_uploaded = page.locator('text="Carregado", text="Uploaded", text="100%", text="Upload complete"').count() > 0

                            btn_pub_teste = page.locator(seletor_btn_pub).last
                            btn_habilitado = False
                            if btn_pub_teste.count() > 0:
                                try:
                                    btn_habilitado = btn_pub_teste.is_enabled()
                                except Exception:
                                    pass

                            if (tem_preview or tem_texto_uploaded) and btn_habilitado:
                                upload_concluido = True
                                logger.info("  [✔] Upload do vídeo concluído e confirmado no TikTok Studio!")
                                break

                            page.wait_for_timeout(2000)

                        if not upload_concluido:
                            btn_final_chk = page.locator(seletor_btn_pub).last
                            if btn_final_chk.count() > 0 and btn_final_chk.is_enabled():
                                upload_concluido = True
                            else:
                                _salvar_screenshot_diagnostico("Timeout aguardando conclusao do upload")
                                browser.close()
                                raise RuntimeError("Timeout aguardando a barra de upload do vídeo atingir 100%.")

                        # 5. Clica no botão Publicar
                        self._tratar_popups_estudio(page)
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(1000)

                        botao_publicar = page.locator(seletor_btn_pub).last
                        _notificar("🚀 Clicando no botão Publicar...")
                        botao_publicar.scroll_into_view_if_needed()
                        page.wait_for_timeout(500)
                        botao_publicar.click(timeout=10000)
                        page.wait_for_timeout(2000)

                        self._tratar_modal_confirmacao_post(page)

                        # 6. Aguarda confirmação real de publicação
                        publicacao_sucesso = False
                        tempo_limite_confirmacao = time.time() + 60.0

                        while time.time() < tempo_limite_confirmacao:
                            self._tratar_popups_estudio(page)
                            self._tratar_modal_confirmacao_post(page)

                            url_check = page.url.lower()
                            if ("content" in url_check or "manage" in url_check or "posts" in url_check) and "upload" not in url_check:
                                publicacao_sucesso = True
                                break

                            try:
                                sucesso_loc = page.locator(
                                    'text="Seu vídeo foi publicado", text="Seu vídeo foi carregado", text="Manage your posts", '
                                    'text="Upload another video", text="Publicações", text="Your video has been uploaded", '
                                    'text="Gerenciar publicações", text="Gerenciar vídeos", text="Video uploaded", '
                                    'text="Seu vídeo está sendo processado", text="Publicado com sucesso"'
                                )
                                if sucesso_loc.count() > 0 and sucesso_loc.first.is_visible(timeout=400):
                                    publicacao_sucesso = True
                                    break
                            except Exception:
                                pass

                            page.wait_for_timeout(1500)

                        if not publicacao_sucesso:
                            if self._tratar_modal_confirmacao_post(page):
                                page.wait_for_timeout(3000)
                                url_pos_modal = page.url.lower()
                                if ("content" in url_pos_modal or "manage" in url_pos_modal) and "upload" not in url_pos_modal:
                                    publicacao_sucesso = True

                        if not publicacao_sucesso:
                            _salvar_screenshot_diagnostico("Nao confirmou publicacao")
                            browser.close()
                            raise RuntimeError(f"O TikTok não confirmou a publicação (URL: {page.url}).")

                        duracao_total = round(time.time() - inicio_ts, 1)
                        logger.info(f"[TikTok] ✅ Publicação concluída com sucesso em {duracao_total}s!")

                        # Salva cookies atualizados
                        try:
                            context.storage_state(path=str(self.session_path))
                            caminho_alt = Path(ROOT_DIR) / ("sessao_tiktok.json" if "config" in str(self.session_path) else "config/tiktok_session.json")
                            os.makedirs(caminho_alt.parent, exist_ok=True)
                            context.storage_state(path=str(caminho_alt))
                        except Exception:
                            pass

                        browser.close()

                        # Registra no histórico anti-duplicidade
                        registrar_publicacao_sucesso(str(video_path_obj), "tiktok", {
                            "video": video_path_obj.name,
                            "duracao_segundos": duracao_total,
                            "legenda": legenda[:100]
                        })

                        return {
                            "sucesso": True,
                            "status": "SUCCESS",
                            "mensagem": "Upload e publicação concluídos com sucesso!",
                            "video": video_path_obj.name,
                            "duracao_segundos": duracao_total,
                            "timestamp": int(time.time())
                        }

                    except PlaywrightTimeoutError as e_timeout:
                        _salvar_screenshot_diagnostico(f"Timeout Playwright: {e_timeout}")
                        browser.close()
                        raise RuntimeError(f"Timeout na interface do TikTok Studio: {e_timeout}")

                    except Exception as e_int:
                        _salvar_screenshot_diagnostico(f"Erro interno: {e_int}")
                        try:
                            browser.close()
                        except Exception:
                            pass
                        raise

            except Exception as e_final:
                duracao_total = round(time.time() - inicio_ts, 1)
                logger.error(f"[TikTok] ❌ Falha no upload: {e_final} (Screenshot: {screenshot_erro})")
                return {
                    "sucesso": False,
                    "status": "ERRO",
                    "erro": str(e_final),
                    "mensagem": str(e_final),
                    "screenshot": screenshot_erro,
                    "video": video_path_obj.name,
                    "duracao_segundos": duracao_total,
                    "timestamp": int(time.time())
                }

def publicar_no_tiktok(video_path: str, legenda: str, agendar: Optional[str] = None) -> Dict[str, Any]:
    publisher = TikTokPublisher()
    return publisher.publicar(video_path=video_path, legenda=legenda, agendar=agendar)

def verificar_status_tiktok() -> Dict[str, Any]:
    publisher = TikTokPublisher()
    return publisher.verificar_sessao()
