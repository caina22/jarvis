import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import memoria

EIXOS = ["preço", "klog", "motorola"]


class TestDividir(unittest.TestCase):
    def test_dividir_por_titulos(self):
        r = memoria.dividir("intro\n## A\nx\n## B\n\n## C\ny", "Nota")
        self.assertEqual(r, [
            {"titulo": "Nota", "texto": "intro"},
            {"titulo": "A", "texto": "x"},
            {"titulo": "C", "texto": "y"},
        ])

    def test_dividir_corta_em_2000(self):
        r = memoria.dividir("## A\n" + "z" * 5000, "Nota")
        self.assertEqual(len(r[0]["texto"]), 2000)

    def test_cosseno_norma_zero(self):
        self.assertEqual(memoria.cosseno([0.0, 0.0], [1.0, 2.0]), 0.0)
        self.assertAlmostEqual(memoria.cosseno([1.0, 0.0], [1.0, 0.0]), 1.0)


class TestMemoria(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name) / "cerebro"
        self.pasta.mkdir()
        self.indice = Path(self.tmp.name) / "indice.json"
        self.chamadas = []
        self.nota("Preços.md", "# Preços\n## Valores\nO preço do plano é 50.")
        self.nota("KLog/KLog.md", "# KLog\n## Sobre\nO klog registra tudo.")
        self.mem = self.nova()

    def embed_falso(self, textos):
        self.chamadas.append(list(textos))
        return [[1.0 if p in t.lower() else 0.0 for p in EIXOS] + [0.1]
                for t in textos]

    def nova(self, embed=None):
        return memoria.Memoria(self.pasta, self.indice,
                               embed=embed or self.embed_falso)

    def nota(self, rel, conteudo):
        p = self.pasta / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(conteudo, str):
            conteudo = conteudo.encode("utf-8")
        p.write_bytes(conteudo)
        return p

    def ler_indice(self):
        return json.loads(self.indice.read_text(encoding="utf-8"))

    def test_bom_removido(self):
        self.nota("Agora.md", "﻿# Agora\n## Em andamento\nx")
        self.mem.indexar()
        for nota in self.ler_indice()["notas"].values():
            for t in nota["trechos"]:
                self.assertNotIn("﻿", t["titulo"])
                self.assertNotIn("﻿", t["texto"])

    def test_diario_fora(self):
        self.nota("Diário/2026-11-05.md", "# Dia\n## X\nalgo")
        self.mem.indexar()
        self.assertFalse([k for k in self.ler_indice()["notas"]
                          if k.startswith("Diário/")])

    def test_chave_com_barra_e_acento(self):
        self.nota("Studio P3D/Studio P3D.md", "# Studio\n## A\nx")
        self.mem.indexar()
        self.assertIn("Studio P3D/Studio P3D.md", self.ler_indice()["notas"])

    def test_prefixos(self):
        self.mem.indexar()
        for lote in self.chamadas:
            for t in lote:
                self.assertTrue(t.startswith("search_document: "))
        self.chamadas.clear()
        self.mem.buscar("qual o preço?")
        self.assertTrue(self.chamadas[-1][0].startswith("search_query: "))

    def test_so_reindexa_alterada(self):
        self.assertEqual(self.mem.indexar(), 2)
        self.assertEqual(self.mem.indexar(), 0)
        p = self.nota("Preços.md", "# Preços\n## Valores\nmudou o preço.")
        st = p.stat()
        os.utime(p, (st.st_atime, st.st_mtime + 10))
        self.assertEqual(self.mem.indexar(), 1)

    def test_remove_apagada(self):
        self.mem.indexar()
        (self.pasta / "KLog/KLog.md").unlink()
        self.mem.indexar()
        self.assertNotIn("KLog/KLog.md", self.ler_indice()["notas"])

    def test_ordem_e_minimo(self):
        r = self.mem.buscar("qual o preço?")
        self.assertEqual(r[0]["nota"], "Preços.md")
        r = self.mem.buscar("qual o preço?", minimo=0.99)
        self.assertNotIn("KLog/KLog.md", [x["nota"] for x in r])

    def test_indice_de_outro_modelo_refeito(self):
        self.indice.write_text(json.dumps(
            {"versao": 1, "modelo": "outro", "notas": {}}), encoding="utf-8")
        self.assertEqual(self.mem.indexar(), 2)

    def test_indice_corrompido_refeito(self):
        self.indice.write_text("{lixo", encoding="utf-8")
        self.assertEqual(self.mem.indexar(), 2)

    def test_ollama_fora_devolve_vazio(self):
        def falha(textos):
            raise urllib.error.URLError("fora")
        mem = self.nova(falha)
        self.assertEqual(mem.buscar("x"), [])
        self.assertIsNotNone(mem.ultimo_erro)

    def test_ollama_cai_depois_usa_indice_antigo(self):
        self.mem.indexar()

        def falha_docs(textos):
            if textos[0].startswith("search_document:"):
                raise urllib.error.URLError("caiu")
            return self.embed_falso(textos)
        mem = self.nova(falha_docs)
        p = self.nota("Preços.md", "# Preços\n## Valores\nmudou o preço.")
        st = p.stat()
        os.utime(p, (st.st_atime, st.st_mtime + 10))
        self.assertTrue(mem.buscar("preço"))
        self.assertIsNotNone(mem.ultimo_erro)

    def test_pergunta_vazia(self):
        self.assertEqual(self.mem.buscar("   "), [])
        self.assertEqual(self.chamadas, [])

    def test_nota_ilegivel_pulada(self):
        original = Path.read_text

        def lê(self_, *a, **k):
            if self_.name == "KLog.md":
                raise OSError("ilegível")
            return original(self_, *a, **k)
        with mock.patch.object(Path, "read_text", lê):
            self.mem.indexar()
        self.assertIn("Preços.md", self.ler_indice()["notas"])

    def test_falha_ao_gravar_nao_derruba(self):
        with mock.patch("os.replace", side_effect=PermissionError):
            r = self.mem.buscar("preço")
        self.assertTrue(r)
        self.assertIn("grava", self.mem.ultimo_erro)

    def test_atributos_publicos(self):
        self.assertEqual(self.mem.pasta, self.pasta)
        self.assertEqual(self.mem.indice, self.indice)
        self.assertEqual(self.mem.modelo, memoria.MODELO_PADRAO)
        self.assertEqual(self.mem.url, memoria.URL_PADRAO)
        self.assertEqual(self.mem.timeout, 3)


if __name__ == "__main__":
    unittest.main()
