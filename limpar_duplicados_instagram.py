#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
limpar_duplicados_instagram.py - Limpeza inteligente e segura de vídeos/Reels duplicados no Instagram.

Critérios:
1. Agrupamento por similaridade visual de thumbnail (dHash com Pillow) + legenda normalizada.
2. Regra estrita de engajamento para MANTER apenas 1 cópia por grupo:
   Score = (likes * 10) + views (desempate: views -> likes -> mais antigo).
3. Modos de execução:
   --dry-run (padrão): auditoria e relatório em logs/relatorio_duplicados_ig.json sem deletar nada.
   --executar: exclusão controlada via instagrapi (com limites e delays anti-bloqueio).
"""

import os
import sys
import re
import json
import time
import random
import argparse
from io import BytesIO
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Tuple

import requests
from dotenv import load_dotenv
from PIL import Image

# Força codificação UTF-8 no terminal Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(ROOT_DIR, "logs")
CONFIG_DIR = os.path.join(ROOT_DIR, "config")
RELATORIO_JSON_PATH = os.path.join(LOGS_DIR, "relatorio_duplicados_ig.json")
SESSION_FILE_PATH = os.path.join(CONFIG_DIR, "session_instagram.json")
SESSION_FILE_ROOT = os.path.join(ROOT_DIR, "session_instagram.json")

load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)


# =============================================================================
# UTILITÁRIOS DE HASH VISUAL E NORMALIZAÇÃO DE TEXTO
# =============================================================================

def normalizar_legenda(legenda: Optional[str]) -> str:
    """Normaliza texto para comparação (minúsculas, sem pontuações, sem quebras/espaços extras)."""
    if not legenda:
        return ""
    texto = legenda.lower()
    # Remove quebras de linha e tabs
    texto = re.sub(r'[\r\n\t]+', ' ', texto)
    # Remove hashtags para comparar o corpo principal da legenda
    texto_sem_hash = re.sub(r'#\w+', '', texto)
    # Remove pontuações e emojis decorativos
    texto_limpo = re.sub(r'[^\w\s]', '', texto_sem_hash)
    # Normaliza múltiplos espaços
    texto_limpo = re.sub(r'\s+', ' ', texto_limpo).strip()
    return texto_limpo if len(texto_limpo) >= 5 else texto.strip()


def extrair_titulo_legenda(legenda: Optional[str]) -> str:
    """Extrai a primeira frase ou linha da legenda para exibição legível."""
    if not legenda:
        return "(Sem legenda / Modo silencioso)"
    primeira_linha = legenda.strip().splitlines()[0]
    return primeira_linha[:75] + ("..." if len(primeira_linha) > 75 else "")


def calcular_dhash(imagem: Image.Image, hash_size: int = 8) -> int:
    """
    Calcula o difference hash (dHash) de uma imagem usando Pillow:
    1. Converte para escala de cinza e redimensiona para 9x8.
    2. Compara cada pixel com seu vizinho da direita (64 bits).
    """
    try:
        cinza = imagem.convert('L').resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
        diff = 0
        for row in range(hash_size):
            for col in range(hash_size):
                bit = cinza.getpixel((col, row)) > cinza.getpixel((col + 1, row))
                diff = (diff << 1) | bit
        return diff
    except Exception:
        return 0


def distancia_hamming(h1: int, h2: int) -> int:
    """Calcula a distância de Hamming entre dois hashes (quantos bits diferem)."""
    if h1 == 0 or h2 == 0:
        return 999
    return bin(h1 ^ h2).count('1')


def baixar_e_calcular_dhash(url: str, timeout: int = 12) -> Optional[int]:
    """Baixa a thumbnail em memória e calcula o dHash sem salvar arquivo em disco."""
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200 and len(resp.content) > 1000:
            img = Image.open(BytesIO(resp.content))
            return calcular_dhash(img)
    except Exception:
        pass
    return None


# =============================================================================
# CONSULTA DE MÍDIAS VIA META GRAPH API
# =============================================================================

def obter_midias_graph_api() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Consulta o perfil do Instagram via Meta Graph API Oficial:
    - Paginação completa de todas as mídias.
    - Filtra vídeos e Reels.
    - Consulta métricas de visualizações (insights) e curtidas.
    """
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    account_id = os.getenv("INSTAGRAM_ACCOUNT_ID")
    version = os.getenv("META_GRAPH_API_VERSION", "v21.0")

    if not token or not account_id:
        return [], {}

    print(f"[*] Conectando à Meta Graph API ({version}) para a conta {account_id}...")
    headers = {"Authorization": f"OAuth {token}"}
    
    # 1. Informações básicas da conta
    perfil_info = {}
    try:
        url_acc = f"https://graph.facebook.com/{version}/{account_id}?fields=id,username,name,media_count"
        res_acc = requests.get(url_acc, headers=headers, timeout=15).json()
        perfil_info = {
            "id": res_acc.get("id", account_id),
            "username": res_acc.get("username", "desconhecido"),
            "name": res_acc.get("name", ""),
            "media_count": res_acc.get("media_count", 0)
        }
        print(f"[+] Conta verificada: @{perfil_info['username']} ({perfil_info['media_count']} mídias registradas)")
    except Exception as e:
        print(f"[-] Aviso ao obter perfil: {e}")

    # 2. Paginação de todas as mídias
    url_media = f"https://graph.facebook.com/{version}/{account_id}/media"
    params = {
        "fields": "id,caption,media_type,media_product_type,thumbnail_url,media_url,permalink,timestamp,like_count,comments_count",
        "limit": 50
    }

    midias_coletadas = []
    pagina = 1

    while url_media:
        print(f"  -> Coletando página {pagina} de mídias...", flush=True)
        try:
            resp = requests.get(url_media, params=params, headers=headers, timeout=25)
            dados = resp.json()
            if "error" in dados:
                print(f"[-] Erro na Graph API: {dados['error'].get('message')}")
                break

            items = dados.get("data", [])
            for item in items:
                m_type = item.get("media_type")
                if m_type in ("VIDEO", "REELS") or item.get("media_product_type") == "REELS":
                    midias_coletadas.append(item)

            url_media = dados.get("paging", {}).get("next")
            params = None  # O next URL já traz todos os parâmetros necessários
            pagina += 1
        except Exception as e:
            print(f"[-] Erro durante paginação: {e}")
            break

    print(f"[+] Total de Reels/vídeos localizados via Graph API: {len(midias_coletadas)}")
    return midias_coletadas, perfil_info


def enriquecer_metricas_graph_api(midias: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Consulta visualizações reais (insights/views) para cada Reel de forma paralela e rápida."""
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    version = os.getenv("META_GRAPH_API_VERSION", "v21.0")

    def _consultar_views(media: Dict[str, Any]) -> Dict[str, Any]:
        m_id = media["id"]
        likes = media.get("like_count", 0) or 0
        views = 0

        # Tenta insights de visualizações
        try:
            url_ins = f"https://graph.facebook.com/{version}/{m_id}/insights"
            resp = requests.get(url_ins, params={"metric": "views", "access_token": token}, timeout=6)
            dados = resp.json()
            for metric in dados.get("data", []):
                if metric.get("name") == "views":
                    for val in metric.get("values", []):
                        views = max(views, int(val.get("value", 0)))
        except Exception:
            pass

        # Parse de timestamp ISO para timestamp UNIX
        ts_raw = media.get("timestamp", "")
        taken_at_ts = 0
        data_formatada = ""
        if ts_raw:
            try:
                # Ex: 2026-11-22T22:37:11+0000
                dt = datetime.strptime(ts_raw[:19], "%Y-%m-%dT%H:%M:%S")
                taken_at_ts = int(dt.timestamp())
                data_formatada = dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                data_formatada = str(ts_raw)[:16]

        # Extrai shortcode a partir do permalink
        permalink = media.get("permalink", "")
        shortcode = ""
        m_code = re.search(r'/reel/([^/?]+)|/p/([^/?]+)', permalink)
        if m_code:
            shortcode = m_code.group(1) or m_code.group(2) or ""

        thumb_url = media.get("thumbnail_url") or media.get("media_url") or ""
        caption = media.get("caption", "")

        return {
            "id": str(m_id),
            "shortcode": shortcode,
            "permalink": permalink,
            "caption": caption,
            "titulo": extrair_titulo_legenda(caption),
            "legenda_norm": normalizar_legenda(caption),
            "like_count": likes,
            "play_count": views,
            "view_count": views,
            "taken_at_ts": taken_at_ts,
            "data_str": data_formatada,
            "thumbnail_url": thumb_url,
            "dhash": None,
        }

    print("[*] Coletando métricas de engajamento (likes e views) para os Reels...")
    resultados = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futuros = [executor.submit(_consultar_views, m) for m in midias]
        for f in as_completed(futuros):
            try:
                resultados.append(f.result())
            except Exception:
                pass

    return resultados


def calcular_hashes_visuais_em_paralelo(midias: List[Dict[str, Any]]) -> None:
    """Baixa thumbnails e calcula dHash perceptual em paralelo sem travar o processamento."""
    print("[*] Processando thumbnails em memória para cálculo de dHash perceptual...")

    def _processar(item: Dict[str, Any]):
        t_url = item.get("thumbnail_url")
        if t_url:
            item["dhash"] = baixar_e_calcular_dhash(t_url)

    with ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(_processar, midias))

    hashes_validos = sum(1 for m in midias if m.get("dhash") is not None)
    print(f"[+] dHash calculado com sucesso para {hashes_validos}/{len(midias)} mídias.")


# =============================================================================
# AGRUPAMENTO INTELIGENTE DE DUPLICADOS
# =============================================================================

def agrupar_videos_duplicados(midias: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """
    Agrupa vídeos combinando:
    1. Similaridade visual da thumbnail (distância de Hamming do dHash <= 6).
    2. Similaridade textual da legenda normalizada (ou título idêntico).
    """
    grupos: List[List[Dict[str, Any]]] = []

    for item in midias:
        atribuido = False
        item_dhash = item.get("dhash")
        item_leg_norm = item.get("legenda_norm", "")
        item_titulo = item.get("titulo", "")

        for grupo in grupos:
            # Compara item com os integrantes do grupo
            for membro in grupo:
                membro_dhash = membro.get("dhash")
                membro_leg_norm = membro.get("legenda_norm", "")
                membro_titulo = membro.get("titulo", "")

                eh_duplicado = False

                # Verificação 1: Similaridade visual por dHash
                if item_dhash is not None and membro_dhash is not None:
                    dist = distancia_hamming(item_dhash, membro_dhash)
                    if dist <= 6:
                        eh_duplicado = True

                # Verificação 2: Legenda normalizada idêntica (se tiver conteúdo substancial)
                if not eh_duplicado and item_leg_norm and membro_leg_norm:
                    if len(item_leg_norm) >= 8 and item_leg_norm == membro_leg_norm:
                        eh_duplicado = True

                # Verificação 3: Título/primeira frase idêntica
                if not eh_duplicado and item_titulo and membro_titulo:
                    if len(item_titulo) >= 15 and item_titulo.lower() == membro_titulo.lower():
                        eh_duplicado = True

                if eh_duplicado:
                    grupo.append(item)
                    atribuido = True
                    break

            if atribuido:
                break

        if not atribuido:
            grupos.append([item])

    return grupos


# =============================================================================
# CLASSIFICAÇÃO POR ENGAJAMENTO (REGRA ESTRITA DE ENGAJAMENTO)
# =============================================================================

def calcular_pontuacao_engajamento(media: Dict[str, Any]) -> int:
    """Calcula pontuação estrita: (like_count * 10) + max(play_count, view_count)."""
    likes = media.get("like_count", 0) or 0
    views = max(media.get("play_count", 0) or 0, media.get("view_count", 0) or 0)
    return (likes * 10) + views


def ordenar_grupo_por_engajamento(grupo: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Ordena o grupo de duplicados priorizando estritamente:
    1. Maior pontuação total: (like_count * 10) + views
    2. Maior número de visualizações
    3. Maior número de curtidas
    4. Desempate final (ex: todos 0): mais antigo (menor taken_at_ts)
    """
    def chave_sort(m: Dict[str, Any]):
        score = calcular_pontuacao_engajamento(m)
        views = max(m.get("play_count", 0) or 0, m.get("view_count", 0) or 0)
        likes = m.get("like_count", 0) or 0
        ts = m.get("taken_at_ts", 0) or 0
        # Inverte o timestamp para que o mais antigo (menor valor) fique no topo no reverse=True
        return (score, views, likes, -ts)

    grupo_ordenado = sorted(grupo, key=chave_sort, reverse=True)
    return grupo_ordenado


# =============================================================================
# EXCLUSÃO SEGURA VIA INSTAGRAPI (SE CREDENCIAIS DISPONÍVEIS)
# =============================================================================

def obter_credenciais_instagrapi(args) -> Tuple[Optional[str], Optional[str]]:
    """Obtém credenciais do Instagram para instagrapi a partir dos args ou do .env."""
    user = (
        args.username or
        os.getenv("IG_USER") or
        os.getenv("INSTAGRAM_USER") or
        os.getenv("INSTAGRAM_USERNAME") or
        os.getenv("IGUSER")
    )
    passwd = (
        args.password or
        os.getenv("IG_PASS") or
        os.getenv("INSTAGRAM_PASS") or
        os.getenv("INSTAGRAM_PASSWORD") or
        os.getenv("IGPASS")
    )
    return user, passwd


def conectar_instagrapi(username: Optional[str], password: Optional[str]) -> Optional[Any]:
    """Inicializa o cliente instagrapi com suporte a reutilização de sessão salva."""
    try:
        from instagrapi import Client
    except ImportError:
        print("[-] Biblioteca 'instagrapi' não instalada.")
        return None

    cl = Client()
    cl.delay_range = [2, 5]

    session_candidatos = [SESSION_FILE_PATH, SESSION_FILE_ROOT]
    session_usada = None
    for sc in session_candidatos:
        if os.path.exists(sc) and os.path.getsize(sc) > 100:
            session_usada = sc
            break

    # Tentativa 1: Reutiliza sessão salva
    if session_usada:
        try:
            print(f"[*] Carregando sessão salva do Instagram de {os.path.basename(session_usada)}...")
            cl.load_settings(session_usada)
            cl.get_timeline_feed()
            print("[+] Sessão salva validada com sucesso!")
            return cl
        except Exception as e_sess:
            print(f"[-] Sessão salva expirada ou inválida ({e_sess}). Tentando login direto...")

    # Tentativa 2: Login com usuário e senha
    if username and password:
        try:
            print(f"[*] Efetuando login no Instagram como @{username}...")
            cl.login(username, password)
            os.makedirs(CONFIG_DIR, exist_ok=True)
            cl.dump_settings(SESSION_FILE_PATH)
            print(f"[+] Login bem-sucedido! Sessão salva em: {SESSION_FILE_PATH}")
            return cl
        except Exception as e_login:
            print(f"[-] Erro ao autenticar no Instagram via instagrapi: {e_login}")
            return None

    return None


def excluir_media_com_seguranca(cl, media: Dict[str, Any]) -> bool:
    """Executa a exclusão de uma mídia única com instagrapi."""
    m_id = media.get("id")
    shortcode = media.get("shortcode")
    permalink = media.get("permalink")

    pk_alvo = None

    # 1. Tenta converter via shortcode
    if shortcode:
        try:
            pk_alvo = cl.media_pk_from_code(shortcode)
        except Exception:
            pass

    # 2. Tenta permalink
    if not pk_alvo and permalink:
        try:
            pk_alvo = cl.media_pk_from_url(permalink)
        except Exception:
            pass

    # 3. Fallback para ID numérico direto
    if not pk_alvo and m_id and str(m_id).isdigit():
        pk_alvo = int(m_id)

    if not pk_alvo:
        print(f"    [-] Não foi possível resolver o PK da mídia {m_id} ({permalink}).")
        return False

    try:
        resultado = cl.media_delete(pk_alvo)
        return bool(resultado)
    except Exception as e:
        raise e


# =============================================================================
# FLUXO PRINCIPAL
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Limpeza inteligente de Reels/vídeos duplicados no Instagram com preservação de engajamento."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Modo seguro de simulação (padrão): apenas lista duplicados e gera relatório em logs/."
    )
    parser.add_argument(
        "--executar",
        action="store_true",
        help="Executa a exclusão real dos vídeos marcados como duplicados."
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=20,
        help="Limite máximo de mídias a excluir por execução (padrão: 20)."
    )
    parser.add_argument(
        "--delay-min",
        type=int,
        default=8,
        help="Delay mínimo em segundos entre exclusões (padrão: 8)."
    )
    parser.add_argument(
        "--delay-max",
        type=int,
        default=18,
        help="Delay máximo em segundos entre exclusões (padrão: 18)."
    )
    parser.add_argument(
        "--username",
        type=str,
        default=None,
        help="Usuário do Instagram (opcional se configurado no .env)."
    )
    parser.add_argument(
        "--password",
        type=str,
        default=None,
        help="Senha do Instagram (opcional se configurado no .env)."
    )

    args = parser.parse_args()

    # Se --executar for passado, desativa o dry-run
    if args.executar:
        args.dry_run = False

    modo_str = "SIMULAÇÃO (DRY-RUN — NADA SERÁ APAGADO)" if args.dry_run else "EXECUÇÃO REAL (EXCLUSÃO ATIVA)"

    print("=" * 75)
    print("=== LIMPEZA DE VÍDEOS DUPLICADOS NO INSTAGRAM — @um.lapso ===")
    print(f"=== MODO: {modo_str} ===")
    print("=" * 75)

    os.makedirs(LOGS_DIR, exist_ok=True)

    # 1. Obtenção de todas as mídias da conta
    midias_brutas, perfil_info = obter_midias_graph_api()
    if not midias_brutas:
        print("[-] Nenhuma mídia localizada ou credenciais da Meta Graph API ausentes.")
        sys.exit(1)

    # 2. Enriquecimento de métricas (views e likes)
    midias_processadas = enriquecer_metricas_graph_api(midias_brutas)

    # 3. Cálculo de hashes de thumbnail
    calcular_hashes_visuais_em_paralelo(midias_processadas)

    # 4. Agrupamento inteligente
    print("\n[*] Agrupando mídias por similaridade visual (dHash) e legendas normalizadas...")
    todos_grupos = agrupar_videos_duplicados(midias_processadas)

    grupos_duplicados = [g for g in todos_grupos if len(g) > 1]
    videos_unicos = [g for g in todos_grupos if len(g) == 1]

    print(f"[+] Total de grupos distintos identificados: {len(todos_grupos)}")
    print(f"    - Vídeos únicos (sem duplicidade): {len(videos_unicos)}")
    print(f"    - Grupos com vídeos duplicados: {len(grupos_duplicados)}")

    total_duplicados_a_apagar = sum(len(g) - 1 for g in grupos_duplicados)
    print(f"    - Total de vídeos excedentes elegíveis para exclusão: {total_duplicados_a_apagar}")
    print("=" * 75)

    if not grupos_duplicados:
        print("\n[✔] Parabéns! Nenhum vídeo duplicado foi encontrado no perfil do Instagram.")
        sys.exit(0)

    # 5. Classificação por engajamento de cada grupo
    relatorio_grupos = []
    lista_itens_excluir = []

    print("\n" + "="*75)
    print("=== ANÁLISE DETALHADA DOS GRUPOS DUPLICADOS ===")
    print("="*75)

    for idx, grupo in enumerate(grupos_duplicados, start=1):
        grupo_ordenado = ordenar_grupo_por_engajamento(grupo)
        mantido = grupo_ordenado[0]
        excluir = grupo_ordenado[1:]

        score_mantido = calcular_pontuacao_engajamento(mantido)

        print(f"\n📁 GRUPO #{idx} — {len(grupo)} cópias encontradas")
        print(f"   Título: \"{mantido['titulo']}\"")
        print(f"   ⭐ [MANTER] ID: {mantido['id']} | Views: {mantido['view_count']} | Likes: {mantido['like_count']} | Score: {score_mantido} | Postado em: {mantido['data_str']}")
        print(f"      Link: {mantido['permalink']}")

        itens_excluir_dados = []
        for ex in excluir:
            sc_ex = calcular_pontuacao_engajamento(ex)
            print(f"   🗑️  [EXCLUIR] ID: {ex['id']} | Views: {ex['view_count']} | Likes: {ex['like_count']} | Score: {sc_ex} | Postado em: {ex['data_str']}")
            print(f"      Link: {ex['permalink']}")
            
            ex_info = {
                "id": ex["id"],
                "shortcode": ex["shortcode"],
                "permalink": ex["permalink"],
                "titulo": ex["titulo"],
                "views": ex["view_count"],
                "likes": ex["like_count"],
                "score": sc_ex,
                "data": ex["data_str"],
                "status": "pendente_exclusao"
            }
            itens_excluir_dados.append(ex_info)
            lista_itens_excluir.append(ex)

        relatorio_grupos.append({
            "grupo_id": idx,
            "titulo": mantido["titulo"],
            "total_copias": len(grupo),
            "video_mantido": {
                "id": mantido["id"],
                "shortcode": mantido["shortcode"],
                "permalink": mantido["permalink"],
                "titulo": mantido["titulo"],
                "views": mantido["view_count"],
                "likes": mantido["like_count"],
                "score": score_mantido,
                "data": mantido["data_str"]
            },
            "videos_para_excluir": itens_excluir_dados
        })

    # 6. Salva relatório completo em JSON
    resumo_json = {
        "timestamp_auditoria": datetime.now().isoformat(),
        "conta": perfil_info.get("username", "um.lapso"),
        "modo_execucao": "dry-run" if args.dry_run else "executar",
        "total_midias_analisadas": len(midias_processadas),
        "total_grupos_duplicados": len(grupos_duplicados),
        "total_copias_excedentes": total_duplicados_a_apagar,
        "limite_aplicado": args.limite,
        "grupos": relatorio_grupos
    }

    with open(RELATORIO_JSON_PATH, "w", encoding="utf-8") as f_rel:
        json.dump(resumo_json, f_rel, ensure_ascii=False, indent=2)

    print("\n" + "=" * 75)
    print(f"[+] Relatório completo salvo em: {RELATORIO_JSON_PATH}")
    print("=" * 75)

    # 7. Decisão de Exclusão Real vs Dry-run
    if args.dry_run:
        print("\n[ℹ] MODO DRY-RUN CONCLUÍDO.")
        print(f"    - Nenhuma mídia foi alterada ou apagada.")
        print(f"    - {total_duplicados_a_apagar} mídias duplicadas foram identificadas e mapeadas.")
        print("\nPara efetuar a exclusão real mantendo as cópias de maior engajamento:")
        print("    python limpar_duplicados_instagram.py --executar")
        print("=" * 75)
        return

    # MODO EXECUÇÃO REAL
    print("\n" + "=" * 75)
    print("=== INICIANDO EXCLUSÃO CONTROLADA DAS MÍDIAS MARCADAS ===")
    print("=" * 75)

    ig_user, ig_pass = obter_credenciais_instagrapi(args)
    cl = conectar_instagrapi(ig_user, ig_pass)

    if not cl:
        print("\n" + "!" * 75)
        print("[!] ATENÇÃO: A Graph API Oficial da Meta NÃO possui permissão para deletar mídias.")
        print("[!] Para executar a exclusão automática via instagrapi:")
        print("    1. Defina 'IG_USER' e 'IG_PASS' no seu arquivo .env, OU")
        print("    2. Passe as credenciais por argumento:")
        print("       python limpar_duplicados_instagram.py --executar --username SEU_USER --password SUA_SENHA")
        print("!" * 75)
        sys.exit(1)

    itens_para_apagar = lista_itens_excluir[:args.limite]
    print(f"[*] Processando lote de {len(itens_para_apagar)} exclusões (Limite configurado: {args.limite})...")

    exclusoes_sucesso = 0
    interrompido = False
    motivo_interrupcao = ""

    for i, item_del in enumerate(itens_para_apagar, start=1):
        print(f"\n[{i}/{len(itens_para_apagar)}] Excluindo: {item_del['permalink']} (ID: {item_del['id']})...")
        try:
            sucesso = excluir_media_com_seguranca(cl, item_del)
            if sucesso:
                exclusoes_sucesso += 1
                print(f"  [✔] Mídia {item_del['id']} excluída com sucesso!")
            else:
                print(f"  [?] O Instagram retornou falso para a exclusão da mídia {item_del['id']}.")

            # Pausa humana com jitter anti-bloqueio entre cada exclusão
            if i < len(itens_para_apagar):
                delay = random.uniform(args.delay_min, args.delay_max)
                print(f"  [*] Pausa anti-bloqueio de {delay:.1f}s...")
                time.sleep(delay)

        except Exception as e_del:
            err_str = str(e_del).lower()
            print(f"  [-] Erro ao excluir mídia: {e_del}")

            # Detecção de restrições anti-spam do Instagram
            if any(term in err_str for term in [
                "feedback_required", "checkpoint_required", "please wait a few minutes",
                "rate limit", "rate_limit", "action_blocked", "login_required"
            ]):
                interrompido = True
                motivo_interrupcao = str(e_del)
                print("\n" + "!" * 75)
                print(f"[!] ALERTA DE SEGURANÇA DO INSTAGRAM: {motivo_interrupcao}")
                print("[!] O processo foi pausado preventivamente para proteger sua conta contra bloqueios.")
                print("!" * 75)
                break

    restantes = total_duplicados_a_apagar - exclusoes_sucesso
    print("\n" + "=" * 75)
    print("=== RESUMO DA OPERAÇÃO DE EXCLUSÃO ===")
    print(f"  - Mídias apagadas nesta rodada: {exclusoes_sucesso}")
    print(f"  - Mídias duplicadas restantes no perfil: {restantes}")
    if interrompido:
        print(f"  - Processo pausado por segurança: {motivo_interrupcao}")
        print("  - Aguarde alguns minutos ou rode novamente mais tarde.")
    elif restantes > 0:
        print("  - Para continuar excluindo as próximas duplicadas:")
        print("    python limpar_duplicados_instagram.py --executar")
    else:
        print("  - [✔] TODAS as duplicatas do perfil foram eliminadas com sucesso!")
    print("=" * 75)


if __name__ == "__main__":
    main()
