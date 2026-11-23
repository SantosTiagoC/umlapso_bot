import os
import sys
import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

root_dir = os.path.dirname(os.path.abspath(__file__))
# Carrega .env da raiz ou config/
env_path = os.path.join(root_dir, ".env")
if not os.path.exists(env_path):
    env_path = os.path.join(root_dir, "config.env")
if not os.path.exists(env_path):
    env_path = os.path.join(root_dir, "config", "config.env")

load_dotenv(env_path, override=True)

print("="*65)
print("=== VALIDACAO META GRAPH API (INSTAGRAM REELS) — UMLAPSO BOT ===")
print("="*65)

access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
account_id = os.getenv("INSTAGRAM_ACCOUNT_ID")
api_version = os.getenv("META_GRAPH_API_VERSION", "v26.0")

if not access_token or not account_id:
    print("[-] Erro: INSTAGRAM_ACCESS_TOKEN ou INSTAGRAM_ACCOUNT_ID nao configurado no .env!")
    sys.exit(1)

print(f"[*] Validando token na Meta Graph API ({api_version})...")

try:
    # 1. Valida identificador Meta do token
    url_me = f"https://graph.facebook.com/{api_version}/me?fields=id,name&access_token={access_token}"
    resp_me = requests.get(url_me, timeout=15)
    dados_me = resp_me.json()

    if resp_me.status_code != 200 or "error" in dados_me:
        erro_msg = dados_me.get("error", {}).get("message", dados_me)
        print(f"[-] Token invalido ou expirado: {erro_msg}")
        sys.exit(1)

    print(f"[+] Token valido! App/Usuario Meta: {dados_me.get('name')} (ID: {dados_me.get('id')})")

    # 2. Valida conta do Instagram
    print(f"[*] Verificando Instagram Account ID: {account_id}...")
    url_acc = f"https://graph.facebook.com/{api_version}/{account_id}?fields=id,username,name,followers_count,media_count&access_token={access_token}"
    resp_acc = requests.get(url_acc, timeout=15)
    dados_acc = resp_acc.json()

    if resp_acc.status_code != 200 or "error" in dados_acc:
        erro_msg = dados_acc.get("error", {}).get("message", dados_acc)
        print(f"[-] Erro ao acessar conta do Instagram: {erro_msg}")
        sys.exit(1)

    print("\n[OK] CONEXAO REALIZADA COM SUCESSO NO INSTAGRAM!")
    print(f"    - ID da Conta: {dados_acc.get('id')}")
    print(f"    - Usuario: @{dados_acc.get('username')}")
    print(f"    - Nome: {dados_acc.get('name')}")
    print(f"    - Seguidores: {dados_acc.get('followers_count', 'N/D')}")
    print(f"    - Total de Midias: {dados_acc.get('media_count', 'N/D')}")
    print("="*65)

except Exception as e:
    print(f"[-] Excecao ao conectar na Meta API: {e}")
    sys.exit(1)

