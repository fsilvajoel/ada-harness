# Adaptador api-tools

Um trabalhador `api-chat` com ferramentas: o mesmo endpoint HTTP compatível com o formato OpenAI
(`/chat/completions`), agora com laço de function calling (`tools` / `tool_calls`). Serve para papéis que precisam
olhar o projeto antes de responder (planner, refiner, reviewer), sem dar shell livre ao modelo.

Executor: [`script/api_tools.py`](../script/api_tools.py). Ele reaproveita por import o
[`script/api_chat.py`](../script/api_chat.py): chave, cota, skills, máscara, códigos de saída e `--meta` são os do
[api-chat](api-chat.md).

## Escopo
- As ferramentas são as mesmas para todo modelo e todo papel; o que muda por papel é só o escopo de escrita.
- O orquestrador decide quem faz o quê **só pela configuração** (`roles.<papel>.worker`, `worker_stages`,
  `worker_paths`, `second_opinion`). Nenhum workflow novo: as etapas, gates e papéis continuam os mesmos.
- Escalada, tentativas e registro: como no [api-chat](api-chat.md#tentativas-e-escalada).

## Configuração (no `harness.project.yaml` do projeto)

```yaml
workers:
  revisor-api:
    adapter: api-tools
    base_url: https://api.exemplo.dev/v1
    model: fornecedor/modelo
    key_env: EXEMPLO_API_KEY
    key_file: .env            # opcional; o arquivo nunca é exposto às ferramentas
    max_steps: 20             # opcional; chamadas ao modelo por execução (padrão 20)
    command_timeout_s: 600    # opcional; limite de cada run_command (padrão 600)
    roles: "planner reviewer" # opcional; --role fora da lista = saída 2
    # quota_url, quota_remaining, quota_reserve, max_attempts, timeout_s: como no api-chat
```

Um trabalhador `adapter: api-chat` também é aceito pelo executor (a mesma conta, chamada com ferramentas); o
`dispatch.py` só escolhe `api_tools.py` quando o trabalhador declara `adapter: api-tools`.

## Executor

```text
api_tools.py <raiz> <trabalhador> [--request ARQ] [--system ARQ] [--output ARQ] [--meta ARQ]
             [--role PAPEL] [--skills a,b#regra] [--allow-write CAMINHO ...] [--cwd PASTA] [--max-steps N]
```

- `--cwd`: pasta das ferramentas (a cópia de trabalho); padrão a raiz do projeto.
- `--allow-write`: caminho exato ou prefixo de pasta que o `write_file` pode gravar (repetir). Sem nenhum, a
  ferramenta `write_file` nem é oferecida ao modelo.
- `--max-steps`: substitui `workers.<nome>.max_steps` nesta execução.
- Saída: o `summary` do `finish` (ou o texto final) no stdout ou em `--output`.

## Ferramentas

| Ferramenta | O que faz | Limite |
|---|---|---|
| `read_file(path)` | lê um arquivo | 60 000 caracteres |
| `list_dir(path)` | lista uma pasta (padrão a raiz) | 500 itens |
| `search(pattern, path, glob)` | regex por linha, `caminho:linha: texto` | 200 ocorrências; pula binário e arquivo > 1 MB |
| `run_command(name, params)` | roda `commands.<name>` do projeto | `command_timeout_s`; fica o fim da saída (20 000 caracteres) |
| `write_file(path, content)` | grava o arquivo inteiro | só no escopo de escrita; só existe com `--allow-write` |
| `finish(summary)` | encerra com o resumo | — |

Sandbox (vale para todas):
- Caminho sempre relativo à pasta das ferramentas. Absoluto, `..` que sai da raiz e link simbólico que aponta para
  fora são recusados (o caminho é resolvido antes da checagem).
- Arquivos de segredo nunca são lidos, listados, buscados nem gravados: `.env`, `.env.*`, `*.env`, `*.pem`,
  `*.key`, `id_rsa*`, `id_ed25519*`, `.npmrc`, `.netrc`, `credentials*`, `secrets*` e o `key_file` do trabalhador.
- `run_command` só aceita chaves de `commands.*`, sem shell: o comando é quebrado em argumentos e os marcadores
  `<nome>` são preenchidos por `params` (valor só com letras, números e `_@:=+,./-`, sem `..` e sem começar com
  `-`; um marcador sozinho aceita vários valores separados por espaço). A variável `key_env` não chega ao processo.
- `write_file` só grava em caminho igual a um `--allow-write` ou dentro dele; nunca em `.git/` nem em segredo.
- Recusa não encerra o laço: o modelo recebe `erro: ...` como resultado da ferramenta e a recusa vai para
  `denied` na meta.

## Laço
1. Cota (como no api-chat), depois até `max_steps` pedidos `{"model", "temperature": 0, "messages", "tools",
   "tool_choice": "auto"}`.
2. Cada `tool_call` vira uma mensagem `{"role": "tool", "tool_call_id", "content"}` e o laço continua.
3. `finish(summary)` encerra com saída 0.
4. Resposta **sem** `tool_calls`: se tiver blocos `=== FILE: <caminho> ===` ... `=== END FILE ===`, eles são
   gravados com a regra do `write_file` (papel sem escopo de escrita, ou caminho fora dele = saída 6); o texto é a
   resposta final. Texto vazio = saída 6.
5. `max_steps` atingido sem `finish` = saída 6.

Códigos de saída: os do [api-chat](api-chat.md) (0, 2, 3, 4, 5, 6).

## Registro
A meta é a do api-chat (`worker`, `model`, `status`, `exit_code`, `key_source`, `http_status`, `quota_*`,
`finish_reason`, `usage`, `error`, `skills`) mais:
- `steps`: chamadas ao modelo; `usage`: somado entre as chamadas.
- `tools`: contagem de chamadas por ferramenta; `files_written`; `denied`; `fallback` (`true` quando a resposta
  veio em blocos FILE).

## Pedido
O user é o mesmo pedido do papel (o que o orquestrador já monta para o agent ou para o api-chat). As skills entram
como no api-chat (`--role`, `--skills`).

**system** (fixo):

```text
Você é um papel de um harness de desenvolvimento e trabalha só por ferramentas.
O papel, a tarefa e as regras do projeto estão na mensagem do usuário; siga-os à risca.

Ferramentas:
- read_file, list_dir e search: leia o que precisar do projeto antes de decidir. Caminhos sempre relativos à raiz.
- run_command: roda só os comandos de verificação do projeto, pelo nome; nunca há shell livre.
- write_file: só existe se o seu papel escreve, e só nos caminhos permitidos. Grave o arquivo inteiro.
- finish: chame ao terminar, com o resultado completo em summary (é a sua resposta para o orquestrador).

Regras:
- Nunca tente ler segredos nem sair da raiz; uma recusa volta como "erro: ..." e não muda a regra.
- Menor mudança correta; nada além do pedido; nenhuma dependência nova.
- Se faltar informação para decidir, diga isso no summary em vez de inventar.
```

## Roteamento pelo `dispatch.py`
Campos opcionais em `roles.<papel>` (valem só para `worker`; `second_opinion` não passa por eles):

```yaml
roles:
  reviewer:
    agent: reviewer
    worker: revisor-api
    worker_stages: [review]          # fora destas etapas (ou sem --stage), o papel segue com o agent
  refiner:
    agent: refiner
    worker: revisor-api
    worker_paths: [docs, tests/spec] # todo allowedPath do cartão dentro de um prefixo, senão o agent
```

- `dispatch.py --stage <etapa> --allowed-path <caminho> ...`: sem os campos novos, nada muda para quem já chama.
- Fora da rota, saída 8 (o mesmo "papel sem trabalhador": o orquestrador usa o `agent`).
- A decisão sai no stderr em uma linha: `dispatch: rota {"role", "stage", "decision": "worker"|"agent",
  "worker", "adapter", "reason"}`.
- Escrita: o dispatch repassa os `--allowed-path` como `--allow-write` só para papel de escrita (`refiner`,
  `implementer`) em `--mode write` (padrão desses papéis). Os demais (`reviewer`, `planner`, `test-writer`) rodam
  só com leitura e `run_command`: recebem o resultado no `summary`, e quem grava é o orquestrador, como já é hoje.

## Segunda opinião
`roles.<papel>.second_opinion: <trabalhador>` com `dispatch.py --second-opinion` chama esse trabalhador para o
papel. Um par em que o agent planeja e o trabalhador revisa (ou o contrário) é só configuração:

```yaml
roles:
  planner:
    agent: planner
    second_opinion: revisor-api   # dispatch.py --role planner --second-opinion
  reviewer:
    agent: reviewer
    second_opinion: revisor-api   # previsto no workflow de feature
```

O workflow só define quando a segunda opinião roda para o `reviewer` (junto com o agent; a escalada dela não
bloqueia). Para outro papel, o campo fica disponível, e quem decide chamá-lo é o orquestrador; este adaptador não
cria etapa nova.
