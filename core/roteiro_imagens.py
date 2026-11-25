#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
roteiro_imagens.py - Gerador de Roteiro de 8 a 10 Cenas para o Modo Imagens do @um.lapso.
Consome o estoque de temas (estoque_temas.txt), grava no histórico (historico_temas.txt)
e produz JSON estruturado com 8 a 10 cenas (~45s a 55s de narração, 120 a 150 palavras).
"""

import os
import re
import json
import logging
from typing import Optional, Dict, Any, List
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

def sanitizar_texto_fala(texto: str, eh_ultima_cena: bool = False) -> str:
    """
    Higieniza o texto falado e garante que termine obrigatoriamente com pontuação
    completa (. ? !), impedindo qualquer frase truncada no meio.
    """
    s = (texto or "").strip()
    # Remove aspas soltas ou quebras de linha
    s = re.sub(r'["\r\n\t]+', ' ', s).strip()
    if not s:
        return "A física do planeta entraria em colapso antes que você pudesse reagir." if not eh_ultima_cena else "Você ficaria na superfície ou tentaria se esconder nos abrigos subterrâneos?"

    # Valida pontuação final
    if not s[-1] in ('.', '?', '!'):
        s += "?" if eh_ultima_cena else "."
    return s

def gerar_roteiro_cenas(tema: str) -> Dict[str, Any]:
    """
    Gera de 8 a 10 cenas cinematográficas no estilo @um.lapso via GPT-4o-mini.
    Total entre 120 e 150 palavras (~45s a 55s de narração fluida), sempre no singular ('você').
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY ausente no ambiente.")

    client = OpenAI(api_key=api_key)

    system_prompt = """
Você é o roteirista sênior e estrategista de retenção do canal '@um.lapso' (Mundo Improvável / Curiosidades Cósmicas / Paradoxos Científicos no TikTok, Reels e YouTube Shorts).
Sua missão é criar um roteiro cinematográfico de ALTÍSSIMA RETENÇÃO E ENGAJAMENTO contendo OBRIGATORIAMENTE entre 8 e 10 cenas curtas (~45s a 55s no total).

REGRAS ESTRITAS DE RETENÇÃO E PSICOLOGIA DE FEED:

1. GANCHO DE CHOQUE NOS PRIMEIROS 3 SEGUNDOS (Cena 1):
   - PROIBIDO TERMINANTEMENTE começar com clichês lentos como: "Imagine que você...", "Você já parou para pensar...", "Neste vídeo...", "E se...", "Existe um lugar...", "O universo guarda...".
   - A Cena 1 DEVE começar no meio da consequência física mais chocante, com um dado visceral, tempo limite ou afirmação contraintuitiva imediata que paralisa a rolagem do feed.
   - Exemplos de Cena 1 obrigatória:
     * "Em menos de 4 minutos, todo o oxigênio dos seus pulmões seria sugado para o vácuo se isso acontecesse."
     * "A temperatura na sua pele saltaria para 800 graus antes mesmo do som da explosão alcançar seus ouvidos."
     * "Se a gravidade falhasse por três segundos, você seria arremessado ao espaço a mais de mil quilômetros por hora."

2. LINGUAGEM CONCRETA E VISCERAL (ZERO POESIA ABSTRATA OU VAGA):
   - PROIBIDO usar devaneios vazios como "a essência do cosmo se transforma", "mistérios que desafiam a compreensão", "o tempo se torna uma ilusão".
   - Use SEMPRE fatos físicos e sensoriais tangíveis: o que acontece com a carne, os ossos, a água dos oceanos, a atmosfera, o aço dos prédios, a temperatura e a gravidade.
   - Fale SEMPRE no singular direcionado ao espectador ("você sente", "você observa", "seu corpo"). NUNCA use "nós", "galera" ou "vocês".

3. ESTRUTURA DE ESCALADA / LINHA DO TEMPO (Cenas 2 a 8/9):
   - Conduza a narrativa como uma linha do tempo implacável de causa e efeito (ex.: "Nos primeiros segundos...", "Após 10 minutos...", "Ao cair da primeira noite...", "No terceiro dia...").
   - Cada cena eleva a tensão com uma nova consequência inevitável, bizarra e cientificamente plausível.

4. FINAL COM CTA POLARIZADOR + GANCHO DE LOOP (Última Cena):
   - A última cena DEVE terminar com uma pergunta curta, polarizadora e de escolha binária que force o espectador a debater nos comentários.
   - Exemplo: "Você tentaria sobreviver na superfície congelada ou arriscaria descer para os bunkers subterrâneos? Deixe sua escolha."
   - Conecte o sentido da última fala de volta com o gancho da Cena 1, criando uma transição perfeita para quem reassiste em loop.

5. EXTENSÃO E PONTUAÇÃO OBRIGATÓRIA:
   - Exatamente entre 8 e 10 cenas.
   - Cada cena DEVE ter entre 13 e 17 palavras (total geral entre 120 e 150 palavras, ideal para ~45s a 55s de narração cadenciada).
   - NUNCA deixe frases cortadas ou sem sentido. TODAS as falas DEVEM terminar com pontuação (. ? ou !).

6. PROMPTS VISUAIS (prompt_imagem_en):
   - Para cada cena, crie um 'prompt_imagem_en' altamente descritivo e cinematográfico em inglês, focado na ação física descrita na cena.
   - Estilo: Dark surrealism cinematic, 9:16 vertical framing, atmospheric cosmic lighting, high contrast, dramatic shadows, photorealistic 8k, Unreal Engine 5 render, SEM NENHUM TEXTO, LETRAS, BORDAS OU SUBTÍTULOS.

7. METADADOS E SEO:
   - 'titulo': Título magnético e intrigante de alto CTR (máximo 60 caracteres).
   - 'descricao': Sinopse rápida em 1 a 2 linhas com chamada provocativa.
   - 'legenda_seo': Gancho de texto magnético + 3 a 5 hashtags cirúrgicas (ex: #umlapso #misterio #curiosidades #universo #paradoxo).

Retorne EXCLUSIVAMENTE um objeto JSON válido com a seguinte estrutura:
{
  "tema": "<tema>",
  "titulo": "<titulo_max_60_chars>",
  "descricao": "<descricao_curta>",
  "legenda_seo": "<legenda_com_3_a_5_hashtags>",
  "cenas": [
    {
      "id": 1,
      "texto_fala": "<fala da cena 1 com pontuação completa>",
      "prompt_imagem_en": "<prompt em inglês da cena 1, 9:16 vertical dark surrealism>"
    },
    ...
  ]
}
"""

    logger.info(f"[*] Gerando roteiro expandido de 8 a 10 cenas (~45-55s) via GPT-4o-mini para: '{tema}'...")
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Crie um roteiro cinematográfico completo de 8 a 10 cenas para o tema: {tema}"}
        ],
        temperature=0.75
    )

    conteudo = response.choices[0].message.content
    try:
        dados = json.loads(conteudo)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Erro ao decodificar JSON do roteiro: {e}\nResposta crua: {conteudo}")

    cenas = dados.get("cenas", [])
    if len(cenas) > 10:
        logger.info(f"[*] Roteiro gerado com {len(cenas)} cenas. Ajustando para o teto de 10 cenas.")
        cenas = cenas[:10]

    # Validação rigorosa de cada cena
    cenas_validadas = []
    total_palavras = 0
    total_cenas = len(cenas)

    for i, c in enumerate(cenas):
        c_id = i + 1
        eh_ultima = (i == total_cenas - 1)
        texto_original = c.get("texto_fala", "")
        texto_limpo = sanitizar_texto_fala(texto_original, eh_ultima_cena=eh_ultima)
        prompt_en = c.get("prompt_imagem_en", "").strip() or f"Dark surreal cinematic visual of {tema}, 9:16 vertical, photorealistic 8k, cosmic mystery"

        palavras = texto_limpo.split()
        total_palavras += len(palavras)

        cenas_validadas.append({
            "id": c_id,
            "texto_fala": texto_limpo,
            "prompt_imagem_en": prompt_en
        })

    dados["cenas"] = cenas_validadas
    dados["tema"] = tema
    logger.info(f"[+] Roteiro estruturado: {len(cenas_validadas)} cenas | {total_palavras} palavras no total | Título: \"{dados.get('titulo')}\"")
    return dados
