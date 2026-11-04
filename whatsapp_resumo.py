"""Envia um texto para a conversa com você mesmo no WhatsApp Desktop (Windows).

Uso:
    python whatsapp_resumo.py <arquivo.txt>          envia o texto
    python whatsapp_resumo.py <arquivo.txt> --teste  só acha a conversa, não envia nada

O destino é fixo: a conversa marcada com "(você)". Nenhum outro contato é aceito.
O texto vem de arquivo, nunca da linha de comando, e é colado pela área de transferência.
"""
import subprocess
import sys
import time
from pathlib import Path

from pywinauto import Desktop, mouse
from pywinauto.keyboard import send_keys

MARCA_DESTINO = "(você)"


def janela_whatsapp():
    for w in Desktop(backend="uia").windows():
        if (w.window_text() or "") == "WhatsApp":
            return w
    raise RuntimeError("WhatsApp Desktop não está aberto")


def achar_lista(el, profundidade=0):
    if profundidade > 25:
        return None
    try:
        filhos = el.children()
    except Exception:
        return None
    for f in filhos:
        try:
            if f.element_info.control_type == "DataGrid" and (f.element_info.name or "") == "Lista de conversas":
                return f
        except Exception:
            pass
        achado = achar_lista(f, profundidade + 1)
        if achado is not None:
            return achado
    return None


def voltar_para_tudo(el, profundidade=0):
    """Se o filtro 'Não lidas' estiver ativo, volta para 'Tudo' (a conversa com você pode não ter não lidas)."""
    if profundidade > 20:
        return
    try:
        filhos = el.children()
    except Exception:
        return
    for f in filhos:
        try:
            if (f.element_info.name or "") == "Tudo":
                r = f.rectangle()
                mouse.click(coords=(int((r.left + r.right) / 2), int((r.top + r.bottom) / 2)))
                time.sleep(1.0)
                return True
        except Exception:
            pass
        if voltar_para_tudo(f, profundidade + 1):
            return True
    return False


def achar_destino(win, lista):
    """Rola a lista até achar a linha do destino fixo e devolve o elemento."""
    for _ in range(60):
        mouse.scroll(coords=(250, 600), wheel_dist=10)
        time.sleep(0.1)
    for _ in range(200):
        for f in lista.children():
            try:
                nome = (f.element_info.name or "")
                if f.element_info.control_type == "DataItem" and MARCA_DESTINO in nome:
                    r = f.rectangle()
                    if 100 < r.top and r.bottom < 1078:
                        return f
            except Exception:
                continue
        mouse.scroll(coords=(250, 600), wheel_dist=-3)
        time.sleep(0.2)
    return None


def main():
    if len(sys.argv) < 2:
        print("uso: whatsapp_resumo.py <arquivo.txt> [--teste]")
        return 2
    arquivo = Path(sys.argv[1]).resolve()
    teste = "--teste" in sys.argv
    texto = arquivo.read_text(encoding="utf-8").strip()
    if not texto or len(texto) > 1200:
        print("texto vazio ou longo demais")
        return 2

    win = janela_whatsapp()
    win.set_focus()
    time.sleep(0.4)
    voltar_para_tudo(win)
    lista = achar_lista(win)
    if lista is None:
        print("lista de conversas não encontrada")
        return 1
    destino = achar_destino(win, lista)
    if destino is None:
        print("conversa com você mesmo não encontrada")
        return 1
    if teste:
        print("destino encontrado (teste, nada enviado)")
        return 0

    r = destino.rectangle()
    mouse.click(coords=(int((r.left + r.right) / 2), int((r.top + r.bottom) / 2)))
    time.sleep(1.8)
    # campo de digitação fica no rodapé da conversa aberta
    mouse.click(coords=(1000, 1041))
    time.sleep(0.5)
    # a área de transferência recebe o texto do arquivo, nunca a linha de comando
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         f"Get-Content -Raw -Encoding utf8 -LiteralPath '{arquivo}' | Set-Clipboard"],
        check=True, timeout=30,
    )
    time.sleep(0.4)
    send_keys("^v")
    time.sleep(1.0)
    send_keys("{ENTER}")
    time.sleep(1.5)
    print("resumo enviado para você mesmo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
