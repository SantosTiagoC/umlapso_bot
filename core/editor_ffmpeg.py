import os
import shutil
import subprocess

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def obter_caminho_ffmpeg() -> str:
    """Retorna o caminho do ffmpeg no sistema (Linux /usr/bin/ffmpeg ou Windows)."""
    # 1. Verifica se ffmpeg está instalado no PATH do sistema (padrão absoluto no Ubuntu)
    caminho_sistema = shutil.which("ffmpeg")
    if caminho_sistema:
        return caminho_sistema
        
    # 2. Fallback para executável local na raiz (Linux 'ffmpeg' ou Windows 'ffmpeg.exe')
    for nome in ["ffmpeg", "ffmpeg.exe"]:
        caminho_local = os.path.join(ROOT_DIR, nome)
        if os.path.isfile(caminho_local):
            return caminho_local
            
    return "ffmpeg"

def eh_arquivo_video(caminho: str) -> bool:
    """
    Detecta com precisão se o arquivo fornecido é um vídeo real (>1s de duração) ou imagem.
    Impede que imagens salvas com extensão .mp4 sejam tratadas erroneamente como vídeo pelo FFmpeg.
    """
    if not os.path.isfile(caminho) or os.path.getsize(caminho) < 10000:
        return False
        
    # 1. Verifica assinaturas de imagens estáticas conhecidas
    try:
        with open(caminho, 'rb') as f:
            header = f.read(32)
            if header.startswith(b'\x89PNG') or header.startswith(b'\xff\xd8\xff') or b'WEBP' in header:
                return False
    except Exception:
        pass

    # 2. Tenta ffprobe para verificar se tem stream de vídeo e duração > 1s
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin:
        try:
            cmd = [
                ffprobe_bin, '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=duration,nb_frames',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                caminho
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            linhas = [l.strip() for l in res.stdout.splitlines() if l.strip()]
            for l in linhas:
                try:
                    val = float(l)
                    if val > 1.0:
                        return True
                except ValueError:
                    pass
        except Exception:
            pass

    # 3. Fallback via OpenCV se disponível
    try:
        import cv2
        cap = cv2.VideoCapture(caminho)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()
        if fps > 0 and frames > (fps * 0.9):
            return True
        return False
    except Exception:
        pass

    # 4. Fallback por extensão e tamanho (vídeo do Veo/Flow tem > 400KB)
    ext = os.path.splitext(caminho)[1].lower()
    if ext in ['.mp4', '.mov', '.webm', '.mkv'] and os.path.getsize(caminho) > 400000:
        return True

    return False

def garantir_midias(midia1: str, midia2: str, midia3: str):
    """Garante que todas as 3 mídias existam, usando duplicação caso alguma falhe no download."""
    midias = [midia1, midia2, midia3]
    existentes = [m for m in midias if os.path.isfile(m) and os.path.getsize(m) > 50000]
    
    if not existentes:
        raise FileNotFoundError("Nenhum arquivo de mídia válido (>50KB) foi gerado para a montagem final.")
        
    for i, m in enumerate(midias):
        if not os.path.isfile(m) or os.path.getsize(m) <= 50000:
            fonte = existentes[i % len(existentes)]
            shutil.copyfile(fonte, m)
            print(f"[*] Fallback: {m} restaurado a partir de {fonte}")

def montar_video_completo(midia1: str, midia2: str, midia3: str, audio_tts: str, output_file: str):
    ffmpeg_bin = obter_caminho_ffmpeg()
    print(f"[*] A iniciar a renderização no FFmpeg ({ffmpeg_bin})...")

    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    
    # Assegura que todas as mídias existem
    garantir_midias(midia1, midia2, midia3)
    
    # Verifica se as mídias são vídeos (ex: saídas do Veo) ou imagens estáticas
    sao_videos = eh_arquivo_video(midia1) or eh_arquivo_video(midia2) or eh_arquivo_video(midia3)

    if sao_videos:
        print("[*] Mídias detectadas como vídeos (Veo 3.1). Ajustando formato vertical 1080x1920 (9:16)...")
        filtro_complexo = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setpts=PTS-STARTPTS[v0];"
            "[1:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setpts=PTS-STARTPTS[v1];"
            "[2:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setpts=PTS-STARTPTS[v2];"
            "[v0][v1][v2]concat=n=3:v=1:a=0[outv]"
        )
        comando = [
            ffmpeg_bin,
            '-y',
            '-i', midia1,
            '-i', midia2,
            '-i', midia3,
            '-i', audio_tts,
            '-filter_complex', filtro_complexo,
            '-map', '[outv]',
            '-map', '3:a',
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-movflags', '+faststart',
            '-shortest',
            output_file
        ]
    else:
        print("[*] Mídias detectadas como imagens estáticas. Aplicando efeito Ken Burns + concatenação...")
        filtro_complexo = (
            "[0:v]format=yuv420p,scale=1080x1920,zoompan=z='zoom+0.001':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=300:s=1080x1920:fps=30,"
            "fade=t=in:st=0:d=1,fade=t=out:st=9:d=1[v0];"
            "[1:v]format=yuv420p,scale=1080x1920,zoompan=z='zoom+0.001':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=300:s=1080x1920:fps=30,"
            "fade=t=in:st=0:d=1,fade=t=out:st=9:d=1[v1];"
            "[2:v]format=yuv420p,scale=1080x1920,zoompan=z='zoom+0.001':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=300:s=1080x1920:fps=30,"
            "fade=t=in:st=0:d=1,fade=t=out:st=9:d=1[v2];"
            "[v0][v1][v2]concat=n=3:v=1:a=0[outv]"
        )
        comando = [
            ffmpeg_bin,
            '-y',
            '-loop', '1', '-i', midia1,
            '-loop', '1', '-i', midia2,
            '-loop', '1', '-i', midia3,
            '-i', audio_tts,
            '-filter_complex', filtro_complexo,
            '-map', '[outv]',
            '-map', '3:a',
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-movflags', '+faststart',
            '-shortest',
            output_file
        ]

    try:
        subprocess.run(comando, check=True, capture_output=True, text=True)
        print(f"[+] Renderização concluída: {output_file}")
    except subprocess.CalledProcessError as e:
        print("[-] ERRO FATAL NO FFMPEG:")
        print(e.stderr)
        raise

    # Validação pós-renderização: impede que vídeos vazios ou corrompidos sigam adiante
    if not os.path.isfile(output_file):
        raise FileNotFoundError(f"FFmpeg finalizou mas o arquivo de saída não foi gerado em: {output_file}")
        
    tamanho_final = os.path.getsize(output_file)
    if tamanho_final < 500_000:
        raise ValueError(f"O vídeo final gerado pelo FFmpeg é inválido ou truncado ({tamanho_final} bytes). Esperado > 500KB.")
        
    print(f"  [+] Vídeo final validado com sucesso! Tamanho: {tamanho_final} bytes")