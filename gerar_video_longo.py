#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gerar_video_longo.py - Pipeline Independente de Vídeos Longos (16:9 Full HD) para @um.lapso no YouTube.
- Roteiro documental profundo de 3 a 5 minutos (22 a 32 cenas, 4 a 5 capítulos).
- 1 imagem 16:9 horizontal inédita por cena no Google Flow + Thumbnail 1280x720.
- Narração Edge-TTS masterizada (-16 LUFS) com pausas narrativas de capítulo.
- Renderização Full HD (1920x1080) com tela 100% limpa, Ken Burns e crossfade suave.
- Publicação automática na aba 'Vídeos' do YouTube com capítulos e capa personalizada.
- Lock inteligente aguardando liberação do robô de Shorts para não colidir com o Flow.
"""

import os
import sys
import time
import json
import shutil
import argparse
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

logger = logging.getLogger("gerar_video_longo")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from video_longo.roteiro_longo import obter_tema_longo, gerar_roteiro_longo
from video_longo.flow_16x9 import Flow16x9Client
from video_longo.audio_longo import gerar_audio_e_timeline_longo
from video_longo.renderizador_16x9 import renderizar_video_longo_16x9
from video_longo.youtube_longo_publisher import publicar_video_longo_youtube

class LockVideoLongo:
    """
    Gerencia a trava de execução exclusiva do vídeo longo (temp/video_longo.lock)
    e aguarda a liberação de travas ativas do bot de Shorts para não abrir duas
    sessões simultâneas do Google Flow.
    """
    def __init__(self, lock_filename: str = "temp/video_longo.lock", max_espera_segundos: int = 600):
        self.lock_path = os.path.join(ROOT_DIR, lock_filename)
        self.max_espera = max_espera_segundos
        self.acquired = False

    def _trava_shorts_ativa(self) -> bool:
        """Verifica se há processos do robô de Shorts ou Flow em execução."""
        from core.lock import processo_esta_ativo
        travas_shorts = [
            os.path.join(ROOT_DIR, ".umlapso.lock"),
            os.path.join(ROOT_DIR, "umlapso.lock"),
            os.path.join(ROOT_DIR, "flow_generation.lock")
        ]
        for t in travas_shorts:
            if os.path.exists(t):
                try:
                    idade = time.time() - os.path.getmtime(t)
                    if idade > 600:
                        try:
                            os.remove(t)
                        except Exception:
                            pass
                        continue
                    with open(t, "r") as f:
                        conteudo = f.read().strip()
                    if conteudo.isdigit() and processo_esta_ativo(int(conteudo)):
                        return True
                except Exception:
                    pass
        return False

    def __enter__(self):
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        inicio = time.time()

        # Aguarda liberação se o robô de Shorts estiver operando no momento
        while self._trava_shorts_ativa():
            tempo_esperado = int(time.time() - inicio)
            if tempo_esperado > self.max_espera:
                raise TimeoutError("Tempo limite esgotado aguardando liberação do robô de Shorts.")
            logger.info(f"[*] Robô de Shorts em execução. Aguardando liberação do Google Flow ({tempo_esperado}s)...")
            time.sleep(10)

        # Verifica se já existe outra instância do vídeo longo rodando
        if os.path.exists(self.lock_path):
            try:
                idade = time.time() - os.path.getmtime(self.lock_path)
                if idade > 1200:  # 20 minutos
                    os.remove(self.lock_path)
                else:
                    with open(self.lock_path, "r") as f:
                        pid = int(f.read().strip())
                    from core.lock import processo_esta_ativo
                    if processo_esta_ativo(pid):
                        raise RuntimeError(f"Pipeline de vídeo longo já em execução no PID {pid}.")
                    os.remove(self.lock_path)
            except RuntimeError:
                raise
            except Exception:
                pass

        with open(self.lock_path, "w") as f:
            f.write(str(os.getpid()))
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.acquired and os.path.exists(self.lock_path):
            try:
                os.remove(self.lock_path)
            except Exception:
                pass
        self.acquired = False

def preparar_pastas_longo() -> tuple[str, str]:
    """Cria pastas isoladas para o episódio longo atual."""
    timestamp = int(time.time())
    dir_episodio = os.path.join(ROOT_DIR, "temp", f"longo_{timestamp}")
    dir_cenas = os.path.join(dir_episodio, "cenas")
    os.makedirs(dir_cenas, exist_ok=True)
    os.makedirs(os.path.join(ROOT_DIR, "output", "longos"), exist_ok=True)
    os.makedirs(os.path.join(ROOT_DIR, "logs"), exist_ok=True)
    return dir_episodio, dir_cenas

def limpar_pasta_episodio(dir_episodio: str):
    """Remove a pasta temporária de cenas após a finalização do vídeo."""
    if os.path.exists(dir_episodio):
        try:
            shutil.rmtree(dir_episodio, ignore_errors=True)
        except Exception:
            pass

def rodar_pipeline_longo(tema_custom: Optional[str] = None, sem_publicar: bool = False) -> Dict[str, Any]:
    """
    Executa o pipeline completo de vídeo longo 16:9:
    1. Roteiro documental (4-5 capítulos, 22-30 cenas, ~450-700 palavras).
    2. Imagens horizontais 16:9 + Thumbnail oficial 1280x720 no Google Flow.
    3. Locução Edge-TTS masterizada (-16 LUFS) com timestamps de capítulos do YouTube.
    4. Renderização Full HD (1920x1080) com Ken Burns lento e crossfade suave.
    5. Upload para o YouTube (Aba Vídeos) com capítulos e capa customizada.
    """
    inicio_total = time.time()
    timestamp = int(inicio_total)
    dir_episodio, dir_cenas = preparar_pastas_longo()

    video_final_path = os.path.join(ROOT_DIR, "output", "longos", f"umlapso_longo_{timestamp}.mp4")
    metadados_path = os.path.join(ROOT_DIR, "output", "longos", f"umlapso_longo_{timestamp}_meta.json")

    resultado = {
        "tema": None,
        "video": video_final_path,
        "youtube": None,
        "status": "FALHA"
    }

    try:
        with LockVideoLongo():
            tema = obter_tema_longo(tema_custom)
            resultado["tema"] = tema

            print("\n" + "=" * 68)
            print("=== INICIANDO PIPELINE DE VÍDEO LONGO 16:9 (DOCUMENTÁRIO YOUTUBE) ===")
            print(f"=== TEMA: {tema} ===")
            print("=" * 68 + "\n")

            # 1. Roteiro Documental
            logger.info("[Passo 1/5] Produzindo roteiro documental longo (3 a 5 min)...")
            roteiro = gerar_roteiro_longo(tema)

            # 2. Imagens 16:9 no Google Flow + Thumbnail Oficial
            logger.info("\n[Passo 2/5] Gerando ilustrações widescreen 16:9 e Thumbnail 1280x720 no Google Flow...")
            flow_client = Flow16x9Client()
            cenas_com_imgs, thumb_path = flow_client.gerar_imagens_para_roteiro_longo(
                roteiro["cenas_planas"],
                dir_cenas,
                prompt_thumbnail=roteiro.get("prompt_thumbnail_en")
            )
            roteiro["cenas_planas"] = cenas_com_imgs

            # 3. Narração Masterizada + Capítulos Automáticos
            logger.info("\n[Passo 3/5] Sintetizando narração cinematográfica e gerando timestamps de capítulos...")
            audio_final, timeline_path, timeline_data = gerar_audio_e_timeline_longo(roteiro, dir_episodio)

            # 4. Renderização Full HD (1920x1080)
            logger.info("\n[Passo 4/5] Renderizando vídeo 1920x1080 Full HD com Ken Burns lento e crossfade...")
            renderizar_video_longo_16x9(timeline_data, audio_final, video_final_path, fps=30)

            # Salva metadados completos
            metadados_completos = {
                "titulo": roteiro.get("titulo", tema),
                "descricao_base": roteiro.get("descricao_base", ""),
                "texto_capitulos_yt": timeline_data.get("texto_capitulos_yt", ""),
                "tags": roteiro.get("tags", []),
                "duracao_total_ms": timeline_data.get("duracao_total_ms", 0),
                "duracao_minutos": round(timeline_data.get("duracao_total_ms", 0) / 60000.0, 2),
                "thumbnail": thumb_path,
                "video_path": video_final_path
            }
            with open(metadados_path, "w", encoding="utf-8") as f_meta:
                json.dump(metadados_completos, f_meta, indent=4, ensure_ascii=False)
            logger.info(f"[+] Metadados salvos em: {metadados_path}")

            # 5. Publicação no YouTube (Aba Vídeos)
            if sem_publicar:
                logger.info("\n[*] Flag --sem-publicar ativa. Pulando envio para o YouTube.")
                resultado["status"] = "SUCESSO"
                return resultado

            logger.info("\n[Passo 5/5] Enviando documentário para o canal @um.lapso no YouTube...")
            res_yt = publicar_video_longo_youtube(
                video_path=video_final_path,
                titulo=metadados_completos["titulo"],
                descricao_base=metadados_completos["descricao_base"],
                texto_capitulos=metadados_completos["texto_capitulos_yt"],
                tags=metadados_completos["tags"],
                thumbnail_path=thumb_path
            )
            resultado["youtube"] = res_yt
            resultado["status"] = "SUCESSO"

            tempo_total = time.time() - inicio_total
            print("\n" + "=" * 68)
            print("=== [✔] DOCUMENTÁRIO LONGO 16:9 CONCLUÍDO COM SUCESSO! ===")
            print(f"  • Vídeo     : {video_final_path}")
            print(f"  • Thumbnail : {thumb_path}")
            print(f"  • Link YT   : {(res_yt or {}).get('url', 'N/A')}")
            print(f"  • Tempo     : {tempo_total/60.0:.1f} minutos")
            print("=" * 68 + "\n")

            return resultado

    except Exception as e:
        logger.error(f"\n[-] FALHA NO PIPELINE DE VÍDEO LONGO: {e}", exc_info=True)
        resultado["status"] = f"ERRO: {e}"
        return resultado
    finally:
        if resultado.get("status") == "SUCESSO":
            limpar_pasta_episodio(dir_episodio)
        else:
            logger.warning(f"[*] Pasta temporária do episódio preservada para recuperação/diagnóstico: {dir_episodio}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de Vídeos Longos Horizontais 16:9 para @um.lapso no YouTube")
    parser.add_argument("--tema", type=str, default=None, help="Define um tema customizado (se omitido, consome do estoque sem alterar o arquivo)")
    parser.add_argument("--sem-publicar", action="store_true", help="Gera o vídeo Full HD e thumbnail sem postar no YouTube")
    parser.add_argument("--auto", action="store_true", help="Execução automática para Systemd Timer / Cron")

    args = parser.parse_args()
    rodar_pipeline_longo(tema_custom=args.tema, sem_publicar=args.sem_publicar)
