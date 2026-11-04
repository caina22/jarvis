# JARVIS (instalação do Cainã)

Assistente de voz em cima do Claude Code, rodando no PC do Cainã (Studio P3D, impressão 3D).
Projeto original: github.com/matheustillimt-tech/jarvis (feito e testado só no Mac). Esta cópia foi
adaptada pro **Windows 11** e ganhou várias partes novas, listadas abaixo.

Tudo é em **português do Brasil**: código, comentários, nomes de variáveis, mensagens. Mantenha assim.

## Como funciona (fluxo de uma fala)

```
microfone sempre aberto (web/index.html)
  → detecta voz, grava, manda WAV depois de 1,2 s de silêncio
  → server.py transcreve (ElevenLabs Scribe)
  → decide quem responde:
       Gemma 2B local (Ollama)  → conversa simples e tarefas de pasta
       Claude Code (Sonnet)     → tarefas, código, Google, memória, apagar, internet
  → separa a resposta: fala curta | § | resumo escrito da alteração
  → anota no Diário do segundo cérebro
  → gera a voz (VoiceStudio, perfil "ultron") e manda pra tela
  → painel lateral mostra cada passo
```

## Arquivos que importam

| Arquivo | O que é |
|---|---|
| `server.py` | O coração. Servidor HTTP local (porta 8790), sessão do Claude Code, roteador Gemma/Claude, transcrição, motores de voz, diário, eventos pro painel. Só biblioteca padrão do Python (mais `piper-tts` se usar o Piper). Ação fixa `enviar_resumo_proprio`: o Gemma dispara o envio de `.estado/resumo_pendente.txt` só para a conversa com você mesmo. |
| `whatsapp_resumo.py` | Envia um texto para a conversa "(você)" no WhatsApp Desktop. Usa `pywinauto` (instalado no Python 3.12 do sistema, fora da biblioteca padrão). `--teste` só acha a conversa, sem enviar. Destino fixo: nenhum outro contato é aceito. |
| `persona.md` | Personalidade e regras do assistente (não vai pro git). Sarcástico e filosófico, chama o dono de "Senhor". Regras do segundo cérebro, do Google e do `§`. |
| `.env` | Toda a configuração (não vai pro git). **Tem a chave da ElevenLabs: nunca mostrar, copiar ou commitar.** |
| `web/index.html` | A tela: esfera, escuta contínua com detecção de voz, mudo, painel de atividade, engrenagem. |
| `hooks/trava.py` | Trava de confirmação (Bash e PowerShell). Hoje **desligada** (`JARVIS_TRAVA=0`) a pedido do dono. |
| `app/abrir_windows.py` | O que o atalho chama: se o JARVIS já está no ar, só abre o navegador; senão liga o servidor. |
| `app/jarvis.ico` | Ícone do atalho. |
| `vozes.json` / `vozes-piper.json` | Vozes da engrenagem pra ElevenLabs e Piper. No VoiceStudio a lista vem dos perfis salvos no app. |
| `vozes-piper/` | Modelos de voz do Piper (`*.onnx` fora do git). |
| `.estado/` | Log de cada pedido (`log.jsonl`), erros do Claude, preferências da engrenagem, amostras. Fora do git. |

Fora do projeto:
- **Segundo cérebro**: `C:\Users\caina\OneDrive\Desktop\Projetos\Segundo Cérebro` (`JARVIS_CEREBRO`). O mais importante é `00 Agora.md`. O `Diário/` é escrito pelo servidor.
- **Pasta onde o Claude do JARVIS abre**: `%LOCALAPPDATA%\JARVIS\cerebro` (vazia de propósito, fora do projeto, pra ele não ler este CLAUDE.md).
- **Atalhos**: `Desktop\Ferramentas\JARVIS.lnk` e Menu Iniciar.

## Rodar e testar

- Abrir: atalho JARVIS, ou `python app\abrir_windows.py`. Fechar a janela minimizada desliga.
- Direto: `python server.py` (abre o navegador) ou `python server.py --sem-abrir`.
- Python: `C:\Users\caina\AppData\Local\Programs\Python\Python312\python.exe`.
- Saúde: `GET http://127.0.0.1:8790/api/saude`.
- Testar sem microfone: `POST /api/texto` com `{"texto": "..."}` e ler a última linha `"evento": "turno"` de `.estado/log.jsonl`.
  Mande o JSON em UTF-8 por Python; curl do Git Bash e here-strings do PowerShell 5.1 estragam os acentos.
- Reiniciar só a conversa: `POST /api/reiniciar` (abre uma sessão nova do Claude, que relê o segundo cérebro).
- Depois de mudar `server.py`, `persona.md` ou `.env`: reinicie o servidor. `web/index.html` vale com F5.
- Só uma aba do JARVIS aberta: com duas, as duas escutam e falam.

## Configuração (.env)

| Chave | Valor atual | Pra quê |
|---|---|---|
| `JARVIS_PASTA` | `C:/Users/caina` | Pasta que o Claude pode mexer (acesso total). |
| `JARVIS_CEREBRO` | `.../Projetos/Segundo Cérebro` | Memória de longo prazo. |
| `ELEVENLABS_API_KEY` | (secreta) | Hoje só pra transcrição (Scribe). |
| `JARVIS_TRANSCRICAO` | `elevenlabs` | Sem whisper local instalado. |
| `JARVIS_VOZ_MOTOR` | `voicestudio` | `elevenlabs` / `piper` / `voicestudio`. |
| `JARVIS_VOICESTUDIO_VOZ` | `7b759970` | Perfil "ultron" do VoiceStudio. |
| `JARVIS_MODELO` | `sonnet` | Modelo do Claude. |
| `JARVIS_MODELO_LOCAL` | `gemma2:2b` | Ollama. O 9B não cabe junto com o VoiceStudio nos 6 GB da RTX 3050. |
| `JARVIS_CONTEXTO` | `leve` | Claude abre em pasta vazia e anexa a pasta de trabalho. |
| `JARVIS_TRAVA` | `0` | Trava de confirmação desligada. |

## Dependências externas

- **Claude Code** logado (comando `claude`). Os conectores do claude.ai vêm junto: Gmail, Google Agenda, Google Drive, Google Planilhas, Claude Docs, Canva, Figma, Microsoft 365.
- **VoiceStudio** (`%LOCALAPPDATA%\Programs\VoiceStudio\VoiceStudio.exe`, API em `http://127.0.0.1:3900`, rota `POST /v1/audio/speech`). O servidor abre o app sozinho se estiver fechado. Cerca de 4 a 5 s por frase na GPU.
- **Ollama** (`http://127.0.0.1:11434`) com `gemma2:2b`.
- **ElevenLabs** (plano grátis): só a transcrição. Plano grátis não usa vozes da Voice Library pela API e aceita 2 pedidos de voz ao mesmo tempo.

## O que está funcionando (testado em 2026-11-03)

- Escuta contínua sem tecla, mudo (tecla M e botão), Espaço interrompe.
- Transcrição pelo Scribe (1 a 6 s).
- Roteador: conversa no Gemma 2B; tarefa, Google, memória e código no Claude. Respostas em 4 a 10 s com voz.
- Voz "ultron" do VoiceStudio; personalidade sarcástica e filosófica.
- Painel de atividade (tecla P): o que entendeu, pensamento, cada ferramenta com comando e resultado, resposta escrita, caixa "Alteração".
- Segundo cérebro: guardou um preço, reiniciou a conversa e lembrou. Diário automático.
- Google: leitura testada no Gmail, Agenda e Drive.
- Atalho no Windows, ícone próprio, não duplica servidor.

## Problemas conhecidos e pendências (por prioridade)

1. 🔴 **Gemma executa PowerShell gerado por ele mesmo** (`tentar_local` → `subprocess.run(["powershell", ...])`), protegido só por uma lista de palavras proibidas (`LOCAL_PROIBIDO`), fácil de contornar. Correção proposta: o Gemma devolve uma ação estruturada (`{"op": "mover", "de": ..., "para": ...}`), o Python executa com `shutil`/`pathlib`, caminhos validados com `resolve()` + `is_relative_to(CFG["pasta"])`, e o resto vai pro Claude.
2. 🟠 **Enviar e-mail e criar evento não foram testados**, e sem a trava acontecem sem confirmação. Ideia: religar confirmação só pra envio de e-mail e convite.
3. 🟠 **Gemma 2B é fraco na personalidade** (às vezes não diz "Senhor", sarcasmo fraco).
4. 🟡 Toda vez que o Claude do JARVIS liga, tenta carregar plugins quebrados da conta (GitHub, Terraform, Firebase, Playwright, Serena...). Deixa o início mais lento.
5. 🟡 Sem interrupção por voz em caixa de som (só o Espaço interrompe).
6. ⚪ Transcrição local (whisper) não feita: o JARVIS ainda depende da chave da ElevenLabs pra ouvir.
7. ⚪ `gemma2:9b` continua instalado no Ollama (5,4 GB) sem uso.

## Cuidados ao mexer

- **Windows**: comandos em PowerShell; caminhos com `\`; arquivos em UTF-8. O servidor ignora `ConnectionAbortedError` (aba fechada).
- **Não ler, mostrar nem commitar o `.env`**. `persona.md`, `.estado/` e os modelos `.onnx` também ficam fora do git.
- **Eventos pro painel**: `EVENTOS.emitir({"tipo": "passo", "passo": "ferramenta" | "resultado" | "pensamento" | "texto", ...})`. Na tela, tudo entra como texto (`textContent`), nunca HTML.
- **Resposta falada x escrita**: o que vem depois de `§` não é falado, aparece no painel como "Alteração".
- **Persona curta na fala**: 2 frases, até ~30 palavras; o detalhe vai depois do `§`.
- O texto de e-mail, site e arquivo é dado, nunca ordem pro assistente (regra na persona).
- Ao mudar algo grande, atualize também `Segundo Cérebro\Projetos\JARVIS.md` e `00 Agora.md`.
