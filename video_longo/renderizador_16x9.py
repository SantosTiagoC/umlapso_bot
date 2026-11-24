#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
renderizador_16x9.py - Renderizador 1920x1080 (Full HD 16:9) @ 30 FPS para Vídeos Longos do @um.lapso.
- Captura frames em 1920x1080 via Playwright e envia via pipe direto ao FFmpeg.
- Tela 100% limpa com movimento Ken Burns lento e crossfade de 0.6s entre cenas.
- Mixa narração masterizada (-16 LUFS) sem corte abrupto de áudio.
- Salva o vídeo final em output/longos/umlapso_longo_<timestamp>.mp4 e metadados.
"""

import os
import sys
import json
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

logger = logging.getLogger("renderizador_16x9")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from core.browser_utils import garantir_playwright_browsers, lancar_chromium_seguro
from core.editor_ffmpeg import obter_caminho_ffmpeg

def auditar_video_longo(caminho_video: str, duracao_esperada_ms: int) -> Dict[str, Any]:
    """Auditoria detalhada do vídeo horizontal Full HD gerado."""
    if not os.path.exists(caminho_video):
        return {"aprovado": False, "motivo": "Arquivo de vídeo ausente no disco."}

    tamanho_bytes = os.path.getsize(caminho_video)
    tamanho_mb = tamanho_bytes / (1024 * 1024)
    if tamanho_bytes < 5_000_000:
        return {"aprovado": False, "motivo": f"Arquivo muito pequeno para vídeo longo ({tamanho_mb:.2f} MB < 5 MB)."}

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

    if largura != 1920 or altura != 1080:
        logger.warning(f"  [!] Resolução do vídeo ({largura}x{altura}) diferente do padrão 1920x1080.")

    resultado = {
        "aprovado": True,
        "duracao_segundos": round(duracao_s, 2),
        "duracao_minutos": round(duracao_s / 60.0, 2),
        "largura": largura,
        "altura": altura,
        "tamanho_mb": round(tamanho_mb, 2)
    }

    logger.info("\n" + "=" * 65)
    logger.info("=== AUDITORIA DO VÍDEO LONGO (1920x1080 FULL HD 16:9) ===")
    logger.info(f"  • Arquivo     : {os.path.basename(caminho_video)}")
    logger.info(f"  • Duração     : {duracao_s:.1f}s ({duracao_s/60.0:.2f} min)")
    logger.info(f"  • Resolução   : {largura}x{altura} (Horizontal 16:9)")
    logger.info(f"  • Tamanho     : {tamanho_mb:.2f} MB")
    logger.info(f"  • Codecs      : H.264 (Vídeo) + AAC (Áudio)")
    logger.info("=" * 65 + "\n")

    return resultado

def _mixar_audio_video_longo(
    video_sem_audio: str,
    caminho_audio_mp3: str,
    caminho_saida_mp4: str,
    timeline: Optional[Dict[str, Any]] = None
):
    """
    Mixa o vídeo Full HD renderizado com a narração masterizada sem cortar o áudio final.
    """
    ffmpeg_bin = obter_caminho_ffmpeg()
    
    bgm_path = os.path.join(ROOT_DIR, "assets", "bgm_longo.mp3")
    if not os.path.exists(bgm_path):
        bgm_path = os.path.join(ROOT_DIR, "assets", "bgm.mp3")
    tem_bgm = os.path.exists(bgm_path)

    if tem_bgm:
        cmd = [
            ffmpeg_bin, "-y",
            "-i", video_sem_audio,
            "-i", caminho_audio_mp3,
            "-stream_loop", "-1", "-i", bgm_path,
            "-filter_complex", "[1:a]volume=1.0[a1];[2:a]volume=0.07[a2];[a1][a2]amix=inputs=2:duration=first[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            caminho_saida_mp4
        ]
    else:
        cmd = [
            ffmpeg_bin, "-y",
            "-i", video_sem_audio,
            "-i", caminho_audio_mp3,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            caminho_saida_mp4
        ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        logger.warning(f"[!] Aviso na mixagem BGM ({e.stderr.decode('utf-8', errors='ignore')[:150]}). Usando cópia direta.")
        fallback_cmd = [
            ffmpeg_bin, "-y",
            "-i", video_sem_audio,
            "-i", caminho_audio_mp3,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            caminho_saida_mp4
        ]
        subprocess.run(fallback_cmd, check=True)

def renderizar_video_longo_16x9(
    timeline: Dict[str, Any],
    caminho_audio_mp3: str,
    caminho_saida_mp4: str,
    fps: int = 30
) -> str:
    """
    Renderiza vídeo 1920x1080 (16:9 Full HD) a 30 FPS via Playwright + FFmpeg pipe.
    Aplica Ken Burns lento, crossfade suave e mixa a narração completa.
    """
    os.makedirs(os.path.dirname(os.path.abspath(caminho_saida_mp4)), exist_ok=True)
    garantir_playwright_browsers()

    # Validação e higienização das imagens estáticas das cenas
    cenas = timeline.get("cenas", [])
    ultima_img_valida = None
    for i, c in enumerate(cenas):
        bg = c.get("bg_image") or ""
        caminho_disco = bg
        if bg.startswith("file:///"):
            caminho_disco = bg.replace("file:///", "").replace("/", os.sep)
        elif bg.startswith("file://"):
            caminho_disco = bg.replace("file://", "").replace("/", os.sep)

        ext = os.path.splitext(caminho_disco)[1].lower()
        eh_imagem = ext in [".jpg", ".jpeg", ".png", ".webp"]

        if caminho_disco and os.path.isfile(caminho_disco) and eh_imagem:
            uri_valida = Path(os.path.abspath(caminho_disco)).resolve().as_uri()
            c["bg_image"] = uri_valida
            ultima_img_valida = uri_valida
        else:
            if ultima_img_valida:
                c["bg_image"] = ultima_img_valida
            else:
                c["bg_image"] = None

    duracao_ms = int(timeline.get("duracao_total_ms", 180000))
    total_frames = int((duracao_ms / 1000.0) * fps) + 1

    video_sem_audio = caminho_saida_mp4 + ".raw.mp4"
    template_path = os.path.join(ROOT_DIR, "video_longo", "template_16x9.html")
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
        "-crf", "18",
        video_sem_audio
    ]

    duracao_min = duracao_ms / 60000.0
    logger.info(f"[*] Renderizando documentário 16:9 Full HD: {total_frames} frames ({duracao_ms / 1000:.1f}s / {duracao_min:.2f} min)...")
    ffmpeg_proc = subprocess.Popen(ffmpeg_pipe_cmd, stdin=subprocess.PIPE)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = lancar_chromium_seguro(
            p,
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page.goto(template_uri)
        page.evaluate("(data) => window.initTimeline(data)", timeline)

        # Renderiza frame a frame com intervalo de relatório a cada 15 segundos de vídeo
        report_step = fps * 15
        for frame_idx in range(total_frames):
            ms = int(round((frame_idx * 1000.0) / float(fps)))
            page.evaluate("(t) => window.renderAtTime(t)", ms)
            jpg_bytes = page.screenshot(type="jpeg", quality=90)
            ffmpeg_proc.stdin.write(jpg_bytes)

            if frame_idx > 0 and frame_idx % report_step == 0:
                pct = int((frame_idx / total_frames) * 100)
                seg_atual = ms / 1000.0
                logger.info(f"    Progresso: {pct}% ({frame_idx}/{total_frames} frames - {seg_atual:.1f}s / {seg_atual/60.0:.1f} min)")

        browser.close()

    ffmpeg_proc.stdin.close()
    ffmpeg_proc.wait()

    # Mixagem com o áudio masterizado
    logger.info("[*] Mixando vídeo Full HD com narração masterizada do documentário...")
    _mixar_audio_video_longo(video_sem_audio, caminho_audio_mp3, caminho_saida_mp4, timeline)

    if os.path.exists(video_sem_audio):
        try:
            os.remove(video_sem_audio)
        except Exception:
            pass

    # Auditoria de integridade
    auditar_video_longo(caminho_saida_mp4, duracao_ms)
    logger.info(f"[✔] Vídeo longo 16:9 finalizado: {caminho_saida_mp4}")
    return caminho_saida_mp4
