import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

def gerar_pauta_umlapso(tema: str) -> dict:
    """Gera o roteiro, metadados e prompts de imagem em formato JSON via GPT-4o-mini."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
    
    print(f"[*] A contactar a OpenAI (gpt-4o-mini) para criar a pauta: {tema}...")
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Crie um roteiro de 30 segundos sobre: {tema}"}
        ],
        temperature=0.7
    )
    
    try:
        texto_json = response.choices[0].message.content
        return json.loads(texto_json)
    except json.JSONDecodeError as e:
        raise Exception(f"Falha ao decodificar JSON da OpenAI: {e}\nRetorno cru: {texto_json}")

def gerar_metadados_video(pauta: str) -> dict:
    """Gera Título, Descrição, Hashtags e Tags focados em conversão para Shorts/Reels/TikTok."""
    print("[*] A gerar metadados (SEO) para as redes sociais...")
    
    cliente = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    prompt = f"""
    Aja como um especialista em SEO para TikTok, Instagram Reels e YouTube Shorts.
    Baseado no roteiro abaixo, crie metadados virais.
    
    ROTEIRO:
    {pauta}
    
    Retorne APENAS um objeto JSON válido com a seguinte estrutura:
    {{
        "titulo": "Título chamativo (max 60 caracteres)",
        "descricao": "Descrição curta e engajadora com chamada para ação",
        "hashtags": "#tag1 #tag2 #tag3",
        "tags": ["tag1", "tag2", "tag3"]
    }}
    """
    
    resposta = cliente.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    
    try:
        dados = json.loads(resposta.choices[0].message.content)
        print(f"  [+] Título gerado: {dados.get('titulo')}")
        return dados
    except json.JSONDecodeError as e:
        raise Exception(f"Falha ao decodificar metadados da OpenAI: {e}")

def gerar_novo_tema_inedito() -> str:
    """
    Obtém o próximo tema do arquivo 'estoque_temas.txt' (fila de temas).
    Se o estoque estiver vazio, gera um tema inédito dinamicamente via OpenAI (gpt-4o-mini).
    Registra sempre o tema selecionado em 'historico_temas.txt'.
    """
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    caminho_estoque = os.path.join(root_dir, "estoque_temas.txt")
    caminho_historico = os.path.join(root_dir, "historico_temas.txt")
    
    temas_historico = set()
    if os.path.exists(caminho_historico):
        with open(caminho_historico, "r", encoding="utf-8") as f:
            temas_historico = {linha.strip() for linha in f if linha.strip()}

    # 1. Tenta pegar do estoque
    if os.path.exists(caminho_estoque):
        with open(caminho_estoque, "r", encoding="utf-8") as f:
            estoque = [linha.strip() for linha in f if linha.strip()]
            
        while estoque:
            candidato = estoque.pop(0)
            if candidato and candidato not in temas_historico:
                # Atualiza o arquivo de estoque com os temas restantes
                with open(caminho_estoque, "w", encoding="utf-8") as f:
                    for t in estoque:
                        f.write(t + "\n")
                        
                # Registra no histórico
                with open(caminho_historico, "a", encoding="utf-8") as f:
                    f.write(candidato + "\n")
                    
                print(f"[+] Tema consumido do estoque ({len(estoque)} restantes): {candidato}")
                return candidato

    # 2. Se o estoque acabou, gera um novo tema via IA
    print(f"[*] Estoque vazio. Gerando novo tema inédito via OpenAI...")
    cliente = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    prompt = f"""
    Atue como diretor de criação do canal 'Umlapso' (Mundo Improvável), focado em YouTube Shorts, Reels e TikTok.
    Seu objetivo é sugerir 1 novo tema no formato hipotético, bizarro, misterioso, cósmico ou apocalíptico ("E se...?").
    
    Regras:
    1. Tema extremamente curioso, instigante e visualmente épico para renderização em vídeo realista.
    2. NUNCA repita ou crie algo muito parecido com os temas já abordados anteriormente:
    {json.dumps(list(temas_historico)[:30], ensure_ascii=False, indent=2)}
    
    3. Retorne APENAS um objeto JSON no formato:
    {{
        "tema": "E se... (frase curta e de impacto)"
    }}
    """
    
    resposta = cliente.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.9
    )
    
    dados = json.loads(resposta.choices[0].message.content)
    novo_tema = dados.get("tema", "").strip()
    if not novo_tema:
        novo_tema = "E se a gravidade da Terra diminuísse pela metade?"
        
    with open(caminho_historico, "a", encoding="utf-8") as f:
        f.write(novo_tema + "\n")
        
    print(f"[+] Novo tema gerado dinamicamente: {novo_tema}")
    return novo_tema