"""Lê as conversas não lidas do WhatsApp Desktop e devolve um JSON (stdout).

Uso: python whatsapp_leitura.py <saida.json>

- Configuração atual (2026-11-05): só lê a conversa "Cainã (você)" (o número fica no .env, JARVIS_NUMERO_WHATSAPP). Outras conversas não são abertas.
- Usa o filtro "Não lidas". Abre só conversas individuais sem resposta.
- NÃO abre os contatos importantes (Pai, Mãe, Angela Mans, Vó Dudes) nem grupos: só lista.
- Ao final, volta o filtro para "Tudo".
"""
import json
import os
import re
import sys
import time
from pathlib import Path

from pywinauto import Desktop, mouse


def _numero_proprio():
    """Número do dono como o WhatsApp mostra (ex.: +55 11 90000-0000). Fica no .env
    (JARVIS_NUMERO_WHATSAPP), nunca no código, porque o repositório é público."""
    valor = os.environ.get("JARVIS_NUMERO_WHATSAPP", "")
    if not valor:
        try:
            for linha in (Path(__file__).parent / ".env").read_text(encoding="utf-8-sig").splitlines():
                if linha.startswith("JARVIS_NUMERO_WHATSAPP="):
                    valor = linha.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    return valor


def nome_proprio():
    """Padrão do nome da conversa consigo mesmo: 'Cainã' ou o número, se estiver no .env."""
    numero = _numero_proprio()
    return "(Cainã|" + re.escape(numero) + ")" if numero else "(Cainã)"


# Só a conversa consigo mesmo (Cainã (você)). Qualquer outra conversa é ignorada e não é aberta.
SO_EU = re.compile(r"^(Mostrar status\s+)?(\d+ mensagens? não lidas? |1 mensagem não lida )?" + nome_proprio() + r" \(você\)")
IMPORTANTE = re.compile(r"^(\d+ mensagens? não lidas? )?(Pai|Mãe|Angela Mans|Vó Dudes)\b")
GRUPO = re.compile(r" : |~|Diretoria|Congresso|Cofras|GTI|GRUPO|Grupo|UNIADQB|AD QUATRO|Imperium|Familia|Família|"
                   r"Jantar quinzenal|Protocolo|PROTOCOLO|IFPR|Netos|Turma|Equipe|Lobão|Tony|OFERTAS|COMUNIDADE|"
                   r"Academia|Nic impressoras|Teorias|Gravação|ATHUS|Estágio")
IGN = re.compile(r"^(Lida|Entregue|Enviada|Reproduzir mensagem de voz|Mensagem de voz|Controle deslizante.*|"
                 r"Mensagem citada|Mostrar reações|reação .*|Você:|.*:$|\d{1,2}:\d{2}( Lida)?|wds-ic-read|Mídia.*|"
                 r"Encaminhar mídia|Modo picture-in-picture.*|Baixar.*|Abrir imagem|Ler mais|Saiba mais|Álbum|"
                 r"Clique para.*|Bloquear|Continuar|Adicionar aos contatos|Pagar agora|Conferir informações|Ver mais)$")


def janela():
    for w in Desktop(backend="uia").windows():
        if (w.window_text() or "") == "WhatsApp":
            return w
    raise RuntimeError("WhatsApp Desktop não está aberto")


def achar(el, nome=None, tipo=None, prefixo=None, profundidade=0):
    if profundidade > 25:
        return None
    try:
        filhos = el.children()
    except Exception:
        return None
    for f in filhos:
        try:
            n = f.element_info.name or ""
            t = f.element_info.control_type
            if (nome is not None and n == nome and (tipo is None or t == tipo)) or \
               (prefixo is not None and re.match(prefixo, n) and (tipo is None or t == tipo)):
                return f
        except Exception:
            pass
        achado = achar(f, nome, tipo, prefixo, profundidade + 1)
        if achado is not None:
            return achado
    return None


def clicar(el):
    r = el.rectangle()
    mouse.click(coords=(int((r.left + r.right) / 2), int((r.top + r.bottom) / 2)))
    time.sleep(1.2)


def qtd_nao_lidas(linha):
    m = re.match(r"^(\d[\d\.]*)\s+mensagens?\s+não\s+lidas?", linha)
    if m:
        return int(m.group(1).replace(".", ""))
    return 1 if linha.startswith("1 mensagem não lida") else 0


def ler_painel(win):
    els = []

    def caminhar(el, profundidade=0):
        if profundidade > 30 or len(els) > 600:
            return
        try:
            filhos = el.children()
        except Exception:
            return
        for f in filhos:
            try:
                info = f.element_info
                n = (info.name or "").replace("\n", " ").strip()
                rc = f.rectangle()
                if n and rc.left > 460 and 120 < rc.top and rc.bottom < 1040:
                    els.append((rc.top, rc.left, info.control_type, n))
            except Exception:
                pass
            caminhar(f, profundidade + 1)

    caminhar(win)
    els.sort()
    # minhas mensagens: rótulo "Você:" ou bolhas do lado direito
    topo_meu = max((e[0] for e in els if e[3] == "Você:" or e[1] > 1200), default=None)
    # mensagens recebidas: texto do lado esquerdo que não é rótulo nem horário
    recebidas = [e for e in els if e[1] < 1000 and len(e[3]) > 2 and not IGN.match(e[3])
                 and not re.match(r"^\d{1,2}:\d{2}", e[3]) and not e[3].endswith(":")]
    topo_recebido = max((e[0] for e in recebidas), default=None)
    if topo_meu is None and topo_recebido is None:
        return {"ultimo": None, "textos": []}
    if topo_recebido is None or (topo_meu is not None and topo_meu >= topo_recebido):
        return {"ultimo": "voce", "textos": []}
    textos, vistos = [], set()
    for e in recebidas:
        if e[0] >= topo_recebido - 400 and e[3] not in vistos:
            vistos.add(e[3])
            textos.append(e[3][:200])
    return {"ultimo": "outro", "textos": textos[-4:]}


def nome_limpo(linha):
    """Tira 'Mostrar status', contagem de não lidas e horário do nome da conversa."""
    s = re.sub(r"^(Mostrar status\s+)", "", linha)
    s = re.sub(r"^\d[\d\.]*\s+mensagens?\s+não\s+lidas?\s+", "", s)
    s = re.sub(r"^1 mensagem não lida\s+", "", s)
    m = re.search(r"\s(\d{1,2}:\d{2}|Ontem|\d{2}/\d{2}/\d{4}|(segunda|terça|quarta|quinta|sexta|sábado|domingo)(-feira)?)\b", s)
    return (s[:m.start()] if m else s[:50]).strip()


def main():
    if len(sys.argv) < 2:
        print("uso: whatsapp_leitura.py <saida.json>")
        return 2
    saida = Path(sys.argv[1])
    win = janela()
    win.set_focus()
    time.sleep(0.4)

    chip = achar(win, prefixo=r"^Não lidas\b")
    if chip is not None:
        clicar(chip)

    grade = achar(win, nome="Lista de conversas", tipo="DataGrid")
    if grade is None:
        print("lista de conversas não encontrada")
        return 1

    def linhas():
        out = []
        for f in grade.children():
            try:
                if f.element_info.control_type == "DataItem":
                    n = (f.element_info.name or "").replace("\n", " ").strip()
                    if len(n) > 25:
                        out.append((n, f))
            except Exception:
                pass
        return out

    lidos, importantes, grupos = [], [], []
    feitos = set()
    inicio = time.time()
    sem_progresso = 0
    while time.time() - inicio < 480 and sem_progresso < 40:
        alvo = None
        for n, f in linhas():
            if n in feitos:
                continue
            if not SO_EU.search(n):  # configuração atual: só lê a conversa "Cainã (você)"
                feitos.add(n)
                continue
            if IMPORTANTE.search(n):
                importantes.append({"nome": nome_limpo(n), "qtd": qtd_nao_lidas(n)})
                feitos.add(n)
                continue
            if GRUPO.search(n):
                grupos.append({"nome": nome_limpo(n), "qtd": qtd_nao_lidas(n)})
                feitos.add(n)
                continue
            r = f.rectangle()
            if 100 < r.top and r.bottom < 1078:
                alvo = (n, f)
                break
        if alvo is None:
            sem_progresso += 1
            mouse.scroll(coords=(250, 600), wheel_dist=-3)
            time.sleep(0.3)
            continue
        sem_progresso = 0
        n, f = alvo
        clicar(f)
        time.sleep(0.6)
        dados = ler_painel(win)
        feitos.add(n)
        lidos.append({"nome": nome_limpo(n), "linha": n, **dados})

    voltar = achar(win, nome="Tudo")
    if voltar is not None:
        clicar(voltar)

    resultado = {"linhas_vistas": len(feitos), "individuais": lidos,
                 "importantes_sem_abrir": importantes, "grupos_sem_abrir": grupos}
    saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"leitura feita: {len(lidos)} individuais, {len(importantes)} importantes, {len(grupos)} grupos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
