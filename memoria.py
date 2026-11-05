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
        self._embed = embed or self._embed_ollama
        self.ultimo_erro: str | None = None
        self._dados: dict | None = None  # índice em memória
        self._trava = threading.Lock()

    # -- vetores ---------------------------------------------------------
    def _embed_ollama(self, textos: list[str]) -> list[list[float]]:
        corpo = json.dumps({"model": self.modelo, "input": textos,
                            "keep_alive": "30m"}).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=corpo, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))["embeddings"]

    def _vetores(self, textos: list[str]) -> list[list[float]]:
        vetores = self._embed(textos)
        if len(vetores) != len(textos):
            raise ValueError("o Ollama devolveu vetores a menos")
        return vetores

    # -- índice ----------------------------------------------------------
    def _vazio(self) -> dict:
        return {"versao": VERSAO, "modelo": self.modelo, "notas": {}}

    def _carregar(self) -> dict:
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
        tmp = self.indice.with_suffix(".tmp")
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

    def indexar(self) -> int:
        """Atualiza o índice; devolve quantas notas foram recalculadas.
        Levanta exceção se o embed falhar."""
        with self._trava:
            self.ultimo_erro = None
            try:
                return self._indexar()
            except Exception as e:
                self.ultimo_erro = f"falha ao indexar: {e}"
                raise

    def _indexar(self) -> int:
        if not self.pasta.is_dir():
            return 0  # pasta fora do ar: não apaga o índice
        dados = self._carregar()
        notas = dados["notas"]
        achadas = self._notas_na_pasta()
        novas, mudou = {}, False

        falha = None
        for chave, caminho in achadas.items():
            try:
                mtime = caminho.stat().st_mtime
                if chave in notas and notas[chave].get("mtime") == mtime:
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
                vetores = self._vetores(docs) if docs else []
            except Exception as e:
                falha = e  # guarda o que já foi calculado antes de propagar
                break
            novas[chave] = {"mtime": mtime, "trechos": [
                {"titulo": t["titulo"], "texto": t["texto"], "vetor": v}
                for t, v in zip(trechos, vetores)]}

        apagadas = [k for k in notas if k not in achadas]
        if novas or apagadas:
            notas.update(novas)
            for k in apagadas:
                del notas[k]
            erro = self.ultimo_erro
            self._gravar(dados)
            self.ultimo_erro = self.ultimo_erro or erro
        if falha is not None:
            raise falha
        return len(novas)

    # -- busca -----------------------------------------------------------
    def buscar(self, pergunta: str, n: int = 4,
               minimo: float | None = None) -> list[dict]:
        """Trechos mais próximos da pergunta, do melhor para o pior.
        Nunca levanta exceção; o motivo da falha fica em `ultimo_erro`."""
        try:
            self.ultimo_erro = None
            if not pergunta or not pergunta.strip():
                return []
            try:
                self.indexar()
            except Exception:
                pass  # ultimo_erro já preenchido; segue com o índice que há
            try:
                vetor = self._vetores([f"search_query: {pergunta.strip()}"])[0]
            except Exception as e:
                self.ultimo_erro = f"falha ao vetorizar a pergunta: {e}"
                return []
            with self._trava:
                notas = self._carregar()["notas"]
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
