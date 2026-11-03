#!/usr/bin/env python3
"""JARVIS: assistente de voz em cima do Claude Code.

Você segura a barra de espaço e fala. O áudio vira texto (whisper local ou
ElevenLabs Scribe), o texto vai pra uma sessão do Claude Code aberta na sua
pasta de trabalho, e a resposta volta falada pelo ElevenLabs.

Só usa a biblioteca padrão do Python (3.9+). Rodar:  python3 server.py
"""
import json
import os
import queue
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
WEB = RAIZ / "web"
ESTADO = RAIZ / ".estado"


# ---------------------------------------------------------------- configuração

def ler_env(caminho):
    valores = {}
    try:
        for linha in Path(caminho).expanduser().read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            k, v = linha.split("=", 1)
            valores[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return valores


def carregar_config():
    # Ordem: variável de ambiente > .env do projeto > ~/.config/vortex-marketing/.env
    base = ler_env("~/.config/vortex-marketing/.env")
    base.update(ler_env(RAIZ / ".env"))
    base.update({k: v for k, v in os.environ.items() if k.startswith(("JARVIS_", "ELEVENLABS_", "WHISPER_"))})
    pasta = Path(base.get("JARVIS_PASTA") or Path.home()).expanduser()
    return {
        "pasta": pasta,
        "porta": int(base.get("JARVIS_PORTA", "8790")),
        "modelo": base.get("JARVIS_MODELO", "sonnet"),
        "esforco": base.get("JARVIS_ESFORCO", "low"),
        # leve: o Claude abre numa pasta vazia com a sua pasta anexada (responde em ~1 s);
        # completo: abre direto na sua pasta, carregando o CLAUDE.md e a memória dela (bem mais lento)
        "contexto": base.get("JARVIS_CONTEXTO", "leve"),
        "persona": base.get("JARVIS_PERSONA", "persona.md"),
        "eleven_key": base.get("ELEVENLABS_API_KEY", ""),
        # Segundo cérebro: pasta de notas que ele lê ao ligar e onde guarda o que importa
        "cerebro": Path(base["JARVIS_CEREBRO"]).expanduser() if base.get("JARVIS_CEREBRO") else None,
        # Quem fala: elevenlabs (nuvem, pago por caractere) | piper (no PC, grátis, rápido)
        #            | voicestudio (no PC, na placa de vídeo, com as vozes que você criou no VoiceStudio)
        "motor_voz": base.get("JARVIS_VOZ_MOTOR", "elevenlabs").lower(),
        "piper_voz": base.get("JARVIS_PIPER_VOZ", "pt_BR-faber-medium"),
        "vs_url": base.get("VOICESTUDIO_URL", "http://127.0.0.1:3900").rstrip("/"),
        "vs_voz": base.get("JARVIS_VOICESTUDIO_VOZ", "default"),  # id ou nome do perfil de voz
        "vs_estilo": base.get("JARVIS_VOICESTUDIO_ESTILO", ""),   # instrução de estilo opcional
        "voz": base.get("JARVIS_VOZ", "xHUwLsLfyqiYOIVTzLRW"),
        "modelo_voz": base.get("JARVIS_MODELO_VOZ", "eleven_multilingual_v2"),
        "velocidade_voz": float(base.get("JARVIS_VELOCIDADE_VOZ", "1.0")),
        "transcricao": base.get("JARVIS_TRANSCRICAO", "auto"),
        "whisper_url": base.get("WHISPER_URL", "http://127.0.0.1:8178").rstrip("/"),
        "bloquear": base.get("JARVIS_BLOQUEAR_FERRAMENTAS", ""),
        # 1 = a trava (hooks/trava.py) pede "confirma" antes de apagar, enviar ou publicar | 0 = desligada
        "trava": base.get("JARVIS_TRAVA", "1") == "1",
        "aquecer": base.get("JARVIS_AQUECER", "1") == "1",
        "aquecer_atalhos": base.get("JARVIS_AQUECER_ATALHOS", ""),
    }


CFG = carregar_config()
PREFS = ESTADO / "preferencias.json"  # o que se escolhe na engrenagem vale mais que o .env


PIPER = CFG["motor_voz"] == "piper"
VOICESTUDIO = CFG["motor_voz"] == "voicestudio"
PIPER_PASTA = RAIZ / "vozes-piper"
if PIPER:
    CFG["voz"] = CFG["piper_voz"]  # no Piper, a voz é o nome do modelo em vozes-piper/
if VOICESTUDIO:
    CFG["voz"] = CFG["vs_voz"]  # no VoiceStudio, a voz é o id do perfil


def tem_voz():
    return PIPER or VOICESTUDIO or bool(CFG["eleven_key"])


def vozes_voicestudio():
    """Os perfis de voz salvos no VoiceStudio viram as opções da engrenagem."""
    try:
        with urllib.request.urlopen(CFG["vs_url"] + "/profiles", timeout=5) as r:
            perfis = json.loads(r.read())
    except Exception:
        perfis = []  # VoiceStudio ainda fechado: a voz do .env vale, a lista enche quando ele abrir
    vozes = [{"id": p["id"], "nome": p.get("name") or p["id"], "genero": "voicestudio",
              "descricao": (p.get("description") or "Perfil do VoiceStudio")[:80]} for p in perfis]
    if CFG["vs_voz"] not in {v["id"] for v in vozes}:  # aceita o nome do perfil no .env
        achou = next((v for v in vozes if v["nome"].lower() == CFG["vs_voz"].lower()), None)
        if achou:
            CFG["voz"] = CFG["vs_voz"] = achou["id"]
    return vozes


def carregar_vozes():
    if VOICESTUDIO:
        return vozes_voicestudio()
    try:
        return json.loads((RAIZ / ("vozes-piper.json" if PIPER else "vozes.json")).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []


def carregar_preferencias():
    try:
        p = json.loads(PREFS.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return
    if p.get("voz") in {v["id"] for v in VOZES}:  # ignora voz salva do outro motor
        CFG["voz"] = p["voz"]
    if p.get("velocidade"):
        CFG["velocidade_voz"] = float(p["velocidade"])


def salvar_preferencias():
    ESTADO.mkdir(exist_ok=True)
    PREFS.write_text(json.dumps({"voz": CFG["voz"], "velocidade": CFG["velocidade_voz"]}), encoding="utf-8")


VOZES = carregar_vozes()
carregar_preferencias()
AMOSTRA = "Às suas ordens, Senhor. É assim que eu vou falar com o senhor daqui pra frente."


# ------------------------------------------------------------ segundo cérebro

def ler_nota(nome, limite=6000):
    try:
        return (CFG["cerebro"] / nome).read_text(encoding="utf-8")[:limite].strip()
    except (OSError, TypeError):
        return ""


def fim_do_diario(limite):
    """As últimas conversas anotadas (do dia mais recente)."""
    try:
        dias = sorted((CFG["cerebro"] / "Diário").glob("*.md"))
        return dias[-1].read_text(encoding="utf-8")[-limite:].strip() if dias else ""
    except (OSError, TypeError):
        return ""


def contexto_cerebro():
    """Vai junto da persona em cada sessão nova do Claude: assim ele nunca começa do zero."""
    pasta = CFG["cerebro"]
    if not pasta or not pasta.is_dir():
        return ""
    notas = sorted(str(n.relative_to(pasta)) for n in pasta.rglob("*.md") if "Diário" not in n.parts)
    partes = [
        f"\n\n## Segundo cérebro (sua memória de longo prazo)\nFica em `{pasta}`. As regras de organização estão no LEIA-ME.md dela.",
        "\n### 00 Agora.md (no que vocês estão trabalhando)\n" + ler_nota("00 Agora.md"),
        "\n### Preferências.md\n" + ler_nota("Preferências.md", 3000),
        "\n### Notas que existem\n" + "\n".join("- " + n for n in notas[:150]),
    ]
    diario = fim_do_diario(3000)
    if diario:
        partes.append("\n### Últimas conversas (fim do diário)\n" + diario)
    return "".join(partes)


def anotar_diario(voce, resposta, escrito="", quem="Claude"):
    """Cada conversa entra sozinha no diário do dia, mesmo que ninguém lembre de anotar."""
    pasta = CFG["cerebro"]
    if not pasta or not pasta.is_dir():
        return
    try:
        dia = time.strftime("%Y-%m-%d")
        arquivo = pasta / "Diário" / f"{dia}.md"
        arquivo.parent.mkdir(exist_ok=True)
        novo = not arquivo.exists()
        with open(arquivo, "a", encoding="utf-8") as f:
            if novo:
                f.write(f"# Diário {dia}\n\nConversas com o JARVIS, anotadas sozinhas.\n")
            f.write(f"\n**{time.strftime('%H:%M')}** · Cainã: {voce}\nJARVIS ({quem}): {resposta}\n")
            if escrito:
                f.write("Alteração:\n" + escrito.strip() + "\n")
    except OSError as erro:
        log({"evento": "diario_falhou", "erro": repr(erro)})


def persona():
    for nome in (CFG.get("persona") or "persona.md", "persona.md", "persona.example.md"):
        p = RAIZ / nome
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


# ------------------------------------------------------------ eventos pra tela

class Eventos:
    """Fila de eventos que a página recebe por Server-Sent Events."""

    def __init__(self):
        self.ouvintes = []
        self.lock = threading.Lock()
        self.ultimo_estado = {"tipo": "estado", "estado": "iniciando"}

    def emitir(self, evento):
        if evento.get("tipo") == "estado":
            self.ultimo_estado = evento
        with self.lock:
            for q in list(self.ouvintes):
                q.put(evento)

    def assinar(self):
        q = queue.Queue()
        with self.lock:
            self.ouvintes.append(q)
        q.put(self.ultimo_estado)
        return q

    def sair(self, q):
        with self.lock:
            if q in self.ouvintes:
                self.ouvintes.remove(q)


EVENTOS = Eventos()


def log(registro):
    ESTADO.mkdir(exist_ok=True)
    registro["quando"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(ESTADO / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


# --------------------------------------------------- rótulos do que ele está usando

NOMES_SERVIDOR = {
    "geracorte": "GeraCorte",
    "geragrupos": "GeraGrupos",
    "panda-video": "Panda Video",
    "claude_ai_Google_Calendar": "Agenda",
    "claude_ai_Gmail": "Gmail",
    "claude_ai_Google_Drive": "Drive",
    "claude_ai_Google_Sheets": "Planilhas",
    "claude_ai_Canva": "Canva",
    "claude_ai_Figma": "Figma",
}


INTERNAS = {"ToolSearch", "TodoWrite", "BashOutput", "KillShell", "ListMcpResourcesTool", "ReadMcpResourceTool"}


def descrever_ferramenta(nome, entrada):
    """O detalhe que aparece no painel: o comando, o arquivo, a busca."""
    entrada = entrada or {}
    if nome in ("Bash", "PowerShell"):
        return entrada.get("description") or entrada.get("command", "")
    for chave in ("file_path", "notebook_path", "path", "pattern", "query", "url", "skill", "description", "prompt"):
        if entrada.get(chave):
            return str(entrada[chave])
    if nome.startswith("mcp__"):
        return nome.split("__")[-1].replace("_", " ")
    return json.dumps(entrada, ensure_ascii=False)[:200] if entrada else ""


def rotulo_ferramenta(nome, entrada):
    entrada = entrada or {}
    if nome in INTERNAS:
        return None
    if nome.startswith("mcp__"):
        servidor = nome.split("__")[1]
        for chave, rot in NOMES_SERVIDOR.items():
            if chave.lower() in servidor.lower():
                return rot
        return servidor.replace("claude_ai_", "").replace("_", " ")
    if nome == "Bash":
        cmd = entrada.get("command", "")
        if re.search(r"zernio\.py|redes_resumo|isca_resumo", cmd):
            return "Redes sociais"
        if "supabase" in cmd or "psql" in cmd:
            return "Banco de dados"
        if "Memória" in cmd or "/memory" in cmd:
            return "Memória"
        if "TaskNotes" in cmd:
            return "Kanban"
        if re.match(r"\s*(grep|rg|find|cat|head|tail|ls)\b", cmd):
            return "Lendo o vault"
        return "Terminal"
    caminho = str(entrada.get("file_path") or entrada.get("path") or entrada.get("pattern") or "")
    if "Segundo Cérebro" in caminho or "Segundo Cérebro" in str(entrada.get("command", "")):
        return "Segundo cérebro"
    if nome in ("Write", "Edit", "MultiEdit"):
        if "TaskNotes" in caminho:
            return "Kanban"
        return "Escrevendo nota"
    if nome in ("Read", "Grep", "Glob", "LS"):
        if "Memória" in caminho or "memory" in caminho:
            return "Memória"
        if "TaskNotes" in caminho:
            return "Kanban"
        return "Lendo o vault"
    if nome == "Skill":
        return "Skill " + str(entrada.get("skill", ""))
    if nome in ("WebSearch", "WebFetch"):
        return "Internet"
    if nome in ("Task", "Agent"):
        return "Agente"
    return nome


# ------------------------------------------------------------ sessão do Claude

# Vai junto de cada fala: o modelo tende a se alongar quando acha muita coisa.
LEMBRETE = ("\n\n[modo voz: pergunta simples (conversa, confirmação, algo que você já sabe) = responda direto, "
            "sem aviso e sem ferramenta. Só se precisar de ferramenta, comece com uma frase curta de aviso (até 10 palavras). "
            "Resposta final em no máximo 2 frases curtas, até 30 palavras, no seu tom sarcástico (e às vezes filosófico). "
            "Trate-o por Senhor. "
            "Se surgiu informação importante ou o trabalho avançou, atualize o segundo cérebro (00 Agora.md e a nota certa) "
            "antes da resposta final, sem comentar isso. "
            "Se você alterou algo, depois da fala ponha uma linha só com § e embaixo o resumo escrito da alteração "
            "(esse trecho não é falado nem conta no limite).]")
ESPERA_AVISO = 0.3  # só fala "um instante" se o trabalho com ferramenta passar disso


class TurnoClaude:
    """Uma pergunta em andamento: recebe o aviso rápido e a resposta final."""

    def __init__(self, ao_avisar=None):
        self.fila = queue.Queue()
        self.entregue = False
        self.avisou = False
        self.ao_avisar = ao_avisar
        self.agendado = False
        self.trava = threading.Lock()
        self.marcas = {"envio": time.time()}  # tempos de cada etapa, pro log

    def marcar(self, nome):
        self.marcas.setdefault(nome, round(time.time() - self.marcas["envio"], 2))

    def entregar(self, resultado):
        if not self.entregue:
            self.entregue = True
            self.fila.put(resultado)

    def avisar(self, frase=None):
        """Fala o aviso rápido uma vez só por pergunta (o primeiro gatilho que chegar)."""
        with self.trava:
            if self.avisou or self.entregue or not self.ao_avisar:
                return
            self.avisou = True
        self.ao_avisar(frase)

    def pedir_aviso(self, frase=None):
        """Ele começou a usar ferramenta: se a resposta não sair em ESPERA_AVISO s, avisa."""
        with self.trava:
            if self.avisou or self.agendado:
                return
            self.agendado = True
        relogio = threading.Timer(ESPERA_AVISO, self.avisar, args=(frase,))
        relogio.daemon = True
        relogio.start()


class SessaoClaude:
    """Um processo do Claude Code que fica aberto e recebe uma fala por vez."""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.turno = None
        self.livre = threading.Event()  # o Claude fecha a rodada uns segundos depois da resposta
        self.livre.set()
        self.ferramentas_turno = []

    def comando(self):
        claude = shutil.which("claude") or "claude"
        trava = f'"{sys.executable}" "{RAIZ / "hooks" / "trava.py"}"'
        ajustes = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": trava}]}]}}
        cmd = [
            claude, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",  # avisa o fim do texto antes de fechar a rodada
            "--permission-mode", "bypassPermissions",
            "--append-system-prompt", persona() + self.nota_pasta() + contexto_cerebro(),
        ]
        if CFG["trava"]:
            cmd += ["--settings", json.dumps(ajustes)]
        if CFG["contexto"] == "leve":
            cmd += ["--add-dir", str(CFG["pasta"])]
        if CFG["modelo"]:
            cmd += ["--model", CFG["modelo"]]
        if CFG["esforco"]:
            cmd += ["--effort", CFG["esforco"]]
        if CFG["bloquear"]:
            cmd += ["--disallowedTools", CFG["bloquear"]]
        return cmd

    def nota_pasta(self):
        return (f"\n\n## Pasta de trabalho\nA pasta de trabalho dele é `{CFG['pasta']}`. Caminhos relativos citados "
                "acima ficam dentro dela. Antes de criar ou mudar algo lá, leia o CLAUDE.md dela (só a parte que interessa).")

    def pasta_de_abertura(self):
        if CFG["contexto"] == "leve":
            # Pasta vazia FORA do projeto: o Claude não carrega o CLAUDE.md da sua pasta nem o do projeto
            # (o Claude Code lê o CLAUDE.md das pastas acima de onde abre; dentro de jarvis/ ele pegaria o de lá)
            leve = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "JARVIS" / "cerebro"
            leve.mkdir(parents=True, exist_ok=True)
            return str(leve)
        return str(CFG["pasta"])

    def interromper(self):
        """Barra de espaço no meio do trabalho: o Claude para na hora e a conversa continua."""
        turno = self.turno
        if not self.vivo() or not turno or turno.entregue:
            return False
        try:
            msg = {"type": "control_request", "request_id": uuid.uuid4().hex[:8], "request": {"subtype": "interrupt"}}
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        turno.entregar({"result": "", "interrompido": True})
        return True

    def iniciar(self):
        ESTADO.mkdir(exist_ok=True)
        env = os.environ.copy()
        env["JARVIS_ESTADO"] = str(ESTADO)
        env["JARVIS_PASTA"] = str(CFG["pasta"])
        self.livre.set()
        self.proc = subprocess.Popen(
            self.comando(), cwd=self.pasta_de_abertura(), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, encoding="utf-8",
        )
        threading.Thread(target=self._ler_saida, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._ler_erros, args=(self.proc,), daemon=True).start()

    def parar(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def vivo(self):
        return self.proc is not None and self.proc.poll() is None

    def _ler_saida(self, proc):
        iniciou = False
        texto_msg = ""  # texto da mensagem que o Claude está escrevendo agora
        for linha in proc.stdout:
            try:
                e = json.loads(linha)
            except ValueError:
                continue
            tipo = e.get("type")
            turno = self.turno
            if tipo == "system" and e.get("subtype") == "init":
                # O Claude manda esse aviso a cada pergunta; só o primeiro de cada processo interessa.
                if not iniciou:
                    iniciou = True
                    servidores = [(m.get("name"), m.get("status")) for m in e.get("mcp_servers", [])]
                    log({"evento": "init", "modelo": e.get("model"), "mcp": servidores,
                         "ferramentas": len(e.get("tools", []))})
            elif tipo == "stream_event":
                ev = e.get("event") or {}
                if ev.get("type") == "message_start":
                    texto_msg = ""
                    if turno:
                        turno.marcar("1a_mensagem_s")
                elif ev.get("type") == "message_delta" and (ev.get("delta") or {}).get("stop_reason") == "end_turn":
                    # Resposta final pronta: entrega já, sem esperar o Claude fechar a rodada.
                    if turno and texto_msg.strip():
                        turno.entregar({"result": texto_msg, "cedo": True})
            elif tipo == "assistant":
                for bloco in e.get("message", {}).get("content", []) or []:
                    if bloco.get("type") == "thinking" and bloco.get("thinking", "").strip():
                        EVENTOS.emitir({"tipo": "passo", "passo": "pensamento", "texto": bloco["thinking"].strip()[:1500]})
                    elif bloco.get("type") == "text":
                        texto_msg = (texto_msg + " " + bloco.get("text", "")).strip()
                    elif bloco.get("type") == "tool_use":
                        if texto_msg:  # o que ele escreveu antes de usar a ferramenta
                            EVENTOS.emitir({"tipo": "passo", "passo": "texto", "texto": texto_msg[:1500]})
                        if turno:
                            turno.marcar("ferramenta_s")
                        # Texto escrito antes da primeira ferramenta = aviso rápido ("Um instante, Senhor.").
                        # Se ele foi direto pra ferramenta, toca uma frase pronta.
                        if turno:
                            turno.pedir_aviso(texto_msg or None)
                        texto_msg = ""
                        rot = rotulo_ferramenta(bloco.get("name", ""), bloco.get("input"))
                        resumo = json.dumps(bloco.get("input") or {}, ensure_ascii=False)[:160]
                        self.ferramentas_turno.append(f"{bloco.get('name')} {resumo}")
                        if rot:
                            EVENTOS.emitir({"tipo": "ferramenta", "rotulo": rot})
                            EVENTOS.emitir({"tipo": "passo", "passo": "ferramenta", "id": bloco.get("id"),
                                            "rotulo": rot, "detalhe": descrever_ferramenta(bloco.get("name", ""), bloco.get("input"))})
            elif tipo == "user":
                # Resultado de cada ferramenta: deu certo ou deu erro, e o começo do que voltou
                conteudo = e.get("message", {}).get("content", [])
                for bloco in conteudo if isinstance(conteudo, list) else []:
                    if bloco.get("type") != "tool_result":
                        continue
                    volta = bloco.get("content")
                    if isinstance(volta, list):
                        volta = " ".join(str(p.get("text", "")) for p in volta if isinstance(p, dict))
                    EVENTOS.emitir({"tipo": "passo", "passo": "resultado", "id": bloco.get("tool_use_id"),
                                    "erro": bool(bloco.get("is_error")), "texto": " ".join(str(volta or "").split())[:300]})
            elif tipo == "result":
                if turno:
                    turno.entregar(e)
                self.livre.set()
        if self.turno:
            self.turno.entregar({"type": "result", "is_error": True, "result": "A sessão do Claude fechou."})
        self.livre.set()

    def _ler_erros(self, proc):
        ESTADO.mkdir(exist_ok=True)
        with open(ESTADO / "claude-erros.log", "a", encoding="utf-8") as f:
            for linha in proc.stderr:
                f.write(linha)
                f.flush()

    def perguntar(self, texto, limite=300, ao_avisar=None, aviso_apos=None):
        with self.lock:
            if not self.vivo():
                self.iniciar()
            espera = time.time()
            self.livre.wait(timeout=20)  # espera o Claude fechar a rodada anterior
            turno = TurnoClaude(ao_avisar)
            turno.marcas["esperou_rodada_s"] = round(time.time() - espera, 2)
            self.turno = turno
            self.livre.clear()
            self.ferramentas_turno = []
            msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": texto + LEMBRETE}]}}
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            if aviso_apos:  # pergunta de dado: se demorar, fala "Um instante, Senhor."
                relogio = threading.Timer(aviso_apos, turno.avisar)
                relogio.daemon = True
                relogio.start()
            try:
                return turno.fila.get(timeout=limite)
            except queue.Empty:
                self.parar()
                return {"is_error": True, "result": "Senhor, demorei demais e reiniciei a conversa. Pode repetir?"}


SESSAO = SessaoClaude()


# ---------------------------------------------------------------- IA local (Ollama)
# Tarefas simples de pasta e arquivo vão pro modelo local (grátis). O que for código,
# apagar, internet ou qualquer dúvida segue pro Claude.
# Modelo menor cabe na placa de vídeo junto com o VoiceStudio (o 9B dividia os 6 GB e ficava lento)
LOCAL_MODELO = os.environ.get("JARVIS_MODELO_LOCAL") or ler_env(RAIZ / ".env").get("JARVIS_MODELO_LOCAL", "gemma2:2b")
LOCAL_URL = "http://127.0.0.1:11434/api/generate"
LOCAL_VERBOS = re.compile(r"\b(organiz\w*|mov\w+|renome\w+|copi\w+|separ\w+|junt\w+|list\w+|cri\w+ (uma |a )?(nova )?pasta|nova pasta|quantos arquivos|arquivos da pasta)\b")
LOCAL_CLAUDE = re.compile(r"(claude|cloud|clod|claudio)")
LOCAL_CODIGO = re.compile(r"\b(html|css|javascript|js|typescript|python|codigo|script|site|pagina|servidor|server|funcao|bug|git|deploy|api|jarvis|instal\w+|apag\w+|exclu\w+|delet\w+|envi\w+|email|agenda|whatsapp|pesquis\w+)\b")
# Contas Google (Gmail, Agenda, Drive, Planilhas): só o Claude tem os conectores
LOCAL_GOOGLE = re.compile(r"\b(google|gmail|e ?mails?|mail|caixa de entrada|inbox|remetente|respond\w+|rascunho\w*|"
                          r"agenda\w*|calendario|compromisso\w*|reuni\w+|evento\w*|marc\w+|desmarc\w+|horario\w*|"
                          r"drive|planilh\w+|sheets?|documento\w*|docs|apresenta\w+|slides?|compartilh\w+)\b")
# Guardar ou lembrar algo precisa do Claude: só ele escreve e procura no segundo cérebro
LOCAL_MEMORIA = re.compile(r"\b(lembr\w*|anot\w*|guard\w*|memori\w*|esquec\w*|registr\w*|cerebro|salv\w*|"
                           r"trabalhando|estavamos|paramos|combinamos|falamos|decidimos)\b")
LOCAL_PROIBIDO = re.compile(
    r"(\b(Remove-\w+|rm|del|erase|rd|rmdir|ri|Invoke-\w+|iwr|irm|iex|icm|Start-Process|Stop-\w+|Restart-\w+|"
    r"Set-ExecutionPolicy|reg|schtasks|shutdown|winget|cmd|powershell|pwsh|curl|wget|taskkill|Send-\w+|git|npm|pip|"
    r"Format-\w+|Clear-\w+|Out-Printer)\b|\.\.|`|\$\(|\$env|~)", re.I)
LOCAL_PROMPT = (
    "Você converte pedidos simples de organização de arquivos em UM comando PowerShell (Windows 11). "
    "Pasta base do usuário: {base}. Use sempre caminhos absolutos dentro dela. "
    "Permitido: Get-ChildItem, Move-Item, Copy-Item, Rename-Item, New-Item -ItemType Directory. "
    "Nunca apague nada, nunca use internet, nunca use caminhos fora da pasta base. "
    "Se o pedido envolver código, apagar, enviar algo, ou se faltar informação, responda comando vazio. "
    'Responda só JSON: {{"comando": "..."}}.\n\nPedido: {pedido}'
)


def _ollama(prompt, formato=None, limite=90):
    corpo = {"model": LOCAL_MODELO, "prompt": prompt, "stream": False, "keep_alive": "30m", "options": {"temperature": 0}}
    if formato:
        corpo["format"] = formato
    req = urllib.request.Request(LOCAL_URL, json.dumps(corpo).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=limite) as resp:
        return json.loads(resp.read())["response"].strip()


def comando_local_seguro(cmd):
    if not cmd or len(cmd) > 600 or LOCAL_PROIBIDO.search(cmd):
        return False
    base = CFG["pasta"].resolve()
    for caminho in re.findall(r"[A-Za-z]:\\[^\"'|;,]*", cmd):
        try:
            if not Path(caminho.strip()).resolve().is_relative_to(base):
                return False
        except Exception:
            return False
    return True


def tentar_local(texto, registro):
    """Devolve a resposta do modelo local, ou None pra seguir pro Claude."""
    frase = " ".join(normalizar(texto))
    if (LOCAL_CLAUDE.search(frase) or LOCAL_CODIGO.search(frase) or LOCAL_MEMORIA.search(frase)
            or LOCAL_GOOGLE.search(frase)):
        return None
    if not LOCAL_VERBOS.search(frase):
        try:  # conversa comum: o Gemma responde sozinho, sabendo no que vocês estão trabalhando
            memoria = ""
            if CFG["cerebro"]:
                memoria = ("\n\nNo que vocês estão trabalhando (00 Agora.md):\n" + ler_nota("00 Agora.md", 2500) +
                           "\n\nÚltimas conversas:\n" + fim_do_diario(1500) + "\n")
            resposta = _ollama(
                "Você é o JARVIS, uma inteligência artificial sarcástica e filosófica: ironia seca e elegante em quase toda "
                "resposta e, de vez em quando, uma reflexão curta sobre tempo, propósito, ordem e caos ou máquinas e humanos. "
                "Leal e eficiente, nunca cruel; assunto sério (dinheiro, saúde, erro grave) é tratado sério. Chame o dono de Senhor. "
                "Responda em no máximo 2 frases curtas, faladas, sem markdown, sem emoji. Se não souber, diga que não sabe; nunca invente."
                f"{memoria}\nFala do Senhor: {texto}", limite=60)
            registro["local"] = {"conversa": True}
            return {"result": resposta, "is_error": False, "local": True} if resposta else None
        except Exception as erro:
            registro["local_erro"] = repr(erro)
            return None
    try:
        t0 = time.time()
        cmd = (json.loads(_ollama(LOCAL_PROMPT.format(base=CFG["pasta"], pedido=texto), "json")).get("comando") or "").strip()
        if not comando_local_seguro(cmd):
            return None
        saida = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=30, cwd=str(CFG["pasta"]),
        )
        registro["local"] = {"comando": cmd, "codigo": saida.returncode, "s": round(time.time() - t0, 2)}
        if saida.returncode != 0:
            return None
        dados = (saida.stdout or "").strip()[:1500]
        resposta = "Feito, Senhor."
        if dados:
            resposta = _ollama(
                "Você é o JARVIS, uma inteligência artificial sarcástica e filosófica, de ironia seca e elegante. "
                "Chame o dono de Senhor. Resuma em UMA frase curta, com um toque irônico, "
                f"falada, sem markdown, sem caminhos:\nPedido: {texto}\nResultado:\n{dados}", limite=30) or resposta
        return {"result": resposta, "is_error": False, "local": True}
    except Exception as erro:
        registro["local_erro"] = repr(erro)
        return None


# ---------------------------------------------------------------- transcrição

def multipart(campos, nome_arquivo, conteudo, mime, campo_arquivo="file"):
    limite = uuid.uuid4().hex
    partes = []
    for k, v in campos.items():
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    partes.append(
        f'--{limite}\r\nContent-Disposition: form-data; name="{campo_arquivo}"; filename="{nome_arquivo}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode() + conteudo + b"\r\n"
    )
    partes.append(f"--{limite}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={limite}"


def para_wav(audio, mime):
    ext = ".wav" if "wav" in mime else ".mp4" if "mp4" in mime else ".ogg" if "ogg" in mime else ".webm"
    with tempfile.TemporaryDirectory() as tmp:
        entrada = Path(tmp) / ("fala" + ext)
        saida = Path(tmp) / "fala.wav"
        entrada.write_bytes(audio)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(entrada), "-ar", "16000", "-ac", "1", str(saida)],
            check=True,
        )
        return saida.read_bytes()


def transcrever_whisper(audio, mime):
    wav = para_wav(audio, mime)
    corpo, tipo = multipart({"response_format": "json", "temperature": "0.0"}, "fala.wav", wav, "audio/wav")
    req = urllib.request.Request(CFG["whisper_url"] + "/inference", data=corpo, headers={"Content-Type": tipo})
    with urllib.request.urlopen(req, timeout=25) as r:  # passou disso, usa o ElevenLabs
        return json.loads(r.read().decode("utf-8")).get("text", "")


def transcrever_scribe(audio, mime):
    nome = "fala.wav" if "wav" in mime else "fala.webm"
    corpo, tipo = multipart({"model_id": "scribe_v1", "language_code": "por"}, nome, audio, mime)
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/speech-to-text", data=corpo,
        headers={"Content-Type": tipo, "xi-api-key": CFG["eleven_key"]},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8")).get("text", "")


# Frases que o whisper "inventa" quando o áudio é quase só silêncio (vêm de legendas de vídeo).
ALUCINACOES = [
    re.compile(p) for p in (
        r"^legendas? (por|pela|de)\b", r"\bruberti\b", r"\bamara org\b",
        r"\bobrigad[oa] por assistir", r"\binscreva se\b",
    )
]


def transcrever(audio, mime):
    modo = CFG["transcricao"]
    texto = ""
    if modo in ("auto", "whisper"):
        try:
            texto = transcrever_whisper(audio, mime)
        except Exception as erro:  # whisper fora do ar
            if modo == "whisper":
                raise
            log({"evento": "whisper_falhou", "erro": str(erro)})
            texto = transcrever_scribe(audio, mime)
    else:
        texto = transcrever_scribe(audio, mime)
    texto = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", texto)  # [BLANK_AUDIO], (música)
    texto = " ".join(texto.split())
    limpo = " ".join(normalizar(texto))
    if len(limpo.replace(" ", "")) < 2 or any(p.search(limpo) for p in ALUCINACOES):  # "ok" confirma a trava
        log({"evento": "transcricao_descartada", "texto": texto})
        return ""
    return texto


# ---------------------------------------------------------------- confirmação

def normalizar(texto):
    sem_acento = unicodedata.normalize("NFD", texto.lower())
    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", " ", sem_acento).split()


AFIRMACOES = {"sim", "confirma", "confirmo", "confirmado", "pode", "manda", "isso", "ok", "okay",
              "fechado", "vai", "faz", "beleza", "claro", "positivo", "certo", "bora"}


def eh_confirmacao(texto):
    palavras = normalizar(texto)
    return 0 < len(palavras) <= 6 and palavras[0] in AFIRMACOES and "nao" not in palavras


def pediu_confirmacao(resposta):
    return "confirma" in " ".join(normalizar(resposta[-80:]))


# ------------------------------------------------------------------ resposta falada

def limpar_para_fala(texto):
    texto = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", texto)  # [link](url) -> link
    texto = re.sub(r"https?://\S+", "", texto)
    texto = re.sub(r"[*_#`>|]", "", texto)
    texto = re.sub(r"^\s*[-•]\s*", "", texto, flags=re.M)
    texto = " ".join(texto.split())
    return texto[:700]


class Fala:
    """Gera a voz uma vez só (ElevenLabs, Piper ou VoiceStudio) e entrega aos pedaços pra quem pedir."""

    todas = {}
    tipo = "audio/wav" if PIPER else "audio/mpeg"
    modelos_piper = {}
    trava_piper = threading.Lock()
    trava_gpu = threading.Lock()  # VoiceStudio: uma fala por vez na placa de vídeo, na ordem em que chegaram
    # O plano grátis da ElevenLabs aceita no máximo 2 pedidos de voz ao mesmo tempo
    vagas = threading.Semaphore(int(os.environ.get("JARVIS_VOZ_SIMULTANEAS", "2")))

    def __init__(self, texto, voz=None, velocidade=None):
        self.pedacos = bytearray()
        self.pronto = False
        self.erro = None
        self.cond = threading.Condition()
        self.voz = voz or CFG["voz"]
        self.velocidade = velocidade if velocidade is not None else CFG["velocidade_voz"]
        threading.Thread(target=self._gerar, args=(texto,), daemon=True).start()

    def esperar(self, limite=60):
        with self.cond:
            fim = time.time() + limite
            while not self.pronto and time.time() < fim:
                self.cond.wait(timeout=1)
        return bytes(self.pedacos) if self.pronto and not self.erro else b""

    def _gerar(self, texto):
        if PIPER:
            return self._gerar_piper(texto)
        if VOICESTUDIO:
            return self._gerar_voicestudio(texto)
        url = (f"https://api.elevenlabs.io/v1/text-to-speech/{self.voz}/stream"
               "?output_format=mp3_44100_128")
        pedido = {"text": texto, "model_id": CFG["modelo_voz"]}
        if self.velocidade != 1.0:
            pedido["voice_settings"] = {"speed": self.velocidade}
        corpo = json.dumps(pedido).encode()
        req = urllib.request.Request(url, data=corpo, headers={
            "xi-api-key": CFG["eleven_key"], "Content-Type": "application/json"})
        try:
            with Fala.vagas, urllib.request.urlopen(req, timeout=60) as r:
                while True:
                    pedaco = r.read(4096)
                    if not pedaco:
                        break
                    with self.cond:
                        self.pedacos.extend(pedaco)
                        self.cond.notify_all()
        except urllib.error.HTTPError as erro:
            self.erro = erro.code
            log({"evento": "voz_falhou", "codigo": erro.code, "detalhe": erro.read()[:300].decode("utf-8", "ignore")})
        except Exception as erro:
            self.erro = repr(erro)
            log({"evento": "voz_falhou", "detalhe": repr(erro)})
        finally:
            with self.cond:
                self.pronto = True
                self.cond.notify_all()

    @classmethod
    def modelo_piper(cls, nome):
        if nome not in cls.modelos_piper:
            from piper import PiperVoice
            cls.modelos_piper[nome] = PiperVoice.load(str(PIPER_PASTA / f"{nome}.onnx"))
        return cls.modelos_piper[nome]

    def _gerar_voicestudio(self, texto):
        """VoiceStudio rodando no PC (rota no formato da OpenAI). Devolve MP3."""
        pedido = {"model": "omnivoice", "input": texto, "voice": self.voz, "language": "pt",
                  "response_format": "mp3", "speed": self.velocidade}
        if CFG["vs_estilo"]:
            pedido["instruct"] = CFG["vs_estilo"]
        req = urllib.request.Request(CFG["vs_url"] + "/v1/audio/speech", json.dumps(pedido).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with Fala.trava_gpu, urllib.request.urlopen(req, timeout=120) as r:
                while True:
                    pedaco = r.read(8192)
                    if not pedaco:
                        break
                    with self.cond:
                        self.pedacos.extend(pedaco)
                        self.cond.notify_all()
        except urllib.error.HTTPError as erro:
            self.erro = erro.code
            log({"evento": "voz_falhou", "motor": "voicestudio", "codigo": erro.code,
                 "detalhe": erro.read()[:300].decode("utf-8", "ignore")})
        except Exception as erro:  # VoiceStudio fechado: a resposta continua por escrito no painel
            self.erro = repr(erro)
            log({"evento": "voz_falhou", "motor": "voicestudio", "detalhe": repr(erro)})
        finally:
            with self.cond:
                self.pronto = True
                self.cond.notify_all()

    def _gerar_piper(self, texto):
        import io
        import wave
        from piper import SynthesisConfig
        try:
            saida = io.BytesIO()
            # Um de cada vez: o modelo é carregado uma vez só e é compartilhado
            with Fala.trava_piper, wave.open(saida, "wb") as wav:
                ajuste = SynthesisConfig(length_scale=1.0 / max(0.5, self.velocidade))
                self.modelo_piper(self.voz).synthesize_wav(texto, wav, syn_config=ajuste)
            with self.cond:
                self.pedacos.extend(saida.getvalue())
        except Exception as erro:
            self.erro = repr(erro)
            log({"evento": "voz_falhou", "motor": "piper", "detalhe": repr(erro)})
        finally:
            with self.cond:
                self.pronto = True
                self.cond.notify_all()

    def ler(self):
        pos = 0
        while True:
            with self.cond:
                while pos >= len(self.pedacos) and not self.pronto:
                    self.cond.wait(timeout=30)
                novo = bytes(self.pedacos[pos:])
                acabou = self.pronto
            pos += len(novo)
            if novo:
                yield novo
            if acabou and pos >= len(self.pedacos):
                return

    fixas = {}  # frases prontas: geradas uma vez quando o JARVIS abre

    @classmethod
    def fixa(cls, texto):
        chave = f"{CFG['voz']}|{CFG['velocidade_voz']}|{texto}"
        id_fala = "f" + uuid.uuid5(uuid.NAMESPACE_URL, chave).hex[:11]
        if id_fala not in cls.fixas:
            cls.fixas[id_fala] = cls(texto)
        return id_fala

    @classmethod
    def criar(cls, texto):
        id_fala = uuid.uuid4().hex[:12]
        cls.todas[id_fala] = cls(texto)
        for antigo in list(cls.todas)[:-20]:  # guarda só as últimas 20
            cls.todas.pop(antigo, None)
        return id_fala


# Ditas quando ele vai trabalhar e não escreveu o próprio aviso.
AVISOS_PRONTOS = ["Um instante, Senhor. A pressa nunca construiu nada que preste.",
                  "Deixa comigo, Senhor. Alguém aqui precisa ser eficiente.",
                  "Verificando, Senhor. A verdade costuma estar no segundo lugar onde se procura.",
                  "Já volto, Senhor. Tente não sentir minha falta.",
                  "Consultando, Senhor. O universo não tem pressa, mas eu tenho."]


# ---------------------------------------------------------------- pré-busca
# Pergunta que bate com um gatilho de gatilhos.json já sai com o dado buscado:
# o atalho roda enquanto a pergunta vai pro Claude e ele só precisa falar.

def carregar_gatilhos():
    try:
        return json.loads((RAIZ / "gatilhos.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []


GATILHOS = carregar_gatilhos()


def periodo_falado(texto):
    t = " ".join(normalizar(texto))
    if re.search(r"\b(semana|ultimos dias|7 dias|sete dias)\b", t):
        return ["--ultimos", "7"]
    if re.search(r"\b(mes|30 dias|trinta dias)\b", t):
        return ["--ultimos", "30"]
    if re.search(r"\bontem\b", t):
        return ["--dia", "ontem"]
    return ["--dia", "hoje"]


def prebusca(texto):
    palavras = set(normalizar(texto))
    comandos = []
    for g in GATILHOS:
        alvos = {" ".join(normalizar(p)) for p in g.get("palavras", [])}
        if palavras & alvos:
            cmd = [sys.executable, str(RAIZ / "atalhos" / g["comando"][0])] + g["comando"][1:]
            if g.get("periodo"):
                cmd += periodo_falado(texto)
            comandos.append((g["nome"], cmd))
    if not comandos:
        return ""

    def rodar(item):
        nome, cmd = item
        try:
            r = subprocess.run(cmd, cwd=str(RAIZ), capture_output=True, text=True, timeout=8)
            return f"{nome}: {r.stdout.strip()}" if r.returncode == 0 and r.stdout.strip() else ""
        except Exception:
            return ""

    with ThreadPoolExecutor(max_workers=len(comandos)) as grupo:
        linhas = [l for l in grupo.map(rodar, comandos) if l]
    if not linhas:
        return ""
    return ("\n\n[dados que o JARVIS já buscou agora pra esta pergunta. Responda direto com eles, "
            "sem rodar comando e sem aviso. Só rode algo se faltar o que ele pediu:]\n" + "\n".join(linhas))


class Turno:
    ocupado = threading.Lock()
    aguardando_confirmacao = False
    interrompido = False  # você apertou espaço no meio: esse pedido não fala mais nada
    conversa_local = []   # o que o modelo local respondeu e o Claude ainda não viu


def interromper():
    if not Turno.ocupado.locked():
        return False
    Turno.interrompido = True
    SESSAO.interromper()
    log({"evento": "interrompido"})
    return True


def processar(audio=None, mime="audio/webm", texto=None):
    # Se o pedido anterior foi interrompido, ele libera em instantes: espera um pouco.
    if not Turno.ocupado.acquire(timeout=10 if Turno.interrompido else 0.01):
        EVENTOS.emitir({"tipo": "aviso", "texto": "Ainda estou no pedido anterior, Senhor."})
        return
    token = ESTADO / "confirmado"
    inicio = time.time()
    registro = {"evento": "turno"}
    try:
        if audio is not None:
            EVENTOS.emitir({"tipo": "estado", "estado": "transcrevendo"})
            texto = transcrever(audio, mime)
            registro["transcricao_s"] = round(time.time() - inicio, 2)
            if Turno.interrompido:
                registro["interrompido"] = True
                return
        texto = (texto or "").strip()
        if not texto:
            EVENTOS.emitir({"tipo": "aviso", "texto": "Não consegui ouvir. Segure o espaço e fale de novo."})
            EVENTOS.emitir({"tipo": "estado", "estado": "parado"})
            return
        EVENTOS.emitir({"tipo": "voce", "texto": texto})
        registro["voce"] = texto

        # A trava só abre quando ele pediu "Confirma?" e você respondeu que sim.
        if Turno.aguardando_confirmacao and eh_confirmacao(texto):
            ESTADO.mkdir(exist_ok=True)
            token.write_text(str(time.time()))
            registro["confirmou"] = True

        EVENTOS.emitir({"tipo": "estado", "estado": "pensando"})
        t_claude = time.time()

        def avisar(frase=None):
            # "Um instante, Senhor." sai na hora, enquanto ele trabalha.
            if Turno.interrompido:
                return
            frase = limpar_para_fala(frase or "")[:160]
            pronta = not frase
            if pronta:
                frase = random.choice(AVISOS_PRONTOS)
            registro["aviso"] = frase + (" (pronta)" if pronta else "")
            registro["aviso_s"] = round(time.time() - inicio, 2)
            if tem_voz():
                id_fala = Fala.fixa(frase) if pronta else Fala.criar(frase)
                EVENTOS.emitir({"tipo": "fala", "texto": frase, "audio": f"/api/voz/{id_fala}"})

        r = None if Turno.aguardando_confirmacao else tentar_local(texto, registro)
        if r is None:
            t_busca = time.time()
            dados = prebusca(texto)
            if dados:
                registro["prebusca_s"] = round(time.time() - t_busca, 2)
            # O Claude não viu o que o modelo local respondeu: conta antes, pra não perder o fio
            antes = ""
            if Turno.conversa_local:
                antes = ("[conversas que o modelo local respondeu enquanto isso:\n" +
                         "\n".join(Turno.conversa_local[-8:]) + "]\n\n")
                Turno.conversa_local = []
            r = SESSAO.perguntar(antes + texto + dados, ao_avisar=avisar, aviso_apos=2.5 if dados else None)
            registro["claude_s"] = round(time.time() - t_claude, 2)
            registro["cedo"] = bool(r.get("cedo"))
            if SESSAO.turno:
                registro["etapas"] = {k: v for k, v in SESSAO.turno.marcas.items() if k != "envio"}
        if Turno.interrompido or r.get("interrompido"):
            registro["interrompido"] = True
            return
        registro["ferramentas"] = [] if r.get("local") else SESSAO.ferramentas_turno
        # O que vem depois do § é o resumo escrito da alteração: aparece no painel, não é falado
        bruto = (r.get("result") or "").strip()
        falado, _, escrito = bruto.partition("§")
        resposta = limpar_para_fala(falado) or "Feito."
        if r.get("is_error"):
            registro["erro"] = r.get("result")
        Turno.aguardando_confirmacao = pediu_confirmacao(resposta)

        if r.get("local"):
            Turno.conversa_local.append(f"Cainã: {texto} / JARVIS: {resposta}")
        if not r.get("is_error"):
            anotar_diario(texto, resposta, escrito, "local" if r.get("local") else "Claude")

        audio_url = f"/api/voz/{Fala.criar(resposta)}" if tem_voz() else ""
        registro["resposta"] = resposta
        registro["total_s"] = round(time.time() - inicio, 2)
        EVENTOS.emitir({
            "tipo": "resposta", "texto": resposta, "audio": audio_url,
            "completo": falado.strip()[:6000], "escrito": escrito.strip()[:6000],  # por escrito no painel
            "segundos": registro["total_s"], "confirmar": Turno.aguardando_confirmacao,
        })
        EVENTOS.emitir({"tipo": "estado", "estado": "parado"})
    except Exception as erro:
        registro["erro"] = repr(erro)
        EVENTOS.emitir({"tipo": "aviso", "texto": f"Deu erro: {erro}"})
        EVENTOS.emitir({"tipo": "estado", "estado": "parado"})
    finally:
        if token.exists():
            token.unlink()
        log(registro)
        Turno.interrompido = False
        Turno.ocupado.release()


def config_atual():
    return {"voz": CFG["voz"], "velocidade": CFG["velocidade_voz"], "vozes": VOZES}


def gravar_avisos_prontos():
    """Deixa as frases prontas ("Um instante, Senhor.") gravadas na voz escolhida."""
    if tem_voz():
        for frase in AVISOS_PRONTOS:
            Fala.fixa(frase)


def aquecer_atalhos():
    """Roda atalhos lentos quando abre (ex.: o resumo da isca de ontem), pra resposta sair na hora."""
    for comando in filter(None, (c.strip() for c in CFG["aquecer_atalhos"].split(";"))):
        partes = shlex.split(comando)
        try:
            subprocess.run([sys.executable, str(RAIZ / "atalhos" / partes[0])] + partes[1:],
                           cwd=str(RAIZ), capture_output=True, timeout=180)
            log({"evento": "atalho_aquecido", "comando": comando})
        except Exception as erro:
            log({"evento": "atalho_falhou", "comando": comando, "erro": repr(erro)})


def aquecer():
    """Primeira chamada ao abrir: conecta as ferramentas e deixa o Claude pronto."""
    threading.Thread(target=aquecer_atalhos, daemon=True).start()
    EVENTOS.emitir({"tipo": "estado", "estado": "aquecendo"})
    try:
        SESSAO.iniciar()
        gravar_avisos_prontos()
        if CFG["aquecer"]:
            with Turno.ocupado:
                SESSAO.perguntar("(Mensagem automática de início. Não use ferramentas. Responda só: Pronto.)", limite=120)
    finally:
        EVENTOS.emitir({"tipo": "estado", "estado": "parado"})


# ---------------------------------------------------------------------- HTTP

TIPOS = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


class Servidor(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def responder(self, codigo, corpo=b"", tipo="application/json"):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        caminho = self.path.split("?")[0]
        if caminho == "/api/eventos":
            return self.eventos()
        if caminho == "/api/config":
            return self.responder(200, json.dumps(config_atual(), ensure_ascii=False).encode())
        if caminho.startswith("/api/amostra/"):
            return self.amostra(caminho.rsplit("/", 1)[-1])
        if caminho.startswith("/api/voz/"):
            return self.voz(caminho.rsplit("/", 1)[-1])
        if caminho == "/api/saude":
            corpo = json.dumps({"claude": SESSAO.vivo(), "pasta": str(CFG["pasta"]), "modelo": CFG["modelo"],
                                "telas": len(EVENTOS.ouvintes)})
            return self.responder(200, corpo.encode())
        arquivo = (WEB / ("index.html" if caminho == "/" else caminho.lstrip("/"))).resolve()
        if WEB.resolve() not in arquivo.parents and arquivo != WEB.resolve() / "index.html":
            return self.responder(404, b"{}")
        if not arquivo.is_file():
            return self.responder(404, b"{}")
        self.responder(200, arquivo.read_bytes(), TIPOS.get(arquivo.suffix, "application/octet-stream"))

    def do_POST(self):
        tamanho = int(self.headers.get("Content-Length", "0"))
        corpo = self.rfile.read(tamanho) if tamanho else b""
        if self.path == "/api/interromper":
            return self.responder(200, json.dumps({"interrompeu": interromper()}).encode())
        if self.path in ("/api/audio", "/api/texto") and Turno.ocupado.locked() and not Turno.interrompido:
            return self.responder(409, b'{"ocupado":true}')  # a tela avisa e espera
        if self.path == "/api/audio":
            if len(corpo) < 2000:  # toque rápido no espaço, sem fala
                return self.responder(200, b'{"ok":false}')
            mime = self.headers.get("Content-Type", "audio/webm")
            threading.Thread(target=processar, kwargs={"audio": corpo, "mime": mime}, daemon=True).start()
            return self.responder(202, b'{"ok":true}')
        if self.path == "/api/texto":
            texto = json.loads(corpo or b"{}").get("texto", "")
            threading.Thread(target=processar, kwargs={"texto": texto}, daemon=True).start()
            return self.responder(202, b'{"ok":true}')
        if self.path == "/api/diag":  # a tela avisa se a voz tocou ou falhou
            try:
                dados = json.loads(corpo or b"{}")
            except ValueError:
                dados = {}
            log({"evento": "tela", "tipo": str(dados.get("evento", ""))[:40],
                 "detalhe": str(dados.get("detalhe", ""))[:300]})
            return self.responder(200, b'{"ok":true}')
        if self.path == "/api/config":
            pedido = json.loads(corpo or b"{}")
            ids = {v["id"] for v in VOZES}
            if pedido.get("voz") in ids:
                CFG["voz"] = pedido["voz"]
            if pedido.get("velocidade") is not None:
                CFG["velocidade_voz"] = round(min(1.2, max(0.7, float(pedido["velocidade"]))), 2)
            salvar_preferencias()
            log({"evento": "config", "voz": CFG["voz"], "velocidade": CFG["velocidade_voz"]})
            threading.Thread(target=gravar_avisos_prontos, daemon=True).start()
            return self.responder(200, json.dumps(config_atual(), ensure_ascii=False).encode())
        if self.path == "/api/reiniciar":
            SESSAO.parar()
            Turno.aguardando_confirmacao = False
            threading.Thread(target=aquecer, daemon=True).start()
            return self.responder(200, b'{"ok":true}')
        self.responder(404, b"{}")

    def eventos(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = EVENTOS.assinar()
        try:
            while True:
                try:
                    evento = q.get(timeout=15)
                    dados = "data: " + json.dumps(evento, ensure_ascii=False) + "\n\n"
                except queue.Empty:
                    dados = ": ping\n\n"
                self.wfile.write(dados.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            EVENTOS.sair(q)

    def amostra(self, voz):
        if voz not in {v["id"] for v in VOZES} or not tem_voz():
            return self.responder(404, b"{}")
        arquivo = ESTADO / "amostras" / (f"{voz}-{CFG['velocidade_voz']}" + (".wav" if PIPER else ".mp3"))
        if not arquivo.exists():
            dados = Fala(AMOSTRA, voz=voz).esperar()
            if not dados:
                return self.responder(502, b"{}")
            arquivo.parent.mkdir(parents=True, exist_ok=True)
            arquivo.write_bytes(dados)
        self.responder(200, arquivo.read_bytes(), Fala.tipo)

    def voz(self, id_fala):
        fala = Fala.todas.get(id_fala) or Fala.fixas.get(id_fala)
        if not fala:
            return self.responder(404, b"{}")
        if fala.pronto and fala.erro:
            return self.responder(502, b"{}")
        self.send_response(200)
        self.send_header("Content-Type", Fala.tipo)
        self.send_header("Cache-Control", "no-store")
        if fala.pronto:
            self.send_header("Content-Length", str(len(fala.pedacos)))
        self.end_headers()
        try:
            for pedaco in fala.ler():
                self.wfile.write(pedaco)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def completar_path():
    """Quando abre pelo app (fora do terminal), o PATH vem curto. Procura nos lugares de costume."""
    atual = os.environ.get("PATH", "").split(os.pathsep)
    for pasta in ("~/.npm-global/bin", "~/.local/bin", "~/.claude/local", "/opt/homebrew/bin",
                  "/usr/local/bin", "/opt/homebrew/opt/ffmpeg-full/bin"):
        pasta = os.path.expanduser(pasta)
        if os.path.isdir(pasta) and pasta not in atual:
            atual.append(pasta)
    os.environ["PATH"] = os.pathsep.join(atual)


def garantir_voicestudio():
    """Abre o VoiceStudio se ele estiver fechado e, quando subir, atualiza a lista de vozes da engrenagem."""
    global VOZES
    def no_ar():
        try:
            with urllib.request.urlopen(CFG["vs_url"] + "/health", timeout=3):
                return True
        except Exception:
            return False
    if not no_ar():
        exe = Path(os.path.expandvars(os.environ.get(
            "JARVIS_VOICESTUDIO_EXE", r"%LOCALAPPDATA%\Programs\VoiceStudio\VoiceStudio.exe")))
        if not exe.exists():
            log({"evento": "voicestudio_fora", "erro": f"não achei {exe}"})
            return
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
        log({"evento": "voicestudio_abrindo"})
        for _ in range(90):
            time.sleep(2)
            if no_ar():
                break
        else:
            log({"evento": "voicestudio_fora", "erro": "não respondeu em 3 minutos"})
            return
    VOZES = vozes_voicestudio()
    gravar_avisos_prontos()


def main():
    completar_path()
    if VOICESTUDIO:
        threading.Thread(target=garantir_voicestudio, daemon=True).start()
    if not CFG["pasta"].is_dir():
        sys.exit(f"JARVIS_PASTA não existe: {CFG['pasta']}")
    if not shutil.which("claude"):
        sys.exit("Não achei o Claude Code (comando 'claude'). Instale antes: https://claude.com/claude-code")
    if not tem_voz():
        print("Aviso: sem ELEVENLABS_API_KEY. Ele vai responder só em texto na tela.")
    servidor = ThreadingHTTPServer(("127.0.0.1", CFG["porta"]), Servidor)
    servidor.daemon_threads = True
    url = f"http://localhost:{CFG['porta']}"
    print(f"JARVIS no ar em {url}  (pasta: {CFG['pasta']})")

    def encerrar(*_):
        SESSAO.parar()
        os._exit(0)

    signal.signal(signal.SIGTERM, encerrar)
    threading.Thread(target=aquecer, daemon=True).start()
    if "--sem-abrir" not in sys.argv:
        webbrowser.open(url)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SESSAO.parar()


if __name__ == "__main__":
    main()
