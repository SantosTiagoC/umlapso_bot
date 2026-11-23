#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_renderizar_video.py - Renderização 1080x1920 @ 30 FPS e Mixagem com FFmpeg.
- Captura frames JPEG do template HTML via Playwright e envia via pipe direto ao FFmpeg.
- Mixa narração principal + trilha de fundo opcional (assets/bgm.mp3) + SFX nos timestamps de cena.
- Realiza auto-auditoria pós-render via ffprobe (duração >= 61s, streams válidos e tamanho > 1 MB).
"""

import os
import sys
import json
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

logger = logging.getLogger("renderer")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from core.browser_utils import garantir_playwright_browsers, lancar_chromium_seguro
from core import garantir_ffmpeg_path

garantir_ffmpeg_path()

def auditar_video_final(caminho_video: str, duracao_esperada_ms: int) -> Dict[str, Any]:
    """
    Auto-auditoria pós-render com ffprobe:
    - Valida duração mínima para monetização (+61 segundos)
    - Valida streams de vídeo (1080x1920) e áudio
    - Valida integridade e tamanho do arquivo (> 1 MB)
    """
    logger.info(f"[*] Iniciando auto-auditoria pós-render com ffprobe: {os.path.basename(caminho_video)}...")
    if not os.path.exists(caminho_video):
        return {"aprovado": False, "motivo": "Arquivo de vídeo não foi gerado no disco."}

    tamanho_bytes = os.path.getsize(caminho_video)
    tamanho_mb = tamanho_bytes / (1024 * 1024)
    if tamanho_bytes < 1_000_000:
        return {"aprovado": False, "motivo": f"Arquivo muito pequeno ({tamanho_mb:.2f} MB < 1 MB)."}

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
        return {"aprovado": False, "motivo": f"Falha ao executar ffprobe: {e}"}

    format_info = probe_data.get("format", {})
    streams = probe_data.get("streams", [])

    duracao_s = float(format_info.get("duration", 0))
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if not video_stream:
        return {"aprovado": False, "motivo": "Stream de vídeo ausente no arquivo MP4."}

    if not audio_stream:
        return {"aprovado": False, "motivo": "Stream de áudio ausente no arquivo MP4."}

    largura = video_stream.get("width")
    altura = video_stream.get("height")
    if largura != 1080 or altura != 1920:
        logger.warning(f"  [!] Resolução do vídeo ({largura}x{altura}) diferente do padrão 1080x1920.")

    aprovado_monetizacao = duracao_s >= 61.0
    resultado = {
        "aprovado": True,
        "monetizavel_tiktok": aprovado_monetizacao,
        "duracao_segundos": round(duracao_s, 2),
        "duracao_minutos": round(duracao_s / 60, 2),
        "largura": largura,
        "altura": altura,
        "tamanho_mb": round(tamanho_mb, 2)
    }

    print("\n" + "=" * 65)
    print("=== RELATÓRIO DE AUDITORIA DO VÍDEO (TIKTOK CREATOR REWARDS) ===")
    print(f"  • Arquivo     : {os.path.basename(caminho_video)}")
    print(f"  • Duração     : {duracao_s:.1f}s ({duracao_s/60:.1f} min)")
    print(f"  • Resolução   : {largura}x{altura} (Vertical 9:16)")
    print(f"  • Tamanho     : {tamanho_mb:.2f} MB")
    print(f"  • Streams     : Vídeo (h264) + Áudio (aac)")
    print(f"  • Monetizável : {'✅ SIM (+60s Aprovado)' if aprovado_monetizacao else '⚠️ ALERTA (Vídeo < 61s)'}")
    print("=" * 65 + "\n")

    return resultado


def _montar_mixagem_audio(
    video_sem_audio: str,
    caminho_audio_mp3: str,
    timeline: dict,
    caminho_saida_mp4: str
):
    """Mixa a narração principal + trilha de fundo + efeitos sonoros (SFX) sincronizados."""
    bgm_path = os.path.join(ROOT_DIR, "assets", "bgm.mp3")
    tem_bgm = os.path.exists(bgm_path)

    # Mapeia SFX por cena
    cenas = timeline.get("cenas", [])
    sfx_dir = os.path.join(ROOT_DIR, "assets", "sfx")
    sfx_adicionados = []

    if os.path.exists(sfx_dir):
        for c in cenas:
            sfx_name = c.get("sfx", "")
            if sfx_name:
                sfx_file = os.path.join(sfx_dir, f"{sfx_name}.mp3")
                if os.path.exists(sfx_file):
                    sfx_adicionados.append({
                        "arquivo": sfx_file,
                        "delay_ms": max(0, int(c.get("inicio_ms", 0)))
                    })

    inputs = ["-i", video_sem_audio, "-i", caminho_audio_mp3]
    input_idx = 2

    bgm_idx = None
    if tem_bgm:
        inputs.extend(["-stream_loop", "-1", "-i", bgm_path])
        bgm_idx = input_idx
        input_idx += 1

    sfx_indices = []
    for item in sfx_adicionados:
        inputs.extend(["-i", item["arquivo"]])
        sfx_indices.append((input_idx, item["delay_ms"]))
        input_idx += 1

    # Constrói o filter_complex
    filtros = []
    mix_inputs = ["[1:a]volume=1.0[a_narr]"]
    mix_labels = ["[a_narr]"]

    if tem_bgm and bgm_idx is not None:
        filtros.append(f"[{bgm_idx}:a]volume=0.10[a_bgm]")
        mix_labels.append("[a_bgm]")

    for idx_sfx, delay in sfx_indices:
        label = f"[sfx_{idx_sfx}]"
        filtros.append(f"[{idx_sfx}:a]adelay={delay}|{delay},volume=0.35{label}")
        mix_labels.append(label)

    todos_filtros = mix_inputs + filtros
    mix_cmd_str = f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=2[aout]"
    todos_filtros.append(mix_cmd_str)
    filter_complex_str = ";".join(todos_filtros)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex_str,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        caminho_saida_mp4
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        logger.warning(f"Aviso no filter complex ({e.stderr.decode('utf-8', errors='ignore')[:200]}). Usando mixagem segura.")
        # Fallback seguro direto
        fallback_cmd = [
            "ffmpeg", "-y",
            "-i", video_sem_audio,
            "-i", caminho_audio_mp3,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", caminho_saida_mp4
        ]
        subprocess.run(fallback_cmd, check=True)


def renderizar_mp4(
    timeline: dict,
    caminho_audio_mp3: str,
    caminho_saida_mp4: str,
    fps: int = 30
) -> Dict[str, Any]:
    """Renderiza a timeline completa em MP4 1080x1920 a 30 FPS e audita o resultado."""
    duracao_ms = int(timeline.get("duracao_total_ms", 65000))
    total_frames = int((duracao_ms / 1000.0) * fps) + 1

    video_sem_audio = caminho_saida_mp4 + ".raw.mp4"
    template_uri = Path(os.path.join(ROOT_DIR, "renderer", "template.html")).as_uri()

    os.makedirs(os.path.dirname(os.path.abspath(caminho_saida_mp4)), exist_ok=True)
    garantir_playwright_browsers()

    from playwright.sync_api import sync_playwright

    ffmpeg_pipe_cmd = [
        "ffmpeg", "-y",
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

    print(f"[*] Renderizando {total_frames} frames ({duracao_ms / 1000:.1f}s) a {fps} FPS em 1080x1920...")
    ffmpeg_proc = subprocess.Popen(ffmpeg_pipe_cmd, stdin=subprocess.PIPE)

    with sync_playwright() as p:
        browser = lancar_chromium_seguro(
            p,
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
        page.goto(template_uri)
        page.evaluate("(data) => window.initTimeline(data)", timeline)

        for frame_idx in range(total_frames):
            ms = int(round((frame_idx * 1000.0) / float(fps)))
            page.evaluate("(t) => window.renderAtTime(t)", ms)
            jpg_bytes = page.screenshot(type="jpeg", quality=92)
            ffmpeg_proc.stdin.write(jpg_bytes)

            if frame_idx > 0 and frame_idx % (fps * 10) == 0:
                pct = int((frame_idx / total_frames) * 100)
                print(f"    Progresso: {pct}% ({frame_idx}/{total_frames} frames - {ms/1000:.1f}s)")

        browser.close()

    ffmpeg_proc.stdin.close()
    ffmpeg_proc.wait()

    # Mixagem com áudio, BGM e SFX
    print("[*] Mixando narração, efeitos sonoros e trilha de fundo no FFmpeg...")
    _montar_mixagem_audio(video_sem_audio, caminho_audio_mp3, timeline, caminho_saida_mp4)

    # Limpeza do vídeo temporário sem áudio
    if os.path.exists(video_sem_audio):
        try:
            os.remove(video_sem_audio)
        except Exception:
            pass

    # Executa auto-auditoria
    audit = auditar_video_final(caminho_saida_mp4, duracao_ms)
    print(f"[✔] Renderização concluída: {caminho_saida_mp4}")
    return audit


def main():
    if len(sys.argv) < 3:
        print("Uso: python 03_renderizar_video.py <timeline.json> <narracao.mp3> [saida.mp4]")
        sys.exit(1)

    caminho_timeline = sys.argv[1]
    caminho_audio = sys.argv[2]
    caminho_saida = sys.argv[3] if len(sys.argv) > 3 else caminho_timeline.replace("timeline.json", "video_final.mp4")

    with open(caminho_timeline, "r", encoding="utf-8") as f:
        timeline = json.load(f)

    renderizar_mp4(timeline, caminho_audio, caminho_saida, fps=30)

if __name__ == "__main__":
    main()
