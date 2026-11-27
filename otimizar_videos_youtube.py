#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
otimizar_videos_youtube.py - Analisador e Otimizador de Títulos e Descrições para o YouTube (@um.lapso).
- Conecta via YouTube Data API v3 com gerenciamento de canal.
- Recupera todos os vídeos publicados no canal via playlist de uploads (baixo consumo de quota).
- Avalia cada título e descrição com OpenAI (GPT-4o / GPT-4o-mini) segundo critérios de alto CTR e retenção.
- Reescreve títulos magnéticos e descrições estruturadas para os vídeos com títulos mornos/fracos.
- Preserva timestamps e capítulos existentes nas descrições.
- Suporta modo preview (--preview) com relatório detalhado e modo de aplicação oficial (--aplicar).
"""

import os
import re
import sys
import json
import time
import argparse
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

logger = logging.getLogger("otimizar_youtube")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from openai import OpenAI

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
]

def obter_cliente_youtube(forcar_reauth: bool = False) -> Any:
    """
    Obtém cliente autenticado do YouTube Data API v3.
    Se o token for inválido para o escopo ampliado ou se forcar_reauth for True, abre autenticação no navegador para autorizar o canal.
    """
    token_candidates = [
        os.path.join(ROOT_DIR, "token_youtube.json"),
        os.path.join(ROOT_DIR, "config", "token_youtube.json")
    ]
    token_file = next((tc for tc in token_candidates if os.path.exists(tc)), token_candidates[0])

    if forcar_reauth and os.path.exists(token_file):
        logger.info("[*] Removendo token salvo para forçar nova autenticação...")
        try:
            os.remove(token_file)
        except Exception as e:
            logger.warning(f"[!] Não foi possível remover {token_file}: {e}")

    def _is_oauth_client_file(path: str) -> bool:
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return "installed" in data or "web" in data
        except Exception:
            return False

    secrets_candidates = [
        os.path.join(ROOT_DIR, "client_secrets.json"),
        os.path.join(ROOT_DIR, "config", "client_secrets.json"),
        os.path.join(ROOT_DIR, "client_secret.json"),
        os.path.join(ROOT_DIR, "credenciais.json")
    ]
    secrets_file = next((sc for sc in secrets_candidates if _is_oauth_client_file(sc)), None)

    creds = None
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception:
            creds = None

    precisa_novo_login = False

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                logger.info("[*] Atualizando token de acesso existente...")
                creds.refresh(Request())
            except Exception as e_ref:
                logger.warning(f"[!] Falha na renovação com escopo expandido ({e_ref}). Solicitando consentimento...")
                precisa_novo_login = True
        else:
            precisa_novo_login = True

    if precisa_novo_login or not creds or not creds.valid:
        if not secrets_file:
            raise FileNotFoundError(
                "Credenciais OAuth (client_secrets.json) não encontradas para autorização. "
                "Coloque o arquivo client_secrets.json na raiz do projeto."
            )

        print("\n" + "=" * 65)
        print("=== AUTORIZAÇÃO DO CANAL DO YOUTUBE (GERENCIAMENTO) ===")
        print("Uma janela do navegador será aberta para autorizar o acesso de gerenciamento.")
        print("Selecione a conta do canal @um.lapso e conceda as permissões.")
        print("=" * 65 + "\n")

        flow = InstalledAppFlow.from_client_secrets_file(secrets_file, SCOPES)
        creds = flow.run_local_server(port=8080, prompt="consent")

        with open(token_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        logger.info(f"[+] Novo token de autenticação gravado em: {token_file}")

    return build("youtube", "v3", credentials=creds)

def listar_todos_videos_canal(youtube: Any, limite: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Recupera os vídeos publicados no canal utilizando a playlist de uploads do próprio canal.
    Custo de quota: apenas 1 unidade por página de 50 itens.
    """
    logger.info("[*] Identificando o canal autenticado no YouTube...")
    res_canal = youtube.channels().list(mine=True, part="contentDetails,snippet").execute()
    items_canal = res_canal.get("items", [])
    if not items_canal:
        raise RuntimeError("Nenhum canal associado às credenciais autenticadas.")

    canal_info = items_canal[0]
    nome_canal = canal_info.get("snippet", {}).get("title", "Desconhecido")
    uploads_playlist_id = canal_info.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")

    logger.info(f"[+] Canal conectado: {nome_canal} (Uploads Playlist: {uploads_playlist_id})")

    videos = []
    page_token = None

    logger.info("[*] Buscando histórico de vídeos publicados no canal...")
    while True:
        res_itens = youtube.playlistItems().list(
            playlistId=uploads_playlist_id,
            part="snippet,contentDetails",
            maxResults=50,
            pageToken=page_token
        ).execute()

        itens = res_itens.get("items", [])
        for it in itens:
            v_id = it.get("contentDetails", {}).get("videoId")
            snippet = it.get("snippet", {})
            if v_id and snippet.get("title") != "Private video" and snippet.get("title") != "Deleted video":
                videos.append({
                    "id": v_id,
                    "titulo_original": snippet.get("title", ""),
                    "descricao_original": snippet.get("description", ""),
                    "publicado_em": snippet.get("publishedAt", ""),
                    "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", "")
                })

            if limite and len(videos) >= limite:
                break

        if limite and len(videos) >= limite:
            break

        page_token = res_itens.get("nextPageToken")
        if not page_token:
            break

    # Obtém detalhes adicionais (tags, categoryId) via videos().list em lotes de 50
    logger.info(f"[+] {len(videos)} vídeos localizados. Coletando metadados completos (tags, categorias)...")
    videos_completos = []
    for i in range(0, len(videos), 50):
        lote = videos[i:i + 50]
        ids_str = ",".join(v["id"] for v in lote)
        res_v = youtube.videos().list(part="snippet,statistics", id=ids_str).execute()
        mapa_detalhes = {item["id"]: item for item in res_v.get("items", [])}

        for v in lote:
            detalhes = mapa_detalhes.get(v["id"], {})
            snip = detalhes.get("snippet", {})
            stats = detalhes.get("statistics", {})
            v["tags_originais"] = snip.get("tags", [])
            v["categoria_id"] = snip.get("categoryId", "27")
            v["visualizacoes"] = int(stats.get("viewCount", 0))
            v["curtidas"] = int(stats.get("likeCount", 0))
            v["comentarios"] = int(stats.get("commentCount", 0))
            # Garante que usamos o snippet oficial retornado por videos().list
            if snip.get("title"):
                v["titulo_original"] = snip.get("title")
            if snip.get("description") is not None:
                v["descricao_original"] = snip.get("description")
            videos_completos.append(v)

    return videos_completos

def extrair_capitulos_preservados(descricao: str) -> str:
    """Extrai blocos de timestamps/capítulos existentes na descrição para não perdê-los."""
    linhas = descricao.splitlines()
    linhas_capitulos = []
    em_capitulos = False

    padrao_tempo = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s+[-–—]?\s*(.+)$")

    for l in linhas:
        if "capítulo" in l.lower() or "capitulo" in l.lower() or "timestamps" in l.lower():
            em_capitulos = True
            linhas_capitulos.append(l)
            continue
        if padrao_tempo.match(l):
            em_capitulos = True
            linhas_capitulos.append(l)
            continue
        if em_capitulos and not l.strip():
            # linha em branco após capítulos pode indicar fim da seção
            continue

    if len(linhas_capitulos) >= 2:
        return "\n".join(linhas_capitulos).strip()
    return ""

def avaliar_e_otimizar_video_ia(client: OpenAI, video: Dict[str, Any]) -> Dict[str, Any]:
    """
    Avalia a atratividade do vídeo com OpenAI e produz versão de alto CTR se necessário.
    """
    titulo_antigo = video["titulo_original"]
    desc_antiga = video["descricao_original"]
    eh_short = "#Shorts" in titulo_antigo or "#shorts" in titulo_antigo or "#Shorts" in desc_antiga

    capitulos_existentes = extrair_capitulos_preservados(desc_antiga)

    prompt_analise = f"""
Você é o estrategista chefe de audiência e CTR do YouTube do canal '@um.lapso' (focado em mistérios cósmicos, física improvável, cenários hipotéticos extremos e ficção científica imersiva).

Analise o vídeo a seguir:
- TÍTULO ATUAL: "{titulo_antigo}"
- DESCRIÇÃO ATUAL:
\"\"\"
{desc_antiga[:1200]}
\"\"\"
- TIPO: {"YouTube Shorts (Vertical)" if eh_short else "Vídeo Padrão / Documentário Horizontal (16:9)"}
- VISUALIZAÇÕES ATUAIS: {video.get('visualizacoes', 0)}

SUA MISSÃO:
1. Avalie a qualidade de atratividade e retenção do título atual em uma nota de 1 a 10.
   - Títulos fracos: genéricos, descritivos demais, poéticos abstratos ("Um Mundo de Luz", "A Alinhamento Cósmico", "O Mistério da Estrela").
   - Títulos fortes: despertam curiosidade visceral imediata, mostram paradoxo, tempo limite ou números chocantes ("Em quanto tempo a Terra congelaria se o Sol sumisse?", "E se a gravidade invertesse por 5 segundos?").
2. Se a nota for menor que 8, reescreva o TÍTULO para atingir ALTÍSSIMO CTR:
   - Tamanho: até 70 caracteres (máximo absoluto para não cortar no mobile).
   - Estilo: Provocativo, misterioso, visceral, contraintuitivo.
   - NÃO faça clickbait mentiroso; ancore no tema do vídeo.
   - Se for Shorts, mantenha a hashtag #Shorts no final do título. Se for vídeo longo horizontal, NUNCA use #Shorts.
3. Reescreva a DESCRIÇÃO para maximizar engajamento:
   - Linhas 1-2: Gancho irresistível para os primeiros segundos de leitura.
   - Parágrafo 2: Sinopse rápida da premissa científica/hipotética.
   - Chamada para comentários: Uma pergunta concreta de escolha polarizadora para o público responder.
   - Seções de capítulos (se fornecidas): DEVEM ser mantidas exatamente onde estão.
   - Bloco final de 4 a 6 tags estratégicas (ex: #umlapso #misterio #curiosidades #ciencia #universo).
4. Sugira 8 a 12 tags de busca otimizadas.

Capítulos a preservar na descrição (se houver):
\"\"\"
{capitulos_existentes}
\"\"\"

Retorne EXCLUSIVAMENTE um objeto JSON:
{{
  "nota_atual": <inteiro de 1 a 10>,
  "precisa_otimizar": <true ou false>,
  "motivo_diagnostico": "<1 a 2 frases explicando por que o título atual atrai ou afasta espectadores>",
  "titulo_otimizado": "<novo título de alto impacto até 70 chars>",
  "descricao_otimizada": "<nova descrição completa e formatada>",
  "tags_otimizadas": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"]
}}
"""
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt_analise}],
            temperature=0.75
        )
        dados = json.loads(res.choices[0].message.content)

        # Se houver capítulos preservados e eles não constarem na descrição gerada, anexa-os
        desc_otimizada = dados.get("descricao_otimizada", desc_antiga)
        if capitulos_existentes and capitulos_existentes not in desc_otimizada:
            desc_otimizada = desc_otimizada.strip() + "\n\n" + capitulos_existentes

        dados["descricao_otimizada"] = desc_otimizada
        return dados
    except Exception as e:
        logger.warning(f"[!] Falha ao otimizar vídeo '{titulo_antigo}': {e}")
        return {
            "nota_atual": 7,
            "precisa_otimizar": False,
            "motivo_diagnostico": f"Erro na análise: {e}",
            "titulo_otimizado": titulo_antigo,
            "descricao_otimizada": desc_antiga,
            "tags_otimizadas": video.get("tags_originais", [])
        }

def aplicar_atualizacao_video(youtube: Any, video_id: str, novo_titulo: str, nova_descricao: str, tags: List[str], categoria_id: str = "27") -> bool:
    """Atualiza o snippet do vídeo no YouTube via videos().update."""
    body = {
        "id": video_id,
        "snippet": {
            "title": novo_titulo[:100],
            "description": nova_descricao,
            "tags": tags[:30],
            "categoryId": categoria_id
        }
    }
    try:
        youtube.videos().update(part="snippet", body=body).execute()
        return True
    except Exception as e:
        logger.error(f"[-] Erro ao atualizar vídeo {video_id}: {e}")
        return False

def executar_otimizacao_canal(
    aplicar: bool = False,
    limite: Optional[int] = None,
    nota_corte: int = 7,
    video_id_especifico: Optional[str] = None,
    forcar_reauth: bool = False
):
    """Fluxo principal de auditoria e otimização dos vídeos do YouTube."""
    api_key_openai = os.getenv("OPENAI_API_KEY")
    if not api_key_openai:
        raise RuntimeError("OPENAI_API_KEY ausente no arquivo .env.")

    client_ai = OpenAI(api_key=api_key_openai)
    youtube = obter_cliente_youtube(forcar_reauth=forcar_reauth)

    print("\n" + "=" * 70)
    print("=== OTIMIZADOR DE TÍTULOS E DESCRIÇÕES DO YOUTUBE (@um.lapso) ===")
    print(f"Modo de Execução : {'APLICAR MUDANÇAS DIRETAMENTE NO YOUTUBE' if aplicar else 'PREVIEW / SIMULAÇÃO (NADA SERÁ ALTERADO)'}")
    print(f"Nota de Corte   : Vídeos com nota <={nota_corte} serão reescritos")
    print("=" * 70 + "\n")

    if video_id_especifico:
        logger.info(f"[*] Buscando informações específicas do vídeo ID: {video_id_especifico}...")
        res = youtube.videos().list(part="snippet,statistics", id=video_id_especifico).execute()
        items = res.get("items", [])
        if not items:
            logger.error(f"[-] Vídeo com ID '{video_id_especifico}' não encontrado.")
            return
        snip = items[0]["snippet"]
        stats = items[0].get("statistics", {})
        videos = [{
            "id": video_id_especifico,
            "titulo_original": snip.get("title", ""),
            "descricao_original": snip.get("description", ""),
            "tags_originais": snip.get("tags", []),
            "categoria_id": snip.get("categoryId", "27"),
            "visualizacoes": int(stats.get("viewCount", 0)),
            "curtidas": int(stats.get("likeCount", 0)),
            "comentarios": int(stats.get("commentCount", 0))
        }]
    else:
        videos = listar_todos_videos_canal(youtube, limite=limite)

    if not videos:
        logger.info("[*] Nenhum vídeo encontrado para processar no canal.")
        return

    logger.info(f"[*] Analisando e gerando sugestões para {len(videos)} vídeos...\n")

    relatorio = []
    otimizados_cont = 0

    for idx, v in enumerate(videos, 1):
        v_id = v["id"]
        t_antigo = v["titulo_original"]
        logger.info(f"[{idx}/{len(videos)}] Analisando: \"{t_antigo[:50]}...\" (Views: {v.get('visualizacoes', 0)})")

        resultado_ia = avaliar_e_otimizar_video_ia(client_ai, v)
        nota = resultado_ia.get("nota_atual", 10)
        motivo = resultado_ia.get("motivo_diagnostico", "")
        t_novo = resultado_ia.get("titulo_otimizado", t_antigo).strip()
        d_nova = resultado_ia.get("descricao_otimizada", v["descricao_original"]).strip()
        tags_novas = resultado_ia.get("tags_otimizadas", v.get("tags_originais", []))

        deve_otimizar = (nota <= nota_corte) and (t_novo != t_antigo or d_nova != v["descricao_original"])

        item_relatorio = {
            "id": v_id,
            "link": f"https://youtu.be/{v_id}",
            "nota_original": nota,
            "deve_otimizar": deve_otimizar,
            "diagnostico": motivo,
            "titulo_antigo": t_antigo,
            "titulo_novo": t_novo,
            "descricao_antiga": v["descricao_original"],
            "descricao_nova": d_nova,
            "tags_novas": tags_novas,
            "atualizado_no_youtube": False
        }

        print("-" * 70)
        print(f"Vídeo [{idx}/{len(videos)}]: https://youtu.be/{v_id}")
        print(f"Nota de Atratividade : {nota}/10 | {'PRECISA MELHORAR' if deve_otimizar else 'BOM TÍTULO (MANTIDO)'}")
        print(f"Diagnóstico IA       : {motivo}")
        print(f"TÍTULO ATUAL         : {t_antigo}")
        if deve_otimizar:
            print(f"TÍTULO SUGERIDO      : \033[92m{t_novo}\033[0m")
            print(f"PRÉVIA DA DESCRIÇÃO  :\n{d_nova[:200]}...")
        else:
            print("Status               : Título e descrição já possuem bom potencial de retenção.")
        print("-" * 70 + "\n")

        if deve_otimizar:
            otimizados_cont += 1
            if aplicar:
                logger.info(f"  -> Aplicando atualização no YouTube para o vídeo {v_id}...")
                sucesso = aplicar_atualizacao_video(
                    youtube=youtube,
                    video_id=v_id,
                    novo_titulo=t_novo,
                    nova_descricao=d_nova,
                    tags=tags_novas,
                    categoria_id=v.get("categoria_id", "27")
                )
                if sucesso:
                    logger.info(f"  [✔] Vídeo {v_id} atualizado com sucesso no YouTube!")
                    item_relatorio["atualizado_no_youtube"] = True
                else:
                    logger.warning(f"  [-] Falha ao atualizar vídeo {v_id} no YouTube.")
                time.sleep(1)  # Intervalo de cortesia para quota de API

        relatorio.append(item_relatorio)

    # Salva relatório de auditoria
    os.makedirs(os.path.join(ROOT_DIR, "data"), exist_ok=True)
    caminho_relatorio = os.path.join(ROOT_DIR, "data", "relatorio_otimizacao_youtube.json")
    with open(caminho_relatorio, "w", encoding="utf-8") as f:
        json.dump(relatorio, f, indent=4, ensure_ascii=False)

    print("\n" + "=" * 70)
    print("=== RESUMO DA AUDITORIA ===")
    print(f"Total de Vídeos Analisados : {len(videos)}")
    print(f"Vídeos com Títulos Fracos  : {otimizados_cont}")
    if aplicar:
        atualizados_sucesso = sum(1 for r in relatorio if r.get("atualizado_no_youtube"))
        print(f"Vídeos Atualizados no YT   : {atualizados_sucesso}/{otimizados_cont}")
    else:
        print(f"Ação Executada             : Simulação concluída. Nenhum dado foi alterado.")
        print(f"Para aplicar as mudanças   : Execute com a flag --aplicar")
    print(f"Relatório salvo em         : {caminho_relatorio}")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Otimizador de Títulos e Descrições do YouTube (@um.lapso)")
    parser.add_argument("--aplicar", action="store_true", help="Aplica as alterações diretamente no canal do YouTube")
    parser.add_argument("--preview", action="store_true", help="Apenas simula e exibe a análise sem alterar nada (padrão)")
    parser.add_argument("--limite", type=int, default=None, help="Limite máximo de vídeos a processar")
    parser.add_argument("--nota-corte", type=int, default=7, help="Nota de corte (1 a 10): vídeos com nota <= nota_corte serão reescritos (padrão: 7)")
    parser.add_argument("--video-id", type=str, default=None, help="Processa apenas um vídeo específico pelo ID do YouTube")
    parser.add_argument("--reauth", action="store_true", help="Força novo login no navegador para conectar a outro canal/conta")

    args = parser.parse_args()

    # Se --aplicar não for passado explicitamente, roda em preview por segurança
    modo_aplicar = args.aplicar
    executar_otimizacao_canal(
        aplicar=modo_aplicar,
        limite=args.limite,
        nota_corte=args.nota_corte,
        video_id_especifico=args.video_id,
        forcar_reauth=args.reauth
    )
