import os
import time
import json
import requests
from urllib.parse import urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

from core.anti_duplicidade import verificar_duplicidade, registrar_publicacao_sucesso
from core.tiktok_publisher import TikTokPublisher

def validar_video_para_publicacao(video_path: str) -> int:
    """
    Valida minuciosamente se o arquivo de vídeo está pronto e apto para distribuição.
    Verifica:
    - Existência do arquivo.
    - Tamanho mínimo (> 500 KB) para impedir envio de arquivos truncados.
    - Duração mínima (> 3s) requerida por Instagram Reels, YouTube Shorts e TikTok.
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Arquivo de vídeo não encontrado em: {video_path}")
        
    tamanho = os.path.getsize(video_path)
    if tamanho < 500_000:
        raise ValueError(
            f"Arquivo de vídeo inválido ou truncado: '{video_path}' possui apenas {tamanho} bytes.\n"
            f"Vídeos verticais completos devem ter no mínimo 1MB a 15MB. "
            f"O envio para as redes foi bloqueado para evitar punições ou falhas de processamento."
        )

    # Verifica integridade de frames se OpenCV estiver instalado
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        ret, _ = cap.read()
        cap.release()
        
        duracao = frames / fps if fps > 0 else 0
        if not ret or duracao < 3.0:
            raise ValueError(
                f"Vídeo corrompido ou muito curto: '{video_path}' tem {duracao:.2f}s e {frames} frames. "
                f"As plataformas (Instagram/YouTube/TikTok) exigem no mínimo 3 segundos de vídeo legível."
            )
    except ImportError:
        pass
        
    return tamanho

def obter_ultimo_video_valido(pasta_output: str = "output") -> tuple[str, dict]:
    """
    Localiza automaticamente o vídeo final correto gerado dentro da pasta 'output'.
    Ignora arquivos temporários, cenas individuais ou versões corrompidas/incompletas.
    Retorna (caminho_video, dados_metadados).
    """
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    caminho_dir = os.path.join(root_dir, pasta_output) if not os.path.isabs(pasta_output) else pasta_output
    
    if not os.path.isdir(caminho_dir):
        raise FileNotFoundError(f"Diretório de saída '{caminho_dir}' não encontrado.")
        
    candidatos = []
    for f in os.listdir(caminho_dir):
        if f.startswith("umlapso_") and f.endswith(".mp4"):
            p = os.path.join(caminho_dir, f)
            try:
                tamanho = os.path.getsize(p)
                mtime = os.path.getmtime(p)
                candidatos.append((mtime, tamanho, p))
            except Exception:
                pass
                
    if not candidatos:
        raise FileNotFoundError(f"Nenhum vídeo 'umlapso_*.mp4' encontrado na pasta '{caminho_dir}'.")
        
    # Ordena pelo mais recente
    candidatos.sort(key=lambda x: x[0], reverse=True)
    
    # Procura o primeiro vídeo com tamanho válido (> 500KB)
    video_escolhido = None
    for mtime, tamanho, p in candidatos:
        if tamanho >= 500_000:
            video_escolhido = p
            break
            
    if not video_escolhido:
        p_mais_recente = candidatos[0][2]
        tam_recente = candidatos[0][1]
        raise ValueError(
            f"O vídeo mais recente na pasta '{caminho_dir}' ({os.path.basename(p_mais_recente)}) "
            f"é inválido/corrompido (tamanho: {tam_recente} bytes). É necessário gerar um vídeo válido primeiro."
        )
        
    # Localiza o arquivo de metadados correspondente
    base_nome = os.path.splitext(video_escolhido)[0]
    meta_path = f"{base_nome}_meta.json"
    metadados = {}
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as fm:
                metadados = json.load(fm)
        except Exception:
            pass
            
    print(f"[*] Vídeo final válido identificado: {video_escolhido} ({os.path.getsize(video_escolhido)} bytes)")
    return video_escolhido, metadados

def resolver_url_publica(caminho_local: str) -> str:
    """Gera uma URL pública direta HTTPS para a Meta Graph API baixar o vídeo."""
    base_url = os.getenv("PUBLIC_MEDIA_URL_BASE", "").strip().rstrip("/")
    if base_url:
        try:
            r = requests.get(base_url, timeout=3)
            if r.status_code < 500:
                rel_path = os.path.relpath(caminho_local, ROOT_DIR).replace("\\", "/")
                return f"{base_url}/{rel_path}"
        except Exception:
            pass

    nome_arquivo = os.path.basename(caminho_local)
    mime_type = "video/mp4"

    # Uploader 1: Uguu.se
    try:
        with open(caminho_local, "rb") as f:
            resp = requests.post(
                "https://uguu.se/upload",
                files={"files[]": (nome_arquivo, f, mime_type)},
                timeout=120
            )
        if resp.status_code == 200:
            dados = resp.json()
            if dados.get("success") and dados.get("files"):
                url = dados["files"][0].get("url")
                if url and url.startswith("http"):
                    return url
    except Exception as e:
        print(f"  [-] Uguu fallback ({e}). Tentando 0x0.st...")

    # Uploader 2: 0x0.st
    try:
        with open(caminho_local, "rb") as f:
            resp_0x = requests.post(
                "https://0x0.st",
                files={"file": (nome_arquivo, f, mime_type)},
                timeout=120
            )
        if resp_0x.status_code == 200 and resp_0x.text.strip().startswith("http"):
            return resp_0x.text.strip()
    except Exception as e:
        print(f"  [-] 0x0 falhou ({e}). Tentando tmpfiles.org...")

    # Uploader 3: tmpfiles.org
    try:
        with open(caminho_local, "rb") as f:
            resp_tmp = requests.post(
                "https://tmpfiles.org/api/v1/upload",
                files={"file": (nome_arquivo, f, mime_type)},
                timeout=120
            )
        if resp_tmp.status_code == 200:
            url_tmp = resp_tmp.json().get("data", {}).get("url", "")
            if url_tmp and "tmpfiles.org/" in url_tmp:
                direct_url = url_tmp.replace("tmpfiles.org/", "tmpfiles.org/dl/")
                return direct_url
    except Exception as e:
        print(f"  [-] tmpfiles falhou ({e})")

    raise RuntimeError("Não foi possível gerar URL pública direta para o vídeo.")

# ============================================================
# INSTAGRAM REELS (API Oficial Meta / Instagram Graph API)
# ============================================================
def postar_instagram_reels(video_path: str, caption: str):
    """
    Publica um vídeo no Instagram Reels utilizando a API Oficial da Meta (Instagram Graph API).
    Utiliza URL pública direta para processamento assíncrono pela infraestrutura da Meta.
    Possui trava anti-duplicidade estrita baseada em hash SHA-256 e nome de arquivo.
    """
    print("  -> A publicar no Instagram Reels (API Oficial Meta)...")
    
    # TRAVA ANTI-DUPLICIDADE ABSOLUTA
    is_dup, motivo_dup = verificar_duplicidade(video_path, "instagram")
    if is_dup:
        msg_skip = f"[SKIP] Vídeo já publicado no Instagram: {os.path.basename(video_path)} ({motivo_dup})"
        print(f"  [-] {msg_skip}")
        return {"status": "SKIPPED", "motivo": msg_skip}
    
    validar_video_para_publicacao(video_path)
    
    load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    account_id = os.getenv("INSTAGRAM_ACCOUNT_ID", "17841441761053564").strip()
    
    if not access_token:
        raise ValueError("INSTAGRAM_ACCESS_TOKEN não foi configurado no arquivo .env. Insira seu token de acesso da Meta.")
    if not account_id:
        raise ValueError("INSTAGRAM_ACCOUNT_ID não foi configurado no arquivo .env.")

    graph_version = os.getenv("META_GRAPH_API_VERSION", "v21.0").strip()
    base_url = f"https://graph.facebook.com/{graph_version}"
    
    # 1. Resolver URL pública para o vídeo
    print("  [*] Passo 1: Resolvendo URL pública acessível para a Meta...")
    public_url = resolver_url_publica(video_path)
    print(f"  [+] URL Pública obtida: {public_url}")
    
    # 2. Criar container do Reel
    print("  [*] Passo 2: Criando container de Reels na Meta...")
    endpoint_media = f"{base_url}/{account_id}/media"
    payload_media = {
        "media_type": "REELS",
        "video_url": public_url,
        "caption": caption,
        "share_to_feed": True,
        "access_token": access_token
    }
    
    resp_media = requests.post(endpoint_media, json=payload_media, timeout=40)
    dados_media = resp_media.json()
    
    if resp_media.status_code != 200 or "id" not in dados_media:
        raise Exception(f"Erro ao inicializar container do Reels: {dados_media.get('error', {}).get('message', dados_media)}")
        
    creation_id = dados_media["id"]
    print(f"  [+] Container criado com sucesso! ID: {creation_id}")
    
    # 3. Aguardar codificação do Reel pelos servidores da Meta (Polling)
    print("  [*] Passo 3: Aguardando codificação do Reel pelos servidores da Meta...")
    endpoint_status = f"{base_url}/{creation_id}"
    params_status = {
        "fields": "status_code,status",
        "access_token": access_token
    }
    
    tentativas = 40
    processado = False
    for i in range(tentativas):
        time.sleep(5)
        resp_check = requests.get(endpoint_status, params=params_status, timeout=20)
        dados_check = resp_check.json()
        status_code = dados_check.get("status_code")
        
        if status_code == "FINISHED":
            processado = True
            print("  [+] Vídeo processado e pronto para publicação!")
            break
        elif status_code in ["ERROR", "EXPIRED"]:
            raise Exception(f"A Meta encontrou um erro ao processar o vídeo ({status_code}): {dados_check}")
        else:
            print(f"  ... Em processamento ({status_code or 'PENDING'}). Tentativa {i+1}/{tentativas}. Aguardando 5s...")
            
    if not processado:
        raise TimeoutError("Tempo limite esgotado enquanto aguardava processamento do Reel pela Meta.")

    # 4. Publicar o container
    print("  [*] Passo 4: Publicando o Reel no perfil do Instagram...")
    endpoint_publish = f"{base_url}/{account_id}/media_publish"
    payload_publish = {
        "creation_id": creation_id,
        "access_token": access_token
    }
    
    resp_publish = requests.post(endpoint_publish, json=payload_publish, timeout=40)
    dados_publish = resp_publish.json()
    
    media_id = None
    if resp_publish.status_code == 200 and "id" in dados_publish:
        media_id = dados_publish["id"]
    else:
        # Fallback de verificação se o post foi publicado em segundo plano
        time.sleep(4)
        r_rec = requests.get(
            f"{base_url}/{account_id}/media",
            params={"fields": "id,permalink,caption,timestamp", "limit": 2, "access_token": access_token},
            timeout=20
        )
        d_rec = r_rec.json().get("data", [])
        if d_rec and d_rec[0].get("caption", "")[:20] == (caption or "")[:20]:
            media_id = d_rec[0]["id"]
        else:
            raise Exception(f"Erro ao publicar o Reel: {dados_publish.get('error', {}).get('message', dados_publish)}")
        
    # 5. Obter permalink público do Reel publicado
    link_instagram = f"https://www.instagram.com/reel/{media_id}/"
    try:
        resp_permalink = requests.get(
            f"{base_url}/{media_id}",
            params={"fields": "permalink,shortcode", "access_token": access_token},
            timeout=15
        )
        if resp_permalink.status_code == 200 and "permalink" in resp_permalink.json():
            link_instagram = resp_permalink.json()["permalink"]
    except Exception:
        pass
        
    print(f"  [!!!] REELS PUBLICADO COM SUCESSO! Link: {link_instagram}")

    # Registra no histórico anti-duplicidade
    registrar_publicacao_sucesso(video_path, "instagram", {
        "media_id": media_id,
        "permalink": link_instagram,
        "caption": caption[:100] if caption else ""
    })

    return {"media_id": media_id, "permalink": link_instagram, "status": "SUCCESS"}

# ============================================================
# YOUTUBE SHORTS (API Oficial do Google)
# ============================================================
def postar_youtube_shorts(video_path: str, titulo: str, descricao: str, tags: list):
    """
    Publica um vídeo no YouTube Shorts via Google API Client (OAuth 2.0).
    Requer o arquivo client_secrets.json na raiz do projeto.
    Categoria 24 = Entretenimento.
    Possui trava anti-duplicidade baseada em SHA-256 e nome de arquivo.
    """
    print("  -> A publicar no YouTube Shorts...")

    # TRAVA ANTI-DUPLICIDADE
    is_dup, motivo_dup = verificar_duplicidade(video_path, "youtube")
    if is_dup:
        msg_skip = f"[SKIP] Vídeo já publicado no YouTube: {os.path.basename(video_path)} ({motivo_dup})"
        print(f"  [-] {msg_skip}")
        return {"status": "SKIPPED", "motivo": msg_skip}

    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube"
    ]
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Procura arquivo de credenciais OAuth
    secrets_candidates = [
        os.path.join(root_dir, "client_secrets.json"),
        os.path.join(root_dir, "config", "client_secrets.json"),
        os.path.join(root_dir, "client_secret.json"),
        os.path.join(root_dir, "credenciais.json")
    ]
    
    token_candidates = [
        os.path.join(root_dir, "token_youtube.json"),
        os.path.join(root_dir, "config", "token_youtube.json"),
    ]
    token_file = None
    for tc in token_candidates:
        if os.path.exists(tc):
            token_file = tc
            break
    if not token_file:
        token_file = token_candidates[0]

    creds = None
    
    # 1. Carrega token salvo se existir
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception:
            creds = None
            
    # 2. Se não houver token válido, renova ou faz autenticação inicial
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("  [*] Atualizando token de acesso expirado do YouTube...")
            creds.refresh(Request())
        else:
            secrets_file = None
            for cand in secrets_candidates:
                if os.path.exists(cand):
                    try:
                        with open(cand, "r", encoding="utf-8") as f:
                            dados = json.load(f)
                            if "installed" in dados or "web" in dados:
                                secrets_file = cand
                                break
                    except Exception:
                        pass
                        
            if not secrets_file:
                raise FileNotFoundError(
                    "Credencial OAuth do YouTube não encontrada!\n"
                    "Crie um 'ID do cliente OAuth' do tipo 'App para computador' no Google Cloud Console,\n"
                    "baixe o arquivo JSON e salve na raiz do projeto como 'client_secrets.json'."
                )
                
            print(f"  [*] Preparando autorização OAuth para o YouTube ({os.path.basename(secrets_file)})...", flush=True)
            flow = InstalledAppFlow.from_client_secrets_file(secrets_file, SCOPES)
            creds = flow.run_local_server(
                port=8080,
                prompt="consent",
                authorization_prompt_message="Acesse este link no seu navegador para autorizar:\n{url}\n",
                success_message="Autenticação com o YouTube concluída com sucesso! Pode fechar esta janela."
            )
            
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        print(f"  [+] Token de acesso salvo permanentemente em {os.path.basename(token_file)}!", flush=True)

    youtube = build("youtube", "v3", credentials=creds)
    
    body = {
        "snippet": {
            "title": titulo[:100],
            "description": f"{descricao}\n\n#shorts",
            "tags": tags if tags else [],
            "categoryId": "24"
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }
    
    validar_video_para_publicacao(video_path)
    
    print(f"  [*] Enviando vídeo para o YouTube ({video_path})...")
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=media)
    response = request.execute()
    
    video_id = response.get("id")
    url_shorts = f"https://youtube.com/shorts/{video_id}"
    
    print(f"  [*] Upload concluído! ID: {video_id}. Verificando status no YouTube...")
    status_final = "uploaded"
    for _ in range(6):
        time.sleep(3)
        try:
            status_req = youtube.videos().list(part="status,processingDetails", id=video_id)
            status_resp = status_req.execute()
            items = status_resp.get("items", [])
            if items:
                v_status = items[0].get("status", {})
                upload_status = v_status.get("uploadStatus", "")
                failure_reason = v_status.get("failureReason")
                if failure_reason or upload_status == "failed":
                    raise Exception(f"YouTube rejeitou o processamento do vídeo: {failure_reason or 'Upload failed'}")
                if upload_status in ["processed", "uploaded"]:
                    status_final = upload_status
                    break
        except Exception as e_check:
            if "rejeitou" in str(e_check):
                raise
            break

    print(f"  [!!!] SHORTS PUBLICADO COM SUCESSO! Link: {url_shorts} (Status: {status_final})")

    registrar_publicacao_sucesso(video_path, "youtube", {
        "video_id": video_id,
        "url": url_shorts,
        "titulo": titulo
    })

    return {"video_id": video_id, "url": url_shorts, "status": status_final}

# ============================================================
# TIKTOK (Arquitetura Blindada via TikTokPublisher)
# ============================================================
def postar_tiktok(video_path: str, caption: str) -> dict:
    """
    Publica um vídeo no TikTok utilizando a arquitetura blindada validada em produção.
    Delega diretamente para TikTokPublisher.
    """
    habilitado_tiktok = os.getenv("TIKTOK_ENABLED", "true").lower()
    if habilitado_tiktok in ["false", "0", "no"]:
        print("  [*] Publicação no TikTok desativada no .env (TIKTOK_ENABLED=false). Pulando...", flush=True)
        return {"status": "SKIPPED", "motivo": "TIKTOK_ENABLED=false"}

    publisher = TikTokPublisher()
    resultado = publisher.publicar(video_path=video_path, legenda=caption)
    return resultado
