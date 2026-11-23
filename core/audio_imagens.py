#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audio_imagens.py - Síntese de Narração Cena por Cena com Edge-TTS Masterizado para @um.lapso.
- Sem cortes a meio da palavra: adiciona 350ms de silêncio natural (apad) ao fim de cada cena.
- Mede a duração exata com ffprobe após o pad.
- Timeline estritamente cumulativa (inicio_ms da cena N = fim_ms da cena N-1, zero gaps).
- Concatenação com re-encode e masterização broadcast (-16 LUFS, EQ, compressor).
- Trava de duração mínima de 35.000 ms (35 segundos).
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

logger = logging.getLogger("audio_imagens")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

VOZ_PADRAO = os.getenv("TTS_VOICE", os.getenv("EDGE_TTS_VOICE", "pt-BR-AntonioNeural"))
RATE_PADRAO = os.getenv("EDGE_TTS_RATE", "-4%")
PITCH_PADRAO = os.getenv("EDGE_TTS_PITCH", "-2Hz")
SILENCIO_PAD_S = 0.35  # 350ms de silêncio natural ao fim de cada cena
DURACAO_MINIMA_MS = 35000  # 35 segundos mínimos (para 8 a 10 cenas)

def _obter_caminho_ffmpeg() -> str:
    from core.editor_ffmpeg import obter_caminho_ffmpeg
    return obter_caminho_ffmpeg()

def _obter_duracao_audio_ms(caminho_arquivo: str) -> int:
    """Extrai duração real exata em milissegundos via ffprobe."""
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
    - Highpass 85Hz: Remove ruído subsônico.
    - Equalizador 170Hz (+3.5dB): Presença encorpada de locutor profissional.
    - Equalizador 3500Hz (-3.0dB) e 6500Hz (-2.0dB): Elimina sibilância e timbre metálico do TTS.
    - Compressor: Nivelamento dinâmico sem picos estridentes.
    - Loudnorm: Normalização EBU R128 (-16 LUFS), padrão estrito do Instagram e TikTok.
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
        _obter_caminho_ffmpeg(), "-y",
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
    """Gera áudio usando Microsoft Edge TTS de forma assíncrona, sem limite artificial de tempo."""
    comunicador = edge_tts.Communicate(texto, voz, rate=rate, pitch=pitch)
    await comunicador.save(output_path)

def _gerar_audio_openai_fallback(texto: str, output_path: str):
    """Fallback via OpenAI TTS caso Edge-TTS apresente instabilidade temporária."""
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
    """Sintetiza a fala da cena na íntegra (SEM corte de duração máxima)."""
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

def adicionar_pad_silencio_cena(caminho_in: str, caminho_out: str, pad_dur_s: float = SILENCIO_PAD_S) -> bool:
    """
    Adiciona 350ms de silêncio natural no final do áudio da cena usando apad=pad_dur=0.35.
    Isso impede que a última palavra ou sílaba da fala seja cortada na transição para a próxima cena.
    """
    cmd = [
        _obter_caminho_ffmpeg(), "-y",
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
        logger.warning(f"[!] Erro ao aplicar apad na cena ({caminho_in}): {e}")
        return False

def gerar_audio_e_timeline(roteiro: Dict[str, Any], output_dir: str) -> Tuple[str, str, Dict[str, Any]]:
    """
    Processa todas as cenas do roteiro (8 a 10 cenas):
    1. Sintetiza a fala completa de cada cena via Edge-TTS (sem qualquer truncamento).
    2. Aplica 350ms de silêncio natural ao fim de cada clipe (apad=pad_dur=0.35) para proteger as palavras finais.
    3. Mede a duração real com ffprobe sobre o clipe já com pad.
    4. Constrói o timeline.json de forma ESTRITAMENTE CUMULATIVA:
       - Cena 1: inicio_ms = 0, fim_ms = dur_0
       - Cena 2: inicio_ms = fim_ms_anterior, fim_ms = inicio_ms + dur_1
       - Zero gaps ou saltos no tempo.
    5. Concatena os clipes com re-encode e masterização broadcast (-16 LUFS).
    6. Valida a trava de duração mínima de 35.000 ms (35s).
    """
    os.makedirs(output_dir, exist_ok=True)
    cenas = roteiro.get("cenas", [])
    if not cenas:
        raise ValueError("Roteiro fornecido não possui cenas válidas.")

    logger.info(f"[*] Sintetizando áudio para {len(cenas)} cenas com Edge-TTS masterizado e pad de 350ms...")

    lista_arquivos_padded = []
    cenas_timeline = []
    cursor_ms = 0

    for i, cena in enumerate(cenas):
        c_id = cena.get("id", i + 1)
        texto = cena.get("texto_fala", "").strip()
        caminho_cena_raw = os.path.join(output_dir, f"cena_{c_id:02d}_raw.mp3")
        caminho_cena_padded = os.path.join(output_dir, f"cena_{c_id:02d}_padded.mp3")

        logger.info(f"  -> Cena {c_id:02d}/{len(cenas)}: \"{texto}\"")
        sucesso_sintese = sintetizar_fala_cena(texto, caminho_cena_raw)
        if not sucesso_sintese:
            raise RuntimeError(f"Falha ao gerar locução da cena {c_id}.")

        # Aplica 350ms de silêncio natural ao final da fala
        sucesso_pad = adicionar_pad_silencio_cena(caminho_cena_raw, caminho_cena_padded, SILENCIO_PAD_S)
        arq_final_cena = caminho_cena_padded if sucesso_pad else caminho_cena_raw

        # Mede a duração real exata do clipe da cena com o pad
        dur_cena_ms = _obter_duracao_audio_ms(arq_final_cena)
        if dur_cena_ms <= 0:
            dur_cena_ms = 4500  # Fallback de segurança

        # Construção estritamente cumulativa da timeline (zero gaps)
        inicio_ms = cursor_ms
        fim_ms = cursor_ms + dur_cena_ms
        cursor_ms = fim_ms

        cena_item = dict(cena)
        cena_item["id"] = c_id
        cena_item["inicio_ms"] = inicio_ms
        cena_item["fim_ms"] = fim_ms
        cena_item["duracao_ms"] = dur_cena_ms
        cenas_timeline.append(cena_item)

        lista_arquivos_padded.append(arq_final_cena)

    # Concatenação robusta dos arquivos com re-encode
    caminho_concat_txt = os.path.join(output_dir, "concat_audio.txt")
    with open(caminho_concat_txt, "w", encoding="utf-8") as f_txt:
        for arq in lista_arquivos_padded:
            caminho_abs = os.path.abspath(arq).replace("\\", "/")
            f_txt.write(f"file '{caminho_abs}'\n")

    caminho_raw_mp3 = os.path.join(output_dir, "narracao_raw.mp3")
    caminho_narracao_final = os.path.join(output_dir, "narracao.mp3")

    cmd_concat = [
        _obter_caminho_ffmpeg(), "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", caminho_concat_txt,
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        caminho_raw_mp3
    ]
    res_concat = subprocess.run(cmd_concat, capture_output=True, text=True)
    if res_concat.returncode != 0 or not os.path.exists(caminho_raw_mp3):
        raise RuntimeError(f"Erro ao concatenar áudios com FFmpeg: {res_concat.stderr}")

    # Masterização broadcast de estúdio (-16 LUFS, EQ, compressor)
    logger.info("[*] Aplicando masterização broadcast de estúdio (-16 LUFS)...")
    sucesso_master = masterizar_audio_estudio(caminho_raw_mp3, caminho_narracao_final)
    if not sucesso_master or not os.path.exists(caminho_narracao_final):
        logger.warning("[!] Masterização falhou, utilizando áudio concatenado direto.")
        caminho_narracao_final = caminho_raw_mp3

    duracao_audio_medida = _obter_duracao_audio_ms(caminho_narracao_final)
    # Garante que a duração total seja no mínimo a soma acumulada das cenas
    duracao_total_ms = max(duracao_audio_medida, cursor_ms)

    logger.info(f"[+] Narração concluída: {duracao_total_ms / 1000:.2f}s ({duracao_total_ms}ms) para {len(cenas)} cenas.")

    # Trava de duração mínima de 35 segundos
    if duracao_total_ms < DURACAO_MINIMA_MS:
        raise RuntimeError(
            f"Duração total do áudio ({duracao_total_ms}ms / {duracao_total_ms/1000:.1f}s) "
            f"inferior ao mínimo exigido ({DURACAO_MINIMA_MS}ms / 35s). Pipeline abortado."
        )

    timeline_data = {
        "tema": roteiro.get("tema", ""),
        "titulo": roteiro.get("titulo", ""),
        "descricao": roteiro.get("descricao", ""),
        "legenda_seo": roteiro.get("legenda_seo", ""),
        "duracao_total_ms": duracao_total_ms,
        "cenas": cenas_timeline
    }

    caminho_timeline_json = os.path.join(output_dir, "timeline.json")
    with open(caminho_timeline_json, "w", encoding="utf-8") as f_json:
        json.dump(timeline_data, f_json, ensure_ascii=False, indent=2)

    logger.info(f"[+] timeline.json gravado cumulativamente com sucesso: {caminho_timeline_json}")
    return caminho_narracao_final, caminho_timeline_json, timeline_data
