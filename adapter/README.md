# Adaptadores

O núcleo do harness (workflow, papéis, policies, gates) é neutro. Um adaptador diz como uma ferramenta de IA
executa esse núcleo: quem orquestra, como cada [papel](../agent/README.md) é chamado e quais travas valem.
O adaptador não muda o núcleo.

| Adaptador | Ferramenta |
|---|---|
| [claude-code](claude-code.md) | Claude Code |
| [api-chat](api-chat.md) | modelo por API de chat compatível com OpenAI, sem ferramentas (trabalhador de um papel) |
| [api-tools](api-tools.md) | o mesmo modelo por API com laço de function calling: ler, buscar, rodar `commands.*` e gravar só no escopo do papel (trabalhador de um papel) |
| [cli-agent](cli-agent.md) | agente de IA de linha de comando que edita arquivos numa cópia de trabalho (trabalhador de um papel) |

O despacho é automático: `script/dispatch.py --role <papel>` escolhe o executor pelo `roles.<papel>.worker` ([cli-agent](cli-agent.md#despacho-automático)).
Rota opcional por papel: `worker_stages` e `worker_paths`, com `--stage` e `--allowed-path` ([api-tools](api-tools.md#roteamento-pelo-dispatchpy)).

O api-chat tem executor próprio: `script/api_chat.py` (uma requisição, chave resolvida pelo script).

### Laço autônomo de cartão (`script/run_card.py`)

Para cartões `test-author`/`implement` com worker api-chat (a Bia, sem ferramentas) ou api-tools (o Baidu, que grava sozinho no escopo): o script poupa tokens do Claude, que só lê o resultado curto.
- Respeita o roteamento: repassa ao `dispatch.py` `--stage` (`test-author` = `tests`, `implement` = `implement`) e um `--allowed-path` por `allowedPaths`, então `worker_stages` e `worker_paths` valem. Cartão que a rota manda para o agent = saída 2, sem rodar.
- api-chat: o script faz o parse dos blocos e grava. api-tools: o worker grava; o script confere que só `allowedPaths` mudou (senão restaura tudo e devolve o erro), formata e roda os `checks`; a nova tentativa reenvia o pedido com o erro, sem o conteúdo dos arquivos.
Comando: `python3 ~/code/harness-ada/script/run_card.py <raiz> --card <ID> [--max-attempts N] [--dry-run] [--keep-on-fail] [--log-dir PASTA]`
- Por tentativa: monta o pedido, chama o worker, rejeita resposta fora do formato, grava, formata, roda os `checks` do cartão e devolve o erro. O pedido repete o original uma vez, com os arquivos atuais e a saída do último check (não cresce).
- `--max-attempts` (padrão: `budget.attempts` do cartão, ou 6); `--dry-run`: só monta o pedido da tentativa 1 e mostra o tamanho (sem API, sem gravar); `--keep-on-fail`: não restaura os arquivos ao esgotar; `--log-dir`: pedidos, respostas e logs de cada check.
- Saídas: 0 PASS, 1 FAIL (esgotou; arquivos restaurados), 2 uso/configuração (inclui cartão já rodando: lock por projeto+cartão), 3 cota, 4 worker, 130 interrompido (Ctrl-C/SIGTERM, arquivos restaurados).
- Depois, o orquestrador roda o gate (`pnpm gate ...`), grava `estado.json` e lê só o resultado curto.
- O script **não** assina gates, **não** faz commit e **não** escreve no `estado.json`.

Ferramenta nova ganha um arquivo aqui quando for usada.
