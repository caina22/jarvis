"""Memória por significado do JARVIS.

Divide as notas Markdown do "segundo cérebro" em trechos (por títulos `## `),
pede os vetores (embeddings) a um Ollama local, guarda o índice num JSON e
faz a busca por cosseno em Python puro. Só biblioteca padrão.

A memória nunca pode travar o JARVIS: `buscar()` nunca levanta exceção.
"""

import json
import math
import os
import threading
import urllib.request
from pathlib import Path
from typing import Callable

VERSAO = 1
MODELO_PADRAO = "nomic-embed-text"
URL_PADRAO = "http://127.0.0.1:11434/api/embed"
MAX_TRECHO = 2000
PASTA_FORA = "Diário"  # diário não entra no índice
TIMEOUT_INDEXAR = 120  # indexação em segundo plano: dá tempo de o Ollama carregar o modelo


def dividir(texto: str, nome: str) -> list[dict]:
    """Quebra a nota em trechos por `## `. O que vem antes do primeiro
    título usa o nome da nota. Trechos vazios somem."""
    trechos = []
    titulo, linhas = nome, []

    def fechar():
        corpo = "\n".join(linhas).strip()
        if corpo:
            trechos.append({"titulo": titulo, "texto": corpo[:MAX_TRECHO]})

    for linha in texto.splitlines():
        if linha.startswith("## "):
            fechar()
            titulo, linhas = linha[3:].strip(), []
        else:
            linhas.append(linha)
    fechar()
    return trechos


def cosseno(a: list[float], b: list[float]) -> float:
    """Similaridade por cosseno; 0.0 se algum vetor tiver norma zero."""
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


class Memoria:
    def __init__(self, pasta: Path, indice: Path, modelo: str = MODELO_PADRAO,
                 url: str = URL_PADRAO,
                 embed: Callable[[list[str]], list[list[float]]] | None = None,
                 timeout: float = 3):
        self.pasta = Path(pasta)
        self.indice = Path(indice)
        self.modelo = modelo
        self.url = url
        self.timeout = timeout
        self._embed = embed  # None = Ollama de verdade
        self.ultimo_erro: str | None = None
        # True só quando a última busca não conseguiu vetorizar a pergunta
        # (Ollama fora, modelo carregando); outros erros ficam só em ultimo_erro
        self.ultima_busca_falhou = False
        self._dados: dict | None = None  # índice em memória (trocado inteiro)
        self._trava = threading.Lock()  # uma indexação por vez
        self._trava_dados = threading.Lock()  # só para ler/trocar _dados (rápida)
        self._trava_fundo = threading.Lock()
        self._fundo: threading.Thread | None = None

    # -- vetores ---------------------------------------------------------
    def _embed_ollama(self, textos: list[str], timeout: float) -> list[list[float]]:
        corpo = json.dumps({"model": self.modelo, "input": textos,
                            "keep_alive": "30m",
                            "options": {"num_gpu": 0}}  # CPU: a GPU é do VoiceStudio/Qwen
                           ).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=corpo, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))["embeddings"]

    def _vetores(self, textos: list[str],
                 timeout: float | None = None) -> list[list[float]]:
        if self._embed is None:
            vetores = self._embed_ollama(textos, self.timeout if timeout is None else timeout)
        else:
            vetores = self._embed(textos)
        if len(vetores) != len(textos):
            raise ValueError("o Ollama devolveu vetores a menos")
        return vetores

    # -- índice ----------------------------------------------------------
    def _vazio(self) -> dict:
        return {"versao": VERSAO, "modelo": self.modelo, "notas": {}}

    def _carregar(self) -> dict:
        with self._trava_dados:
            if self._dados is not None:
                return self._dados
            try:
                dados = json.loads(self.indice.read_text(encoding="utf-8"))
                if (dados.get("versao") != VERSAO or dados.get("modelo") != self.modelo
                        or not isinstance(dados.get("notas"), dict)):
                    dados = self._vazio()
            except (OSError, ValueError, AttributeError):
                dados = self._vazio()
            self._dados = dados
            return dados

    def _gravar(self, dados: dict) -> None:
        # nome com o pid: o servidor e o busca.py podem gravar ao mesmo tempo
        tmp = self.indice.with_suffix(f".{os.getpid()}.tmp")
        try:
            self.indice.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dados, f, ensure_ascii=False)
            os.replace(tmp, self.indice)
        except OSError as e:
            # o índice segue na memória desta instância
            self.ultimo_erro = f"não deu para gravar o índice: {e}"
            try:
                tmp.unlink()
            except OSError:
                pass

    def _notas_na_pasta(self) -> dict[str, Path]:
        achadas = {}
        for p in self.pasta.rglob("*.md"):
            rel = p.relative_to(self.pasta)
            if PASTA_FORA in rel.parts:
                continue
            achadas[rel.as_posix()] = p
        return achadas

    def indexar(self, timeout: float | None = None) -> int:
        """Atualiza o índice; devolve quantas notas foram recalculadas.
        `timeout` vale para cada chamada ao Ollama (padrão: self.timeout).
        Levanta exceção se o embed falhar."""
        with self._trava:
            self.ultimo_erro = None
            try:
                return self._indexar(timeout)
            except Exception as e:
                self.ultimo_erro = f"falha ao indexar: {e}"
                raise

    def _indexar(self, timeout: float | None) -> int:
        if not self.pasta.is_dir():
            return 0  # pasta fora do ar: não apaga o índice
        antigas = self._carregar()["notas"]  # nunca alterado: a busca lê sem trava
        achadas = self._notas_na_pasta()
        novas = {}

        falha = None
        for chave, caminho in achadas.items():
            try:
                mtime = caminho.stat().st_mtime
                if chave in antigas and antigas[chave].get("mtime") == mtime:
                    continue
                texto = caminho.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                # nota ilegível: pula, as outras seguem
                self.ultimo_erro = f"nota ilegível: {chave}"
                continue
            trechos = dividir(texto, caminho.stem)
            docs = [f"search_document: {caminho.stem} — {t['titulo']}\n{t['texto']}"
                    for t in trechos]
            try:
                vetores = self._vetores(docs, timeout) if docs else []
            except Exception as e:
                falha = e  # guarda o que já foi calculado antes de propagar
                break
            novas[chave] = {"mtime": mtime, "trechos": [
                {"titulo": t["titulo"], "texto": t["texto"], "vetor": v}
                for t, v in zip(trechos, vetores)]}

        apagadas = [k for k in antigas if k not in achadas]
        if novas or apagadas:
            # monta um dicionário novo e troca a referência de uma vez
            notas = {k: v for k, v in antigas.items() if k in achadas}
            notas.update(novas)
            dados = {"versao": VERSAO, "modelo": self.modelo, "notas": notas}
            with self._trava_dados:
                self._dados = dados
            erro = self.ultimo_erro
            self._gravar(dados)
            self.ultimo_erro = self.ultimo_erro or erro
        if falha is not None:
            raise falha
        return len(novas)

    # -- indexação em segundo plano -------------------------------------
    def _indexar_fundo(self) -> None:
        with self._trava:
            try:
                self._indexar(TIMEOUT_INDEXAR)
            except Exception as e:
                self.ultimo_erro = f"falha ao indexar: {e}"

    def _atualizar_em_fundo(self) -> None:
        """Dispara uma reindexação com timeout longo, se nenhuma estiver
        rodando. O timeout longo também deixa o Ollama recarregar o modelo."""
        with self._trava_fundo:
            if self._fundo is not None and self._fundo.is_alive():
                return
            self._fundo = threading.Thread(target=self._indexar_fundo,
                                           name="memoria-indexar", daemon=True)
            self._fundo.start()

    def aguardar_indexacao(self, timeout: float | None = None) -> bool:
        """Espera a reindexação em segundo plano; True se não há nenhuma rodando."""
        fundo = self._fundo
        if fundo is None:
            return True
        fundo.join(timeout)
        return not fundo.is_alive()

    # -- busca -----------------------------------------------------------
    def buscar(self, pergunta: str, n: int = 4, minimo: float | None = None,
               atualizar: bool = True) -> list[dict]:
        """Trechos mais próximos da pergunta, do melhor para o pior.
        Só vetoriza a pergunta (até self.timeout) e compara com o índice que
        já está na memória; a reindexação roda em segundo plano (atualizar).
        Nunca levanta exceção; o motivo da falha fica em `ultimo_erro`."""
        try:
            self.ultimo_erro = None
            self.ultima_busca_falhou = False
            if not pergunta or not pergunta.strip():
                return []
            try:
                vetor = self._vetores([f"search_query: {pergunta.strip()}"])[0]
            except Exception as e:
                self.ultima_busca_falhou = True
                self.ultimo_erro = f"falha ao vetorizar a pergunta: {e}"
                return []
            finally:
                if atualizar:
                    self._atualizar_em_fundo()
            notas = self._carregar()["notas"]  # retrato: não espera a indexação
            achados = []
            for chave, nota in notas.items():
                for t in nota.get("trechos", []):
                    score = cosseno(vetor, t["vetor"])
                    if minimo is None or score >= minimo:
                        achados.append({"nota": chave, "titulo": t["titulo"],
                                        "texto": t["texto"], "score": score})
            achados.sort(key=lambda x: x["score"], reverse=True)
            return achados[:n]
        except Exception as e:
            self.ultimo_erro = f"falha na busca: {e}"
            return []


# -- integração ------------------------------------------------------------
TITULO_BLOCO = "\n\n## Notas que podem ajudar (segundo cérebro)\n"


def bloco_contexto(trechos: list[dict], limite: int = 1500) -> str:
    """Monta o bloco de contexto com os trechos achados. O que vem depois do
    título nunca passa de `limite` caracteres; o último trecho é cortado."""
    if not trechos:
        return ""
    corpo = ""
    for t in trechos:
        cabecalho = f"### {t['nota']} — {t['titulo']}\n"
        sobra = limite - len(corpo)
        if sobra <= len(cabecalho):
            break
        corpo += (cabecalho + t["texto"] + "\n")[:sobra]
    return TITULO_BLOCO + corpo if corpo else ""


def _ler_env(arquivo: Path) -> dict[str, str]:
    valores = {}
    try:
        linhas = arquivo.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return valores
    for linha in linhas:
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        valores[chave.strip()] = valor.strip().strip('"').strip("'")
    return valores


def de_env(raiz: Path, timeout: float = 3) -> tuple["Memoria | None", dict]:
    """Lê `raiz/.env` e monta a Memoria. Sem JARVIS_CEREBRO devolve None.
    `timeout` é o limite para vetorizar a pergunta (3 s na voz)."""
    raiz = Path(raiz)
    env = _ler_env(raiz / ".env")
    try:
        minimo = float(env.get("JARVIS_MEMORIA_MINIMO") or 0.55)
    except ValueError:
        minimo = 0.55
    config = {"ligada": env.get("JARVIS_MEMORIA") != "0", "minimo": minimo}
    cerebro = env.get("JARVIS_CEREBRO")
    if not cerebro:
        return None, config
    mem = Memoria(Path(cerebro), raiz / ".estado" / "memoria.json",
                  modelo=env.get("JARVIS_MEMORIA_MODELO") or MODELO_PADRAO,
                  timeout=timeout)
    return mem, config
