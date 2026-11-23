import os
import sys
import json
import time
import argparse
import subprocess

from core.brain_openai import gerar_pauta_umlapso, gerar_metadados_video, gerar_novo_tema_inedito
from core.voice_tts import gerar_audio_tts
from core.vision_generator import gerar_imagem_com_fallback
from core.editor_ffmpeg import montar_video_completo, obter_caminho_ffmpeg
from core.publisher import postar_instagram_reels, postar_youtube_shorts, postar_tiktok, obter_ultimo_video_valido
from core.lock import LockSimultaneo

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
        for ficheiro in os.listdir('temp'):
            caminho = os.path.join('temp', ficheiro)
            if os.path.isfile(caminho):
                try:
                    os.remove(caminho)
                except Exception:
                    pass

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

def rodar_pipeline(tema: str | None = None, pref_visual="veo") -> dict:
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
    print(f"=== INICIANDO PIPELINE UMLAPSO ===")
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

            # 6. Publicação Multi-Plataforma
            print("\n[*] A iniciar a distribuição nas redes sociais...")
            legenda_completa = f"{metadados_completos['titulo']}\n\n{metadados_completos['descricao']}\n\n{metadados_completos['hashtags']}"
            
            # 6.1 Publicação no Instagram Reels (API Oficial da Meta)
            res_ig = None
            try:
                res_ig = postar_instagram_reels(video_final_path, legenda_completa)
                resultado_execucao["instagram"] = res_ig
            except Exception as ei:
                print(f"[-] Falha no Instagram Reels: {ei}")

            # 6.2 Publicação no YouTube Shorts (OAuth2 Oficial)
            res_yt = None
            try:
                res_yt = postar_youtube_shorts(video_final_path, metadados_completos['titulo'], legenda_completa, metadados_completos['tags'])
                resultado_execucao["youtube"] = res_yt
            except Exception as ey:
                print(f"[-] Falha no YouTube: {ey}")
                
            # 6.3 Publicação no TikTok (Automação Playwright com Sessão Persistente)
            res_tk = None
            try:
                res_tk = postar_tiktok(video_final_path, legenda_completa)
                resultado_execucao["tiktok"] = res_tk
            except Exception as et:
                print(f"[-] Falha no TikTok: {et}")
            
            print("[+] Passo 6/6: Etapa de publicação concluída.")

            # 7. Notificação Telegram com Capa e Botões Diretos
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline Autônomo do Umlapso Bot")
    parser.add_argument("--publicar-ultimo-tiktok", action="store_true", help="Publica isoladamente o último vídeo gerado no TikTok sem produzir novo conteúdo")
    parser.add_argument("--tema", type=str, default=None, help="Define um tema customizado (se omitido, consome do estoque ou gera via IA)")
    parser.add_argument("--pref-visual", type=str, default="veo", choices=["veo", "flow", "leonardo"], help="Motor de geração de vídeo/imagem")
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
        rodar_pipeline(tema=args.tema, pref_visual=args.pref_visual)
