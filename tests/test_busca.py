import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import busca
import memoria

try:
    from test_memoria import vetor_falso
except ImportError:
    from tests.test_memoria import vetor_falso


def falha(textos):
    raise OSError("sem ollama")


class TestBusca(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pasta = Path(tmp.name) / "cerebro"
        self.pasta.mkdir()
        (self.pasta / "Preços.md").write_text(
            "## Valores\nO preço é 50.", encoding="utf-8")
        (self.pasta / "KLog.md").write_text(
            "## Sobre\nO klog registra.", encoding="utf-8")
        self.indice = Path(tmp.name) / "indice.json"

    def mem(self, embed):
        return memoria.Memoria(self.pasta, self.indice, embed=embed)

    def rodar(self, argv, mem):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            codigo = busca.main(argv, mem)
        return codigo, buf.getvalue()

    def test_imprime_resultados(self):
        codigo, saida = self.rodar(["preço"], self.mem(vetor_falso))
        self.assertEqual(codigo, 0)
        self.assertIn("Preços.md", saida)
        self.assertLess(saida.index("Preços.md"), saida.index("KLog.md"))

    def test_sem_argumento(self):
        codigo, saida = self.rodar([], self.mem(vetor_falso))
        self.assertEqual(codigo, 2)
        self.assertIn("uso: python busca.py", saida)

    def test_indisponivel(self):
        codigo, saida = self.rodar(["x"], self.mem(falha))
        self.assertEqual(codigo, 1)
        self.assertIn("ollama pull nomic-embed-text", saida)
