# ==============================================================================
# CONFIGURAÇÃO GERAL E FUSO HORÁRIO
# ==============================================================================
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
CRON_TZ=America/Sao_Paulo

# ==============================================================================
# 1. MANUTENÇÃO DO SISTEMA & WATCHDOGS
# ==============================================================================
# Limpeza de arquivos temporários e cache da VPS (Diariamente às 03:00)
0 3 * * * /opt/clean_vps.sh > /var/log/clean_vps.log 2>&1

# Monitor de saúde do CRM EvoCRM (A cada 5 minutos)
*/5 * * * * /root/scripts/evocrm_watchdog.sh >> /var/log/evocrm-watchdog/cron.log 2>&1


# ==============================================================================
# 2. BOT: UMLAPSO (@um.lapso)
# ==============================================================================
# Agendador Inteligente de Vídeos Inéditos (A cada 15 min entre 07h00 e 22h59, fora de 13h-17h)
*/15 7-22 * * * cd /home/umlapso_bot && /usr/bin/xvfb-run -a ./venv/bin/python scheduler.py --check >> logs/cron.log 2>&1

# Repost Engine — Stories/Reels (Desativado ou controlado com trava anti-duplicidade)
# 5 10,18 * * * cd /home/umlapso_bot && ./venv/bin/python repost_engine.py >> logs/cron_repost.log 2>&1


# ==============================================================================
# 3. BOT: BRASIL ENCANTADOR
# ==============================================================================
# Publicação de Carrosséis (Horários estratégicos do dia)
0 9,12,14,17,19,21,22 * * * cd /home/brasil_encantador && /usr/bin/xvfb-run -a ./venv/bin/python main.py --slides 8 >> logs/cron.log 2>&1

# Keepalive da Sessão Google Flow (A cada hora no minuto :00)
0 * * * * cd /home/brasil_encantador && /usr/bin/xvfb-run -a ./venv/bin/python manter_sessao_flow.py >> logs/keepalive_flow.log 2>&1

# Repost Engine — Brasil Encantador (A cada hora no minuto :10 para não sobrecarregar o :00)
10 * * * * cd /home/brasil_encantador && ./venv/bin/python3 repost_engine.py >> logs/cron_repost.log 2>&1


# ==============================================================================
# 4. BOT: NOTÍCIAS DO NORTÃO
# ==============================================================================
# Verificação de notícias e agendamento (A cada 15 minutos)
*/15 * * * * cd /home/noticias_nortao && /usr/bin/xvfb-run -a ./venv/bin/python scheduler.py --check >> logs/noticias_nortao.log 2>&1

# Repost Engine — Notícias do Nortão (A cada hora no minuto :20)
20 * * * * cd /home/noticias_nortao && ./venv/bin/python3 repost_engine.py >> logs/cron_repost.log 2>&1


# ==============================================================================
# 5. BOT: REELS BÍBLICOS
# ==============================================================================
# Verificador de slots agendados (A cada 5 minutos)
*/5 * * * * cd /home/reels.biblicos.br && ./venv/bin/python scheduler.py --check >> logs/scheduler.log 2>&1


# ==============================================================================
# 6. BOT: TIRINHAS
# ==============================================================================
# Lote 1 de publicações (08h, 10h, 12h, 15h)
0 8,10,12,15 * * * cd /home/tirinhas && /usr/bin/xvfb-run -a ./venv/bin/python main.py >> logs/cron.log 2>&1

# Lote 2 de publicações (17h30, 19h30, 21h30, 22h30)
30 17,19,21,22 * * * cd /home/tirinhas && /usr/bin/xvfb-run -a ./venv/bin/python main.py >> logs/cron.log 2>&1

# Repost Engine — Tirinhas (A cada hora no minuto :40)
40 * * * * cd /home/tirinhas && ./venv/bin/python3 repost_engine.py >> logs/cron.log 2>&1