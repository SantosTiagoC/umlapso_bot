import os
import json
import hashlib
from datetime import datetime
from typing import Tuple, Dict, Any, List

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORICO_PATH = os.path.join(ROOT_DIR, "data", "historico_publicacoes.json")

def calcular_sha256(caminho_arquivo: str) -> str:
    """Calcula o hash SHA-256 do arquivo binário."""
    if not os.path.isfile(caminho_arquivo):
        return ""
    sha = hashlib.sha256()
    with open(caminho_arquivo, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()

def carregar_historico_publicacoes() -> Dict[str, List[Dict[str, Any]]]:
    """Carrega o histórico centralizado de publicações dividido por plataforma."""
    padrao = {
        "instagram": [],
        "tiktok": [],
        "youtube": []
    }
    if not os.path.exists(HISTORICO_PATH):
        return padrao

    try:
        with open(HISTORICO_PATH, "r", encoding="utf-8") as f:
            dados = json.load(f)
            if isinstance(dados, dict):
                for p in ["instagram", "tiktok", "youtube"]:
                    if p not in dados or not isinstance(dados[p], list):
                        dados[p] = []
                return dados
            elif isinstance(dados, list):
                # Migração defensiva caso tenha sido salvo como lista plana
                padrao["tiktok"] = dados
                return padrao
    except Exception as e:
        print(f"[!] Aviso ao ler historico_publicacoes.json: {e}")
    return padrao

def salvar_historico_publicacoes(historico: Dict[str, List[Dict[str, Any]]]) -> None:
    """Salva atomicamente o histórico de publicações."""
    pasta_data = os.path.dirname(HISTORICO_PATH)
    os.makedirs(pasta_data, exist_ok=True)
    temp_path = f"{HISTORICO_PATH}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(historico, f, ensure_ascii=False, indent=2)
        if os.path.exists(HISTORICO_PATH):
            os.replace(temp_path, HISTORICO_PATH)
        else:
            os.rename(temp_path, HISTORICO_PATH)
    except Exception as e:
        print(f"[!] Erro ao salvar historico_publicacoes.json: {e}")
        try:
            with open(HISTORICO_PATH, "w", encoding="utf-8") as f:
                json.dump(historico, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

def verificar_duplicidade(caminho_arquivo: str, plataforma: str) -> Tuple[bool, str]:
    """
    Verifica se o arquivo já foi publicado na plataforma especificada.
    Trava absoluta baseada em:
    1. Hash SHA-256 do arquivo .mp4
    2. Nome exato do arquivo (ex: umlapso_1792804828.mp4)
    3. Nome base (ex: umlapso_1792804828)
    """
    if not caminho_arquivo or not os.path.exists(caminho_arquivo):
        return False, ""

    sha256_atual = calcular_sha256(caminho_arquivo)
    nome_arquivo = os.path.basename(caminho_arquivo)
    nome_base = os.path.splitext(nome_arquivo)[0]

    historico = carregar_historico_publicacoes()
    registros = historico.get(plataforma, [])

    for reg in registros:
        if not isinstance(reg, dict):
            continue
        reg_sha = reg.get("sha256")
        if reg_sha and sha256_atual and reg_sha == sha256_atual:
            return True, f"SHA-256 idêntico ({sha256_atual[:12]}...)"
        if reg.get("arquivo") == nome_arquivo:
            return True, f"Nome de arquivo já registrado ({nome_arquivo})"
        if reg.get("nome_base") == nome_base:
            return True, f"Nome base já registrado ({nome_base})"

    return False, ""

def verificar_media_id_duplicado(media_id: str, plataforma: str = "instagram") -> Tuple[bool, str]:
    """Verifica se um ID de mídia do Instagram já consta no histórico de publicações."""
    if not media_id:
        return False, ""
    historico = carregar_historico_publicacoes()
    registros = historico.get(plataforma, [])
    for reg in registros:
        if str(reg.get("media_id", "")) == str(media_id):
            return True, f"media_id já publicado anteriormente ({media_id})"
    return False, ""

def registrar_publicacao_sucesso(caminho_arquivo: str, plataforma: str, metadados: Dict[str, Any] = None) -> None:
    """Registra uma publicação confirmada no histórico anti-duplicidade."""
    sha256_atual = calcular_sha256(caminho_arquivo) if caminho_arquivo and os.path.exists(caminho_arquivo) else ""
    nome_arquivo = os.path.basename(caminho_arquivo) if caminho_arquivo else ""
    nome_base = os.path.splitext(nome_arquivo)[0] if nome_arquivo else ""

    historico = carregar_historico_publicacoes()
    if plataforma not in historico:
        historico[plataforma] = []

    novo_registro = {
        "sha256": sha256_atual,
        "arquivo": nome_arquivo,
        "nome_base": nome_base,
        "publicado_em": datetime.now().isoformat(),
        **(metadados or {})
    }
    historico[plataforma].append(novo_registro)
    # Limita o histórico das últimas 500 publicações por plataforma para manter desempenho
    historico[plataforma] = historico[plataforma][-500:]
    salvar_historico_publicacoes(historico)
    print(f"[+] [Anti-Duplicidade] Publicação registrada com sucesso em '{plataforma}': {nome_arquivo} (SHA: {sha256_atual[:8]}...)")
