import os
import sys
import json
import time
import random
import argparse
from datetime import datetime
import pytz

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.lock import LockSimultaneo

ESTADO_AGENDAMENTO_FILE = os.path.join(ROOT_DIR, "agendamento_estado.json")

# Configurações Estratégicas de Publicação (fora da janela 13h–17h)
JANELA_INICIO_HORA = 7    # 07h00
JANELA_FIM_HORA = 23      # 23h00

# Faixas de Pico de Engajamento fora da janela 13h–17h:
# Slot 1: Manhã (08h00 às 09h15)
SLOT_1_MIN_MINUTOS = 8 * 60          # 08:00
SLOT_1_MAX_MINUTOS = 9 * 60 + 15     # 09:15

# Slot 2: Almoço (12h00 às 12h45) — antes das 13h
SLOT_2_MIN_MINUTOS = 12 * 60         # 12:00
SLOT_2_MAX_MINUTOS = 12 * 60 + 45    # 12:45

# Slot 3: Noite / Prime Time (19h00 às 20h30)
SLOT_3_MIN_MINUTOS = 19 * 60         # 19:00
SLOT_3_MAX_MINUTOS = 20 * 60 + 30    # 20:30

def obter_timezone() -> pytz.timezone:
    tz_str = os.getenv("TIMEZONE", "America/Sao_Paulo")
    try:
        return pytz.timezone(tz_str)
    except Exception:
        return pytz.timezone("America/Sao_Paulo")

def minutos_para_horario_str(minutos_do_dia: int) -> str:
    h = minutos_do_dia // 60
    m = minutos_do_dia % 60
    return f"{h:02d}:{m:02d}"

def horario_str_para_minutos(horario_str: str) -> int:
    h, m = map(int, horario_str.split(":"))
    return h * 60 + m

def sortear_horarios_do_dia() -> list:
    """
    Sorteia 3 horários estratégicos no dia com variação inteligente (jitter),
    fora da janela 13h-17h:
    1. Manhã (~08:30)
    2. Almoço (~12:30)
    3. Noite (~19:30)
    """
    m1 = random.randint(SLOT_1_MIN_MINUTOS, SLOT_1_MAX_MINUTOS)
    m2 = random.randint(SLOT_2_MIN_MINUTOS, SLOT_2_MAX_MINUTOS)
    m3 = random.randint(SLOT_3_MIN_MINUTOS, SLOT_3_MAX_MINUTOS)
    
    return [
        {
            "id": 1,
            "nome": "Slot Manhã",
            "horario_planejado": minutos_para_horario_str(m1),
            "status": "pendente",
            "executado_as": None,
            "detalhes": None
        },
        {
            "id": 2,
            "nome": "Slot Almoço",
            "horario_planejado": minutos_para_horario_str(m2),
            "status": "pendente",
            "executado_as": None,
            "detalhes": None
        },
        {
            "id": 3,
            "nome": "Slot Noite/PrimeTime",
            "horario_planejado": minutos_para_horario_str(m3),
            "status": "pendente",
            "executado_as": None,
            "detalhes": None
        }
    ]

def carregar_ou_inicializar_estado(tz) -> dict:
    agora = datetime.now(tz)
    data_hoje_str = agora.strftime("%Y-%m-%d")

    estado = None
    if os.path.exists(ESTADO_AGENDAMENTO_FILE):
        try:
            with open(ESTADO_AGENDAMENTO_FILE, "r", encoding="utf-8") as f:
                estado = json.load(f)
        except Exception:
            estado = None

    if not estado or estado.get("data") != data_hoje_str:
        estado = {
            "data": data_hoje_str,
            "timezone": str(tz),
            "slots": sortear_horarios_do_dia()
        }
        salvar_estado(estado)
        print(f"[*] Nova grade de publicação gerada para {data_hoje_str}:")
        for s in estado["slots"]:
            print(f"    - {s['nome']}: {s['horario_planejado']}")

    return estado

def salvar_estado(estado: dict):
    with open(ESTADO_AGENDAMENTO_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, indent=4, ensure_ascii=False)

def verificar_e_executar_se_necessario(forcar: bool = False):
    """
    Verifica se o momento atual atende aos critérios para publicação.
    Protegido contra execução simultânea por LockSimultaneo.
    """
    lock_file = os.path.join(ROOT_DIR, ".scheduler.lock")
    try:
        with LockSimultaneo(lock_file):
            _executar_verificacao(forcar=forcar)
    except RuntimeError as e_lock:
        print(f"[*] Scheduler bloqueado por concorrência: {e_lock}")

def _executar_verificacao(forcar: bool = False):
    tz = obter_timezone()
    agora = datetime.now(tz)
    hora_atual = agora.hour
    minuto_atual = agora.minute
    minutos_hoje = hora_atual * 60 + minuto_atual

    # Validação estrita da janela 07h00 - 23h00
    if not forcar and (hora_atual < JANELA_INICIO_HORA or hora_atual >= JANELA_FIM_HORA):
        print(f"[*] Fora da janela permitida (07h00 - 23h00). Hora atual: {agora.strftime('%H:%M')}. Nenhuma ação executada.")
        return

    # Bloqueio explícito da janela 13h00 às 16h59 (baixa entrega)
    if not forcar and (13 <= hora_atual < 17):
        print(f"[*] Janela de baixa entrega (13h00–17h00). Hora atual: {agora.strftime('%H:%M')}. Pulando disparo.")
        return

    estado = carregar_ou_inicializar_estado(tz)
    from main import rodar_pipeline

    algo_executado = False
    for slot in estado["slots"]:
        if slot["status"] == "pendente":
            minutos_slot = horario_str_para_minutos(slot["horario_planejado"])
            
            if forcar or minutos_hoje >= minutos_slot:
                print(f"\n=======================================================")
                print(f"[!] HORA DE PUBLICAR: {slot['nome']} ({slot['horario_planejado']})")
                print(f"[!] Horário Real do Servidor: {agora.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"=======================================================\n")
                
                try:
                    resultado = rodar_pipeline(pref_visual="veo")
                    slot["status"] = "concluido"
                    slot["executado_as"] = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
                    slot["detalhes"] = resultado
                    salvar_estado(estado)
                    algo_executado = True
                    print(f"[+] {slot['nome']} concluído com sucesso!")
                    break
                except Exception as e:
                    print(f"[-] Erro ao executar {slot['nome']}: {e}")
                    break

    if not algo_executado:
        pendentes = [s for s in estado["slots"] if s["status"] == "pendente"]
        if pendentes:
            proximo = pendentes[0]
            print(f"[*] Nenhuma publicação pendente para agora. Próximo slot: {proximo['nome']} às {proximo['horario_planejado']}.")
        else:
            print("[*] Todas as publicações diárias já foram concluídas para o dia de hoje!")

def rodar_modo_daemon():
    tz = obter_timezone()
    print("="*60)
    print("=== UMLAPSO BOT - SERVIÇO DE AGENDAMENTO INTELIGENTE ===")
    print(f"=== Fuso Horário: {tz} ===")
    print("="*60)
    
    while True:
        try:
            verificar_e_executar_se_necessario()
        except Exception as e:
            print(f"[-] Erro no loop de agendamento: {e}")
        time.sleep(60)

def main():
    parser = argparse.ArgumentParser(description="Agendador Inteligente do Umlapso Bot")
    parser.add_argument("--check", action="store_true", help="Verifica se há publicação devida agora (para Cron)")
    parser.add_argument("--force", action="store_true", help="Força a execução do próximo slot pendente imediatamente")
    parser.add_argument("--daemon", action="store_true", help="Executa continuamente em segundo plano (para Systemd)")
    parser.add_argument("--status", action="store_true", help="Exibe a grade e status dos agendamentos de hoje")
    
    args = parser.parse_args()
    
    if args.daemon:
        rodar_modo_daemon()
    elif args.force:
        verificar_e_executar_se_necessario(forcar=True)
    elif args.status:
        tz = obter_timezone()
        est = carregar_ou_inicializar_estado(tz)
        print(json.dumps(est, indent=4, ensure_ascii=False))
    else:
        verificar_e_executar_se_necessario()

if __name__ == "__main__":
    main()
