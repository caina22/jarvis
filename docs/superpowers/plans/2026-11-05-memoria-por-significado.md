# Memória por significado — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O JARVIS acha a nota certa do segundo cérebro pelo significado e entrega o trecho ao Claude junto do pedido, além de oferecer o comando `busca.py`.

**Architecture:** Um módulo independente, `memoria.py`, divide as notas por `##`, pede os vetores ao Ollama (`nomic-embed-text`) e guarda tudo em `.estado/memoria.json`; a busca é cosseno em Python puro. O `busca.py` é a linha de comando sobre ele. O `server.py` só faz a ligação: indexa ao ligar e anexa os trechos em `SessaoClaude.perguntar()`.

**Tech Stack:** Python 3.12 (só biblioteca padrão: `json`, `urllib.request`, `math`, `threading`, `unittest`), Ollama local.

**Spec:** `docs/superpowers/specs/2026-11-05-memoria-por-significado-design.md`

## Global Constraints

- Nenhuma biblioteca nova; só a biblioteca padrão do Python.
- `memoria.py` não importa nada do `server.py`.
- Modelo padrão `nomic-embed-text`, endpoint `http://127.0.0.1:11434/api/embed`.
- Prefixos: notas `search_document: <nome da nota> — <título do trecho>\n<texto>`; pergunta `search_query: <pergunta>`.
- Trecho cortado em 2.000 caracteres antes de gerar o vetor.
- Índice em `.estado/memoria.json`, formato `{"versao": 1, "modelo": ..., "notas": {<caminho relativo com "/">: {"mtime", "trechos": [{"titulo", "texto", "vetor"}]}}}`.
- Pasta `Diário` fica fora do índice.
- Busca automática: `n=3`, bloco limitado a 1.500 caracteres, título `## Notas que podem ajudar (segundo cérebro)`.
- `busca.py` mostra 5 resultados; sai com código 1 se a memória estiver indisponível, com a dica `ollama pull nomic-embed-text`.
- Variáveis no `.env`: `JARVIS_MEMORIA` (0 desliga), `JARVIS_MEMORIA_MINIMO` (padrão 0.55), `JARVIS_MEMORIA_MODELO` (padrão `nomic-embed-text`), `JARVIS_CEREBRO`.
- Timeout da busca: 3 s. Aviso "memória indisponível" no painel no máximo uma vez a cada 5 minutos.
- A memória nunca trava o JARVIS: qualquer falha vira "sem bloco".
- `persona.md` está no `.gitignore`: editar, mas commitar só o `persona.example.md`.

## Review Focus

- **Nota com BOM** (o `00 Agora.md` começa com `\ufeff`): ler com `utf-8-sig`, sem o BOM aparecer no título nem no texto. Teste na Task 1.
- **Nomes com acento e espaço** (`Segundo Cérebro`, `Studio P3D/Studio P3D.md`): a chave do índice usa `/` mesmo no Windows e o JSON grava acentos sem quebrar. Teste na Task 1.
- **Ollama cai depois do índice pronto:** a busca não pode descartar o índice que já existe. Se `indexar()` falhar dentro de `buscar()`, a busca usa o índice antigo; só devolve `[]` se o vetor da pergunta também falhar. Teste na Task 1.
- **`busca.py` e o servidor gravando o índice ao mesmo tempo:** a trava é só por processo, e no Windows o `os.replace` falha com `PermissionError` se o outro processo estiver lendo o arquivo. A falha de gravação não pode derrubar a busca: o índice fica na memória e a próxima indexação tenta gravar de novo. Teste na Task 1.
- **Nota apagada ou travada no meio da indexação** (OneDrive sincronizando): `OSError` na leitura pula a nota sem derrubar a indexação. Teste na Task 1.

---

### Task 1: Núcleo `memoria.py` (divisão, índice e busca)

**Files:**
- Create: `memoria.py`
- Test: `tests/__init__.py` (vazio), `tests/test_memoria.py`

**Interfaces:**
- Produces:
  - `VERSAO = 1`, `MODELO_PADRAO = "nomic-embed-text"`, `URL_PADRAO = "http://127.0.0.1:11434/api/embed"`
  - `dividir(texto: str, nome: str) -> list[dict]` — cada item `{"titulo": str, "texto": str}`. O texto antes do primeiro `## ` vira um trecho com `titulo=nome`; trechos vazios somem; `texto` cortado em 2.000 caracteres.
  - `cosseno(a: list[float], b: list[float]) -> float` — 0.0 se algum vetor tiver norma zero.
  - `class Memoria(pasta: Path, indice: Path, modelo: str = MODELO_PADRAO, url: str = URL_PADRAO, embed: Callable[[list[str]], list[list[float]]] | None = None, timeout: float = 3)`
    - `embed` injetável; o padrão faz POST `{"model": modelo, "input": textos, "keep_alive": "30m"}` em `url` e devolve `resposta["embeddings"]`.
    - `indexar() -> int` — número de notas recalculadas. Levanta exceção se o `embed` falhar.
    - `buscar(pergunta: str, n: int = 4, minimo: float | None = None) -> list[dict]` — itens `{"nota": str, "titulo": str, "texto": str, "score": float}`, ordem decrescente. Nunca levanta exceção.
    - `ultimo_erro: str | None` — motivo da última falha de `buscar()`/`indexar()`, `None` depois de um sucesso.

- [ ] **Step 1: Escrever os testes**

`tests/test_memoria.py` com `unittest`, usando `tempfile.TemporaryDirectory()` como cérebro e um `embed` falso que gera vetores determinísticos por palavra-chave, por exemplo:

```python
EIXOS = ["preço", "klog", "motorola"]
def embed_falso(textos):
    chamadas.append(list(textos))
    return [[1.0 if p in t.lower() else 0.0 for p in EIXOS] + [0.1] for t in textos]
```

Testes e asserções:
- `test_dividir_por_titulos`: `dividir("intro\n## A\nx\n## B\n\n## C\ny", "Nota")` → `[{"titulo": "Nota", "texto": "intro"}, {"titulo": "A", "texto": "x"}, {"titulo": "C", "texto": "y"}]`.
- `test_dividir_corta_em_2000`: trecho com 5.000 caracteres → `len(texto) == 2000`.
- `test_bom_removido`: nota gravada como `"\ufeff# Agora\n## Em andamento\nx"` em bytes UTF-8 → nenhum título ou texto do índice contém `"\ufeff"`.
- `test_diario_fora`: `Diário/2026-11-05.md` existe → nenhuma chave do índice começa com `"Diário/"`.
- `test_chave_com_barra_e_acento`: `Studio P3D/Studio P3D.md` → chave `"Studio P3D/Studio P3D.md"` presente no JSON lido de volta com `encoding="utf-8"`.
- `test_prefixos`: os textos mandados ao `embed` na indexação começam com `"search_document: "`; o da pergunta com `"search_query: "`.
- `test_so_reindexa_alterada`: `indexar()` → 2; nada muda → `indexar()` == 0; edita uma nota e avança o `mtime` com `os.utime` → `indexar()` == 1.
- `test_remove_apagada`: apaga uma nota, `indexar()` → a chave sumiu do índice.
- `test_ordem_e_minimo`: notas sobre preço e KLog; `buscar("qual o preço?")[0]["nota"]` é a do preço; com `minimo=0.99` a da KLog não aparece.
- `test_indice_de_outro_modelo_refeito`: grava índice com `"modelo": "outro"` → `indexar()` recalcula todas as notas.
- `test_indice_corrompido_refeito`: grava `"{lixo"` no índice → `indexar()` recalcula todas, sem exceção.
- `test_ollama_fora_devolve_vazio`: `embed` que levanta `urllib.error.URLError` → `buscar("x") == []` e `ultimo_erro` não é `None`.
- `test_ollama_cai_depois_usa_indice_antigo`: indexa com o falso; troca por um `embed` que falha só quando recebe textos `search_document:`; edita uma nota → `buscar("preço")` ainda devolve resultado do índice antigo.
- `test_pergunta_vazia`: `buscar("   ") == []` e `embed` não foi chamado.
- `test_nota_ilegivel_pulada`: `unittest.mock.patch` em `Path.read_text` que levanta `OSError` só para uma das notas → `indexar()` não levanta e as outras notas entram no índice.
- `test_falha_ao_gravar_nao_derruba`: `unittest.mock.patch("os.replace", side_effect=PermissionError)` → `buscar("preço")` ainda devolve resultado (índice fica na memória) e `ultimo_erro` cita a gravação.

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m unittest tests.test_memoria -v`
Expected: erro `ModuleNotFoundError: No module named 'memoria'`.

- [ ] **Step 3: Implementar `memoria.py`**

Notas listadas com `pasta.rglob("*.md")`, excluindo qualquer caminho com `"Diário"` em `relative_to(pasta).parts`; chave = `relative_to(pasta).as_posix()`; leitura com `encoding="utf-8-sig"`. Uma `threading.Lock` por instância envolve todo o `indexar()`. Gravação: `json.dump(..., ensure_ascii=False)` num `indice.with_suffix(".tmp")` e depois `os.replace`; `OSError` na gravação só preenche `ultimo_erro` (o índice segue na memória da instância). Índice que não abre, ou com `versao`/`modelo` diferentes, vira `{"versao": VERSAO, "modelo": modelo, "notas": {}}`. Em `buscar()`: se a pergunta estiver vazia devolve `[]`; chama `indexar()` dentro de `try` (falha só grava `ultimo_erro` e segue com o índice em memória/disco); gera o vetor da pergunta dentro de `try` (falha → `[]`).

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m unittest tests.test_memoria -v`
Expected: todos `ok`.

- [ ] **Step 5: Commit**

```bash
git add memoria.py tests/__init__.py tests/test_memoria.py
git commit -m "Memória por significado: núcleo de índice e busca"
```

---

### Task 2: Bloco de contexto, configuração e comando `busca.py`

**Files:**
- Modify: `memoria.py` (acrescentar duas funções)
- Create: `busca.py`
- Modify: `.env.example`, `persona.example.md:41`, `persona.md:41`
- Test: `tests/test_memoria.py`, `tests/test_busca.py`

**Interfaces:**
- Consumes: `Memoria`, `Memoria.buscar`, `Memoria.ultimo_erro` (Task 1).
- Produces:
  - `bloco_contexto(trechos: list[dict], limite: int = 1500) -> str` — `""` para lista vazia; senão começa com `"\n\n## Notas que podem ajudar (segundo cérebro)\n"` e cada trecho vem como `"### <nota> — <titulo>\n<texto>\n"`. O texto total nunca passa de `limite` caracteres depois do título (o último trecho é cortado, não omitido, se couber pelo menos o cabeçalho dele).
  - `de_env(raiz: Path) -> tuple[Memoria | None, dict]` — lê `raiz/".env"` (linhas `CHAVE=valor`, ignora `#` e aspas), monta `Memoria(Path(JARVIS_CEREBRO), raiz/".estado"/"memoria.json", modelo=JARVIS_MEMORIA_MODELO or MODELO_PADRAO)`; devolve `(None, config)` se `JARVIS_CEREBRO` faltar. `config` contém `"ligada": bool` (`JARVIS_MEMORIA != "0"`) e `"minimo": float` (`JARVIS_MEMORIA_MINIMO`, padrão `0.55`).
  - `busca.main(argv: list[str], mem: Memoria | None = None) -> int` — sem `mem`, usa `de_env(RAIZ)`.

- [ ] **Step 1: Escrever os testes**

Em `tests/test_memoria.py`:
- `test_bloco_vazio`: `bloco_contexto([]) == ""`.
- `test_bloco_formato`: um trecho `{"nota": "Studio P3D/Studio P3D.md", "titulo": "Preços", "texto": "R$ 120/kg", "score": 0.8}` → contém `"## Notas que podem ajudar (segundo cérebro)"` e `"### Studio P3D/Studio P3D.md — Preços\nR$ 120/kg"`.
- `test_bloco_limite`: três trechos de 1.000 caracteres → parte depois do título com `len <= 1500`.
- `test_de_env`: `.env` temporário com `JARVIS_CEREBRO=<pasta>`, `JARVIS_MEMORIA=0`, `JARVIS_MEMORIA_MINIMO=0.7` → `mem.pasta == Path(pasta)`, `config == {"ligada": False, "minimo": 0.7}`; sem `JARVIS_CEREBRO` → `mem is None`.

Em `tests/test_busca.py`, com `contextlib.redirect_stdout`:
- `test_imprime_resultados`: `Memoria` com `embed` falso e duas notas → `main(["preço"], mem) == 0` e a saída contém o nome da nota do preço antes da outra.
- `test_sem_argumento`: `main([], mem) == 2` e a saída tem `"uso: python busca.py"`.
- `test_indisponivel`: `embed` que falha → `main(["x"], mem) == 1` e a saída contém `"ollama pull nomic-embed-text"`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m unittest discover -s tests -v`
Expected: `ImportError` para `bloco_contexto`/`de_env` e `No module named 'busca'`.

- [ ] **Step 3: Implementar `bloco_contexto`, `de_env` e `busca.py`**

`busca.py` chama `mem.buscar(" ".join(argv), n=5)`; para cada resultado imprime `"<score:.2f>  <nota> — <titulo>"` e as 3 primeiras linhas do texto, recuadas. Lista vazia com `ultimo_erro` → mensagem com o erro e a dica, código 1; lista vazia sem erro → `"Nada parecido no segundo cérebro."`, código 0. Antes de imprimir, `sys.stdout.reconfigure(encoding="utf-8")` (o console do Windows quebra acentos).

- [ ] **Step 4: Configuração e persona**

- `.env.example`: acrescentar, com um comentário de uma linha cada, `JARVIS_CEREBRO=`, `JARVIS_MEMORIA=1`, `JARVIS_MEMORIA_MINIMO=0.55`, `JARVIS_MEMORIA_MODELO=nomic-embed-text`.
- `persona.example.md` e `persona.md`, logo depois da linha "Antes de dizer que não sabe": `- Para lembrar algo do segundo cérebro que não veio no bloco "Notas que podem ajudar", rode primeiro \`python busca.py "pergunta"\` na pasta do JARVIS; só depois abra as notas uma a uma.`

- [ ] **Step 5: Rodar e ver passar**

Run: `python -m unittest discover -s tests -v`
Expected: todos `ok`.

- [ ] **Step 6: Commit**

```bash
git add memoria.py busca.py tests/test_memoria.py tests/test_busca.py .env.example persona.example.md
git commit -m "Memória por significado: bloco de contexto, configuração e busca.py"
```

---

### Task 3: Ligação no `server.py`

**Files:**
- Modify: `server.py` (imports perto da linha 33; nova seção depois de `contexto_cerebro()` em `server.py:278-293`; `SessaoClaude.perguntar()` em `server.py:660-682`; `main()` em `server.py:2000`)

**Interfaces:**
- Consumes: `memoria.de_env`, `memoria.bloco_contexto`, `Memoria.buscar`, `Memoria.indexar`, `Memoria.ultimo_erro` (Tasks 1 e 2); `EVENTOS.emitir`, `log` (já existem no `server.py`).
- Produces: `com_memoria(texto: str) -> str` em `server.py`; o evento `{"tipo": "memoria", "notas": [str]}` que o grafo do item 2 vai consumir.

- [ ] **Step 1: Seção de memória no `server.py`**

Depois de `contexto_cerebro()`: `import memoria`; `MEMORIA, MEMORIA_CFG = memoria.de_env(RAIZ)`; e `com_memoria(texto)`:
- Se `MEMORIA` for `None` ou `MEMORIA_CFG["ligada"]` for falso, devolve `texto`.
- Tudo dentro de `try/except Exception` (qualquer erro → `log({"tipo": "memoria_erro", ...})` e devolve `texto`).
- `trechos = MEMORIA.buscar(texto, n=3, minimo=MEMORIA_CFG["minimo"])`.
- Sem trechos e com `MEMORIA.ultimo_erro`: emite `{"tipo": "passo", "passo": "resultado", "id": ..., "erro": True, "texto": "memória indisponível"}` (com um `"passo": "ferramenta"` antes, como em `server.py:953`), no máximo uma vez a cada 300 s (guardar o horário do último aviso numa variável do módulo).
- Com trechos: emite `{"tipo": "memoria", "notas": [...]}` e um passo ferramenta com `"rotulo": "Consultou: " + ", ".join(notas únicas)`; devolve `texto + memoria.bloco_contexto(trechos)`.

- [ ] **Step 2: Usar em `perguntar()` e indexar ao ligar**

- Em `perguntar()`, a primeira linha, antes do `with self.lock:`, passa a ser `texto_final = com_memoria(texto)`; a mensagem usa `texto_final + LEMBRETE`. Assim a busca (até 3 s) roda enquanto a rodada anterior termina, sem segurar o `lock`.
- Em `main()`, antes de subir o `ThreadingHTTPServer`: se `MEMORIA` existe e está ligada, `threading.Thread(target=_indexar_ao_ligar, daemon=True).start()`, onde `_indexar_ao_ligar` cria uma `Memoria` igual com `timeout=120`, chama `indexar()` e registra `log({"tipo": "memoria_indexada", "notas": n})` ou o erro.

- [ ] **Step 3: Conferir que o servidor ainda carrega**

Run: `python -c "import ast,sys; ast.parse(open('server.py',encoding='utf-8').read())" && python -m unittest discover -s tests -v`
Expected: sem saída do `ast` e todos os testes `ok`.

Run: `python -c "import server; print(server.MEMORIA, server.MEMORIA_CFG); print(server.com_memoria('teste')[:200])"`
Expected: imprime a `Memoria` e o config sem exceção (o servidor não sobe, porque `main()` só roda no `__main__`). Com o Ollama sem o modelo, a segunda linha é só `teste`.

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "Memória por significado: servidor anexa notas e indexa ao ligar"
```

---

### Task 4: Teste real, ajuste do limite e registro

**Files:**
- Modify: `.env` (só local, fora do Git) se o limite mudar
- Modify: `C:\Users\caina\OneDrive\Desktop\Projetos\Segundo Cérebro\Projetos\JARVIS - Rede Neural.md` (Estado e Registro de alterações)

**Interfaces:**
- Consumes: `busca.py` (Task 2), `com_memoria` (Task 3).

- [ ] **Step 1: Baixar o modelo**

Run: `ollama pull nomic-embed-text` e depois `ollama list`
Expected: `nomic-embed-text` na lista (~274 MB). Conferir antes com `df -h /c` que há mais de 1 GB livre.

- [ ] **Step 2: Rodar as seis perguntas**

Run, uma de cada vez: `python busca.py "<pergunta>"` com:
1. `o que combinamos sobre o preço do Studio P3D?` → esperado `Studio P3D/Studio P3D.md` no topo.
2. `qual é a ideia daquele marketplace de fretes da faculdade?` → `Projetos/KLog*.md`.
3. `como está a recuperação do celular da Motorola?` → `00 Agora.md` ou `Projetos/JARVIS - Versão Celular.md`.
4. `como eu gosto de ser chamado?` → `Preferências.md`.
5. `o que falta fazer hoje?` → `Tarefas.md`.
6. `qual o plano do aplicativo no telefone?` → `Projetos/JARVIS - Versão Celular.md`.

Expected: a nota esperada em 1.º lugar em pelo menos 5 das 6, e obrigatoriamente na pergunta 1. Se a 1 falhar, parar e relatar ao Cainã antes de mexer no código.

- [ ] **Step 3: Escolher o limite**

Com os scores impressos, rodar também 3 frases sem relação com as notas (`sim`, `que horas são?`, `conta uma piada`). Escolher `JARVIS_MEMORIA_MINIMO` abaixo do score do acerto mais fraco e acima do score mais alto dessas três. Se não existir esse valor, manter 0.55 e anotar o conflito. Gravar o valor no `.env`.

- [ ] **Step 4: Registrar**

No `JARVIS - Rede Neural.md`: marcar `[x]` em "Escolher e baixar o modelo de embeddings" e acrescentar ao Registro de alterações uma linha com a data, os commits, quantas perguntas acertaram, o limite escolhido e o aviso de que o servidor precisa ser reiniciado pelo Cainã para valer. Marcar `[x]` em "1. Memória por significado" só depois do Step 5.

- [ ] **Step 5: Teste no JARVIS (depende do Cainã)**

Pedir ao Cainã que feche e abra o JARVIS (o servidor roda como administrador). Depois, perguntar por voz "o que combinamos sobre o preço do Studio P3D?".
Expected: o painel de atividade (tecla P) mostra `Consultou: Studio P3D/Studio P3D.md` e a resposta usa o conteúdo da nota.
