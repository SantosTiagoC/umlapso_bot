#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
youtube_longo_publisher.py - Publicador de Vídeos Longos (16:9) na Aba "Vídeos" do YouTube para @um.lapso.
- Reutiliza credenciais OAuth (token_youtube.json / client_secrets.json).
- Publicação sem a tag #Shorts (classificado automaticamente como vídeo regular horizontal).
- Injeção automática dos timestamps de capítulos na descrição.
- Upload de capa personalizada (thumbnail.jpg 1280x720) com tratamento gracioso de permissões.
- Histórico anti-duplicidade próprio em data/historico_longos.json (indexado por SHA-256).
"""

import os
import time
import json
import hashlib
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

logger = logging.getLogger("youtube_longo_publisher")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

CAMINHO_HISTORICO_LONGOS_JSON = os.path.join(ROOT_DIR, "data", "historico_longos.json")

def calcular_sha256(caminho_arquivo: str) -> str:
    """Calcula o SHA-256 do arquivo de vídeo."""
    h = hashlib.sha256()
    with open(caminho_arquivo, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def verificar_duplicidade_longo(video_path: str) -> tuple[bool, str]:
    """Verifica se o vídeo longo já foi publicado anteriormente no YouTube."""
    if not os.path.exists(CAMINHO_HISTORICO_LONGOS_JSON):
        return False, ""
    try:
        with open(CAMINHO_HISTORICO_LONGOS_JSON, "r", encoding="utf-8") as f:
            historico = json.load(f)
        sha = calcular_sha256(video_path)
        nome_arquivo = os.path.basename(video_path)
        for item in historico:
            if item.get("sha256") == sha or item.get("arquivo") == nome_arquivo:
                return True, f"Vídeo já registrado com ID {item.get('video_id', 'desconhecido')}"
    except Exception as e:
        logger.warning(f"[!] Erro ao ler historico_longos.json: {e}")
    return False, ""

def registrar_sucesso_longo(video_path: str, video_id: str, url: str, titulo: str):
    """Registra publicação bem-sucedida em data/historico_longos.json."""
    os.makedirs(os.path.dirname(CAMINHO_HISTORICO_LONGOS_JSON), exist_ok=True)
    historico = []
    if os.path.exists(CAMINHO_HISTORICO_LONGOS_JSON):
        try:
            with open(CAMINHO_HISTORICO_LONGOS_JSON, "r", encoding="utf-8") as f:
                historico = json.load(f)
        except Exception:
            historico = []

    registro = {
        "video_id": video_id,
        "url": url,
        "titulo": titulo,
        "arquivo": os.path.basename(video_path),
        "sha256": calcular_sha256(video_path),
        "data_publicacao": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    historico.append(registro)
    try:
        with open(CAMINHO_HISTORICO_LONGOS_JSON, "w", encoding="utf-8") as f:
            json.dump(historico, f, indent=2, ensure_ascii=False)
        logger.info(f"[+] Registro gravado com sucesso em {CAMINHO_HISTORICO_LONGOS_JSON}")
    except Exception as e:
        logger.warning(f"[!] Erro ao gravar historico_longos.json: {e}")

def obter_servico_youtube():
    """Autentica e retorna o cliente da YouTube Data API v3."""
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube"
    ]

    token_candidates = [
        os.path.join(ROOT_DIR, "token_youtube.json"),
        os.path.join(ROOT_DIR, "config", "token_youtube.json"),
    ]
    token_file = next((tc for tc in token_candidates if os.path.exists(tc)), token_candidates[0])

    secrets_candidates = [
        os.path.join(ROOT_DIR, "client_secrets.json"),
        os.path.join(ROOT_DIR, "config", "client_secrets.json"),
        os.path.join(ROOT_DIR, "client_secret.json"),
        os.path.join(ROOT_DIR, "credenciais.json")
    ]

    creds = None
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("[*] Atualizando token de acesso expirado do YouTube...")
            creds.refresh(Request())
        else:
            secrets_file = next((sc for sc in secrets_candidates if os.path.exists(sc)), None)
            if not secrets_file:
                raise FileNotFoundError(
                    "Credenciais OAuth do YouTube não encontradas (client_secrets.json). "
                    "Verifique se o arquivo está na raiz do projeto."
                )

            logger.info(f"[*] Preparando autorização OAuth para o YouTube ({os.path.basename(secrets_file)})...")
            flow = InstalledAppFlow.from_client_secrets_file(secrets_file, SCOPES)
            creds = flow.run_local_server(port=8080, prompt="consent")

        with open(token_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)

def publicar_video_longo_youtube(
    video_path: str,
    titulo: str,
    descricao_base: str,
    texto_capitulos: str,
    tags: List[str],
    thumbnail_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Realiza o upload completo de vídeo horizontal (16:9) na aba 'Vídeos' do YouTube.
    Insere capítulos automaticamente na descrição e aplica capa customizada.
    """
    logger.info(f"[*] Iniciando publicação de vídeo longo no YouTube: {os.path.basename(video_path)}")

    # 1. Trava anti-duplicidade
    is_dup, motivo_dup = verificar_duplicidade_longo(video_path)
    if is_dup:
        msg_skip = f"[SKIP] Vídeo longo já publicado no YouTube ({motivo_dup})"
        logger.warning(f"  [-] {msg_skip}")
        return {"status": "SKIPPED", "motivo": msg_skip}

    if not os.path.isfile(video_path) or os.path.getsize(video_path) < 2_000_000:
        raise ValueError(f"Arquivo de vídeo inválido ou incompleto ({video_path}).")

    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    youtube = obter_servico_youtube()

    # 2. Formata título e descrição completa com capítulos
    titulo_limpo = titulo.replace("#Shorts", "").replace("#shorts", "").strip()[:100]

    partes_desc = [descricao_base.strip()]
    if texto_capitulos and texto_capitulos.strip():
        partes_desc.append(texto_capitulos.strip())

    partes_desc.append("\n---\nInscreva-se no canal @um.lapso para explorar os maiores mistérios do universo e da ciência.")
    descricao_final = "\n\n".join(partes_desc).strip()

    body = {
        "snippet": {
            "title": titulo_limpo,
            "description": descricao_final,
            "tags": [t for t in tags if t.lower() not in ("shorts", "#shorts")],
            "categoryId": "27"  # 27 = Educação / Ciência & Tecnologia (ideal para documentários @um.lapso)
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    logger.info(f"  [*] Enviando vídeo ({os.path.getsize(video_path)/(1024*1024):.1f} MB)...")
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=media)

    response = None
    tentativas = 0
    while response is None and tentativas < 5:
        tentativas += 1
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                logger.info(f"    Upload progresso: {pct}%")
        except Exception as e_upload:
            logger.warning(f"  [!] Aviso no envio de chunk: {e_upload}. Retentando...")
            time.sleep(3)

    if not response or "id" not in response:
        raise RuntimeError(f"Falha ao concluir upload do vídeo no YouTube: {response}")

    video_id = response["id"]
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    logger.info(f"  [✔] VÍDEO LONGO PUBLICADO COM SUCESSO! Link: {video_url}")

    # 3. Upload da Capa Customizada (Thumbnail 1280x720)
    if thumbnail_path and os.path.exists(thumbnail_path):
        logger.info(f"  [*] Aplicando capa customizada (Thumbnail 1280x720): {os.path.basename(thumbnail_path)}...")
        try:
            thumb_media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg", resumable=False)
            youtube.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()
            logger.info("  [✔] Thumbnail customizada definida com sucesso!")
        except HttpError as err_thumb:
            logger.warning(
                f"  [!] Aviso ao definir thumbnail: {err_thumb.reason} (Código {err_thumb.status_code}).\n"
                f"      A publicação do vídeo prosseguiu normalmente. Caso o canal não seja verificado para capas customizadas,\n"
                f"      o YouTube utilizará o frame representativo gerado automaticamente."
            )
        except Exception as e_thumb_geral:
            logger.warning(f"  [!] Aviso inesperado na thumbnail: {e_thumb_geral}")

    # 4. Registra no histórico anti-duplicidade
    registrar_sucesso_longo(video_path, video_id, video_url, titulo_limpo)

    return {
        "status": "SUCCESS",
        "video_id": video_id,
        "url": video_url,
        "titulo": titulo_limpo
    }
