#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE INSTALAÇÃO E CONFIGURAÇÃO DO UMLAPSO BOT NO UBUNTU 24.04 LTS
# ==============================================================================
set -e

echo "=========================================================="
echo "  Iniciando instalação do Umlapso Bot no Ubuntu 24.04 LTS"
echo "=========================================================="

# 1. Atualização do repositório de pacotes
echo "[1/6] Atualizando pacotes do sistema..."
sudo apt-get update -y

# 2. Instalação de dependências do sistema e FFmpeg
echo "[2/6] Instalando Python 3, venv, FFmpeg e Xvfb (Virtual Framebuffer)..."
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    ffmpeg \
    xvfb \
    git \
    curl \
    wget \
    ca-certificates

# 3. Criação do Ambiente Virtual (venv)
echo "[3/6] Criando ambiente virtual Python (venv)..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# 4. Instalação das dependências Python
echo "[4/6] Instalando dependências do requirements.txt..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# 5. Instalação dos navegadores e bibliotecas do Playwright
echo "[5/6] Instalando Chromium do Playwright e bibliotecas de sistema..."
./venv/bin/playwright install --with-deps chromium

# 6. Criação de pastas de produção e arquivo .env
echo "[6/6] Criando diretórios de produção (temp, output, logs)..."
mkdir -p temp output logs
chmod -R 755 temp output logs

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "[!] Arquivo .env criado a partir de .env.example. Não esqueça de preencher suas credenciais!"
    fi
fi

echo ""
echo "=========================================================="
echo "  Instalação concluída com sucesso no Ubuntu 24.04 LTS!"
echo "=========================================================="
echo "Para testar manualmente, execute:"
echo "    ./venv/bin/python main.py"
echo ""
echo "Para verificar o agendador inteligente de hoje:"
echo "    ./venv/bin/python core/scheduler.py --status"
echo "=========================================================="