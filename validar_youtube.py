import os
import sys
import json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
]
root_dir = os.path.dirname(os.path.abspath(__file__))

token_candidates = [
    os.path.join(root_dir, "token_youtube.json"),
    os.path.join(root_dir, "config", "token_youtube.json")
]

token_path = None
for cand in token_candidates:
    if os.path.exists(cand):
        token_path = cand
        break

print("="*65)
print("=== VALIDACAO DO CANAL YOUTUBE SHORTS — UMLAPSO BOT ===")
print("="*65)

if not token_path:
    print("[-] Arquivo 'token_youtube.json' nao encontrado!")
    sys.exit(1)

print(f"[*] Carregando credenciais de: {token_path}")

try:
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds:
        raise Exception("Nao foi possivel carregar credenciais.")

    if creds.expired and creds.refresh_token:
        print("[*] Access token expirado. Renovando automaticamente via refresh token...")
        creds.refresh(Request())
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        print("[+] Token renovado e atualizado no arquivo com sucesso!")
    elif creds.valid:
        print("[+] Access token esta valido!")

    youtube = build("youtube", "v3", credentials=creds)
    print("\n[OK] SUCESSO! CREDENCIAIS DO YOUTUBE CONECTADAS E OPERACIONAIS!")
    print(f"    - Escopos ativos: {creds.scopes}")
    print(f"    - Expira em: {creds.expiry}")
    print("="*65)

except Exception as e:
    print(f"\n[!] Falha na validacao do YouTube: {e}")
    sys.exit(1)
