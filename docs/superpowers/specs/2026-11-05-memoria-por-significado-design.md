# Memória por significado (item 1 da "rede neural" do JARVIS)

Data: 2026-11-05. Plano geral e registro de alterações: `Segundo Cérebro/Projetos/JARVIS - Rede Neural.md`.

## Objetivo

O JARVIS acha sozinho a nota certa do segundo cérebro pelo significado, sem precisar do nome exato, e entrega o trecho ao Claude junto do pedido.

**Pronto quando:** a pergunta "o que combinamos sobre o preço do Studio P3D?" traz a nota do Studio P3D no topo, sem citar o nome da nota.

## Restrições

- Tudo local e leve. A RTX 3050 de 6 GB é dividida com o VoiceStudio e o Qwen, então os embeddings rodam em CPU.
- O disco está em 97% (17 GB livres). Nenhuma biblioteca nova: só a biblioteca padrão do Python e o Ollama que já está instalado.
- A memória nunca trava o JARVIS. Se ela falhar, o pedido segue para o Claude sem o bloco de notas.

## Fora do escopo

- Grafo na tela (item 2) e agentes especializados (item 3): cada um terá spec própria.
- Indexar a pasta `Diário`.
- Mudar o que `contexto_cerebro()` manda no início da sessão (`00 Agora.md`, `Preferências.md`, lista de notas, fim do diário).

## Decisões

- O Claude usa a busca de duas formas: **automática** (o servidor anexa trechos a cada pedido) e **por comando** (`busca.py`, quando ele quiser procurar mais).
- O índice fica num arquivo JSON em `.estado/memoria.json` (abordagem A). ChromaDB e servidor MCP foram descartados: dependências pesadas para o disco cheio e o Termux, e um MCP não resolveria o modo automático.
- Modelo de embeddings: `nomic-embed-text` no Ollama (~274 MB), chamado em `http://127.0.0.1:11434/api/embed`.

## Peças

### `memoria.py` (novo, na raiz do repositório)

Não importa nada do `server.py`. Recebe a pasta do cérebro e o caminho do índice por parâmetro.

- **Divisão em trechos:** cada nota `.md` do cérebro (exceto a pasta `Diário`) é dividida pelos títulos `##`. O texto antes do primeiro `##` vira um trecho com o título da nota. Trecho vazio é ignorado. Trechos com mais de 2.000 caracteres são cortados nesse limite antes de gerar o vetor.
- **Texto enviado ao modelo:** `search_document: <nome da nota> — <título do trecho>\n<texto>`. A pergunta vai como `search_query: <pergunta>`. Esses prefixos são os que o `nomic-embed-text` espera.
- **`indexar(pasta, caminho_indice)`:** compara a data de modificação de cada nota com a do índice. Só recalcula as notas novas ou alteradas e remove do índice as notas apagadas. Grava o resultado num arquivo temporário e depois renomeia, para o índice nunca ficar pela metade. Uma trava (`threading.Lock`) protege a escrita.
- **`buscar(pergunta, n=4, minimo=None)`:** chama `indexar()` antes (o que mantém o índice em dia sem precisar de vigia), gera o vetor da pergunta, calcula a similaridade de cosseno com cada trecho em Python puro e devolve os `n` melhores, em ordem decrescente: `[{"nota", "titulo", "texto", "score"}]`. Se `minimo` for passado, descarta os que ficarem abaixo dele.
- **Formato do índice:**
  ```json
  {"versao": 1, "modelo": "nomic-embed-text",
   "notas": {"Studio P3D/Studio P3D.md": {"mtime": 1730770000.0,
     "trechos": [{"titulo": "...", "texto": "...", "vetor": [0.01, ...]}]}}}
  ```
  Se `versao` ou `modelo` não baterem, ou o JSON não puder ser lido, o índice é apagado e refeito do zero.

### `busca.py` (novo, na raiz do repositório)

Linha de comando para o Claude: `python busca.py "pergunta"`. Lê a pasta do cérebro do `.env` (`JARVIS_CEREBRO`) e imprime os 5 melhores trechos, cada um com a nota, o título, o score e as primeiras linhas do texto. Se a memória estiver indisponível, imprime o motivo e o comando para resolver (`ollama pull nomic-embed-text`) e sai com código 1.

O `persona.md` ganha uma linha dizendo que, para lembrar algo do segundo cérebro que não veio no bloco automático, o Claude deve rodar `busca.py` antes de abrir as notas uma a uma. O `persona.example.md` ganha a mesma linha.

### Mudanças no `server.py`

- **Ao ligar:** uma thread em segundo plano chama `memoria.indexar()`, para que o primeiro pedido não espere a indexação inicial (10 a 30 segundos em CPU).
- **Em `SessaoClaude.perguntar()`:** antes de montar a mensagem, chama `memoria.buscar(texto, n=3, minimo=LIMITE)`. Se vierem trechos, acrescenta ao texto um bloco `## Notas que podem ajudar (segundo cérebro)` com nota, título e texto de cada um, limitado a 1.500 caracteres no total. Depois emite dois eventos pelo `EVENTOS.emitir()` que já existe: `{"tipo": "memoria", "notas": [...]}`, que a tela ainda ignora e que o grafo do item 2 vai usar, e um `{"tipo": "passo", "passo": "ferramenta", ...}` com o rótulo "Consultou: <notas>", que o painel de atividade já sabe mostrar sem mexer em `web/`.
- **Limite de similaridade (`LIMITE`):** lido de `JARVIS_MEMORIA_MINIMO` no `.env`. O valor padrão começa em 0,55 e é ajustado no teste real. O `.env.example` ganha essa variável, `JARVIS_MEMORIA_MODELO` (padrão `nomic-embed-text`) e `JARVIS_CEREBRO`, que hoje falta nele.
- **Desligar a memória:** `JARVIS_MEMORIA=0` no `.env` pula a busca automática.

## Falhas

| Situação | Comportamento |
|---|---|
| Ollama desligado ou modelo não baixado | `buscar()` desiste em até 3 s e devolve lista vazia. Registra no log e mostra "memória indisponível" no painel, no máximo uma vez a cada 5 minutos. |
| Índice corrompido, de outra versão ou de outro modelo | Apaga e refaz do zero. |
| Nenhum trecho acima do limite | Não anexa nada. |
| Pedidos simultâneos | A trava de escrita e a gravação por arquivo temporário evitam índice pela metade. |
| Erro inesperado dentro da busca | Capturado em `perguntar()`. O pedido segue sem o bloco e o erro vai para o log. |

## Testes

- **Automáticos (`tests/test_memoria.py`, com `unittest`):** substituem a chamada ao Ollama por uma função de mentira que devolve vetores fixos. Conferem:
  - divisão por `##`, incluindo o trecho antes do primeiro título e a pasta `Diário` fora do índice;
  - reindexação só da nota alterada e remoção da nota apagada;
  - ordem dos resultados por similaridade e o corte pelo `minimo`;
  - índice de outra versão ou modelo sendo refeito;
  - Ollama fora do ar devolvendo lista vazia, sem exceção.
- **Teste real:** com o modelo baixado, rodar `busca.py` com a pergunta do "pronto quando" e com mais cinco perguntas sobre outras notas (KLog, Motorola One, preferências, tarefas, JARVIS no celular), sem citar o nome das notas. Anotar os acertos e o limite escolhido no registro do `JARVIS - Rede Neural.md`.
- **Teste no JARVIS:** depois de reiniciar o servidor, fazer a pergunta do Studio P3D por voz e conferir no painel de atividade a linha "Consultou: Studio P3D/Studio P3D.md".

## Antes de começar

- `ollama pull nomic-embed-text` (~274 MB). O `gemma2:2b` (1,6 GB) continua instalado e pode ser removido se faltar espaço.
- O servidor roda como administrador e só o Cainã consegue reiniciá-lo. As mudanças no `server.py` valem depois que ele fechar e abrir o JARVIS.
