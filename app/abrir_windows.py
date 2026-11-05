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


def navegador():
    import os, shutil
    for c in (
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    ):
        if os.path.exists(c):
            return c
    return shutil.which("msedge") or shutil.which("chrome")


def abrir_janela(url):
    """Abre o JARVIS como janela de aplicativo (sem abas nem barra de endereço).
    Perfil próprio, para o processo só terminar quando a janela fechar."""
    import subprocess, time
    exe = navegador()
    if not exe:
        webbrowser.open(url)
        return None
    perfil = RAIZ / ".estado" / "janela"
    perfil.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={perfil}",
                             "--window-size=1100,800", "--no-first-run",
                             "--autoplay-policy=no-user-gesture-required"])


def main():
    import subprocess, time
    url = f"http://localhost:{porta()}"
    if no_ar(url):
        abrir_janela(url)
        return
    srv = subprocess.Popen([sys.executable, str(RAIZ / "server.py"), "--sem-abrir"],
                           cwd=str(RAIZ), creationflags=0x08000000)
    for _ in range(60):
        if no_ar(url):
            break
        time.sleep(0.5)
    jan = abrir_janela(url)
    try:
        if jan:
            jan.wait()
        else:
            srv.wait()
    finally:
        srv.terminate()


if __name__ == "__main__":
    main()
