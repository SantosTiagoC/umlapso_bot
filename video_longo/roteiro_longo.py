#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
roteiro_longo.py - Gerador de Roteiro Documental 16:9 (3 a 5 minutos) para @um.lapso no YouTube.
- Consome temas de estoque_temas.txt sem modificar o arquivo original.
- Salva histórico exclusivo em data/historico_temas_longos.txt.
- Gera entre 450 e 720 palavras (22 a 32 cenas, 4 a 5 capítulos).
- Identidade: mistério cósmico, ciência surreal, interlocução em 'você', fechando com pergunta de escolha.
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

logger = logging.getLogger("roteiro_longo")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

CAMINHO_HISTORICO_LONGOS = os.path.join(ROOT_DIR, "data", "historico_temas_longos.txt")
CAMINHO_ESTOQUE = os.path.join(ROOT_DIR, "estoque_temas.txt")

def obter_tema_longo(tema_custom: Optional[str] = None) -> str:
    """
    Obtém tema customizado ou seleciona o próximo tema inédito para vídeo longo,
    registrando exclusivamente em data/historico_temas_longos.txt sem alterar estoque_temas.txt.
    """
    os.makedirs(os.path.dirname(CAMINHO_HISTORICO_LONGOS), exist_ok=True)
    
    temas_usados = set()
    if os.path.exists(CAMINHO_HISTORICO_LONGOS):
        try:
            with open(CAMINHO_HISTORICO_LONGOS, "r", encoding="utf-8") as f:
                temas_usados = {linha.strip() for linha in f if linha.strip()}
        except Exception:
            pass

    if tema_custom and tema_custom.strip():
        tema = tema_custom.strip()
        if tema not in temas_usados:
            try:
                with open(CAMINHO_HISTORICO_LONGOS, "a", encoding="utf-8") as f:
                    f.write(tema + "\n")
            except Exception:
                pass
        return tema

    # Busca no estoque sem remover a linha do arquivo para não alterar arquivos existentes
    if os.path.exists(CAMINHO_ESTOQUE):
        try:
            with open(CAMINHO_ESTOQUE, "r", encoding="utf-8") as f:
                candidatos = [linha.strip() for linha in f if linha.strip()]
            for c in candidatos:
                if c not in temas_usados:
                    with open(CAMINHO_HISTORICO_LONGOS, "a", encoding="utf-8") as f:
                        f.write(c + "\n")
                    logger.info(f"[+] Tema selecionado do estoque para vídeo longo: {c}")
                    return c
        except Exception as e:
            logger.warning(f"[!] Erro ao ler estoque de temas: {e}")

    # Fallback: Gera tema longo dinamicamente via OpenAI
    logger.info("[*] Gerando novo tema documental longo inédito via OpenAI...")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "E se a Terra entrasse na órbita de um buraco negro invisível?"

    client = OpenAI(api_key=api_key)
    prompt_tema = f"""
    Atue como diretor de criação do canal @um.lapso (Mundo Improvável / Ficção Científica / Paradoxos Cósmicos).
    Gere 1 tema épico e profundo para documentário cinematográfico de 4 a 5 minutos no YouTube ("E se...").
    
    Temas já usados recentemente:
    {json.dumps(list(temas_usados)[-25:], ensure_ascii=False)}
    
    Retorne apenas JSON: {{"tema": "E se... (frase instigante)"}}
    """
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt_tema}],
            temperature=0.85
        )
        dados = json.loads(res.choices[0].message.content)
        novo_tema = dados.get("tema", "").strip() or "E se o núcleo da Terra começasse a esfriar abruptamente?"
        with open(CAMINHO_HISTORICO_LONGOS, "a", encoding="utf-8") as f:
            f.write(novo_tema + "\n")
        return novo_tema
    except Exception as e:
        logger.warning(f"[!] Erro ao gerar tema dinâmico: {e}")
        return "E se a velocidade da luz diminuísse para 100 quilômetros por hora?"

def sanitizar_fala_pt(texto: str, eh_ultima: bool = False) -> str:
    """Garante que a fala termine com pontuação gramatical completa."""
    s = (texto or "").strip()
    s = re.sub(r'["\r\n\t]+', ' ', s).strip()
    if not s:
        return "O universo guarda mistérios que desafiam nossa compreensão." if not eh_ultima else "Qual caminho você escolheria se estivesse lá?"
    if s[-1] not in ('.', '?', '!'):
        s += "?" if eh_ultima else "."
    return s

def expandir_cenas_curtas(client: OpenAI, capitulos_raw: List[Dict[str, Any]], tema: str) -> List[Dict[str, Any]]:
    """Expande as falas das cenas com OpenAI caso a contagem inicial de palavras fique abaixo de 440."""
    prompt_exp = f"""
    Você é o diretor narrativo de documentários de @um.lapso.
    O roteiro gerado sobre o tema '{tema}' possui cenas com falas muito curtas.
    Sua missão é REESCREVER e EXPANDIR o texto 'fala_pt' de CADA uma das cenas para conter OBRIGATORIAMENTE entre 20 e 28 palavras em português, com tom solene, atmosférico, cósmico e imersivo, sempre no singular ('você') e com pontuação gramatical completa no final (. ? !).
    Mantenha EXATAMENTE os mesmos capítulos, mesma quantidade de cenas, mesmos IDs e mesmos 'prompt_imagem_en'. Apenas expanda o campo 'fala_pt'.

    Roteiro atual para expansão:
    {json.dumps(capitulos_raw, ensure_ascii=False)}

    Retorne EXCLUSIVAMENTE um objeto JSON:
    {{"capitulos": [...]}}
    """
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt_exp}],
            temperature=0.7
        )
        dados_exp = json.loads(res.choices[0].message.content)
        caps = dados_exp.get("capitulos", [])
        if caps and isinstance(caps, list) and len(caps) == len(capitulos_raw):
            logger.info("[+] Cenas expandidas com sucesso pela OpenAI para atingir duração ideal.")
            return caps
    except Exception as e:
        logger.warning(f"[!] Falha na expansão de cenas: {e}")
    return capitulos_raw

def gerar_roteiro_longo(tema: str) -> Dict[str, Any]:
    """
    Gera roteiro documental de 3 a 5 minutos (450 a 720 palavras, 4 a 5 capítulos, 22 a 32 cenas).
    Formato horizontal widescreen 16:9, ritmo cinematográfico e pergunta de escolha concreta ao final.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY ausente no arquivo .env.")

    client = OpenAI(api_key=api_key)

    system_prompt = """
Você é o roteirista-chefe e diretor narrativo do canal '@um.lapso', especializado em documentários cinematográficos hipnóticos de mistério cósmico, ciência surreal e cenários "E se...".

Sua missão é escrever um roteiro completo de 3 a 5 minutos para vídeo horizontal Full HD (16:9) no YouTube.

REGRAS OBRIGATÓRIAS DE ESTRUTURA E EXTENSÃO:
1. Duração: Entre 500 e 720 palavras narradas no total (média de 20 a 28 palavras por cena para atingir de 3m30s a 5m00s).
2. Divisão em Capítulos: Exatamente 4 ou 5 capítulos bem definidos (Exemplo: 1. O Ponto de Ruptura / 2. As Primeiras Horas do Caos / 3. A Nova Física da Realidade / 4. O Colapso Silencioso / 5. O Paradoxo Final).
3. Divisão em Cenas: Cada capítulo deve conter entre 5 e 7 cenas. No total, o vídeo deve ter entre 22 e 28 cenas.
   - TAMANHO OBRIGATÓRIO DE CADA CENA: Cada cena DEVE conter obrigatoriamente entre 20 e 28 palavras ricas e descritivas em português.
   - PROIBIDO gerar cenas curtas de 8 a 15 palavras. Frases curtas deixam a narração rápida demais e o vídeo será rejeitado por duração insuficiente.
   - Cada cena corresponde a uma imagem cinematográfica horizontal 16:9 exibida por cerca de 8 a 10 segundos.
4. Identidade e Tom (@um.lapso):
   - Fale SEMPRE no singular ("você", "imagine que você observa...", "ao olhar para o horizonte..."). NUNCA use "nós", "galera" ou "vocês".
   - Estilo solene, reflexivo, atmosférico, quase poético, com momentos de tensão crescente.
   - A ÚLTIMA CENA deve obrigatoriamente terminar com uma pergunta de escolha concreta para instigar os comentários (ex: "Se você tivesse apenas uma decisão entre proteger a memória do passado ou abraçar o vazio desconhecido, qual das duas escolheria nos comentários?").
5. Pontuação Absoluta: TODAS as falas devem terminar com pontuação completa (. ? ou !). Nunca deixe frases incompletas.
6. Prompts de Imagem 16:9:
   - Para cada cena, crie um 'prompt_imagem_en' altamente descritivo em inglês.
   - Obrigatório incluir em todos: "widescreen 16:9 horizontal composition, cinematic, atmospheric cosmic lighting, photorealistic 8k, Unreal Engine 5 render, deep shadows, no text, no watermark, no border".
   - Crie também 'prompt_thumbnail_en' para a capa do YouTube: ultra impactante, alto contraste, sem texto, widescreen 16:9.
7. Metadados YouTube:
   - 'titulo': Título forte de alto CTR com até 75 caracteres, SEM a hashtag #Shorts e sem emojis exagerados.
   - 'descricao_base': Sinopse cativante em 2 parágrafos + pergunta de engajamento + 3 a 5 tags no final (SEM #Shorts).
   - 'tags': Lista de 8 a 12 tags estratégicas de busca no YouTube.

Retorne EXCLUSIVAMENTE um objeto JSON válido com este formato:
{
  "tema": "<tema>",
  "titulo": "<titulo_ate_75_chars>",
  "descricao_base": "<sinopse_e_chamada>",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"],
  "prompt_thumbnail_en": "widescreen 16:9 cinematic thumbnail, ultra detailed, dramatic lighting, epic cosmic visual, no text, no letters, no watermark",
  "capitulos": [
    {
      "nome_capitulo": "1. O Ponto de Ruptura",
      "cenas": [
        {
          "id": 1,
          "fala_pt": "Texto da cena 1.",
          "prompt_imagem_en": "widescreen 16:9 horizontal composition, cinematic dark surrealism..."
        }
      ]
    }
  ]
}
"""

    logger.info(f"[*] Gerando roteiro documental longo para o tema: '{tema}'...")
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Desenvolva o documentário cinematográfico completo sobre: {tema}"}
        ],
        temperature=0.75
    )

    conteudo = response.choices[0].message.content
    try:
        dados = json.loads(conteudo)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Erro ao decodificar JSON do roteiro longo: {e}\nResposta crua: {conteudo}")

    # Normaliza e valida capítulos e cenas
    capitulos_raw = dados.get("capitulos", [])
    if not capitulos_raw:
        raise ValueError("Roteiro gerado não contém lista de capítulos.")

    # Verifica se a contagem de palavras está abaixo do ideal para documentário
    palavras_iniciais = sum(
        len(c.get("fala_pt", "").split())
        for cap in capitulos_raw
        for c in cap.get("cenas", [])
    )
    if palavras_iniciais < 440:
        logger.info(f"[*] Roteiro com {palavras_iniciais} palavras (abaixo de 440). Expandindo imersivamente as cenas...")
        capitulos_raw = expandir_cenas_curtas(client, capitulos_raw, tema)

    cenas_planas = []
    capitulos_normalizados = []
    contador_cena = 1

    total_cenas_estimado = sum(len(c.get("cenas", [])) for c in capitulos_raw)
    palavras_totais = 0

    for cap_idx, cap in enumerate(capitulos_raw):
        nome_cap = cap.get("nome_capitulo", f"Capítulo {cap_idx + 1}").strip()
        cenas_cap = []
        for c in cap.get("cenas", []):
            eh_ultima = (contador_cena == total_cenas_estimado)
            texto_fala = sanitizar_fala_pt(c.get("fala_pt", ""), eh_ultima=eh_ultima)
            palavras = texto_fala.split()
            palavras_totais += len(palavras)

            prompt_en = c.get("prompt_imagem_en", "").strip()
            if "16:9" not in prompt_en:
                prompt_en = f"widescreen 16:9 horizontal composition, {prompt_en}, cinematic, no text, no watermark"

            cena_obj = {
                "id": contador_cena,
                "capitulo": nome_cap,
                "capitulo_idx": cap_idx,
                "fala_pt": texto_fala,
                "prompt_imagem_en": prompt_en
            }
            cenas_cap.append(cena_obj)
            cenas_planas.append(cena_obj)
            contador_cena += 1

        capitulos_normalizados.append({
            "nome_capitulo": nome_cap,
            "cenas": cenas_cap
        })

    titulo = dados.get("titulo", tema).replace("#Shorts", "").replace("#shorts", "").strip()
    descricao_base = dados.get("descricao_base", "").replace("#Shorts", "").replace("#shorts", "").strip()
    
    prompt_thumb = dados.get("prompt_thumbnail_en", "").strip()
    if not prompt_thumb or "16:9" not in prompt_thumb:
        prompt_thumb = f"widescreen 16:9 cinematic thumbnail, ultra detailed, dramatic lighting, epic cosmic visual of {tema}, no text, no letters, no watermark"

    resultado = {
        "tema": tema,
        "titulo": titulo[:75],
        "descricao_base": descricao_base,
        "tags": dados.get("tags", ["umlapso", "misterio", "ciencia", "universo", "documentario"]),
        "prompt_thumbnail_en": prompt_thumb,
        "capitulos": capitulos_normalizados,
        "cenas_planas": cenas_planas,
        "total_cenas": len(cenas_planas),
        "total_palavras": palavras_totais
    }

    logger.info(f"[+] Roteiro longo concluído: {len(capitulos_normalizados)} capítulos | {len(cenas_planas)} cenas | {palavras_totais} palavras | Título: '{resultado['titulo']}'")
    return resultado
