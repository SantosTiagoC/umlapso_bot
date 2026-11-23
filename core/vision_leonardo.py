import os
import time
import requests
from dotenv import load_dotenv

# Carrega as variáveis do ficheiro .env
load_dotenv()

def gerar_imagem_leonardo(prompt: str, output_path: str):
    """
    Envia o prompt para a API do Leonardo AI, aguarda a geração da imagem (9:16)
    e guarda o ficheiro no disco.
    """
    api_key = os.getenv("LEONARDO_API_KEY")
    if not api_key:
        raise ValueError("A chave LEONARDO_API_KEY não foi encontrada no .env")

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {api_key}"
    }

    # 1. Iniciar a geração
    url_generate = "https://cloud.leonardo.ai/api/rest/v1/generations"
    
    payload = {
        "prompt": prompt,
        "num_images": 1,
        "width": 768,
        "height": 1344, # Proporção 9:16 ideal para Reels/Shorts/TikTok
        "alchemy": True, # Ativa o motor de alta qualidade
        "presetStyle": "CINEMATIC" # Força o estilo sombrio/filme
    }

    print(f"[*] A enviar prompt para o Leonardo AI...")
    response = requests.post(url_generate, json=payload, headers=headers)
    
    if response.status_code != 200:
        raise Exception(f"Erro ao solicitar geração: {response.status_code} - {response.text}")

    # Extrai o ID da tarefa de geração
    geracao_id = response.json().get("sdGenerationJob", {}).get("generationId")
    
    # 2. Ciclo de Polling (Aguardar que a imagem seja processada)
    url_status = f"https://cloud.leonardo.ai/api/rest/v1/generations/{geracao_id}"
    
    print("[*] A aguardar a renderização nos servidores do Leonardo...")
    while True:
        time.sleep(4) # Aguarda 4 segundos entre cada verificação para não sobrecarregar a API
        res_status = requests.get(url_status, headers=headers)
        dados = res_status.json()
        
        status = dados.get("generations_by_pk", {}).get("status")
        
        if status == "COMPLETE":
            # Extrai o link direto da imagem gerada
            imagem_url = dados["generations_by_pk"]["generated_images"][0]["url"]
            break
        elif status == "FAILED":
            raise Exception("A geração da imagem falhou no servidor do Leonardo AI.")
            
    # 3. Descarregar e guardar o ficheiro
    print("[*] A descarregar o ficheiro gerado...")
    img_data = requests.get(imagem_url).content
    
    with open(output_path, 'wb') as handler:
        handler.write(img_data)
        
    print(f"[+] Imagem guardada com sucesso: {output_path}")

# --- Teste isolado do módulo ---
# Se quiser testar apenas este script antes de ligar tudo, remova os cardinais (#) abaixo:
# if __name__ == "__main__":
#     prompt_teste = "A dark and surreal empty street, giant clock melting in the sky, cinematic lighting, 8k"
#     gerar_imagem_leonardo(prompt_teste, "cena_teste.jpg")