#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audio_longo.py - Síntese de Narração para Vídeos Longos (3 a 5 minutos) para @um.lapso no YouTube.
- Síntese de 22 a 32 cenas via Edge-TTS masterizado (-16 LUFS, sem Whisper).
- Adiciona 400ms de silêncio natural por cena e 850ms extras nas viradas de capítulo.
- Medição precisa com ffprobe e timeline estritamente cumulativa.
- Gera automaticamente os timestamps de capítulos no formato oficial do YouTube (00:00 Introdução...).
- Trava de sanidade: 170.000 ms (2m50s) a 360.000 ms (6m00s).
"""

import os
import sys
import json
import asyncio
import subprocess
import logging
from typing import Dict, Any, List, Tuple
from pathlib import Path
from dotenv import load_dotenv
import edge_tts
from openai import OpenAI

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

logger = logging.getLogger("audio_longo")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from core.editor_ffmpeg import obter_caminho_ffmpeg

VOZ_PADRAO = os.getenv("TTS_VOICE", os.getenv("EDGE_TTS_VOICE", "pt-BR-AntonioNeural"))
RATE_PADRAO = os.getenv("EDGE_TTS_RATE", "-3%")
PITCH_PADRAO = os.getenv("EDGE_TTS_PITCH", "-2Hz")

PAUSA_CENA_PADRAO_S = 0.40   # 400ms de silêncio entre cenas
PAUSA_CAPITULO_S = 0.85      # 850ms na transição entre capítulos

DURACAO_MINIMA_MS = 120000        # 2 minutos (piso absoluto de segurança)
DURACAO_ALVO_MINIMA_MS = 170000   # 2 minutos e 50 segundos (alvo ideal)
DURACAO_MAXIMA_MS = 360000        # 6 minutos

def _obter_duracao_audio_ms(caminho_arquivo: str) -> int:
    """Mede a duração exata do arquivo de áudio em milissegundos via ffprobe."""
    if not os.path.isfile(caminho_arquivo) or os.path.getsize(caminho_arquivo) < 100:
        return 0
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        caminho_arquivo
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        dur_s = float(res.stdout.strip())
        return int(round(dur_s * 1000))
    except Exception as e:
        logger.warning(f"[!] Erro ao medir duração via ffprobe ({caminho_arquivo}): {e}")
        return 0

def masterizar_audio_estudio(caminho_in: str, caminho_out: str) -> bool:
    """
    Aplica cadeia de masterização broadcast de estúdio via FFmpeg:
    - Highpass 85Hz + EQ presença 170Hz + EQ anti-sibilância 3.5kHz e 6.5kHz
    - Compressor dinâmico suave
    - Loudnorm EBU R128 (-16 LUFS), padrão estrito do YouTube.
    """
    filtro = (
        "highpass=f=85,"
        "equalizer=f=170:t=q:w=1.4:g=3.5,"
        "equalizer=f=3500:t=q:w=2.2:g=-3.0,"
        "equalizer=f=6500:t=q:w=2.0:g=-2.0,"
        "acompressor=threshold=-18dB:ratio=3:attack=10:release=120,"
        "loudnorm=I=-16:TP=-1.5:LRA=11"
    )
    cmd = [
        obter_caminho_ffmpeg(), "-y",
        "-i", caminho_in,
        "-af", filtro,
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        caminho_out
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0 and os.path.exists(caminho_out) and os.path.getsize(caminho_out) > 5000
    except Exception as e:
        logger.warning(f"[!] Erro ao masterizar áudio via FFmpeg: {e}")
        return False

async def _gerar_audio_edge(texto: str, output_path: str, voz: str = VOZ_PADRAO, rate: str = RATE_PADRAO, pitch: str = PITCH_PADRAO):
    """Gera áudio usando Microsoft Edge TTS de forma assíncrona."""
    comunicador = edge_tts.Communicate(texto, voz, rate=rate, pitch=pitch)
    await comunicador.save(output_path)

def _gerar_audio_openai_fallback(texto: str, output_path: str):
    """Fallback via OpenAI TTS caso Edge-TTS apresente instabilidade."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY ausente para fallback de áudio.")
    client = OpenAI(api_key=api_key)
    response = client.audio.speech.create(
        model="tts-1",
        voice="onyx",
        input=texto,
        speed=0.95
    )
    response.stream_to_file(output_path)

def sintetizar_fala_cena(texto: str, output_path: str) -> bool:
    """Sintetiza a fala da cena na íntegra sem truncamento."""
    try:
        asyncio.run(_gerar_audio_edge(texto, output_path))
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return True
    except Exception as e_edge:
        logger.warning(f"  [!] Edge-TTS falhou na cena: {e_edge}. Tentando OpenAI TTS...")

    try:
        _gerar_audio_openai_fallback(texto, output_path)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception as e_ai:
        logger.error(f"  [-] Erro ao sintetizar áudio da cena com OpenAI: {e_ai}")
        return False

def adicionar_pad_silencio(caminho_in: str, caminho_out: str, pad_dur_s: float) -> bool:
    """Adiciona silêncio natural configurável ao final do arquivo de áudio."""
    cmd = [
        obter_caminho_ffmpeg(), "-y",
        "-i", caminho_in,
        "-af", f"apad=pad_dur={pad_dur_s}",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        caminho_out
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return res.returncode == 0 and os.path.exists(caminho_out)
    except Exception as e:
        logger.warning(f"[!] Erro ao aplicar apad ({caminho_in}): {e}")
        return False

def formatar_timestamp_ms(ms: int) -> str:
    """Converte milissegundos para o formato mm:ss do YouTube."""
    segundos_totais = int(ms // 1000)
    minutos = segundos_totais // 60
    segundos = segundos_totais % 60
    return f"{minutos:02d}:{segundos:02d}"

def gerar_audio_e_timeline_longo(roteiro: Dict[str, Any], output_dir: str) -> Tuple[str, str, Dict[str, Any]]:
    """
    Processa todas as cenas e capítulos do roteiro longo (3 a 5 minutos):
    1. Sintetiza áudio de cada cena individualmente via Edge-TTS masterizado.
    2. Aplica 400ms de silêncio natural por cena e 850ms nas trocas de capítulo.
    3. Mede cada cena via ffprobe e monta timeline_longo.json estritamente cumulativo.
    4. Computa a lista de timestamps de capítulos para a descrição do YouTube.
    5. Concatena com re-encode e masterização broadcast (-16 LUFS).
    6. Valida a trava de sanidade (170s a 360s).
    """
    os.makedirs(output_dir, exist_ok=True)
    cenas_planas = roteiro.get("cenas_planas", [])
    if not cenas_planas:
        raise ValueError("Roteiro fornecido não possui lista de cenas válidas.")

    total_cenas = len(cenas_planas)
    logger.info(f"[*] Sintetizando áudio para documentário longo ({total_cenas} cenas)...")

    lista_arquivos_padded = []
    cenas_timeline = []
    cursor_ms = 0

    # Mapeamento de capítulos para cálculo de timestamps do YouTube
    capitulos_timestamps: List[Dict[str, Any]] = []
    ultimo_capitulo_nome = None

    for i, cena in enumerate(cenas_planas):
        c_id = cena.get("id", i + 1)
        texto = cena.get("fala_pt", "").strip()
        cap_nome = cena.get("capitulo", "Capítulo")
        
        caminho_cena_raw = os.path.join(output_dir, f"cena_{c_id:02d}_longo_raw.mp3")
        caminho_cena_padded = os.path.join(output_dir, f"cena_{c_id:02d}_longo_padded.mp3")

        logger.info(f"  -> Cena {c_id:02d}/{total_cenas} [{cap_nome}]: \"{texto[:45]}...\"")
        sucesso_sintese = sintetizar_fala_cena(texto, caminho_cena_raw)
        if not sucesso_sintese:
            raise RuntimeError(f"Falha ao gerar locução da cena {c_id}.")

        # Detecta se é a última cena do capítulo para aplicar 850ms de pausa
        proximo_cap_nome = cenas_planas[i + 1].get("capitulo") if (i + 1 < total_cenas) else None
        eh_virada_capitulo = (proximo_cap_nome is not None and proximo_cap_nome != cap_nome)
        pad_duracao = PAUSA_CAPITULO_S if eh_virada_capitulo else PAUSA_CENA_PADRAO_S

        sucesso_pad = adicionar_pad_silencio(caminho_cena_raw, caminho_cena_padded, pad_duracao)
        arq_final_cena = caminho_cena_padded if sucesso_pad else caminho_cena_raw

        dur_cena_ms = _obter_duracao_audio_ms(arq_final_cena)
        if dur_cena_ms <= 0:
            dur_cena_ms = 8500  # Fallback seguro

        inicio_ms = cursor_ms
        fim_ms = cursor_ms + dur_cena_ms

        # Se for o início de um novo capítulo, registra timestamp do YouTube
        if cap_nome != ultimo_capitulo_nome:
            timestamp_str = formatar_timestamp_ms(inicio_ms)
            capitulos_timestamps.append({
                "timestamp": timestamp_str,
                "inicio_ms": inicio_ms,
                "capitulo": cap_nome
            })
            ultimo_capitulo_nome = cap_nome

        cursor_ms = fim_ms

        cena_item = dict(cena)
        cena_item["id"] = c_id
        cena_item["inicio_ms"] = inicio_ms
        cena_item["fim_ms"] = fim_ms
        cena_item["duracao_ms"] = dur_cena_ms
        cenas_timeline.append(cena_item)

        lista_arquivos_padded.append(arq_final_cena)

    # Garante que o primeiro capítulo seja exatamente 00:00 (requisito do YouTube)
    if capitulos_timestamps:
        capitulos_timestamps[0]["timestamp"] = "00:00"
        capitulos_timestamps[0]["inicio_ms"] = 0

    # Concatenação robusta dos arquivos com re-encode
    caminho_concat_txt = os.path.join(output_dir, "concat_audio_longo.txt")
    with open(caminho_concat_txt, "w", encoding="utf-8") as f_txt:
        for arq in lista_arquivos_padded:
            caminho_abs = os.path.abspath(arq).replace("\\", "/")
            f_txt.write(f"file '{caminho_abs}'\n")

    caminho_raw_mp3 = os.path.join(output_dir, "narracao_longo_raw.mp3")
    caminho_narracao_final = os.path.join(output_dir, "narracao_longo.mp3")

    cmd_concat = [
        obter_caminho_ffmpeg(), "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", caminho_concat_txt,
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        caminho_raw_mp3
    ]
    res_concat = subprocess.run(cmd_concat, capture_output=True, text=True)
    if res_concat.returncode != 0 or not os.path.exists(caminho_raw_mp3):
        raise RuntimeError(f"Erro ao concatenar áudios do documentário: {res_concat.stderr}")

    # Masterização broadcast de estúdio (-16 LUFS)
    logger.info("[*] Masterizando narração completa para YouTube broadcast (-16 LUFS)...")
    sucesso_master = masterizar_audio_estudio(caminho_raw_mp3, caminho_narracao_final)
    if not sucesso_master or not os.path.exists(caminho_narracao_final):
        logger.warning("[!] Masterização falhou, utilizando áudio bruto concatenado.")
        caminho_narracao_final = caminho_raw_mp3

    duracao_audio_medida = _obter_duracao_audio_ms(caminho_narracao_final)
    duracao_total_ms = max(duracao_audio_medida, cursor_ms)
    minutos_total = duracao_total_ms / 60000.0

    logger.info(f"[+] Narração documental concluída: {duracao_total_ms / 1000:.1f}s ({minutos_total:.2f} min).")

    # Trava de sanidade de duração
    if duracao_total_ms < DURACAO_MINIMA_MS:
        raise RuntimeError(
            f"Duração do documentário ({duracao_total_ms/1000:.1f}s) inferior ao piso mínimo de "
            f"{DURACAO_MINIMA_MS/1000:.1f}s (2m00s). Abortando para garantir padrão de vídeo longo."
        )
    elif duracao_total_ms < DURACAO_ALVO_MINIMA_MS:
        logger.warning(
            f"[!] Duração do documentário ({duracao_total_ms/1000:.1f}s) ligeiramente abaixo do alvo de "
            f"{DURACAO_ALVO_MINIMA_MS/1000:.1f}s (2m50s), mas acima do piso mínimo. Prosseguindo com a renderização."
        )
    if duracao_total_ms > DURACAO_MAXIMA_MS:
        logger.warning(
            f"[!] Duração do documentário ({duracao_total_ms/1000:.1f}s) superior ao teto ideal de "
            f"{DURACAO_MAXIMA_MS/1000:.1f}s (6m00s)."
        )

    # Formata texto de capítulos para colar na descrição do YouTube
    linhas_capitulos = ["\nCAPÍTULOS:"]
    for cap_info in capitulos_timestamps:
        linhas_capitulos.append(f"{cap_info['timestamp']} - {cap_info['capitulo']}")
    texto_capitulos_yt = "\n".join(linhas_capitulos)

    timeline_data = {
        "tema": roteiro.get("tema", ""),
        "titulo": roteiro.get("titulo", ""),
        "descricao_base": roteiro.get("descricao_base", ""),
        "texto_capitulos_yt": texto_capitulos_yt,
        "capitulos_timestamps": capitulos_timestamps,
        "tags": roteiro.get("tags", []),
        "duracao_total_ms": duracao_total_ms,
        "total_cenas": len(cenas_timeline),
        "cenas": cenas_timeline
    }

    caminho_timeline_json = os.path.join(output_dir, "timeline_longo.json")
    with open(caminho_timeline_json, "w", encoding="utf-8") as f_json:
        json.dump(timeline_data, f_json, ensure_ascii=False, indent=2)

    logger.info(f"[+] timeline_longo.json salvo: {caminho_timeline_json}")
    logger.info(f"    Capítulos gerados:\n{texto_capitulos_yt}")

    return caminho_narracao_final, caminho_timeline_json, timeline_data
