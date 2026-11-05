import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        mem = memoria.Memoria(self.pasta, self.indice, embed=embed)
        self.addCleanup(mem.aguardar_indexacao, 10)
        return mem

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

    def test_indexa_antes_e_nao_deixa_thread(self):
        mem = self.mem(vetor_falso)
        codigo, saida = self.rodar(["preço"], mem)
        self.assertEqual(codigo, 0)
        self.assertIn("Preços.md", saida)  # índice novo, já na primeira busca
        self.assertTrue(mem.aguardar_indexacao(0))  # nada em segundo plano

    def test_ollama_cai_usa_indice_antigo(self):
        self.mem(vetor_falso).indexar()

        def falha_docs(textos):
            if textos[0].startswith("search_document:"):
                raise OSError("caiu")
            return vetor_falso(textos)
        (self.pasta / "Nova.md").write_text("## A\nmotorola", encoding="utf-8")
        codigo, saida = self.rodar(["preço"], self.mem(falha_docs))
        self.assertEqual(codigo, 0)
        self.assertIn("Preços.md", saida)

    def test_timeout_longo(self):
        with mock.patch.object(memoria, "de_env", return_value=(None, {})) as de_env:
            codigo, _ = self.rodar(["x"], None)
        self.assertEqual(codigo, 1)
        de_env.assert_called_once_with(busca.RAIZ, timeout=30)
