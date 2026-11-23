import os
import json
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent

def _carregar_envs():
    candidatos = [
        ROOT_DIR / "config" / "config.env",
        ROOT_DIR / "config" / ".env",
        ROOT_DIR / "config.env",
        ROOT_DIR / ".env",
    ]
    for c in candidatos:
        if c.exists():
            load_dotenv(c)

def _obter_credenciais():
    _carregar_envs()
    token = (
        os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("TELEGRAMBOTTOKEN")
        or ""
    ).strip()
    chat_id = (
        os.getenv("TELEGRAM_CHAT_ID")
        or os.getenv("TELEGRAMCHATID")
        or ""
    ).strip()
    return token, chat_id

def _enviar_mensagem(texto: str, botoes: list = None) -> bool:
    """Envia uma mensagem formatada em HTML para o Telegram com suporte a botões inline."""
    token, chat_id = _obter_credenciais()
    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    if botoes:
        payload["reply_markup"] = {"inline_keyboard": botoes}

    try:
        resp = requests.post(url, json=payload, timeout=15)
        return resp.status_code == 200
    except Exception as e:
        print(f"[*] Aviso Telegram (sendMessage não bloqueante): {e}")
        return False

def _enviar_foto(caminho_foto: str, legenda: str, botoes: list = None) -> bool:
    """Envia a foto de capa do vídeo com legenda formatada e botões inline."""
    token, chat_id = _obter_credenciais()
    if not token or not chat_id:
        return False

    if not caminho_foto or not os.path.isfile(caminho_foto):
        return _enviar_mensagem(legenda, botoes)

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    data = {
        "chat_id": chat_id,
        "caption": legenda[:1024],
        "parse_mode": "HTML"
    }
    if botoes:
        data["reply_markup"] = json.dumps({"inline_keyboard": botoes})

    try:
        with open(caminho_foto, "rb") as foto:
            resp = requests.post(url, data=data, files={"photo": foto}, timeout=30)
            if resp.status_code == 200:
                return True
    except Exception as e:
        print(f"[*] Aviso Telegram sendPhoto falhou ({e}). Tentando fallback em texto...")

    return _enviar_mensagem(legenda, botoes)

def notificar_fim_ciclo(
    titulo: str,
    tema: str,
    link_instagram: str | None = None,
    link_tiktok: str | None = None,
    link_youtube: str | None = None,
    tempo_segundos: float = 0,
    cover_frame: str | None = None,
    dry_run: bool = False,
) -> bool:
    """Notifica a conclusão da postagem com capa e botões diretos para as redes."""
    try:
        duracao_str = f"{int(tempo_segundos // 60)}m {int(tempo_segundos % 60)}s" if tempo_segundos > 0 else "N/A"

        botoes = []
        linha_botoes = []
        if link_instagram and link_instagram.startswith("http"):
            linha_botoes.append({"text": "📸 Ver no Instagram", "url": link_instagram})
        if link_youtube and link_youtube.startswith("http"):
            linha_botoes.append({"text": "▶️ YouTube Shorts", "url": link_youtube})
        if link_tiktok and link_tiktok.startswith("http"):
            linha_botoes.append({"text": "🎵 Ver no TikTok", "url": link_tiktok})

        if linha_botoes:
            botoes.append(linha_botoes)

        redes_ativas = []
        if link_instagram:
            redes_ativas.append("Instagram Reels")
        if link_youtube:
            redes_ativas.append("YouTube Shorts")
        if link_tiktok:
            redes_ativas.append("TikTok")
        redes_str = ", ".join(redes_ativas) if redes_ativas else "Publicação Concluída"

        legenda = (
            f"<b>✅ UMLAPSO BOT | VÍDEO PUBLICADO COM SUCESSO!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>🎬 Título:</b> <code>{titulo[:75]}</code>\n"
            f"<b>🌌 Tema:</b> {tema[:100]}\n"
            f"<b>🌐 Redes:</b> {redes_str}\n"
            f"<b>⏱️ Duração do Ciclo:</b> {duracao_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )
        if dry_run:
            legenda += "<i>🧪 Simulação finalizada. Nenhuma publicação externa efetuada.</i>"
        else:
            legenda += "<i>🚀 Vídeo publicado e distribuído nas redes sociais!</i>"

        return _enviar_foto(cover_frame, legenda, botoes)
    except Exception as e:
        print(f"[*] Aviso Telegram notificar_fim_ciclo: {e}")
        return False

