#!/usr/bin/env python3
"""
Ponto de entrada do Agendador Inteligente do Umlapso Bot.
Delega diretamente para a lógica centralizada em core.scheduler.
"""
import os
import sys

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.scheduler import main

if __name__ == "__main__":
    main()
