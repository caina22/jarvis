#!/usr/bin/env python3
"""Abre o JARVIS no Windows (é o que o atalho da Área de Trabalho chama).

Se o JARVIS já estiver no ar, só abre a esfera no navegador. Se não, liga o
servidor nesta janela (fica minimizada). Fechar a janela desliga o JARVIS.
"""
import ctypes
import runpy
import sys
import urllib.request
import webbrowser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def porta():
    try:
        for linha in (RAIZ / ".env").read_text(encoding="utf-8").splitlines():
            if linha.strip().startswith("JARVIS_PORTA="):
                return int(linha.split("=", 1)[1].strip())
    except (FileNotFoundError, ValueError):
        pass
    return 8790


def no_ar(url):
    try:
        with urllib.request.urlopen(url + "/api/saude", timeout=2):
            return True
    except Exception:
        return False


def main():
    url = f"http://localhost:{porta()}"
    if no_ar(url):
        webbrowser.open(url)
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW("JARVIS (feche esta janela para desligar)")
    except Exception:
        pass
    sys.argv = [str(RAIZ / "server.py")]
    runpy.run_path(str(RAIZ / "server.py"), run_name="__main__")


if __name__ == "__main__":
    main()
