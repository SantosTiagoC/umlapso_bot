#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
02_gerar_audio.py - Síntese de Narração Realista e Timestamps Whisper com Arquitetura Determinística.
Fonte Única da Verdade para Legendas:
1. Sintetiza cenas (ElevenLabs com fallback para Edge-TTS) e concatena com silêncio de 120ms.
2. Aplica masterização broadcast de estúdio (EQ, compressor e loudnorm -16 LUFS) ANTES de extrair timestamps.
3. Mede a duração exata do narracao.mp3 final com ffprobe.
4. Roda OpenAI Whisper (whisper-1, word-level) DIRETAMENTE sobre o narracao.mp3 definitivo.
5. Constrói a lista 'legendas' EXCLUSIVAMENTE a partir das palavras do Whisper na ordem estrita.
6. Agrupa em blocos de 2 a 3 palavras com monotonicidade absoluta (sem sobreposição, sem saltos reversos).
7. Particiona as 16 cenas da timeline de forma desacoplada das legendas (cursor linear que só avança).
8. Executa auditoria automática estrita (auditar_timeline) antes de gravar a timeline.json.
"""

import os
import sys
import re
import json
import asyncio
import subprocess
from typing import Dict, Any, List, Optional
from pathlib import Path
from dotenv import load_dotenv
import edge_tts

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

from core import garantir_ffmpeg_path
from core.elevenlabs_client import ElevenLabsClient

garantir_ffmpeg_path()

VOZ_PADRAO = os.getenv("TTS_VOICE", os.getenv("EDGE_TTS_VOICE", "pt-BR-AntonioNeural"))
RATE_PADRAO = os.getenv("EDGE_TTS_RATE", "+3%")
PITCH_PADRAO = os.getenv("EDGE_TTS_PITCH", "-2Hz")
PAUSA_CENA_S = 0.120  # 120ms de micro-pausa ágil entre cenas
PAUSA_CENA_MS = int(PAUSA_CENA_S * 1000)

def masterizar_audio_estudio(caminho_in: str, caminho_out: str) -> bool:
    """
    Aplica cadeia de masterização broadcast de estúdio via FFmpeg:
    - Highpass 85Hz: Remove ruído subsônico / puff do microfone.
    - Equalizador 170Hz (+3.5dB): Presença encorpada de locutor profissional.
    - Equalizador 3500Hz (-3.0dB) e 6500Hz (-2.0dB): Elimina sibilância e timbre metálico do TTS.
    - Compressor: Nivelamento dinâmico profissional sem picos estridentes.
    - Loudnorm: Normalização EBU R128 (-16 LUFS), padrão estrito do algoritmo do TikTok.
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
        "ffmpeg", "-y",
        "-i", caminho_in,
        "-af", filtro,
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        caminho_out
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0
    except Exception as e:
        print(f"[!] Erro ao masterizar áudio via FFmpeg: {e}")
        return False

def _obter_duracao_audio_ms(caminho_arquivo: str) -> int:
    """Extrai duração real exata em milissegundos via ffprobe."""
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
        print(f"[!] Erro ao medir duração via ffprobe ({caminho_arquivo}): {e}")
        return 0

def _gerar_arquivo_silencio(caminho_silencio: str, duracao_s: float = PAUSA_CENA_S):
    """Gera um arquivo de áudio MP3 de silêncio absoluto calibrado."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(duracao_s),
        "-c:a", "libmp3lame",
        "-q:a", "2",
        caminho_silencio
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)

MAPA_PRONUNCIA_TTS = {
    r"\bEUA\b": "Estados Unidos",
    r"\beua\b": "Estados Unidos",
    r"\bE\.U\.A\.\b": "Estados Unidos",
    r"\bIA\b": "inteligência artificial",
    r"\bI\.A\.\b": "inteligência artificial",
    r"\bONU\b": "O.N.U.",
    r"\bO\.N\.U\.\b": "O.N.U."
}

def normalizar_texto_para_tts(texto: str) -> str:
    """Substitui siglas problemáticas por pronúncia natural em português antes do TTS."""
    res = texto or ""
    for pat, rep in MAPA_PRONUNCIA_TTS.items():
        res = re.sub(pat, rep, res)
    return res.strip()

CORRECOES_FONETICAS_WHISPER = {
    "IUA": "EUA",
    "iua": "EUA",
    "Iua": "EUA",
    "eua": "EUA",
    "Eua": "EUA",
    "ia": "IA",
    "Ia": "IA"
}

def _separar_palavras_coladas(texto: str) -> str:
    """
    Separa palavras grudadas comuns em TTS e Whisper:
    - CamelCase: 'EssaRegulamentação' -> 'Essa Regulamentação'
    - Dígitos e palavras: '100países' -> '100 países' ou 'dia21' -> 'dia 21'
    - Sufixos colados: 'EXPONENCIALMENTEATÉ' -> 'EXPONENCIALMENTE ATÉ'
    - Palavras em caixa alta grudadas: 'ESSAREGULAMENTAÇÃO' -> 'ESSA REGULAMENTAÇÃO'
    """
    if not texto:
        return ""
    # 1. CamelCase
    t = re.sub(r'([a-zà-ÿ])([A-ZÀ-Ý])', r'\1 \2', str(texto))
    # 2. Dígitos seguidos de letras e vice-versa
    t = re.sub(r'(\d+)([a-zA-Zà-ÿÀ-Ý])', r'\1 \2', t)
    t = re.sub(r'([a-zA-Zà-ÿÀ-Ý])(\d+)', r'\1 \2', t)

    # 3. Sufixos colados em maiúsculas (ex: EXPONENCIALMENTEATÉ -> EXPONENCIALMENTE ATÉ)
    sufixos_caps = ["ATÉ", "ATE", "PARA", "COM", "SEM", "POR", "EM", "NO", "NA", "DE", "DA", "DO", "MAIS", "MUITO"]
    for suf in sufixos_caps:
        t = re.sub(rf'([A-ZÀ-Ý]{{4,}})({suf})\b', r'\1 \2', t)
        t = re.sub(rf'\b({suf})([A-ZÀ-Ý]{{4,}})', r'\1 \2', t)

    # 4. Tratamento de palavras coladas com conectivos/prefixos comuns (case-insensitive)
    PALAVRAS_LEGITIMAS = {
        "SOBREVIVER", "SOBRETUDO", "SOBRECARGA", "SOBREVIVENCIA", "SOBRESSAIR",
        "SOBREAVISO", "SOBRENATURAL", "SOBRESSALENTE", "PARALISAR", "PARALELO",
        "PARADOXO", "PARLAMENTO", "PARALISIA", "PARAMETRO", "ENTRETANTO",
        "ENTREGAR", "ENTREVISTA", "ENTRADA", "CONTRATO", "CONTRARIO",
        "ATENDIMENTO", "ATENCAO", "ATENCIOSO", "ESTABELECER", "ESTATISTICA",
        "ESTABILIDADE", "ESPECIAL", "ESPECIFICO"
    }

    PREFIXOS_COLADOS = [
        "SOBRE", "PARA", "ESSA", "ESSE", "ESTA", "ESTE", "ENTRE", "CONTRA",
        "ATE", "ATÉ", "MUITO", "MAIS", "COMO", "ONDE", "QUANDO", "QUEM",
        "QUAL", "DESDE", "APOS", "APÓS"
    ]

    tokens = t.split()
    tokens_proc = []
    for tok in tokens:
        tok_limpo = re.sub(r'[^\w]', '', tok)
        tok_upper = tok_limpo.upper()
        separado = False

        if len(tok_limpo) >= 10 and tok_upper not in PALAVRAS_LEGITIMAS:
            for pref in PREFIXOS_COLADOS:
                if tok_upper.startswith(pref) and len(tok_limpo) >= len(pref) + 4:
                    p1 = tok[:len(pref)]
                    p2 = tok[len(pref):]
                    tokens_proc.extend([p1, p2])
                    separado = True
                    break

        if not separado and tok_limpo.isupper() and len(tok_limpo) >= 7:
            for pref in PREFIXOS_COLADOS:
                if tok.startswith(pref) and len(tok) >= len(pref) + 3:
                    resto = tok[len(pref):]
                    tokens_proc.extend([pref, resto])
                    separado = True
                    break

        if not separado:
            tokens_proc.append(tok)
    return " ".join([p.strip() for p in tokens_proc if p.strip()])

def _arquivos_iguais(p1: str, p2: str) -> bool:
    """Verifica se dois arquivos de áudio têm exatamente os mesmos bytes."""
    if not p1 or not p2 or not os.path.exists(p1) or not os.path.exists(p2):
        return False
    try:
        if os.path.getsize(p1) != os.path.getsize(p2):
            return False
        import filecmp
        return filecmp.cmp(p1, p2, shallow=False)
    except Exception:
        return False

def _transcrever_whisper_direto(caminho_audio_mp3: str) -> Optional[List[Dict[str, Any]]]:
    """
    Executa o OpenAI Whisper diretamente sobre o narracao.mp3 final completo.
    Retorna a lista crua de palavras na ordem temporal estrita.
    """
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not openai_key or not openai_key.startswith("sk-"):
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key)
        print("[✨] Transcrevendo narracao.mp3 completa com OpenAI Whisper (whisper-1, word-level)...")
        with open(caminho_audio_mp3, "rb") as f_aud:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f_aud,
                language="pt",
                response_format="verbose_json",
                timestamp_granularities=["word"]
            )

        words_data = getattr(transcript, "words", None)
        if not words_data and isinstance(transcript, dict):
            words_data = transcript.get("words")

        if not words_data:
            return None

        palavras_raw = []
        for item in words_data:
            w = item.get("word") if isinstance(item, dict) else getattr(item, "word", "")
            st = item.get("start") if isinstance(item, dict) else getattr(item, "start", 0.0)
            en = item.get("end") if isinstance(item, dict) else getattr(item, "end", 0.0)
            palavras_raw.append({
                "word": str(w).strip(),
                "start_ms": int(round(st * 1000)),
                "end_ms": int(round(en * 1000))
            })
        print(f"[✔] Whisper extraiu {len(palavras_raw)} palavras na linha do tempo contínua!")
        return palavras_raw
    except Exception as e:
        print(f"[!] Falha na transcrição Whisper direta: {e}")
        return None

def _processar_palavras_whisper(words_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Pós-processamento estrito das palavras do Whisper:
    1. Separa palavras coladas no token.
    2. Funde tokens numéricos seguidos de 'por cento' (ex: ['70', 'por', 'cento'] -> '70%').
    3. Garante monotonicidade estrita em cada palavra (end_ms >= start_ms + 60, start_ms >= prev_end_ms).
    """
    if not words_raw:
        return []

    tokens_base = []
    for item in words_raw:
        w_limpo = _separar_palavras_coladas(item["word"])
        partes = w_limpo.split()
        if not partes:
            continue
        if len(partes) == 1:
            w_raw = partes[0]
            w_corr = CORRECOES_FONETICAS_WHISPER.get(w_raw.strip(), CORRECOES_FONETICAS_WHISPER.get(w_raw.strip().upper(), w_raw))
            tokens_base.append({
                "word": w_corr,
                "start_ms": item["start_ms"],
                "end_ms": item["end_ms"]
            })
        else:
            dur = max(100, item["end_ms"] - item["start_ms"])
            step = dur // len(partes)
            for k, p in enumerate(partes):
                p_corr = CORRECOES_FONETICAS_WHISPER.get(p.strip(), CORRECOES_FONETICAS_WHISPER.get(p.strip().upper(), p))
                tokens_base.append({
                    "word": p_corr,
                    "start_ms": item["start_ms"] + (k * step),
                    "end_ms": item["start_ms"] + ((k + 1) * step)
                })

    fundidas = []
    i = 0
    while i < len(tokens_base):
        curr = tokens_base[i]
        curr_w = curr["word"].strip()
        num_clean = re.sub(r'[^\d\.,]', '', curr_w)
        if num_clean and any(ch.isdigit() for ch in num_clean):
            # Caso 1: ['70', 'por', 'cento']
            if (i + 2 < len(tokens_base) and
                tokens_base[i+1]["word"].lower() == "por" and
                tokens_base[i+2]["word"].lower().startswith("cento")):
                fundidas.append({
                    "word": f"{num_clean}%",
                    "start_ms": curr["start_ms"],
                    "end_ms": tokens_base[i+2]["end_ms"]
                })
                i += 3
                continue
            # Caso 2: ['70', '%'] ou ['70', 'porcento']
            elif (i + 1 < len(tokens_base) and
                  (tokens_base[i+1]["word"].strip() == "%" or tokens_base[i+1]["word"].lower() == "porcento")):
                fundidas.append({
                    "word": f"{num_clean}%",
                    "start_ms": curr["start_ms"],
                    "end_ms": tokens_base[i+1]["end_ms"]
                })
                i += 2
                continue

        fundidas.append(curr)
        i += 1

    palavras_monotonicas = []
    prev_end = 0
    for j, item in enumerate(fundidas):
        st = item["start_ms"]
        en = item["end_ms"]
        if j > 0:
            st = max(st, prev_end)
        en = max(st + 60, en)
        prev_end = en
        palavras_monotonicas.append({
            "word": item["word"],
            "start_ms": st,
            "end_ms": en
        })

    return palavras_monotonicas

def _agrupar_palavras_em_legendas(palavras: List[Dict[str, Any]], duracao_total_ms: int) -> List[Dict[str, Any]]:
    """
    Agrupa palavras em blocos sequenciais de 2 a 3 palavras com monotonicidade absoluta.
    Garante que JAMAIS dois blocos existam no mesmo milissegundo (blocos estritamente disjuntos).
    """
    if not palavras:
        return []

    grupos_brutos = []
    atual = []

    for i, p in enumerate(palavras):
        atual.append(p)
        texto_bloco = " ".join(w["word"] for w in atual)
        termina_pont = any(p["word"].endswith(pt) for pt in [".", "!", "?", ",", ";", ":"])

        deve_quebrar = False
        if len(atual) >= 3 or termina_pont:
            deve_quebrar = True
        elif i + 1 < len(palavras):
            prox = palavras[i + 1]
            pausa_ms = prox["start_ms"] - p["end_ms"]
            caracteres_com_prox = len(texto_bloco) + 1 + len(prox["word"])
            if pausa_ms > 350 or caracteres_com_prox > 22:
                deve_quebrar = True

        if deve_quebrar:
            grupos_brutos.append({
                "inicio_ms": atual[0]["start_ms"],
                "fim_ms": atual[-1]["end_ms"],
                "palavras": list(atual)
            })
            atual = []

    if atual:
        grupos_brutos.append({
            "inicio_ms": atual[0]["start_ms"],
            "fim_ms": atual[-1]["end_ms"],
            "palavras": list(atual)
        })

    blocos_sanitizados = []
    cursor_ms = 0

    for i in range(len(grupos_brutos)):
        b = grupos_brutos[i]
        b_inicio = max(cursor_ms, b["inicio_ms"])
        b_fim = max(b_inicio + 150, b["fim_ms"])

        if i + 1 < len(grupos_brutos):
            prox_inicio = grupos_brutos[i + 1]["inicio_ms"]
            pausa_ms = prox_inicio - b_fim
            if pausa_ms <= 550:
                b_fim = prox_inicio
            elif b_fim >= prox_inicio:
                b_fim = prox_inicio
            else:
                b_fim = min(b_fim + 80, prox_inicio)
        else:
            limite_audio = max(b_inicio + 150, duracao_total_ms - 100)
            b_fim = min(b_fim + 80, limite_audio)

        palavras_bloco = []
        w_cur = b_inicio
        for pw in b["palavras"]:
            p_st = max(w_cur, pw["start_ms"])
            p_en = max(p_st + 60, pw["end_ms"])
            w_cur = p_en
            palavras_bloco.append({
                "word": pw["word"],
                "start_ms": p_st,
                "end_ms": p_en
            })

        blocos_sanitizados.append({
            "inicio_ms": b_inicio,
            "fim_ms": b_fim,
            "palavras": palavras_bloco
        })
        cursor_ms = b_fim

    return blocos_sanitizados

def _calcular_palavras_cena_fallback(texto: str, dur_audio_ms: int, offset_ms: int) -> List[Dict[str, Any]]:
    """Cálculo fonético proporcional de palavras para fallback sem Whisper."""
    texto_limpo = _separar_palavras_coladas(texto)
    tokens = [t.strip() for t in texto_limpo.split() if t.strip()]
    if not tokens:
        return []

    pesos = []
    for t in tokens:
        peso = float(len(t))
        if any(t.endswith(p) for p in [".", "!", "?"]):
            peso += 4.5
        elif any(t.endswith(p) for p in [",", ";", ":", "—", "-"]):
            peso += 2.5
        pesos.append(max(2.0, peso))

    soma_pesos = sum(pesos)
    lead_in = min(80, max(30, int(dur_audio_ms * 0.03)))
    tail = min(120, max(40, int(dur_audio_ms * 0.04)))
    tempo_fala = max(200, dur_audio_ms - lead_in - tail)

    palavras = []
    acumulado_ms = 0.0
    last_end = offset_ms + lead_in
    for t, p in zip(tokens, pesos):
        dur_palavra = (p / soma_pesos) * tempo_fala
        start_ms = max(last_end, offset_ms + lead_in + int(acumulado_ms))
        end_ms = start_ms + max(120, int(dur_palavra * 0.94))
        last_end = end_ms
        acumulado_ms += dur_palavra
        palavras.append({
            "word": t,
            "start_ms": start_ms,
            "end_ms": end_ms
        })

    return palavras

def _alinhar_cenas_timeline(
    cenas_roteiro: List[Dict[str, Any]],
    palavras_referencia: List[Dict[str, Any]],
    duracao_total_ms: int,
    duracoes_cenas_ms: Optional[List[int]] = None
) -> List[Dict[str, Any]]:
    """
    Particiona a linha do tempo em cenas (troca de imagem 3D, SFX e HUD)
    com monotonicidade estrita. Quando 'duracoes_cenas_ms' é fornecido,
    utiliza o tempo de fala exato de cada arquivo de áudio (+ pausa calibrada),
    garantindo alinhamento 1:1 perfeito entre voz e corte visual:
    - cena[0] começa em 0.
    - cena[k].inicio_ms = cena[k-1].fim_ms.
    - última cena termina exatamente em duracao_total_ms.
    """
    total_cenas = len(cenas_roteiro)
    if total_cenas == 0:
        return []

    tem_duracoes_exatas = bool(duracoes_cenas_ms and len(duracoes_cenas_ms) == total_cenas)
    pesos = [max(1, len(c.get("texto_fala", "").split())) for c in cenas_roteiro]
    total_palavras_roteiro = sum(pesos)

    cenas_timeline = []
    cursor_inicio = 0

    for idx, c_orig in enumerate(cenas_roteiro):
        c_dict = dict(c_orig)
        c_dict["id"] = idx + 1

        if idx == total_cenas - 1:
            fim_cena = duracao_total_ms
        else:
            if tem_duracoes_exatas:
                # Soma cumulativa exata dos arquivos de áudio + pausas de transição
                acum_ms = sum(duracoes_cenas_ms[:idx + 1]) + ((idx + 1) * PAUSA_CENA_MS)
                target_ms = int(acum_ms)
            elif palavras_referencia and len(palavras_referencia) >= total_cenas:
                frac = sum(pesos[:idx + 1]) / float(total_palavras_roteiro)
                w_target_idx = min(len(palavras_referencia) - 1, int(round(frac * len(palavras_referencia))))
                target_ms = palavras_referencia[w_target_idx]["start_ms"]
            else:
                frac = sum(pesos[:idx + 1]) / float(total_palavras_roteiro)
                target_ms = int(round(frac * duracao_total_ms))

            min_fim = cursor_inicio + 1500
            max_fim = duracao_total_ms - ((total_cenas - 1 - idx) * 1500)
            fim_cena = max(min_fim, min(target_ms, max_fim))

        c_dict["inicio_ms"] = cursor_inicio
        c_dict["fim_ms"] = fim_cena
        c_dict["duracao_ms"] = fim_cena - cursor_inicio
        cenas_timeline.append(c_dict)
        cursor_inicio = fim_cena

    return cenas_timeline

def _alinhar_cenas_whisper_continuo(
    cenas: List[Dict[str, Any]],
    palavras: List[Dict[str, Any]],
    duracao_total_ms: int
) -> List[Dict[str, Any]]:
    """
    Alinha os cortes visuais das cenas diretamente a partir da lista contínua de palavras do Whisper.
    Usa cursor sequencial monotônico que busca o final da fala de cada cena sem saltos reversos:
    - cena[0] inicia em 0.
    - cena[k].inicio_ms = cena[k-1].fim_ms.
    - última cena termina exatamente em duracao_total_ms.
    """
    total_cenas = len(cenas)
    if total_cenas == 0:
        return []

    cursor_w = 0
    cenas_timeline = []
    cursor_ms = 0

    import difflib

    for idx, c_orig in enumerate(cenas):
        c_dict = dict(c_orig)
        c_dict["id"] = idx + 1

        if idx == total_cenas - 1:
            fim_ms = duracao_total_ms
        else:
            tokens_cena = [w.lower().strip(".,!?") for w in c_orig.get("texto_fala", "").split() if w.strip()]
            n_tokens = max(1, len(tokens_cena))
            ultimo_token = tokens_cena[-1] if tokens_cena else ""

            # Janela de busca para encontrar o encerramento da fala da cena
            janela = palavras[cursor_w : cursor_w + n_tokens + 8]
            melhor_offset = n_tokens - 1
            melhor_score = 0.0

            for off, w in enumerate(janela):
                w_limpo = w.get("word", "").lower().strip(".,!?")
                if w_limpo == ultimo_token:
                    melhor_offset = off
                    break
                ratio = difflib.SequenceMatcher(None, w_limpo, ultimo_token).ratio()
                if ratio > 0.75 and ratio > melhor_score:
                    melhor_score = ratio
                    melhor_offset = off

            w_idx = min(len(palavras) - 1, cursor_w + melhor_offset) if palavras else 0
            target_ms = palavras[w_idx]["end_ms"] if palavras else int(round((idx + 1) / total_cenas * duracao_total_ms))

            min_fim = cursor_ms + 1500
            max_fim = duracao_total_ms - ((total_cenas - 1 - idx) * 1500)
            fim_ms = max(min_fim, min(target_ms, max_fim))
            cursor_w = min(len(palavras) - 1, w_idx + 1) if palavras else 0

        c_dict["inicio_ms"] = cursor_ms
        c_dict["fim_ms"] = fim_ms
        c_dict["duracao_ms"] = fim_ms - cursor_ms
        cenas_timeline.append(c_dict)
        cursor_ms = fim_ms

    return cenas_timeline

def auditar_timeline(timeline: dict, caminho_audio_mp3: str) -> None:
    """
    Auditoria automática estrita de sincronia:
    - Lança ValueError se houver sobreposição entre blocos de legendas.
    - Lança ValueError se houver salto reverso em palavras ou cenas.
    - Valida consistência de duração com o arquivo narracao.mp3 medido via ffprobe.
    - Imprime resumo visual com as 3 primeiras e 3 últimas legendas.
    """
    duracao_audio_ms = _obter_duracao_audio_ms(caminho_audio_mp3)
    duracao_total_ms = int(timeline.get("duracao_total_ms", 0))

    if abs(duracao_audio_ms - duracao_total_ms) > 600:
        raise ValueError(
            f"Divergência crítica de duração: áudio ffprobe={duracao_audio_ms}ms, "
            f"timeline={duracao_total_ms}ms (diferença > 600ms)"
        )

    legendas = timeline.get("legendas", [])
    if not legendas:
        raise ValueError("A timeline não contém blocos de legendas!")

    for i in range(len(legendas)):
        b = legendas[i]
        b_ini = b.get("inicio_ms", -1)
        b_fim = b.get("fim_ms", -1)

        if b_fim <= b_ini:
            raise ValueError(f"Bloco {i} com duração inválida: inicio={b_ini}ms, fim={b_fim}ms")

        if i > 0:
            b_ant_fim = legendas[i-1].get("fim_ms", 0)
            if b_ini < b_ant_fim:
                raise ValueError(
                    f"SOBREPOSIÇÃO DETECTADA! Bloco {i} inicio ({b_ini}ms) "
                    f"< Bloco {i-1} fim ({b_ant_fim}ms)"
                )

        palavras = b.get("palavras", [])
        w_prev_end = b_ini
        for j, w in enumerate(palavras):
            w_st = w.get("start_ms", -1)
            w_en = w.get("end_ms", -1)
            if w_en <= w_st:
                raise ValueError(f"Palavra {j} do Bloco {i} inválida: '{w.get('word')}' [{w_st}-{w_en}ms]")
            if j > 0 and w_st < w_prev_end:
                raise ValueError(f"Salto reverso de palavra no Bloco {i}: '{w.get('word')}' [{w_st}-{w_en}ms]")
            w_prev_end = w_en

    cenas = timeline.get("cenas", [])
    if not cenas:
        raise ValueError("A timeline não contém cenas!")

    if len(cenas) >= 12 and duracao_audio_ms < 45000:
        raise ValueError(
            f"Áudio truncado detectado! Duração {duracao_audio_ms}ms < 45000ms para {len(cenas)} cenas. "
            "Abortando renderização."
        )

    if cenas[0].get("inicio_ms", -1) != 0:
        raise ValueError(f"Cena 1 deve iniciar em 0ms (atual: {cenas[0].get('inicio_ms')}ms)")

    for k in range(1, len(cenas)):
        if cenas[k]["inicio_ms"] != cenas[k-1]["fim_ms"]:
            raise ValueError(f"Descontinuidade de cenas: Cena {k+1} inicio != Cena {k} fim")

    if abs(cenas[-1]["fim_ms"] - duracao_total_ms) > 30:
        raise ValueError(f"Última cena fim ({cenas[-1]['fim_ms']}ms) != duracao_total_ms ({duracao_total_ms}ms)")

    print("\n" + "=" * 68)
    print("=== AUDITORIA DE SINCRONIA DETERMINÍSTICA (FATOR X) ===")
    print(f"  • Áudio Real (ffprobe) : {duracao_audio_ms} ms ({duracao_audio_ms/1000:.2f}s)")
    print(f"  • Duração Timeline     : {duracao_total_ms} ms")
    print(f"  • Total de Cenas       : {len(cenas)} cenas contínuas")
    print(f"  • Total de Legendas    : {len(legendas)} blocos disjuntos (0 sobreposições)")
    print("  • Primeiras 3 Legendas :")
    for b in legendas[:3]:
        txt = " ".join(p["word"] for p in b["palavras"])
        print(f"     [{b['inicio_ms']:05d}ms - {b['fim_ms']:05d}ms] {txt}")
    print("  • Últimas 3 Legendas   :")
    for b in legendas[-3:]:
        txt = " ".join(p["word"] for p in b["palavras"])
        print(f"     [{b['inicio_ms']:05d}ms - {b['fim_ms']:05d}ms] {txt}")
    print("  [✔] APROVADO: Sincronia 100% determinística, monotônica e auditada!")
    print("=" * 68 + "\n")

def gerar_audio_e_sincronizar(
    roteiro: dict,
    caminho_audio_mp3: str,
    caminho_timeline_json: str,
    motor_tts: str = "auto"
) -> dict:
    """
    Executa o pipeline determinístico completo de áudio e sincronia:
    1. Sintetiza em áudio único no ElevenLabs (ou cena-a-cena no Edge-TTS dependendo de 'motor_tts').
    2. Aplica masterização broadcast de estúdio.
    3. Mede duração real via ffprobe com validação de sanidade (>= 45s).
    4. Roda Whisper diretamente no narracao.mp3 final (ou fallback fonético estruturado).
    5. Agrupa palavras em legendas disjuntas e particiona cenas de forma desacoplada.
    6. Executa auditar_timeline antes de gravar timeline.json.
    """
    cenas = roteiro.get("cenas", [])
    if not cenas:
        raise ValueError("Roteiro não contém cenas para narrar.")

    caminho_audio_mp3 = os.path.abspath(caminho_audio_mp3)
    caminho_timeline_json = os.path.abspath(caminho_timeline_json)

    os.makedirs(os.path.dirname(caminho_audio_mp3), exist_ok=True)
    os.makedirs(os.path.dirname(caminho_timeline_json), exist_ok=True)

    temp_dir = os.path.abspath(os.path.join(os.path.dirname(caminho_audio_mp3), "_temp_scenes"))
    os.makedirs(temp_dir, exist_ok=True)

    # 1. Geração do arquivo de silêncio de 120ms
    silencio_path = os.path.join(temp_dir, "silence_120ms.mp3")
    _gerar_arquivo_silencio(silencio_path, PAUSA_CENA_S)

    # 2. Avalia motor TTS: 'auto', 'edge' ou 'elevenlabs'
    motor_tts_norm = (motor_tts or "auto").lower().strip()
    eleven_client = ElevenLabsClient()

    if motor_tts_norm == "edge":
        usar_elevenlabs = False
        print("[🎙️] Flag --tts edge ativada: usando exclusivamente Edge-TTS (Custo: 0 créditos ElevenLabs).")
    elif motor_tts_norm == "elevenlabs":
        usar_elevenlabs = True
        print(f"[🎙️] Flag --tts elevenlabs forçada: usando ElevenLabs ({eleven_client.voice_name}).")
    else:
        usar_elevenlabs = eleven_client.sessao_valida()

    sintetizou_eleven_unico = False
    raw_narracao_eleven = os.path.join(temp_dir, "raw_narracao_eleven.mp3")

    total_palavras_rot = sum(len(c.get("texto_fala", "").split()) for c in cenas)
    min_esperado_ms = max(45000, total_palavras_rot * 200) if len(cenas) >= 12 else 20000

    if usar_elevenlabs:
        print(f"[🎙️] Modo ElevenLabs ATIVO ({eleven_client.voice_name}): Sintetizando roteiro unificado completo...")
        # Usa espaço simples (bloco contínuo) para ProseMirror não descartar parágrafos
        texto_cenas = [normalizar_texto_para_tts(c.get("texto_fala", "").strip()) for c in cenas]
        texto_completo = " ".join(t for t in texto_cenas if t)
        try:
            sintetizou_eleven_unico = eleven_client.sintetizar_texto_unico_playwright(texto_completo, raw_narracao_eleven)
        except Exception as e_el:
            print(f"  [!] Falha na síntese unificada ElevenLabs: {e_el}. Usando fallback Edge-TTS...")
            sintetizou_eleven_unico = False

    # Validação rigorosa de sanidade da duração do áudio bruto ElevenLabs
    if sintetizou_eleven_unico and os.path.exists(raw_narracao_eleven):
        dur_raw_ms = _obter_duracao_audio_ms(raw_narracao_eleven)
        if dur_raw_ms < min_esperado_ms:
            print(f"[⚠️] ALERTA CRÍTICO: Áudio bruto ElevenLabs truncado detectado ({dur_raw_ms}ms < {min_esperado_ms}ms)!")
            print(f"[*] Rejeitando áudio de {dur_raw_ms/1000:.1f}s e ativando fallback automático Edge-TTS masterizado...")
            sintetizou_eleven_unico = False
            try:
                os.remove(raw_narracao_eleven)
            except Exception:
                pass

    if sintetizou_eleven_unico and os.path.exists(raw_narracao_eleven) and os.path.getsize(raw_narracao_eleven) > 8000:
        # Caminho A: ElevenLabs Único Definitivo (Zero race condition, zero duplicações)
        print("[🎛️] Masterizando áudio unificado ElevenLabs com cadeia broadcast (EQ, compressor e -16 LUFS)...")
        sucesso_master = masterizar_audio_estudio(raw_narracao_eleven, caminho_audio_mp3)
        if not sucesso_master or not os.path.exists(caminho_audio_mp3) or os.path.getsize(caminho_audio_mp3) < 4000:
            import shutil
            shutil.copy2(raw_narracao_eleven, caminho_audio_mp3)
            print("  [-] Áudio mantido sem masterização adicional.")
        else:
            print("  [✔] Áudio ElevenLabs definitivo salvo e masterizado!")

        duracao_total_ms = _obter_duracao_audio_ms(caminho_audio_mp3)
        if duracao_total_ms < min_esperado_ms:
            print(f"[⚠️] ALERTA CRÍTICO: Áudio masterizado ElevenLabs truncado ({duracao_total_ms}ms < {min_esperado_ms}ms)!")
            print(f"[*] Ativando fallback automático Edge-TTS cena-por-cena...")
            sintetizou_eleven_unico = False
            try:
                os.remove(caminho_audio_mp3)
            except Exception:
                pass

    if sintetizou_eleven_unico and os.path.exists(caminho_audio_mp3):
        duracao_total_ms = _obter_duracao_audio_ms(caminho_audio_mp3)
        palavras_whisper_raw = _transcrever_whisper_direto(caminho_audio_mp3)
        if palavras_whisper_raw and len(palavras_whisper_raw) >= 15:
            palavras_processadas = _processar_palavras_whisper(palavras_whisper_raw)
            todas_legendas = _agrupar_palavras_em_legendas(palavras_processadas, duracao_total_ms)
            cenas_timeline = _alinhar_cenas_whisper_continuo(cenas, palavras_processadas, duracao_total_ms)
        else:
            print("[*] Aplicando distribuição fonética proporcional sobre o áudio contínuo (Fallback)...")
            pesos = [max(1, len(c.get("texto_fala", "").split())) for c in cenas]
            tot_w = max(1, sum(pesos))
            palavras_fallback = []
            cur_ms = 0
            for idx, c in enumerate(cenas):
                dur_cena = int(round((pesos[idx] / tot_w) * duracao_total_ms))
                p_c = _calcular_palavras_cena_fallback(normalizar_texto_para_tts(c.get("texto_fala", "")), dur_cena, cur_ms)
                palavras_fallback.extend(p_c)
                cur_ms += dur_cena
            palavras_processadas = _processar_palavras_whisper(palavras_fallback)
            todas_legendas = _agrupar_palavras_em_legendas(palavras_processadas, duracao_total_ms)
            cenas_timeline = _alinhar_cenas_whisper_continuo(cenas, palavras_processadas, duracao_total_ms)

    else:
        # Caminho B: Fallback Seguro com Edge-TTS cena-por-cena (local/assíncrono sem race condition)
        if usar_elevenlabs and not sintetizou_eleven_unico:
            print(f"[🎙️] Ativando fallback Edge-TTS ({VOZ_PADRAO}) para as {len(cenas)} cenas...")
        else:
            print(f"[🎙️] Sintetizando {len(cenas)} cenas com Edge-TTS ({VOZ_PADRAO})...")

        arquivos_cenas = []
        for idx, c in enumerate(cenas):
            f_cena = os.path.join(temp_dir, f"scene_{idx:02d}.mp3")
            texto = normalizar_texto_para_tts(c.get("texto_fala", "").strip())

            async def _sint_edge(txt: str, out_p: str):
                comm = edge_tts.Communicate(txt, VOZ_PADRAO, rate=RATE_PADRAO, pitch=PITCH_PADRAO)
                await comm.save(out_p)
            asyncio.run(_sint_edge(texto, f_cena))
            arquivos_cenas.append((idx, f_cena))

        arquivos_cenas.sort(key=lambda r: r[0])

        # Concatenação de áudio via FFmpeg
        concat_lines = []
        duracoes_cenas_ms = []
        for idx, f_cena in arquivos_cenas:
            dur_ms = _obter_duracao_audio_ms(f_cena)
            duracoes_cenas_ms.append(dur_ms)
            nome_cena = os.path.basename(f_cena)
            concat_lines.append(f"file '{nome_cena}'")
            concat_lines.append("file 'silence_120ms.mp3'")

        concat_txt_path = os.path.join(temp_dir, "concat_list.txt")
        with open(concat_txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(concat_lines) + "\n")

        raw_concat_mp3 = os.path.join(temp_dir, "raw_concat.mp3")
        cmd_concat = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", "concat_list.txt",
            "-c:a", "libmp3lame",
            "-q:a", "2",
            raw_concat_mp3
        ]
        res_concat = subprocess.run(cmd_concat, capture_output=True, text=True, cwd=temp_dir)
        if res_concat.returncode != 0:
            raise RuntimeError(f"Falha no FFmpeg concat: {res_concat.stderr[:300]}")

        print("[🎛️] Masterizando áudio definitivo com cadeia broadcast (EQ anti-metálico, compressor e -16 LUFS)...")
        sucesso_master = masterizar_audio_estudio(raw_concat_mp3, caminho_audio_mp3)
        if not sucesso_master or not os.path.exists(caminho_audio_mp3) or os.path.getsize(caminho_audio_mp3) < 4000:
            import shutil
            shutil.copy2(raw_concat_mp3, caminho_audio_mp3)
            print("  [-] Áudio concatenado mantido sem masterização adicional.")
        else:
            print("  [✔] Áudio definitivo salvo e masterizado!")

        duracao_total_ms = _obter_duracao_audio_ms(caminho_audio_mp3)
        if duracao_total_ms <= 0:
            raise RuntimeError(f"Erro ao medir duração do áudio final gerado: {caminho_audio_mp3}")

        palavras_whisper_raw = _transcrever_whisper_direto(caminho_audio_mp3)
        if palavras_whisper_raw and len(palavras_whisper_raw) >= 15:
            palavras_processadas = _processar_palavras_whisper(palavras_whisper_raw)
            todas_legendas = _agrupar_palavras_em_legendas(palavras_processadas, duracao_total_ms)
            cenas_timeline = _alinhar_cenas_timeline(cenas, palavras_processadas, duracao_total_ms, duracoes_cenas_ms)
        else:
            print("[*] Aplicando sincronização fonética determinística de alta precisão (Fallback)...")
            palavras_fallback = []
            offset_cena_ms = 0
            for idx, f_cena in arquivos_cenas:
                cena_orig = cenas[idx]
                dur_fala = duracoes_cenas_ms[idx]
                p_cena = _calcular_palavras_cena_fallback(
                    normalizar_texto_para_tts(cena_orig.get("texto_fala", "")),
                    dur_fala,
                    offset_cena_ms
                )
                palavras_fallback.extend(p_cena)
                offset_cena_ms += dur_fala + PAUSA_CENA_MS

            palavras_processadas = _processar_palavras_whisper(palavras_fallback)
            todas_legendas = _agrupar_palavras_em_legendas(palavras_processadas, duracao_total_ms)
            cenas_timeline = _alinhar_cenas_timeline(cenas, palavras_processadas, duracao_total_ms, duracoes_cenas_ms)

    # 7. Limpeza dos temporários
    try:
        for f in os.listdir(temp_dir):
            os.remove(os.path.join(temp_dir, f))
        os.rmdir(temp_dir)
    except Exception:
        pass

    # 8. Montagem do payload da timeline
    timeline_data = {
        "titulo": roteiro.get("titulo", "Vídeo Viral"),
        "hashtags": roteiro.get("hashtags", "#curiosidades #fatos #vocesabia"),
        "tema_cor": roteiro.get("tema_cor", "#38BDF8"),
        "duracao_total_ms": duracao_total_ms,
        "total_cenas": len(cenas_timeline),
        "total_palavras": sum(len(c.get("texto_fala", "").split()) for c in cenas),
        "cenas": cenas_timeline,
        "legendas": todas_legendas
    }

    # 9. Auto-auditoria estrita obrigatória antes de gravar
    auditar_timeline(timeline_data, caminho_audio_mp3)

    with open(caminho_timeline_json, "w", encoding="utf-8") as f:
        json.dump(timeline_data, f, ensure_ascii=False, indent=2)

    return timeline_data

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Gerador e sincronizador de áudio e legendas (Fator X)")
    parser.add_argument("roteiro", nargs="?", help="Caminho do arquivo roteiro.json")
    parser.add_argument("--auditar", nargs=2, metavar=("TIMELINE", "AUDIO"), help="Audita sincronia de timeline.json e narracao.mp3")
    parser.add_argument("--tts", type=str, choices=["auto", "edge", "elevenlabs"], default="auto", help="Motor TTS (auto, edge, elevenlabs)")
    args = parser.parse_args()

    if args.auditar:
        with open(args.auditar[0], "r", encoding="utf-8") as f:
            timeline = json.load(f)
        auditar_timeline(timeline, args.auditar[1])
        return

    if not args.roteiro:
        parser.print_help()
        sys.exit(1)

    caminho_roteiro = args.roteiro
    with open(caminho_roteiro, "r", encoding="utf-8") as f:
        roteiro = json.load(f)

    dir_saida = os.path.dirname(caminho_roteiro) or "output"
    caminho_audio = os.path.join(dir_saida, "narracao.mp3")
    caminho_timeline = os.path.join(dir_saida, "timeline.json")

    gerar_audio_e_sincronizar(roteiro, caminho_audio, caminho_timeline, motor_tts=args.tts)

if __name__ == "__main__":
    main()
