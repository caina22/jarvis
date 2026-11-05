"""Busca por significado no segundo cérebro: python busca.py "pergunta"."""

import sys
from pathlib import Path

import memoria

RAIZ = Path(__file__).resolve().parent
DICA = "Confira se o Ollama está aberto e rode: ollama pull nomic-embed-text"


def main(argv: list[str], mem: "memoria.Memoria | None" = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass  # saída trocada (ex.: nos testes)
    if not argv:
        print('uso: python busca.py "pergunta"')
        return 2
    if mem is None:
        mem, _ = memoria.de_env(RAIZ)
        if mem is None:
            print("JARVIS_CEREBRO não está definido no .env; "
                  "não sei onde fica o segundo cérebro.")
            return 1
    achados = mem.buscar(" ".join(argv), n=5)
    if not achados:
        if mem.ultimo_erro:
            print(f"Memória indisponível: {mem.ultimo_erro}\n{DICA}")
            return 1
        print("Nada parecido no segundo cérebro.")
        return 0
    for a in achados:
        print(f"{a['score']:.2f}  {a['nota']} — {a['titulo']}")
        for linha in a["texto"].splitlines()[:3]:
            print(f"    {linha}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
