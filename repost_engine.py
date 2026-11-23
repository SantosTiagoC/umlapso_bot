#!/usr/bin/env python3
# =============================================================================
# repost_engine.py  —  v3  (comportamento humano, multi-ação por sessão)
# =============================================================================
# Autodeteção: detecta BOTDIR via __file__, carrega config.env do próprio bot.
# Sem caminhos fixos — portátil entre qualquer bot da home.
#
# Novidade v3: sessão com comportamento humano simulado
#   - Cada execução decide quantas ações fazer (1–N) e quando
#   - Intervalos variáveis com distribuição não-uniforme (curtos + longos)
#   - Probabilidade de "pausa inesperada" entre ações (simula distração)
#   - Probabilidade de encerrar cedo (simula humano que para no meio)
#   - Horários aleatórios controlados por cron com janela ampla
#   - Todas as configurações via config.env, sem tocar no bot principal
# =============================================================================

import os
import json
import time
import random
import logging
import sqlite3
import tempfile
import subprocess
import warnings
from datetime import datetime
from pathlib import Path

from core.lock import LockSimultaneo
from core.anti_duplicidade import (
    verificar_duplicidade,
    verificar_media_id_duplicado,
    registrar_publicacao_sucesso
)

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

import requests

# =============================================================================
# AUTODETEÇÃO DE DIRETÓRIO
# =============================================================================

BOTDIR: Path = Path(__file__).resolve().parent

# =============================================================================
# CARREGAMENTO GENÉRICO DE CONFIG
# =============================================================================

_KEY_ALIASES = {
    "IG_USER":            "IGUSER",
    "IG_PASS":            "IGPASS",
    "OPENAI_KEY":         "OPENAIKEY",
    "OPENAI_API_KEY":     "OPENAIKEY",
    "OPEN_AI_KEY":        "OPENAIKEY",
    "TELEGRAM_BOT_TOKEN": "TELEGRAMBOTTOKEN",
    "TELEGRAM_CHAT_ID":   "TELEGRAMCHATID",
    "META_ACCESS_TOKEN":   "META_ACCESS_TOKEN",
    "ACCESS_TOKEN":        "META_ACCESS_TOKEN",
    "INSTAGRAM_ACCESS_TOKEN": "META_ACCESS_TOKEN",
    "INSTAGRAM_ACCOUNT_ID": "INSTAGRAM_ACCOUNT_ID",
    "IG_ACCOUNT_ID":       "INSTAGRAM_ACCOUNT_ID",
    "META_GRAPH_API_VERSION": "META_GRAPH_VERSION",
    "META_GRAPH_VERSION": "META_GRAPH_VERSION",
    "BOT_BASEDIR":        "BOT_BASEDIR",
    "BOT_CONFIGDIR":      "BOT_CONFIGDIR",
    "BOT_DBDIR":          "BOT_DBDIR",
    "BOT_LOGDIR":         "BOT_LOGDIR",
    "BOT_TEMPDIR":        "BOT_TEMPDIR",
    "BOT_SESSIONPATH":    "BOT_SESSIONPATH",
    "UMLAPSO_BASEDIR":    "BOT_BASEDIR",
    "QUIZBOT_BASEDIR":    "BOT_BASEDIR",
}


def _strip_comment(val: str) -> str:
    in_q, qc = False, None
    for i, ch in enumerate(val):
        if ch in ('"', "'") and not in_q:
            in_q, qc = True, ch
        elif in_q and ch == qc:
            in_q = False
        elif not in_q and ch == "#" and i > 0 and val[i - 1] in (" ", "\t"):
            return val[:i].strip()
    return val.strip()


def _load_env_file(path: Path, target: dict) -> bool:
    if not path.exists() or not path.is_file():
        return False
    loaded = False
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip().replace(" ", "")
                v = v.strip().replace("\n", "")
                v = v.strip('"').strip("'")
                v = _strip_comment(v)
                target[k] = v
                alias = _KEY_ALIASES.get(k)
                if alias and alias != k:
                    target[alias] = v
                loaded = True
    except Exception:
        pass
    return loaded


def _discover_env_files(botdir: Path) -> list:
    return [
        botdir / "config" / "config.env",
        botdir / "config" / ".env",
        botdir / "config" / ".env.txt",
        botdir / "config.env",
        botdir / ".env",
        botdir / ".env.txt",
        botdir.parent / "config" / "config.env",
        botdir.parent / ".env",
    ]


_config: dict = {}
_env_loaded_from = None

for _ep in _discover_env_files(BOTDIR):
    if _load_env_file(_ep, _config):
        if "IGUSER" in _config or "IG_USER" in _config or "META_ACCESS_TOKEN" in _config:
            _env_loaded_from = _ep
            break

if not _env_loaded_from:
    for _ep in _discover_env_files(BOTDIR):
        if _load_env_file(_ep, _config) and _config:
            _env_loaded_from = _ep
            break


def _cfg(key: str, default: str = "") -> str:
    if key in _config:
        return _config[key] or default
    alias = _KEY_ALIASES.get(key)
    if alias and alias in _config:
        return _config[alias] or default
    return os.getenv(key, default)

# =============================================================================
# RESOLUÇÃO DE PATHS
# =============================================================================

def _resolve_path(env_keys: list, default: Path) -> Path:
    for key in env_keys:
        val = _cfg(key)
        if val:
            p = Path(val)
            return p.resolve() if p.is_absolute() else (BOTDIR / p).resolve()
    return default.resolve()


_BASEDIR   = _resolve_path(["BOT_BASEDIR"],     BOTDIR)
_CONFIGDIR = _resolve_path(["BOT_CONFIGDIR"],   _BASEDIR / "config")
_DBDIR     = _resolve_path(["BOT_DBDIR"],       _BASEDIR / "db")
_LOGDIR    = _resolve_path(["BOT_LOGDIR"],      _BASEDIR / "logs")
_TEMPDIR   = _resolve_path(["BOT_TEMPDIR"],     _BASEDIR / "temp")
_SESSION   = _resolve_path(["BOT_SESSIONPATH"], _CONFIGDIR / "session.json")

_REPOST_DB   = _DBDIR     / "reposts.db"
_REPOST_LOG  = _LOGDIR    / "repost_engine.log"
_SESSION_BCK = _CONFIGDIR / "session_repost.json"
_REST_STATE  = _CONFIGDIR / "repost_restday.json"


def _ensure_dirs() -> None:
    for d in (_CONFIGDIR, _DBDIR, _LOGDIR, _TEMPDIR):
        d.mkdir(parents=True, exist_ok=True)

# =============================================================================
# CREDENCIAIS
# =============================================================================

IGUSER               = _cfg("IGUSER")
IGPASS               = _cfg("IGPASS")
META_ACCESS_TOKEN    = _cfg("META_ACCESS_TOKEN") or _cfg("ACCESS_TOKEN") or _cfg("INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_ACCOUNT_ID = _cfg("INSTAGRAM_ACCOUNT_ID") or _cfg("IG_ACCOUNT_ID")
META_GRAPH_VERSION   = _cfg("META_GRAPH_VERSION") or _cfg("META_GRAPH_API_VERSION", "v26.0")
OPENAIKEY            = (
    _cfg("OPENAI_API_KEY") or 
    _cfg("OPENAIKEY") or 
    _cfg("OPENAI_KEY") or 
    os.getenv("OPENAI_API_KEY") or 
    os.getenv("OPENAIKEY") or 
    ""
)
PERPLEXITY_KEY       = _cfg("PERPLEXITY_KEY")
TELEGRAMBOTTOKEN     = _cfg("TELEGRAMBOTTOKEN")
TELEGRAMCHATID       = _cfg("TELEGRAMCHATID")

# =============================================================================
# CONFIGURAÇÕES DO ENGINE
# =============================================================================

REPOST_STORY_ENABLED = _cfg("REPOST_STORY_ENABLED", "1") == "1"
REPOST_REELS_ENABLED = _cfg("REPOST_REELS_ENABLED", "0") == "1"

try:    REPOST_LOOKBACK_AMOUNT = int(_cfg("REPOST_LOOKBACK_AMOUNT", "30"))
except: REPOST_LOOKBACK_AMOUNT = 30

try:    REPOST_NO_REPEAT_DAYS = int(_cfg("REPOST_NO_REPEAT_DAYS", "7"))
except: REPOST_NO_REPEAT_DAYS = 7

try:    CUTOFFHOUR = int(_cfg("CUTOFFHOUR", "23"))  # Pausa noturna às 23h
except: CUTOFFHOUR = 23

try:    RESUMEHOUR = int(_cfg("RESUMEHOUR", "6"))   # Retomada matutina às 06h
except: RESUMEHOUR = 6

try:    REPOST_REST_PROB = float(_cfg("REPOST_REST_PROB", "0.0"))  # 0.0 para execução consistente via Cron
except: REPOST_REST_PROB = 0.0

try:    REPOST_MODE_PROB_SAME = float(_cfg("REPOST_MODE_PROB_SAME", "0.45"))
except: REPOST_MODE_PROB_SAME = 0.45

try:    REPOST_MODE_PROB_NEW = float(_cfg("REPOST_MODE_PROB_NEW", "0.40"))
except: REPOST_MODE_PROB_NEW = 0.40

try:    REPOST_DELAY_MIN = int(_cfg("REPOST_DELAY_MIN", "20"))
except: REPOST_DELAY_MIN = 20

try:    REPOST_DELAY_MAX = int(_cfg("REPOST_DELAY_MAX", "75"))
except: REPOST_DELAY_MAX = 75

# ── Configurações por sessão (1 repost por acionamento do cron a cada 1h30) ───

try:    SESSION_ACTIONS_MIN = int(_cfg("REPOST_SESSION_ACTIONS_MIN", "1"))
except: SESSION_ACTIONS_MIN = 1

try:    SESSION_ACTIONS_MAX = int(_cfg("REPOST_SESSION_ACTIONS_MAX", "1"))
except: SESSION_ACTIONS_MAX = 1

# Intervalo curto entre ações consecutivas (segundos) — simula "fez dois seguidos"
try:    SESSION_SHORT_DELAY_MIN = int(_cfg("REPOST_SHORT_DELAY_MIN", "30"))
except: SESSION_SHORT_DELAY_MIN = 30

try:    SESSION_SHORT_DELAY_MAX = int(_cfg("REPOST_SHORT_DELAY_MAX", "90"))
except: SESSION_SHORT_DELAY_MAX = 90

# Intervalo longo entre ações — simula "parou e voltou depois"
try:    SESSION_LONG_DELAY_MIN = int(_cfg("REPOST_LONG_DELAY_MIN", "600"))
except: SESSION_LONG_DELAY_MIN = 600   # 10 min

try:    SESSION_LONG_DELAY_MAX = int(_cfg("REPOST_LONG_DELAY_MAX", "1800"))
except: SESSION_LONG_DELAY_MAX = 1800  # 30 min

# Probabilidade de usar delay longo (vs curto) entre ações
try:    SESSION_LONG_DELAY_PROB = float(_cfg("REPOST_LONG_DELAY_PROB", "0.30"))
except: SESSION_LONG_DELAY_PROB = 0.30

# Probabilidade de encerrar a sessão mais cedo (após cada ação)
try:    SESSION_EARLY_STOP_PROB = float(_cfg("REPOST_EARLY_STOP_PROB", "0.20"))
except: SESSION_EARLY_STOP_PROB = 0.20

# Probabilidade de pular uma ação planejada (simula "decidiu não postar agora")
try:    SESSION_SKIP_PROB = float(_cfg("REPOST_SKIP_PROB", "0.0"))  # 0.0 para garantir disparo pontual do cron
except: SESSION_SKIP_PROB = 0.0

# Delay inicial aleatório antes da 1ª ação da sessão (segundos)
# Leve jitter natural de até 60 segundos
try:    SESSION_INITIAL_DELAY_MIN = int(_cfg("REPOST_INITIAL_DELAY_MIN", "0"))
except: SESSION_INITIAL_DELAY_MIN = 0

try:    SESSION_INITIAL_DELAY_MAX = int(_cfg("REPOST_INITIAL_DELAY_MAX", "60"))
except: SESSION_INITIAL_DELAY_MAX = 60

REPOST_CAPTION_PROMPT = _cfg(
    "REPOST_CAPTION_PROMPT",
    (
        "Voce e um criador de conteudo no Instagram.\n"
        "Crie uma legenda curta e engajante para repostar este conteudo.\n"
        "Contexto do post original: {caption_original}\n"
        "Formato de destino: {formato}\n\n"
        "Regras:\n"
        "- Maximo 2 frases curtas\n"
        "- Tom divertido e direto\n"
        "- Incentive interacao\n"
        "- Inclua 3 a 5 hashtags relevantes ao final\n"
        "- NAO repita palavra por palavra a legenda original\n"
        "- Responda APENAS com a legenda pronta, sem explicacoes."
    ),
)

# =============================================================================
# LOGGING
# =============================================================================

_ensure_dirs()

_log = logging.getLogger("repost_engine." + BOTDIR.name)
_log.setLevel(logging.INFO)
if not _log.handlers:
    _fh = logging.FileHandler(str(_REPOST_LOG), encoding="utf-8")
    _fh.setFormatter(logging.Formatter(
        "%(asctime)s [" + BOTDIR.name.upper() + "] %(levelname)s — %(message)s"
    ))
    _log.addHandler(_fh)


def log(msg: str, level: str = "info") -> None:
    print("[REPOST/" + BOTDIR.name + "] " + msg)
    getattr(_log, level, _log.info)(msg)


log("INIT: BOTDIR=" + str(BOTDIR))
log("INIT: config=" + str(_env_loaded_from or "(nenhum)"))
log("INIT: @" + (IGUSER or "?") +
    " | Story=" + str(REPOST_STORY_ENABLED) +
    " | Reels=" + str(REPOST_REELS_ENABLED))

# =============================================================================
# IA — GERAÇÃO DE LEGENDA
# =============================================================================

_ai_perplexity = None
_ai_openai     = None
_ai_client     = None

try:
    from openai import OpenAI as _OpenAI
    # Modelo Obrigatório de IA: OpenAI (gpt-4o-mini)
    _ai_perplexity = None
    if OPENAIKEY:
        _ai_openai = _OpenAI(api_key=OPENAIKEY)
    _ai_client = _ai_openai
except ImportError:
    log("INIT: openai nao instalado — modo NEW desabilitado.", "warning")


def gerar_legenda_repost(caption_original, formato: str) -> str:
    if not _ai_client:
        return ""
    ctx = caption_original[:300] if caption_original else "(sem legenda original)"
    prompt = REPOST_CAPTION_PROMPT.format(
        caption_original=ctx,
        formato=formato,
    )

    def _call(client, model: str) -> str:
        return client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=120,
        ).choices[0].message.content.strip()

    if _ai_openai:
        try:
            return _call(_ai_openai, "gpt-4o-mini")
        except Exception as e:
            log("IA: OpenAI (gpt-4o-mini) falhou (" + str(e) + ")", "error")
    return ""

# =============================================================================
# BANCO DE DADOS
# =============================================================================

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_REPOST_DB), timeout=30)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS reposts ("
        "    id           INTEGER PRIMARY KEY AUTOINCREMENT,"
        "    media_id     TEXT    NOT NULL,"
        "    formato      TEXT    NOT NULL,"
        "    modo         TEXT,"
        "    legenda      TEXT,"
        "    repostado_em TEXT    NOT NULL"
        ");"
        "CREATE INDEX IF NOT EXISTS idx_reposts_media ON reposts(media_id);"
        "CREATE INDEX IF NOT EXISTS idx_reposts_data  ON reposts(repostado_em);"
    )
    conn.commit()
    return conn


def _ja_repostado(conn: sqlite3.Connection, media_id: str) -> bool:
    row = conn.execute(
        "SELECT id FROM reposts WHERE media_id=? "
        "AND repostado_em > datetime('now', '-" + str(REPOST_NO_REPEAT_DAYS) + " days')",
        (str(media_id),),
    ).fetchone()
    return row is not None


def _registrar(conn: sqlite3.Connection, media_id: str,
               formato: str, modo: str, legenda: str) -> None:
    conn.execute(
        "INSERT INTO reposts (media_id, formato, modo, legenda, repostado_em) VALUES (?,?,?,?,?)",
        (str(media_id), formato, modo,
         legenda[:500] if legenda else "",
         datetime.now().isoformat()),
    )
    conn.commit()

# =============================================================================
# UTILITÁRIOS
# =============================================================================

def _dentro_do_horario() -> bool:
    h = datetime.now().hour
    if CUTOFFHOUR > RESUMEHOUR:
        # Ex: silêncio das 23h às 06h da manhã
        return not (h >= CUTOFFHOUR or h < RESUMEHOUR)
    else:
        # Ex: silêncio de 01h às 06h da manhã
        return not (CUTOFFHOUR <= h < RESUMEHOUR)


def _deve_descansar() -> bool:
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        if _REST_STATE.exists():
            data = json.loads(_REST_STATE.read_text(encoding="utf-8"))
            if data.get("date") == today:
                return bool(data.get("rest", False))
        rest = random.random() < REPOST_REST_PROB
        _REST_STATE.write_text(
            json.dumps({"date": today, "rest": rest}),
            encoding="utf-8",
        )
        return rest
    except Exception:
        return False


def _notificar_telegram(texto: str) -> None:
    if not TELEGRAMBOTTOKEN or not TELEGRAMCHATID:
        return
    try:
        resp = requests.post(
            "https://api.telegram.org/bot" + TELEGRAMBOTTOKEN + "/sendMessage",
            json={"chat_id": TELEGRAMCHATID, "text": texto[:4000]},
            timeout=10,
        )
        if not resp.ok:
            log("TELEGRAM: Falha (" + str(resp.status_code) + ")", "warning")
    except Exception as e:
        log("TELEGRAM: Erro: " + str(e), "error")


def _escolher_modo() -> str:
    r = random.random()
    if r < REPOST_MODE_PROB_SAME:
        return "same"
    if r < REPOST_MODE_PROB_SAME + REPOST_MODE_PROB_NEW:
        return "new"
    return "silent"


def _rm(path) -> None:
    if path and os.path.exists(str(path)):
        try:
            os.unlink(str(path))
        except Exception:
            pass

# =============================================================================
# DELAYS HUMANOS
# =============================================================================

def _delay_inicial() -> None:
    """Delay aleatório antes da 1ª ação — desacopla o horário exato do cron."""
    d = random.randint(SESSION_INITIAL_DELAY_MIN, SESSION_INITIAL_DELAY_MAX)
    if d > 0:
        log("SESSAO: Delay inicial de " + str(d) + "s antes de comecar...")
        time.sleep(d)


def _delay_entre_acoes(acao_num: int, total: int) -> None:
    """
    Escolhe entre delay curto e delay longo de forma ponderada.
    Distribuição não-uniforme: favorece delays curtos mas inclui pausas longas.
    """
    usar_longo = random.random() < SESSION_LONG_DELAY_PROB

    if usar_longo:
        d = random.randint(SESSION_LONG_DELAY_MIN, SESSION_LONG_DELAY_MAX)
        tipo = "longo"
    else:
        # Delay curto com leve variação gaussiana para não ser uniforme
        base = random.randint(SESSION_SHORT_DELAY_MIN, SESSION_SHORT_DELAY_MAX)
        jitter = int(random.gauss(0, base * 0.15))
        d = max(10, base + jitter)
        tipo = "curto"

    log(
        "SESSAO: Delay " + tipo + " de " + str(d) + "s "
        "(acao " + str(acao_num) + "/" + str(total) + ")..."
    )
    time.sleep(d)

# =============================================================================
# RESOLUÇÃO DO BINÁRIO DO FFMPEG
# =============================================================================

def _obter_caminho_ffmpeg() -> str:
    import shutil
    f = shutil.which("ffmpeg")
    if f:
        return f
    for c in [BOTDIR / "ffmpeg.exe", BOTDIR / "ffmpeg", BOTDIR / "core" / "ffmpeg"]:
        if c.is_file():
            return str(c)
    return "ffmpeg"

# =============================================================================
# DOWNLOAD E CONVERSÃO DE VÍDEO
# =============================================================================

def _download_video(video_url: str, suffix: str = "_orig.mp4"):
    try:
        fd, tmp = tempfile.mkstemp(suffix=suffix, dir=str(_TEMPDIR))
        os.close(fd)
        resp = requests.get(video_url, stream=True, timeout=90)
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(65536):
                f.write(chunk)
        if os.path.getsize(tmp) < 10_000:
            _rm(tmp)
            return None
        return tmp
    except Exception as e:
        log("DOWNLOAD: Falha: " + str(e), "error")
        return None


def _adaptar_para_story(video_url: str):
    """
    Adapta o vídeo para o formato Story 9:16 (1080x1920) com h264/aac e faststart.
    Retorna (caminho_adaptado, caminho_orig) para permitir fallback seguro.
    """
    src = _download_video(video_url, "_story_src.mp4")
    if not src:
        return None, None
    fd, dst = tempfile.mkstemp(suffix="_story.mp4", dir=str(_TEMPDIR))
    os.close(fd)
    try:
        cmd = [
            _obter_caminho_ffmpeg(), "-y", "-i", src,
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
            "setsar=1",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", "30",
            "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "44100",
            "-t", "60",
            "-map_metadata", "-1",
            dst,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 10_000:
            log("STORY: Conversao 9:16 concluida.")
            return dst, src
        log("STORY: FFmpeg avisou retorno != 0. Usando video original como fallback.", "warning")
        return None, src
    except Exception as e:
        log("STORY: Aviso na conversao FFmpeg (" + str(e) + "). Usando original.", "warning")
        return None, src


def _adaptar_para_reels(video_url: str):
    """Garante que o vídeo de Reels esteja estritamente no padrão 1080x1920 vertical com faststart."""
    src = _download_video(video_url, "_reels_src.mp4")
    if not src:
        return None, None
    fd, dst = tempfile.mkstemp(suffix="_reels.mp4", dir=str(_TEMPDIR))
    os.close(fd)
    try:
        cmd = [
            _obter_caminho_ffmpeg(), "-y", "-i", src,
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
            "setsar=1",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", "30",
            "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "44100",
            "-t", "90",
            "-map_metadata", "-1",
            dst,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 10_000:
            log("REELS: Adaptacao 1080x1920 (9:16) concluida com sucesso.")
            return dst, src
        log("REELS: FFmpeg avisou retorno != 0. Usando video original como fallback.", "warning")
        return None, src
    except Exception as e:
        log("REELS: Aviso na adaptacao FFmpeg (" + str(e) + "). Usando original.", "warning")
        return None, src

# =============================================================================
# SELEÇÃO INTELIGENTE DE MÍDIA VIA META GRAPH API
# =============================================================================

def _selecionar_midia(cl, conn: sqlite3.Connection, ja_usadas: set):
    """
    Seleciona uma mídia do perfil via Meta Graph API que:
    - Seja vídeo (media_type == 'VIDEO')
    - Não tenha sido repostada nos últimos N dias (banco)
    - Não tenha sido usada nesta mesma sessão (ja_usadas)
    """
    token = cl.get("token")
    account_id = cl.get("account_id")
    version = cl.get("version", META_GRAPH_VERSION)

    try:
        url = f"https://graph.facebook.com/{version}/{account_id}/media"
        params = {
            "fields": "id,caption,media_type,media_url,permalink,timestamp",
            "limit": REPOST_LOOKBACK_AMOUNT,
            "access_token": token,
        }
        res = requests.get(url, params=params, timeout=30)
        res_data = res.json()
        medias = res_data.get("data", [])
    except Exception as e:
        log("SELECAO: Erro ao buscar midias via Meta Graph API: " + str(e), "error")
        return None

    videos = [m for m in medias if m.get("media_type") in ("VIDEO", "REELS")]
    if not videos:
        log("SELECAO: Nenhum video encontrado no perfil via Meta Graph API.", "warning")
        return None

    # Prioridade Única: não usada na sessão atual E não repostada recentemente E não presente no histórico central
    disponiveis = []
    for m in videos:
        mid_str = str(m.get("id"))
        if mid_str in ja_usadas:
            continue
        if _ja_repostado(conn, mid_str):
            continue
        is_dup_id, _ = verificar_media_id_duplicado(mid_str, "instagram")
        if is_dup_id:
            continue
        disponiveis.append(m)

    # NUNCA permitir fallback para repostar os mesmos vídeos em loop
    if not disponiveis:
        log("SELECAO: [SKIP] Não há vídeos inéditos para repostar (todos já constam no histórico/banco). Bloqueando envio para impedir shadowban/duplicações.", "warning")
        return None

    escolhida = random.choice(disponiveis)
    log("SELECAO: Escolhida ID=" + str(escolhida.get("id")) +
        " (" + str(len(disponiveis)) + " candidatas).")

    # Mapeia propriedades para manter compatibilidade de interface
    return {
        "id": escolhida.get("id"),
        "media_id": escolhida.get("id"),
        "video_url": escolhida.get("media_url"),
        "caption_text": escolhida.get("caption", ""),
    }

# =============================================================================
# PUBLICAÇÃO EM STORY VIA META GRAPH API
# =============================================================================

def _postar_story(cl, media, legenda: str) -> bool:
    video_url = media.get("video_url") if isinstance(media, dict) else getattr(media, "video_url", None)
    if not video_url:
        log("STORY: Sem video_url.", "error")
        return False

    token = cl.get("token")
    account_id = cl.get("account_id")
    version = cl.get("version", "v26.0")
    base_url = f"https://graph.facebook.com/{version}/{account_id}"

    adapted_video, orig_video = _adaptar_para_story(str(video_url))
    candidatos_video = [f for f in (adapted_video, orig_video) if f and os.path.exists(f)]

    # Trava anti-duplicidade estrita
    for v_chk in candidatos_video:
        is_dup, motivo = verificar_duplicidade(v_chk, "instagram")
        if is_dup:
            log(f"STORY: [SKIP] Vídeo já publicado no Instagram ({motivo}). Bloqueando envio.", "warning")
            return False

    container_id = None

    try:
        # Tentativa 1: Resumable Upload Oficial com bytes locais (adaptado ou original)
        for v_path in candidatos_video:
            try:
                tipo_arquivo = "adaptado" if v_path == adapted_video else "original"
                log(f"STORY: Criando container de Story (Resumable Upload) via Meta Graph API [{tipo_arquivo}]...")
                res = requests.post(
                    f"{base_url}/media",
                    data={
                        "media_type": "STORIES",
                        "upload_type": "resumable",
                        "access_token": token,
                    },
                    timeout=30,
                )
                res_data = res.json()
                c_id = res_data.get("id")
                rupload_uri = res_data.get("uri")

                if c_id and rupload_uri:
                    log("STORY: Enviando bytes do video via Rupload para a Meta...")
                    with open(v_path, "rb") as f:
                        v_bytes = f.read()

                    headers = {
                        "Authorization": f"OAuth {token}",
                        "offset": "0",
                        "file_size": str(len(v_bytes)),
                        "Content-Type": "application/octet-stream",
                    }
                    up_res = requests.post(rupload_uri, headers=headers, data=v_bytes, timeout=300)
                    if up_res.status_code in (200, 201):
                        container_id = c_id
                        log(f"STORY: Bytes do Story enviados com SUCESSO via Rupload [{tipo_arquivo}].")
                        break
                    else:
                        log(f"STORY: Rupload HTTP {up_res.status_code} ({up_res.text[:120]}). Tentando fallback...", "warning")
            except Exception as up_err:
                log(f"STORY: Aviso no Rupload ({up_err}).", "warning")

        # Tentativa 2: Fallback via video_url direta (caso rupload falhe)
        if not container_id:
            log("STORY: Tentando container de Story via URL direta...")
            res = requests.post(
                f"{base_url}/media",
                data={
                    "media_type": "STORIES",
                    "video_url": str(video_url),
                    "access_token": token,
                },
                timeout=30,
            )
            res_data = res.json()
            container_id = res_data.get("id")

        if not container_id:
            log("STORY: Falha ao obter container_id na Meta Graph API.", "error")
            return False

        # Polling status do processamento
        published = False
        for i in range(30):
            time.sleep(5)
            st_res = requests.get(
                f"https://graph.facebook.com/{version}/{container_id}",
                params={"fields": "status_code,status", "access_token": token},
                timeout=15,
            )
            st_data = st_res.json()
            st_code = st_data.get("status_code")
            if st_code == "FINISHED":
                published = True
                break
            elif st_code in ("ERROR", "EXPIRED"):
                log("STORY: Container retornou erro na Meta: " + str(st_data.get("status")), "error")
                break

        if not published:
            log("STORY: Container nao ficou pronto a tempo.", "error")
            return False

        pub_res = requests.post(
            f"{base_url}/media_publish",
            data={"creation_id": container_id, "access_token": token},
            timeout=30,
        )
        pub_json = pub_res.json()
        if "id" in pub_json:
            log(f"STORY: Story publicado com SUCESSO via Meta Graph API! ID={pub_json['id']}")
            try:
                video_ref = candidatos_video[0] if candidatos_video else ""
                registrar_publicacao_sucesso(video_ref, "instagram", {
                    "media_id": pub_json["id"],
                    "tipo": "story",
                    "origem": "repost_engine"
                })
            except Exception:
                pass
            return True

        log("STORY: Falha ao publicar container: " + pub_res.text, "error")
        return False
    except Exception as e:
        log("STORY: Erro no upload Meta: " + str(e), "error")
        return False
    finally:
        if adapted_video:
            _rm(adapted_video)
        if orig_video:
            _rm(orig_video)

# =============================================================================
# PUBLICAÇÃO EM REELS VIA META GRAPH API
# =============================================================================

def _postar_reels(cl, media, legenda: str) -> bool:
    video_url = media.get("video_url") if isinstance(media, dict) else getattr(media, "video_url", None)
    if not video_url:
        log("REELS: Sem video_url.", "error")
        return False

    token = cl.get("token")
    account_id = cl.get("account_id")
    version = cl.get("version", META_GRAPH_VERSION)
    base_url = f"https://graph.facebook.com/{version}/{account_id}"

    # Adapta o vídeo para padrão obrigatório do Reels (1080x1920, 9:16, faststart)
    adapted_video, orig_video = _adaptar_para_reels(str(video_url))
    candidatos_video = [f for f in (adapted_video, orig_video) if f and os.path.exists(f)]

    # Trava anti-duplicidade estrita
    for v_chk in candidatos_video:
        is_dup, motivo = verificar_duplicidade(v_chk, "instagram")
        if is_dup:
            log(f"REELS: [SKIP] Vídeo já publicado no Instagram ({motivo}). Bloqueando envio.", "warning")
            return False

    container_id = None

    try:
        # Tentativa 1: Resumable Upload Oficial com bytes locais (adaptado ou original)
        for v_path in candidatos_video:
            try:
                tipo = "adaptado" if v_path == adapted_video else "original"
                log(f"REELS: Criando container de Reels (Resumable Upload) via Meta Graph API [{tipo}]...")
                res = requests.post(
                    f"{base_url}/media",
                    data={
                        "media_type": "REELS",
                        "upload_type": "resumable",
                        "caption": legenda if legenda else "",
                        "access_token": token,
                    },
                    timeout=30,
                )
                res_data = res.json()
                c_id = res_data.get("id")
                rupload_uri = res_data.get("uri")

                if c_id and rupload_uri:
                    log("REELS: Enviando bytes do video via Rupload para a Meta...")
                    with open(v_path, "rb") as f:
                        v_bytes = f.read()

                    headers = {
                        "Authorization": f"OAuth {token}",
                        "offset": "0",
                        "file_size": str(len(v_bytes)),
                        "Content-Type": "application/octet-stream",
                    }
                    up_res = requests.post(rupload_uri, headers=headers, data=v_bytes, timeout=300)
                    if up_res.status_code in (200, 201):
                        container_id = c_id
                        log(f"REELS: Bytes do Reels enviados com SUCESSO via Rupload [{tipo}].")
                        break
                    else:
                        log(f"REELS: Rupload HTTP {up_res.status_code} ({up_res.text[:120]}). Tentando fallback...", "warning")
            except Exception as up_err:
                log(f"REELS: Aviso no Rupload ({up_err}).", "warning")

        # Tentativa 2: Fallback via video_url direta
        if not container_id:
            log("REELS: Criando container de Reels via URL direta...")
            res = requests.post(
                f"{base_url}/media",
                data={
                    "media_type": "REELS",
                    "video_url": str(video_url),
                    "caption": legenda if legenda else "",
                    "access_token": token,
                },
                timeout=30,
            )
            res_data = res.json()
            container_id = res_data.get("id")

        if not container_id:
            log("REELS: Falha ao obter container_id na Meta Graph API.", "error")
            return False

        # Polling status
        published = False
        for _ in range(30):
            time.sleep(5)
            st_res = requests.get(
                f"https://graph.facebook.com/{version}/{container_id}",
                params={"fields": "status_code,status", "access_token": token},
                timeout=15,
            )
            st_data = st_res.json()
            st_code = st_data.get("status_code")
            if st_code == "FINISHED":
                published = True
                break
            elif st_code in ("ERROR", "EXPIRED"):
                log("REELS: Container retornou erro na Meta: " + str(st_data.get("status")), "error")
                break

        if not published:
            log("REELS: Container nao ficou pronto a tempo.", "error")
            return False

        pub_res = requests.post(
            f"{base_url}/media_publish",
            data={"creation_id": container_id, "access_token": token},
            timeout=30,
        )
        pub_data = pub_res.json()
        if "id" in pub_data:
            log(f"REELS: Reels publicado com SUCESSO via Meta Graph API! ID={pub_data['id']}")
            try:
                video_ref = candidatos_video[0] if candidatos_video else ""
                registrar_publicacao_sucesso(video_ref, "instagram", {
                    "media_id": pub_data["id"],
                    "tipo": "reels",
                    "origem": "repost_engine"
                })
            except Exception:
                pass
            return True

        log("REELS: Falha ao publicar container: " + pub_res.text, "error")
        return False
    except Exception as e:
        log("REELS: Erro no upload Meta: " + str(e), "error")
        return False
    finally:
        if adapted_video:
            _rm(adapted_video)
        if orig_video:
            _rm(orig_video)

# =============================================================================
# LOGIN / AUTENTICAÇÃO META GRAPH API
# =============================================================================

def _criar_cliente():
    if not META_ACCESS_TOKEN or not INSTAGRAM_ACCOUNT_ID:
        log("LOGIN: META_ACCESS_TOKEN e/ou INSTAGRAM_ACCOUNT_ID nao configurados.", "error")
        return None

    # Teste de conectividade com a API Oficial da Meta
    try:
        url = f"https://graph.facebook.com/{META_GRAPH_VERSION}/{INSTAGRAM_ACCOUNT_ID}"
        res = requests.get(url, params={"fields": "id,username,name", "access_token": META_ACCESS_TOKEN}, timeout=15)
        if res.status_code == 200:
            data = res.json()
            username = data.get("username", INSTAGRAM_ACCOUNT_ID)
            log("LOGIN: Autenticado com sucesso via Meta Graph API como @" + username)
            return {
                "token": META_ACCESS_TOKEN,
                "account_id": INSTAGRAM_ACCOUNT_ID,
                "version": META_GRAPH_VERSION,
                "username": username,
            }
        else:
            log("LOGIN: Erro de autenticacao Meta Graph API: " + res.text, "error")
            return None
    except Exception as e:
        log("LOGIN: Falha ao conectar na Meta Graph API: " + str(e), "error")
        return None

# =============================================================================
# AÇÃO UNITÁRIA (1 repost)
# =============================================================================

def _executar_acao(cl, conn: sqlite3.Connection, ja_usadas: set) -> dict:
    """
    Executa uma única ação de repost.
    Retorna dict com resultado e atualiza ja_usadas.
    """
    media = _selecionar_midia(cl, conn, ja_usadas)
    if not media:
        return {"status": "no_media"}

    media_id = str(media.get("id")) if isinstance(media, dict) else str(getattr(media, "id", ""))

    # Marca como usada nesta sessão imediatamente
    ja_usadas.add(media_id)

    modo = _escolher_modo()
    caption_original = media.get("caption_text", "") if isinstance(media, dict) else (getattr(media, "caption_text", None) or "")

    if modo == "same":
        legenda = caption_original
        log("MODO: same (" + str(len(legenda)) + " chars)")
    elif modo == "new":
        fmt = "Story" if REPOST_STORY_ENABLED else "Reels"
        legenda = gerar_legenda_repost(caption_original, fmt)
        log("MODO: new — IA gerou " + str(len(legenda)) + " chars")
    else:
        legenda = ""
        log("MODO: silent")

    resultados: dict = {}

    if REPOST_STORY_ENABLED:
        ok = _postar_story(cl, media, legenda)
        resultados["story"] = ok
        if ok:
            _registrar(conn, media_id, "story", modo, legenda)

    if REPOST_REELS_ENABLED:
        ok = _postar_reels(cl, media, legenda)
        resultados["reels"] = ok
        if ok:
            _registrar(conn, media_id, "reels", modo, legenda)

    return {
        "status":   "success" if any(resultados.values()) else "error",
        "media_id": media_id,
        "modo":     modo,
        "formatos": resultados,
    }

# =============================================================================
# SESSÃO HUMANIZADA — múltiplas ações com comportamento variável
# =============================================================================

def run_repost_session() -> dict:
    """
    Executa uma sessão completa de repost com comportamento humano simulado.
    Protegido contra concorrência via LockSimultaneo.
    """
    _ensure_dirs()
    try:
        with LockSimultaneo(str(BOTDIR / ".repost.lock")):
            return _executar_run_repost_session()
    except RuntimeError as e_lock:
        log(f"LOCK: Concorrência bloqueada ({e_lock})", "warning")
        return {"status": "locked", "motivo": str(e_lock)}


def _executar_run_repost_session() -> dict:
    if not _dentro_do_horario():
        log("CUTOFF: Fora do horario permitido.")
        return {"status": "cutoff"}

    if _deve_descansar():
        log("REST DAY: Engine em descanso hoje.")
        return {"status": "rest_day"}

    if not REPOST_STORY_ENABLED and not REPOST_REELS_ENABLED:
        log("CONFIG: Nenhum formato habilitado.")
        return {"status": "disabled"}

    # ── Delay inicial para desacoplar do segundo exato do cron ────────────
    _delay_inicial()

    # ── Verificação pós-delay (pode ter entrado no cutoff durante a espera)
    if not _dentro_do_horario():
        log("CUTOFF: Entrou no horario de silencio durante delay inicial.")
        return {"status": "cutoff"}

    # ── Sorteia quantidade de ações com peso (mais 1-2 que 3-4) ──────────
    # Distribuição triangular favorece valores menores
    n_acoes = int(random.triangular(
        SESSION_ACTIONS_MIN,
        SESSION_ACTIONS_MAX,
        SESSION_ACTIONS_MIN  # moda = mínimo → maioria das sessões tem poucas ações
    ))
    n_acoes = max(SESSION_ACTIONS_MIN, min(SESSION_ACTIONS_MAX, n_acoes))

    log("SESSAO: Planejando " + str(n_acoes) + " acao(oes) nesta sessao.")

    conn     = _get_conn()
    cl       = _criar_cliente()
    ja_usadas: set = set()

    if not cl:
        conn.close()
        return {"status": "error", "stage": "login"}

    resultados_sessao = []
    acoes_executadas  = 0
    acoes_puladas     = 0

    for i in range(1, n_acoes + 1):

        # Recheck de horário a cada iteração
        if not _dentro_do_horario():
            log("CUTOFF: Sessao interrompida na acao " + str(i) + " (horario).")
            break

        # Probabilidade de pular esta ação
        if random.random() < SESSION_SKIP_PROB:
            acoes_puladas += 1
            log("SKIP: Acao " + str(i) + "/" + str(n_acoes) + " pulada (comportamento humano).")
            # Mesmo pulando, pode haver um delay curto (humano "abriu e fechou o app")
            if random.random() < 0.5:
                d = random.randint(5, 20)
                time.sleep(d)
            continue

        log("ACAO " + str(i) + "/" + str(n_acoes) + ": iniciando...")
        resultado = _executar_acao(cl, conn, ja_usadas)
        resultados_sessao.append(resultado)
        acoes_executadas += 1

        # Probabilidade de encerrar a sessão antes do planejado
        if i < n_acoes and random.random() < SESSION_EARLY_STOP_PROB:
            log("EARLY STOP: Sessao encerrada apos acao " + str(i) + " (comportamento humano).")
            break

        # Delay entre ações (apenas se ainda há próxima)
        if i < n_acoes:
            _delay_entre_acoes(i, n_acoes)

    conn.close()

    sucessos = sum(1 for r in resultados_sessao if r.get("status") == "success")

    # ── Resumo via Telegram ───────────────────────────────────────────────
    _notificar_telegram(
        "Repost Engine — Sessao concluida\n"
        "Bot: " + BOTDIR.name + " | @" + IGUSER + "\n"
        "Acoes planejadas: " + str(n_acoes) + "\n"
        "Executadas: " + str(acoes_executadas) +
        " | Puladas: " + str(acoes_puladas) +
        " | Sucesso: " + str(sucessos)
    )

    return {
        "status":           "success" if sucessos > 0 else ("noop" if acoes_executadas == 0 else "error"),
        "bot":              BOTDIR.name,
        "acoes_planejadas": n_acoes,
        "acoes_executadas": acoes_executadas,
        "acoes_puladas":    acoes_puladas,
        "sucessos":         sucessos,
        "detalhes":         resultados_sessao,
    }


# Alias de compatibilidade com v2
run_repost_cycle = run_repost_session

# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    log("=" * 60)
    log("REPOST ENGINE v3 — bot: " + BOTDIR.name)
    log("=" * 60)
    try:
        resultado = run_repost_session()
        log("SESSAO FINALIZADA: " + str(resultado))
    except Exception as e:
        log("CRITICO: " + str(e), "error")
        raise
