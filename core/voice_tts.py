import os
import sys
import shutil
import asyncio
import subprocess
import edge_tts
from openai import OpenAI
from core.editor_ffmpeg import obter_caminho_ffmpeg

def masterizar_audio_estudio(caminho_in: str, caminho_out: str) -> bool:
    """
    Aplica cadeia de masterização broadcast de estúdio via FFmpeg:
    - Highpass 85Hz: Remove ruído subsônico / puff do microfone.
    - Equalizador 170Hz (+3.5dB): Presença encorpada de locutor profissional.
    - Equalizador 3500Hz (-3.0dB) e 6500Hz (-2.0dB): Elimina sibilância e timbre metálico do TTS.
    - Compressor: Nivelamento dinâmico profissional sem picos estridentes.
    - Loudnorm: Normalização EBU R128 (-16 LUFS), padrão estrito do algoritmo do TikTok e Reels.
    """
    filtro = (
        "highpass=f=85,"
        "equalizer=f=170:t=q:w=1.4:g=3.5,"
        "equalizer=f=3500:t=q:w=2.2:g=-3.0,"
        "equalizer=f=6500:t=q:w=2.0:g=-2.0,"
        "acompressor=threshold=-18dB:ratio=3:attack=10:release=120,"
        "loudnorm=I=-16:TP=-1.5:LRA=11"
    )
    cmd = [
        obter_caminho_ffmpeg(), "-y",
        "-i", caminho_in,
        "-af", filtro,
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        caminho_out
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and os.path.exists(caminho_out) and os.path.getsize(caminho_out) > 5000:
            print("  [+] Masterização broadcast concluída com sucesso (-16 LUFS / EQ Estúdio)!")
            return True
        else:
            print(f"  [!] Aviso na masterização FFmpeg ({res.returncode}): {res.stderr[:150]}")
            return False
    except Exception as e:
        print(f"  [!] Erro ao masterizar áudio via FFmpeg: {e}")
        return False

async def _gerar_audio_edge(texto: str, output_path: str):
    """Motor assíncrono para contactar a Microsoft Edge TTS."""
    voz = "pt-BR-AntonioNeural"
    comunicador = edge_tts.Communicate(texto, voz, rate="-10%", pitch="-5Hz")
    await comunicador.save(output_path)

def _gerar_audio_openai_fallback(texto: str, output_path: str):
    """Fallback profissional usando OpenAI TTS caso o Edge TTS falhe."""
    print("  [!] Fallback ativado: Gerando locução cinematográfica via OpenAI TTS (Voz: Onyx)...")
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.audio.speech.create(
        model="tts-1",
        voice="onyx",
        input=texto,
        speed=0.95
    )
    response.stream_to_file(output_path)

def gerar_audio_tts(texto: str, output_path: str):
    """
    Wrapper síncrono para geração de voz com tolerância a falhas
    e aplicação automática de cadeia de masterização broadcast.
    """
    print("[*] A renderizar áudio TTS (Voz: Antonio Neural)...")
    raw_path = f"{output_path}.raw.mp3"
    
    sucesso_geracao = False
    try:
        asyncio.run(_gerar_audio_edge(texto, raw_path))
        print(f"[+] Áudio bruto gerado via Edge TTS: {raw_path}")
        sucesso_geracao = True
    except Exception as e:
        print(f"[-] Edge TTS falhou ({e}). Alternando automaticamente para fallback da OpenAI...")
        try:
            _gerar_audio_openai_fallback(texto, raw_path)
            print(f"[+] Áudio bruto gerado via OpenAI Fallback: {raw_path}")
            sucesso_geracao = True
        except Exception as e_ai:
            print(f"[-] Falha crítica na geração de voz: {e_ai}")
            raise e_ai

    if sucesso_geracao and os.path.exists(raw_path):
        print("[*] Aplicando masterização broadcast de áudio (EQ + Compressor + Loudnorm -16 LUFS)...")
        masterizado = masterizar_audio_estudio(raw_path, output_path)
        if masterizado and os.path.exists(output_path):
            try:
                os.remove(raw_path)
            except Exception:
                pass
        else:
            # Em caso de falha no filtro FFmpeg, usa o arquivo bruto para não interromper o pipeline
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            os.rename(raw_path, output_path)
            print("  [*] Áudio bruto preservado como saída final (fallback de áudio).")
