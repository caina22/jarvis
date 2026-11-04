"""Anexa um áudio na conversa com você mesmo (WhatsApp Desktop) e envia.

Uso: python whatsapp_anexo.py <arquivo.mp3>

Pré-requisito: a conversa "(você)" já aberta (whatsapp_resumo.py faz isso ao enviar o texto).
Destino fixo: só a conversa com você mesmo. Confere o título antes de anexar.
"""
import sys
import time
from pathlib import Path

from pywinauto import Desktop, mouse
from pywinauto.keyboard import send_keys

COORD_CLIPE = (481, 1041)
COORD_ENVIAR = (1873, 1030)


def janela():
    for w in Desktop(backend="uia").windows():
        if (w.window_text() or "") == "WhatsApp":
            return w
    raise RuntimeError("WhatsApp Desktop não está aberto")


def conversa_com_voce(win):
    """Confere que a conversa aberta é a sua (cabeçalho com '(você)')."""
    def procurar(el, n=0):
        if n > 15:
            return False
        try:
            filhos = el.children()
        except Exception:
            return False
        for f in filhos:
            try:
                if "(você)" in (f.element_info.name or "") and f.rectangle().left > 460:
                    return True
            except Exception:
                pass
            if procurar(f, n + 1):
                return True
        return False
    return procurar(win)


def item_menu_audio():
    for w in Desktop(backend="uia").windows():
        try:
            itens = w.descendants(control_type="MenuItem", title="Áudio")
        except Exception:
            continue
        if itens:
            return itens[0]
    return None


def main():
    if len(sys.argv) < 2:
        print("uso: whatsapp_anexo.py <arquivo.mp3>")
        return 2
    arquivo = Path(sys.argv[1]).resolve()
    if not arquivo.exists():
        print("arquivo não existe")
        return 2
    win = janela()
    win.set_focus()
    time.sleep(0.4)
    if not conversa_com_voce(win):
        print("a conversa com você mesmo não está aberta")
        return 1

    mouse.click(coords=COORD_CLIPE)
    time.sleep(1.0)
    item = item_menu_audio()
    if item is None:
        print("menu de anexos não abriu")
        return 1
    r = item.rectangle()
    mouse.click(coords=(int((r.left + r.right) / 2), int((r.top + r.bottom) / 2)))
    time.sleep(2.5)

    # diálogo "Abrir": o campo Nome já tem o foco
    send_keys(str(arquivo), with_spaces=True, pause=0.01)
    time.sleep(0.4)
    send_keys("{ENTER}")
    time.sleep(2.5)

    mouse.click(coords=COORD_ENVIAR)
    time.sleep(3.0)
    print("áudio enviado para você mesmo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
