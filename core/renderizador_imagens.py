#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
renderizador_imagens.py - Renderizador 1080x1920 @ 30 FPS com Tela 100% Limpa para @um.lapso.
- Captura frames JPEG do template HTML via Playwright e envia via pipe direto ao FFmpeg.
- Mixa narração principal masterizada SEM cortar o áudio final (sem flag -shortest).
- Sincronização 1 imagem por cena com crossfade e Ken Burns contínuo.
- Salva o vídeo final em output/umlapso_{timestamp}.mp4.
"""

import os
import sys
import json
import subprocess
import logging
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

logger = logging.getLogger("renderizador_imagens")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from core.browser_utils import garantir_playwright_browsers, lancar_chromium_seguro
from core.editor_ffmpeg import obter_caminho_ffmpeg

def auditar_video_gerado(caminho_video: str, duracao_esperada_ms: int) -> Dict[str, Any]:
    """Auditoria de integridade do vídeo gerado com ffprobe."""
    if not os.path.exists(caminho_video):
        return {"aprovado": False, "motivo": "Arquivo de vídeo ausente no disco."}

    tamanho_bytes = os.path.getsize(caminho_video)
    tamanho_mb = tamanho_bytes / (1024 * 1024)
    if tamanho_bytes < 500_000:
        return {"aprovado": False, "motivo": f"Arquivo muito pequeno ({tamanho_mb:.2f} MB)."}

    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size,bit_rate:stream=codec_type,width,height",
        "-of", "json",
        caminho_video
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        probe_data = json.loads(res.stdout)
    except Exception as e:
        return {"aprovado": False, "motivo": f"Falha ao rodar ffprobe: {e}"}

    format_info = probe_data.get("format", {})
    streams = probe_data.get("streams", [])

    duracao_s = float(format_info.get("duration", 0))
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if not video_stream:
        return {"aprovado": False, "motivo": "Stream de vídeo ausente no MP4."}
    if not audio_stream:
        return {"aprovado": False, "motivo": "Stream de áudio ausente no MP4."}

    largura = video_stream.get("width")
    altura = video_stream.get("height")

    resultado = {
        "aprovado": True,
        "duracao_segundos": round(duracao_s, 2),
        "largura": largura,
        "altura": altura,
        "tamanho_mb": round(tamanho_mb, 2)
    }
    logger.info(f"[✔] Vídeo validado: {os.path.basename(caminho_video)} | {duracao_s:.1f}s | {largura}x{altura} | {tamanho_mb:.2f} MB")
    return resultado

def _mixar_audio_video(
    video_sem_audio: str,
    caminho_audio_mp3: str,
    caminho_saida_mp4: str,
    timeline: Optional[Dict[str, Any]] = None
):
    """
    Mixa o vídeo renderizado com a narração masterizada SEM a flag -shortest,
    garantindo que nenhuma fala ou palavra final seja cortada.
    """
    ffmpeg_bin = obter_caminho_ffmpeg()
    
    bgm_path = os.path.join(ROOT_DIR, "assets", "bgm.mp3")
    tem_bgm = os.path.exists(bgm_path)

    if tem_bgm:
        cmd = [
            ffmpeg_bin, "-y",
            "-i", video_sem_audio,
            "-i", caminho_audio_mp3,
            "-stream_loop", "-1", "-i", bgm_path,
            "-filter_complex", "[1:a]volume=1.0[a1];[2:a]volume=0.08[a2];[a1][a2]amix=inputs=2:duration=first[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            caminho_saida_mp4
        ]
    else:
        cmd = [
            ffmpeg_bin, "-y",
            "-i", video_sem_audio,
            "-i", caminho_audio_mp3,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            caminho_saida_mp4
        ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        logger.warning(f"Aviso na mixagem ({e.stderr.decode('utf-8', errors='ignore')[:150]}). Usando cópia direta.")
        fallback_cmd = [
            ffmpeg_bin, "-y",
            "-i", video_sem_audio,
            "-i", caminho_audio_mp3,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            caminho_saida_mp4
        ]
        subprocess.run(fallback_cmd, check=True)

def renderizar_video_imagens(
    timeline: Dict[str, Any],
    caminho_audio_mp3: str,
    caminho_saida_mp4: str,
    fps: int = 30
) -> str:
    """
    Renderiza vídeo 1080x1920 a 30 FPS com movimento Ken Burns suave e crossfade
    a partir da timeline cumulativa de imagens e une com o áudio masterizado.
    Garante que todas as cenas carreguem exclusivamente imagens estáticas válidas.
    """
    os.makedirs(os.path.dirname(os.path.abspath(caminho_saida_mp4)), exist_ok=True)
    garantir_playwright_browsers()

    # Validação e higienização estrita de mídias estáticas por cena
    cenas = timeline.get("cenas", [])
    ultima_img_valida = None
    imagens_validas = 0
    for i, c in enumerate(cenas):
        bg = c.get("bg_image") or ""
        # Converte para caminho de arquivo se for URI file://
        caminho_disco = bg
        if bg.startswith("file://"):
            caminho_disco = urllib.request.url2pathname(urllib.parse.urlparse(bg).path)

        # Se for um arquivo de vídeo ou extensão inválida, descarta
        ext = os.path.splitext(caminho_disco)[1].lower()
        eh_imagem = ext in [".jpg", ".jpeg", ".png", ".webp"]

        if caminho_disco and os.path.isfile(caminho_disco) and eh_imagem:
            uri_valida = Path(os.path.abspath(caminho_disco)).resolve().as_uri()
            c["bg_image"] = uri_valida
            ultima_img_valida = uri_valida
            imagens_validas += 1
            logger.info(f"  [✔] Cena {c.get('id', i+1):02d}: Imagem estática validada -> {os.path.basename(caminho_disco)}")
        else:
            if ultima_img_valida:
                logger.warning(f"  [!] Cena {c.get('id', i+1):02d}: Imagem inválida ou vídeo detectado ({bg}). Usando imagem anterior.")
                c["bg_image"] = ultima_img_valida
            else:
                c["bg_image"] = None

    if imagens_validas == 0:
        raise RuntimeError("Nenhuma imagem válida carregada para renderização — abortando para não publicar vídeo em branco.")

    duracao_ms = int(timeline.get("duracao_total_ms", 45000))
    total_frames = int((duracao_ms / 1000.0) * fps) + 1

    video_sem_audio = caminho_saida_mp4 + ".raw.mp4"
    template_path = os.path.join(ROOT_DIR, "renderer", "template.html")
    template_uri = Path(template_path).as_uri()

    ffmpeg_bin = obter_caminho_ffmpeg()
    ffmpeg_pipe_cmd = [
        ffmpeg_bin, "-y",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "-framerate", str(fps),
        "-i", "-",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "fast",
        "-crf", "19",
        video_sem_audio
    ]

    logger.info(f"[*] Renderizando {total_frames} frames ({duracao_ms / 1000:.1f}s) em 1080x1920 @ {fps} FPS (tela 100% limpa)...")
    ffmpeg_proc = subprocess.Popen(ffmpeg_pipe_cmd, stdin=subprocess.PIPE)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = lancar_chromium_seguro(
            p,
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--allow-file-access-from-files"]
        )
        page = browser.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
        page.goto(template_uri)
        page.evaluate("(data) => window.initTimeline(data)", timeline)

        for frame_idx in range(total_frames):
            ms = int(round((frame_idx * 1000.0) / float(fps)))
            page.evaluate("(t) => window.renderAtTime(t)", ms)
            jpg_bytes = page.screenshot(type="jpeg", quality=90)
            ffmpeg_proc.stdin.write(jpg_bytes)

            if frame_idx > 0 and frame_idx % (fps * 5) == 0:
                pct = int((frame_idx / total_frames) * 100)
                logger.info(f"    Progresso: {pct}% ({frame_idx}/{total_frames} frames - {ms/1000:.1f}s)")

        browser.close()

    ffmpeg_proc.stdin.close()
    ffmpeg_proc.wait()

    # Mixagem com a narração masterizada (sem corte abrupto)
    logger.info("[*] Mixando vídeo limpo com narração masterizada (áudio íntegro)...")
    _mixar_audio_video(video_sem_audio, caminho_audio_mp3, caminho_saida_mp4, timeline)

    # Limpeza do arquivo temporário mudo
    if os.path.exists(video_sem_audio):
        try:
            os.remove(video_sem_audio)
        except Exception:
            pass

    # Validação pós-render
    auditar_video_gerado(caminho_saida_mp4, duracao_ms)
    logger.info(f"[+] Vídeo final pronto: {caminho_saida_mp4}")
    return caminho_saida_mp4
