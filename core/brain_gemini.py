import os
import json
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

def gerar_pauta_umlapso(tema: str) -> dict:
    """Gera o roteiro, metadados e prompts de imagem em formato JSON."""
    
    generation_config = {
        "temperature": 0.7,
        "response_mime_type": "application/json",
    }
    
    model = genai.GenerativeModel(
        model_name="gemini-2.5-pro",
        generation_config=generation_config
    )

    system_instruction = """
    Atue como um roteirista sênior de vídeos curtos de mistério e paradoxos.
    O retorno DEVE ser um objeto JSON exato com esta estrutura:
    {
      "titulo_youtube": "Título chamativo (máx 60 caracteres)",
      "descricao": "Descrição curta com 3 hashtags relevantes",
      "roteiro_tts": "Roteiro narrado em português. Frases curtas, sombrias e de impacto.",
      "prompts_video": [
        "Prompt em inglês, muito detalhado, estilo cinematic, dark, surrealism, cena 1",
        "Prompt em inglês, muito detalhado, estilo cinematic, dark, surrealism, cena 2",
        "Prompt em inglês, muito detalhado, estilo cinematic, dark, surrealism, cena 3"
      ]
    }
    """
    
    prompt = f"{system_instruction}\nCrie um roteiro de 30 segundos sobre: {tema}"
    
    print(f"[*] A contactar o Gemini para criar a pauta: {tema}...")
    response = model.generate_content(prompt)
    
    try:
        return json.loads(response.text)
    except json.JSONDecodeError as e:
        raise Exception(f"Falha ao decodificar JSON: {e}\nRetorno cru: {response.text}")