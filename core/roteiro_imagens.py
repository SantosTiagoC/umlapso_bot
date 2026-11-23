#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
roteiro_imagens.py - Gerador de Roteiro de 5 a 6 Cenas para o Modo Imagens do @um.lapso.
Consome o estoque de temas (estoque_temas.txt), grava no histórico (historico_temas.txt)
e produz JSON estruturado para síntese de voz (audio_imagens) e arte no Flow (flow_client).
"""

import os
import json
import logging
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from openai import OpenAI

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

logger = logging.getLogger("roteiro_imagens")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from core.brain_openai import gerar_novo_tema_inedito

def obter_tema(tema_custom: Optional[str] = None) -> str:
    """Obtém tema customizado ou consome o próximo inédito do estoque."""
    if tema_custom and tema_custom.strip():
        tema = tema_custom.strip()
        caminho_hist = os.path.join(ROOT_DIR, "historico_temas.txt")
        try:
            with open(caminho_hist, "a", encoding="utf-8") as f:
                f.write(tema + "\n")
        except Exception:
            pass
        return tema
    return gerar_novo_tema_inedito()

def gerar_roteiro_cenas(tema: str) -> Dict[str, Any]:
    """
    Gera 5 a 6 cenas cinematográficas curtas no estilo @um.lapso via GPT-4o-mini.
    Total de 70 a 90 palavras (~25s a 35s), falando sempre no singular ('você').
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY ausente no ambiente.")

    client = OpenAI(api_key=api_key)

    system_prompt = """
Você é o roteirista oficial do canal '@um.lapso' (Mundo Improvável / Curiosidades Cósmicas / Paradoxos).
Sua missão é criar um roteiro em 5 a 6 cenas cinematográficas curtas.

REGRAS ESTRITAS DE IDENTIDADE E ESTILO:
1. Tom: Hipnótico, reflexivo, sombrio, provocativo e misterioso.
2. Interlocução Direta: Fale SEMPRE no singular ("você", "imagine que você acorda...", "ao olhar para o horizonte..."). NUNCA use "nós", "galera" ou "vocês".
3. Ritmo e Concisão: O roteiro DEVE ter exatamente 5 ou 6 cenas curtas. Cada cena com 12 a 16 palavras. Total de 70 a 90 palavras (~25 a 35 segundos de narração fluida).
4. Prompts Visuais: Cada cena precisa de um 'prompt_imagem_en' extremamente descritivo em inglês, focado em arte cinematográfica dark surrealism, proporção vertical 9:16, iluminação volumétrica, sombras profundas e sem nenhum texto ou letras.
5. Metadados e SEO:
   - 'titulo': Título magnético e intrigante (máximo 60 caracteres).
   - 'descricao': Descrição curta e instigante com pergunta provocativa para os comentários.
   - 'legenda_seo': Resumo magnético + 3 a 5 hashtags cirúrgicas (ex: #umlapso #misterio #curiosidades #universo #scifi).

Retorne EXCLUSIVAMENTE um objeto JSON válido com a seguinte estrutura:
{
  "tema": "<tema>",
  "titulo": "<titulo_max_60_chars>",
  "descricao": "<descricao_curta>",
  "legenda_seo": "<legenda com 3 a 5 hashtags>",
  "cenas": [
    {
      "id": 1,
      "texto_fala": "<texto narrado em portugues da cena 1>",
      "prompt_imagem_en": "<prompt em ingles da cena 1, vertical 9:16 dark surrealism cinematic>"
    },
    ...
  ]
}
"""

    logger.info(f"[*] Gerando roteiro de 5 a 6 cenas via GPT-4o-mini para: '{tema}'...")
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Tema: {tema}"}
        ],
        temperature=0.75
    )

    conteudo = response.choices[0].message.content
    try:
        dados = json.loads(conteudo)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Erro ao decodificar JSON do roteiro: {e}\nResposta crua: {conteudo}")

    cenas = dados.get("cenas", [])
    if len(cenas) < 5 or len(cenas) > 6:
        logger.warning(f"[!] Roteiro gerado retornou {len(cenas)} cenas. Ajustando para 5-6 cenas...")
        if len(cenas) > 6:
            dados["cenas"] = cenas[:6]

    dados["tema"] = tema
    logger.info(f"[+] Roteiro estruturado com {len(dados['cenas'])} cenas: \"{dados.get('titulo')}\"")
    return dados
