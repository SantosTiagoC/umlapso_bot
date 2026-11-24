import os
import sys
import json
import time
import argparse
import subprocess
import shutil

from core.brain_openai import gerar_pauta_umlapso, gerar_metadados_video, gerar_novo_tema_inedito
from core.voice_tts import gerar_audio_tts
from core.vision_generator import gerar_imagem_com_fallback
from core.editor_ffmpeg import montar_video_completo, obter_caminho_ffmpeg
from core.publisher import postar_instagram_reels, postar_youtube_shorts, postar_tiktok, obter_ultimo_video_valido
from core.lock import LockSimultaneo

from core.roteiro_imagens import obter_tema, gerar_roteiro_cenas
from core.flow_client import FlowClient
from core.audio_imagens import gerar_audio_e_timeline
from core.renderizador_imagens import renderizar_video_imagens

def registrar_tema_historico(tema: str):
    """Registra o tema concluído em historico_temas.txt para evitar repetições."""
    if not tema:
        return
    caminho_hist = "historico_temas.txt"
    temas_existentes = set()
    if os.path.exists(caminho_hist):
        try:
            with open(caminho_hist, "r", encoding="utf-8") as f:
                temas_existentes = {linha.strip() for linha in f if linha.strip()}
        except Exception:
            pass
    if tema.strip() not in temas_existentes:
        try:
            with open(caminho_hist, "a", encoding="utf-8") as f:
                f.write(tema.strip() + "\n")
            print(f"[+] Tema gravado permanentemente em historico_temas.txt: {tema.strip()}")
        except Exception as e:
            print(f"[-] Aviso ao gravar historico_temas.txt: {e}")

def preparar_ambiente():
    for pasta in ['temp', 'output', 'logs']:
        os.makedirs(pasta, exist_ok=True)

def limpar_temporarios():
    print("[*] A limpar mídias temporárias...")
    if os.path.exists('temp'):
        for item in os.listdir('temp'):
            caminho = os.path.join('temp', item)
            try:
                if os.path.isfile(caminho):
                    os.remove(caminho)
                elif os.path.isdir(caminho) and not item.startswith("."):
                    shutil.rmtree(caminho, ignore_errors=True)
            except Exception:
                pass

def limpar_arquivos_expirados(horas: int = 24):
    """
    Remove arquivos de saída e temporários mais antigos que o tempo estipulado (padrão 24h).
    - Remove .mp4 e .json em output/ expirados.
    - Remove pastas e arquivos residuais em temp/ expirados.
    - Não toca em assets/, logs/, tokens, sessões ou registros de anti-duplicidade.
    - Loga quantos arquivos e pastas foram removidos.
    """
    limite_segundos = horas * 3600
    agora = time.time()
    arquivos_removidos = 0
    pastas_removidas = 0

    # 1. Limpeza em output/ (.mp4 e .json expirados)
    pasta_output = "output"
    if os.path.exists(pasta_output):
        for item in os.listdir(pasta_output):
            ext = os.path.splitext(item)[1].lower()
            if ext in [".mp4", ".json"]:
                caminho = os.path.join(pasta_output, item)
                try:
                    if os.path.isfile(caminho):
                        mtime = os.path.getmtime(caminho)
                        if (agora - mtime) > limite_segundos:
                            os.remove(caminho)
                            arquivos_removidos += 1
                except Exception as e:
                    print(f"[-] Erro ao remover {caminho}: {e}")

    # 2. Limpeza em temp/ (pastas e arquivos expirados)
    pasta_temp = "temp"
    if os.path.exists(pasta_temp):
        for item in os.listdir(pasta_temp):
            if item.startswith("."):
                continue
            caminho = os.path.join(pasta_temp, item)
            try:
                mtime = os.path.getmtime(caminho)
                if (agora - mtime) > limite_segundos:
                    if os.path.isfile(caminho):
                        os.remove(caminho)
                        arquivos_removidos += 1
                    elif os.path.isdir(caminho):
                        shutil.rmtree(caminho, ignore_errors=True)
                        pastas_removidas += 1
            except Exception as e:
                print(f"[-] Erro ao remover {caminho}: {e}")

    if arquivos_removidos > 0 or pastas_removidas > 0:
        print(f"[*] Limpeza concluída: {arquivos_removidos} arquivo(s) e {pastas_removidas} pasta(s) expirados (> {horas}h) removidos.")
    else:
        print(f"[*] Limpeza de expirados (> {horas}h): nenhum item antigo a remover.")

def extrair_cover_frame(video_path: str, output_image: str) -> bool:
    """Extrai o primeiro frame representativo do vídeo para cover e notificações."""
    try:
        ffmpeg_bin = obter_caminho_ffmpeg()
        cmd = [
            ffmpeg_bin, "-y", "-ss", "00:00:01",
            "-i", video_path, "-vframes", "1",
            "-q:v", "2", output_image
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=15)
        return res.returncode == 0 and os.path.exists(output_image)
    except Exception:
        return False

def rodar_pipeline_imagens(tema: str | None = None, sem_publicar: bool = False) -> dict:
    """
    Pipeline econômico de imagens para @um.lapso:
    - Consome estoque_temas.txt -> gera 5 a 6 cenas via GPT-4o-mini.
    - Gera imagens cinematográficas 9:16 no Google Flow (1 por cena).
    - Sintetiza áudio cena por cena via Edge-TTS masterizado (EQ + compressor + -16 LUFS).
    - Renderiza vídeo 1080x1920 a 30 FPS com tela 100% limpa (sem textos/karaoke).
    - Publica no Instagram, YouTube Shorts e TikTok (salvo se --sem-publicar).
    """
    preparar_ambiente()
    inicio_pipeline = time.time()
    timestamp = int(inicio_pipeline)
    dir_episodio = os.path.join("temp", f"ep_{timestamp}")
    dir_cenas = os.path.join(dir_episodio, "cenas_flow")
    os.makedirs(dir_cenas, exist_ok=True)

    tema_final = obter_tema(tema)

    video_final_path = f"output/umlapso_{timestamp}.mp4"
    metadados_path = f"output/umlapso_{timestamp}_meta.json"
    cover_path = f"temp/cover_{timestamp}.jpg"

    print("\n" + "=" * 65)
    print("=== INICIANDO PIPELINE @UM.LAPSO (MODO IMAGENS / TELA LIMPA) ===")
    print(f"=== TEMA: {tema_final} ===")
    print("=" * 65 + "\n")

    resultado_execucao = {
        "tema": tema_final,
        "video": video_final_path,
        "instagram": None,
        "youtube": None,
        "tiktok": None,
        "status": "FALHA"
    }

    try:
        with LockSimultaneo():
            # 1. Roteiro (8 a 10 cenas, 120-150 palavras, singular 'você')
            print("[+] Passo 1/5: Gerando roteiro cinematográfico em 8-10 cenas...")
            roteiro = gerar_roteiro_cenas(tema_final)

            # 2. Imagens no Google Flow em pasta isolada por episódio
            print("\n[+] Passo 2/5: Solicitando imagens 9:16 inéditas no Google Flow...")
            flow = FlowClient()
            cenas_com_imgs = flow.gerar_imagens_para_roteiro(roteiro["cenas"], dir_cenas)
            roteiro["cenas"] = cenas_com_imgs

            # 3. Locução e Timeline (sem Whisper, medição exata via ffprobe)
            print("\n[+] Passo 3/5: Sintetizando áudio cena por cena com Edge-TTS masterizado...")
            audio_final, timeline_path, timeline_data = gerar_audio_e_timeline(roteiro, dir_episodio)

            # 4. Renderização com tela 100% limpa (Playwright + Ken Burns + FFmpeg)
            print("\n[+] Passo 4/5: Renderizando vídeo cinematográfico 1080x1920 @ 30 FPS...")
            renderizar_video_imagens(timeline_data, audio_final, video_final_path, fps=30)
            extrair_cover_frame(video_final_path, cover_path)

            # 5. Metadados e SEO
            titulo = roteiro.get("titulo", tema_final)
            descricao = roteiro.get("descricao", "")
            legenda_seo = roteiro.get("legenda_seo", "#umlapso #misterio #curiosidades #universo")
            metadados_completos = {
                "titulo": titulo,
                "descricao": descricao,
                "hashtags": legenda_seo,
                "tags": ["umlapso", "misterio", "curiosidades", "universo", "paradoxos"],
                "ficheiro": video_final_path
            }
            with open(metadados_path, "w", encoding="utf-8") as f_meta:
                json.dump(metadados_completos, f_meta, indent=4, ensure_ascii=False)
            print(f"[+] Passo 5/5: Metadados gravados em {metadados_path}")

            # 6. Distribuição / Publicação Multi-Plataforma
            legenda_completa = f"{titulo}\n\n{descricao}\n\n{legenda_seo}".strip()
            if sem_publicar:
                print("\n[*] Flag --sem-publicar ativa. Pulando etapa de upload nas redes.")
                resultado_execucao["status"] = "SUCESSO"
                registrar_tema_historico(tema_final)
                return resultado_execucao

            print("\n[*] A iniciar a distribuição nas redes sociais...")
            res_ig = None
            try:
                res_ig = postar_instagram_reels(video_final_path, legenda_completa)
                resultado_execucao["instagram"] = res_ig
            except Exception as ei:
                print(f"[-] Falha no Instagram Reels: {ei}")

            res_yt = None
            try:
                res_yt = postar_youtube_shorts(video_final_path, titulo, legenda_completa, metadados_completos['tags'])
                resultado_execucao["youtube"] = res_yt
            except Exception as ey:
                print(f"[-] Falha no YouTube Shorts: {ey}")

            res_tk = None
            try:
                res_tk = postar_tiktok(video_final_path, legenda_completa)
                resultado_execucao["tiktok"] = res_tk
            except Exception as et:
                print(f"[-] Falha no TikTok: {et}")

            # 7. Notificação Telegram
            try:
                from core.telegram_notifier import notificar_fim_ciclo
                link_ig = (res_ig or {}).get("permalink")
                link_yt = (res_yt or {}).get("url")
                link_tk = "TikTok Studio" if (res_tk and res_tk.get("status") == "SUCCESS") else None
                notificar_fim_ciclo(
                    titulo=titulo,
                    tema=tema_final,
                    link_instagram=link_ig,
                    link_youtube=link_yt,
                    link_tiktok=link_tk,
                    tempo_segundos=time.time() - inicio_pipeline,
                    cover_frame=cover_path if os.path.exists(cover_path) else None,
                )
            except Exception as e_tg:
                print(f"[*] Aviso notificação Telegram: {e_tg}")

            resultado_execucao["status"] = "SUCESSO"
            registrar_tema_historico(tema_final)

            print(f"\n[!!!] OPERAÇÃO CONCLUÍDA COM SUCESSO [!!!]")
            print(f"Vídeo em: {video_final_path}")
            print(f"Metadados em: {metadados_path}\n")
            return resultado_execucao

    except RuntimeError as e_lock:
        print(f"[-] Concorrência bloqueada: {e_lock}")
        resultado_execucao["status"] = "BLOQUEADO"
        return resultado_execucao
    except Exception as e:
        print(f"\n[-] FALHA NO PIPELINE DE IMAGENS: {e}")
        resultado_execucao["status"] = f"ERRO: {e}"
        return resultado_execucao
    finally:
        limpar_temporarios()
        limpar_arquivos_expirados(horas=24)

def rodar_pipeline_veo(tema: str | None = None, pref_visual="veo", sem_publicar: bool = False) -> dict:
    """Pipeline legado usando Veo para geração de vídeos curtos."""
    preparar_ambiente()
    inicio_pipeline = time.time()
    timestamp = int(inicio_pipeline)

    if not tema:
        print("[*] Nenhum tema explícito informado. Buscando do estoque ou gerando via IA...")
        tema = gerar_novo_tema_inedito()

    audio_path = f"temp/audio_{timestamp}.mp3"
    img_paths = [f"temp/cena_{i}_{timestamp}.mp4" for i in range(1, 4)]
    cover_path = f"temp/cover_{timestamp}.jpg"
    video_final_path = f"output/umlapso_{timestamp}.mp4"
    metadados_path = f"output/umlapso_{timestamp}_meta.json"

    print(f"\n=========================================")
    print(f"=== INICIANDO PIPELINE UMLAPSO (MODO VEO) ===")
    print(f"=== TEMA: {tema} ===")
    print(f"=========================================\n")

    resultado_execucao = {
        "tema": tema,
        "video": video_final_path,
        "instagram": None,
        "youtube": None,
        "tiktok": None,
        "status": "FALHA"
    }

    try:
        with LockSimultaneo():
            # 1. Roteiro
            pauta = gerar_pauta_umlapso(tema)
            print("[+] Passo 1/6: Pauta gerada.")
            
            # 2. Voz
            gerar_audio_tts(pauta['roteiro_tts'], audio_path)
            print("[+] Passo 2/6: Locução gravada.")
            
            # 3. Vídeos (Google Flow / Veo 3.1)
            prompts = pauta['prompts_video']
            for i in range(3):
                print(f"\n[*] Renderizando Cena {i+1}/3...")
                gerar_imagem_com_fallback(prompts[i], img_paths[i], preferencia=pref_visual)
            print("\n[+] Passo 3/6: Elementos visuais prontos.")
                
            # 4. Montagem
            print("\n[*] A iniciar a renderização no FFmpeg...")
            montar_video_completo(img_paths[0], img_paths[1], img_paths[2], audio_path, video_final_path)
            print("[+] Passo 4/6: Empacotamento finalizado.")

            # Extração de cover para notificação
            extrair_cover_frame(video_final_path, cover_path)

            # 5. Metadados e SEO (OpenAI)
            print("\n[*] A gerar Metadados (SEO) para as redes sociais...")
            metadados_seo = gerar_metadados_video(pauta['roteiro_tts'])
            
            metadados_completos = {
                "titulo": metadados_seo.get("titulo", tema),
                "descricao": metadados_seo.get("descricao", ""),
                "hashtags": metadados_seo.get("hashtags", ""),
                "tags": metadados_seo.get("tags", []),
                "ficheiro": video_final_path
            }
            
            with open(metadados_path, 'w', encoding='utf-8') as f:
                json.dump(metadados_completos, f, indent=4, ensure_ascii=False)
            print("[+] Passo 5/6: Metadados gerados e guardados.")

            if sem_publicar:
                print("\n[*] Flag --sem-publicar ativa. Pulando etapa de upload.")
                resultado_execucao["status"] = "SUCESSO"
                registrar_tema_historico(tema)
                return resultado_execucao

            # 6. Publicação Multi-Plataforma
            print("\n[*] A iniciar a distribuição nas redes sociais...")
            legenda_completa = f"{metadados_completos['titulo']}\n\n{metadados_completos['descricao']}\n\n{metadados_completos['hashtags']}"
            
            res_ig = None
            try:
                res_ig = postar_instagram_reels(video_final_path, legenda_completa)
                resultado_execucao["instagram"] = res_ig
            except Exception as ei:
                print(f"[-] Falha no Instagram Reels: {ei}")

            res_yt = None
            try:
                res_yt = postar_youtube_shorts(video_final_path, metadados_completos['titulo'], legenda_completa, metadados_completos['tags'])
                resultado_execucao["youtube"] = res_yt
            except Exception as ey:
                print(f"[-] Falha no YouTube: {ey}")
                
            res_tk = None
            try:
                res_tk = postar_tiktok(video_final_path, legenda_completa)
                resultado_execucao["tiktok"] = res_tk
            except Exception as et:
                print(f"[-] Falha no TikTok: {et}")
            
            print("[+] Passo 6/6: Etapa de publicação concluída.")

            # 7. Notificação Telegram
            try:
                from core.telegram_notifier import notificar_fim_ciclo
                link_ig = (res_ig or {}).get("permalink")
                link_yt = (res_yt or {}).get("url")
                link_tk = "TikTok Studio" if (res_tk and res_tk.get("status") == "SUCCESS") else None
                notificar_fim_ciclo(
                    titulo=metadados_completos['titulo'],
                    tema=tema,
                    link_instagram=link_ig,
                    link_youtube=link_yt,
                    link_tiktok=link_tk,
                    tempo_segundos=time.time() - inicio_pipeline,
                    cover_frame=cover_path if os.path.exists(cover_path) else None,
                )
            except Exception as e_tg:
                print(f"[*] Aviso notificação Telegram: {e_tg}")

            resultado_execucao["status"] = "SUCESSO"
            registrar_tema_historico(tema)

            print(f"\n[!!!] OPERAÇÃO CONCLUÍDA COM SUCESSO [!!!]")
            print(f"Vídeo em: {video_final_path}")
            print(f"Metadados em: {metadados_path}\n")
            return resultado_execucao

    except RuntimeError as e_lock:
        print(f"[-] Concorrência bloqueada: {e_lock}")
        resultado_execucao["status"] = "BLOQUEADO"
        return resultado_execucao
    except Exception as e:
        print(f"\n[-] FALHA NO PIPELINE: {e}")
        resultado_execucao["status"] = f"ERRO: {e}"
        return resultado_execucao
    finally:
        limpar_temporarios()
        limpar_arquivos_expirados(horas=24)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline Autônomo do Umlapso Bot")
    parser.add_argument("--modo", type=str, default="imagens", choices=["imagens", "veo"], help="Modo de geração: 'imagens' (padrão, rápido e econômico) ou 'veo' (vídeo gerado)")
    parser.add_argument("--publicar-ultimo-tiktok", action="store_true", help="Publica isoladamente o último vídeo gerado no TikTok sem produzir novo conteúdo")
    parser.add_argument("--tema", type=str, default=None, help="Define um tema customizado (se omitido, consome do estoque ou gera via IA)")
    parser.add_argument("--pref-visual", type=str, default="veo", choices=["veo", "flow", "leonardo"], help="Motor de geração visual no modo veo")
    parser.add_argument("--sem-publicar", action="store_true", help="Gera o vídeo e metadados sem publicar nas redes sociais")
    parser.add_argument("--auto", action="store_true", help="Execução automática para Cron/Systemd")

    args = parser.parse_args()

    if args.publicar_ultimo_tiktok:
        print("\n" + "="*65)
        print("=== TESTE ISOLADO: PUBLICAR ÚLTIMO VÍDEO NO TIKTOK ===")
        print("="*65)
        try:
            video_path, meta = obter_ultimo_video_valido("output")
            titulo = meta.get("titulo", "Mistério e Paradoxos")
            hashtags = meta.get("hashtags", "#curiosidades #misterio #fatoscuriosos #paradoxo")
            descricao = meta.get("descricao", "")
            legenda = f"{titulo}\n\n{descricao}\n\n{hashtags}".strip()
            print(f"[*] Vídeo alvo: {video_path}")
            print(f"[*] Legenda preparada ({len(legenda)} caracteres): {legenda[:120]}...")
            
            res = postar_tiktok(video_path, legenda)
            print("\n" + "="*65)
            print(f"[+] Resultado da publicação TikTok: {res}")
            print("="*65)
            sucesso_tk = bool(res.get("sucesso") or res.get("status") in ("SUCCESS", "SKIPPED", "publicado"))
            sys.exit(0 if sucesso_tk else 1)
        except Exception as e_pub:
            print(f"\n[-] Erro ao publicar último vídeo no TikTok: {e_pub}")
            sys.exit(1)
    else:
        if args.modo == "imagens":
            rodar_pipeline_imagens(tema=args.tema, sem_publicar=args.sem_publicar)
        else:
            rodar_pipeline_veo(tema=args.tema, pref_visual=args.pref_visual, sem_publicar=args.sem_publicar)
