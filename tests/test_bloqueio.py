import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server


class TestBloqueioDeSenha(unittest.TestCase):
    def test_espera_cresce_e_para_no_teto_de_15_minutos(self):
        self.assertEqual([server.espera_apos(n) for n in (1, 2, 3)], [30, 60, 240])
        self.assertEqual(server.espera_apos(4), 900)
        self.assertEqual(server.espera_apos(10), 900)
        self.assertEqual(server.espera_apos(500), 900)  # sem estourar com muitos erros

    def _com_arquivo_temporario(self):
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        arq = Path(pasta.name) / "tentativas.json"
        for alvo, valor in (("TENTATIVAS_ARQ", arq),):
            p = mock.patch.object(server, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(server, "log")
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.dict(server.TENTATIVAS, {"falhas": 0, "bloqueado_ate": 0.0})
        p.start()
        self.addCleanup(p.stop)
        return arq

    def test_acesso_do_proprio_pc_zera_o_bloqueio(self):
        arq = self._com_arquivo_temporario()
        server.TENTATIVAS["falhas"] = 6
        server.TENTATIVAS["bloqueado_ate"] = 9e12
        self.assertTrue(server.destravar_tentativas())
        self.assertEqual((server.TENTATIVAS["falhas"], server.TENTATIVAS["bloqueado_ate"]), (0, 0.0))
        self.assertEqual(json.loads(arq.read_text(encoding="utf-8")), {"falhas": 0, "bloqueado_ate": 0.0})

    def test_sem_bloqueio_nao_escreve_nada(self):
        arq = self._com_arquivo_temporario()
        self.assertFalse(server.destravar_tentativas())
        self.assertFalse(arq.exists())


if __name__ == "__main__":
    unittest.main()
