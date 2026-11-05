"""Vigia a conversa "Cainã (você)" (lista JARVIS; o número fica no .env, JARVIS_NUMERO_WHATSAPP) e registra mensagens novas.

Uso: python whatsapp_monitor.py [intervalo_segundos=300]

- Abre a conversa pela lista completa (filtro Tudo), lê as últimas mensagens e compara com a leitura anterior.
- Se houver texto novo, grava em .estado/alertas_whatsapp.json (lista de alertas, mais recente no fim).
- Não envia nada, não responde e não marca outras conversas. Só mexe no mouse enquanto lê.
- Estado da última leitura: .estado/monitor_whatsapp.json.
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from pywinauto import mouse

from whatsapp_leitura import achar, clicar, janela, ler_painel, nome_proprio

BASE = Path(__file__).parent / ".estado"
ESTADO = BASE / "monitor_whatsapp.json"
ALERTAS = BASE / "alertas_whatsapp.json"
ALVO = re.compile(nome_proprio() + r" \(você\)")  # o WhatsApp mostra o próprio número no lugar do nome


def carregar(caminho, padrao):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except Exception:
        return padrao


def achar_conversa(win):
    """Procura a linha 'Cainã (você)' na lista completa. Devolve o elemento ou None."""
    tudo = achar(win, nome="Tudo")
    if tudo is not None:
        clicar(tudo)
    grade = achar(win, nome="Lista de conversas", tipo="DataGrid")
    if grade is None:
        return None
    for _ in range(40):
        for f in grade.children():
            try:
                if f.element_info.control_type == "DataItem":
                    n = (f.element_info.name or "").replace("\n", " ").strip()
                    if ALVO.search(n):
                        return f
            except Exception:
                pass
        mouse.scroll(coords=(250, 600), wheel_dist=-3)
        time.sleep(0.3)
    return None


def checar():
    """Uma rodada: abre a conversa, lê e compara. Devolve (novos_textos, leitura)."""
    win = janela()
    win.set_focus()
    time.sleep(0.4)
    linha = achar_conversa(win)
    if linha is None:
        raise RuntimeError("conversa 'Cainã (você)' não encontrada na lista")
    clicar(linha)
    time.sleep(0.6)
    leitura = ler_painel(win)
    anterior = carregar(ESTADO, {"textos": []})
    vistos = set(anterior.get("textos", []))
    novos = [t for t in leitura.get("textos", []) if t not in vistos]
    return novos, leitura


def registrar(novos):
    alertas = carregar(ALERTAS, [])
    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    for t in novos:
        alertas.append({"quando": agora, "texto": t})
    BASE.mkdir(exist_ok=True)
    ALERTAS.write_text(json.dumps(alertas, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    intervalo = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    BASE.mkdir(exist_ok=True)
    while True:
        try:
            novos, leitura = checar()
            if novos:
                registrar(novos)
                print(f"{len(novos)} mensagem(ns) nova(s) registrada(s).", flush=True)
            ESTADO.write_text(json.dumps({"textos": leitura.get("textos", []),
                                          "lido_em": datetime.now().isoformat()},
                                         ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:  # não derruba o vigia por uma falha pontual
            print(f"falha na checagem: {e}", flush=True)
        time.sleep(intervalo)


if __name__ == "__main__":
    sys.exit(main())
