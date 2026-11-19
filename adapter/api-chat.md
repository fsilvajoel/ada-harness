# Adaptador: modelo por API de chat (sem ferramentas)

Um **trabalhador** é um modelo acessado por uma API de chat compatível com OpenAI (`POST <base_url>/chat/completions`).
Ele não tem ferramentas: recebe um pedido em texto e devolve texto. O orquestrador monta o pedido, aplica a resposta
e roda os gates. **Quem aprova é o gate, nunca a palavra do modelo.**

## Escopo
- Hoje cobre só o papel `implementer` (etapa 4 do [workflow feature](../workflow/feature.md)), e só quando o plano e os
  testes já estão assinados.
- O trabalhador não escreve nem altera teste, plano ou `estado.json` e não faz commit. Isso fica com o orquestrador.

## Configuração (no `harness.project.yaml` do projeto)
- `workers.<nome>`: `adapter: api-chat`, `base_url`, `model`, `key_env` (nome da chave),
  `key_file` (arquivo de segredos com a linha da chave, relativo à raiz), `quota_url` e `quota_remaining`
  (caminho jq simples até as requisições restantes no dia), `quota_reserve` (folga mínima), `max_attempts`
  (padrão 6) e `timeout_s`.
- `roles.<papel>.worker: <nome>`: o papel pode ser executado por esse trabalhador. Sem a chave, o papel segue
  com o `agent` de sempre.
- `role_context.<papel>`: arquivos do projeto com as regras de tipagem e lint que vão no pedido.

## Chave
- O orquestrador não lê, não exporta, não imprime nem cita a chave ou o `key_file` no comando: quem resolve é o
  [executor](#executor) ([security](../policy/security.md), [edit-guard](../gate/edit-guard.md)).
- Ordem de resolução: variável `key_env` do ambiente, se não vazia; senão só a linha `<key_env>=` do `key_file`
  (relativo à raiz do projeto; aceita `export `, aspas e CRLF). O resto do arquivo não entra no ambiente.
- Sem chave nas duas = escalada (`no-key`).

## Executor
A consulta de cota ([Antes de cada requisição](#antes-de-cada-requisição), item 2) e o pedido de cada tentativa
são uma execução de `script/api_chat.py` do harness:

```bash
python3 <harness>/script/api_chat.py <raiz do projeto> <trabalhador> \
  --system <arquivo system> --request <arquivo user> --output <arquivo resposta> --meta <arquivo meta>
```

- Sem `--request`, o pedido user vem do stdin; sem `--output`, a resposta vai para o stdout; sem `--meta`, o resumo
  vai para o stderr. Os arquivos ficam no temp do orquestrador.
- Consulta a cota e faz **uma** chamada (`temperature` 0, `timeout_s`). Tentativas, aplicação e gates seguem com o
  orquestrador.
- Resumo (JSON): `worker`, `model`, `status`, `exit_code`, `key_source` (`env` ou `key_file`), `http_status`,
  `quota_remaining`, `quota_reserve`, `finish_reason`, `usage` e `error`. Nunca traz a chave; o que ecoar a chave
  sai mascarado (`***`).
- Saída: `0` ok; `2` uso ou configuração inválida; `3` sem chave (`no-key`, escala); `4` cota abaixo da reserva ou
  consulta falhou (`quota`, escala); `5` HTTP 429 (escala); `6` HTTP diferente de 200, timeout, rede, corpo com
  `error` ou resposta vazia (tentativa falha).

## Antes de cada requisição
1. Pré-condição (só na primeira): `testShas` gravado e `gates.tests-reviewed.status = PASS` no estado.
2. Cota (feita pelo executor): `GET <quota_url>` com a chave; ler `quota_remaining`. Se o valor for menor ou
   igual a `quota_reserve`, ou se a consulta falhar, escala (`quota`). Não espera a cota voltar.
3. Guardar uma cópia dos arquivos de `sources` como estavam antes da primeira tentativa (no temp da ferramenta).

## Pedido
Cada tentativa é um pedido novo e fechado, sem histórico: `{"model": <model>, "temperature": 0, "messages":
[system, user]}`.

O pedido **nunca** depende de o modelo calcular um valor esperado: os valores vêm do plano e dos testes assinados.
O modelo pode errar aritmética, e o gate é o que decide.

**system** (fixo):

```text
Você é o papel implementer de um harness de desenvolvimento. Você não tem ferramentas: recebe texto e devolve texto.
Sua tarefa: escrever o código de produção que faz os testes assinados passarem, seguindo o plano e as regras do projeto.

Regras:
- Os testes assinados são somente leitura e já trazem os valores esperados. Não recalcule nem questione esses valores; implemente a regra do plano.
- Escreva só os arquivos listados em "Arquivos que você pode escrever". Nunca escreva arquivo de teste.
- Menor mudança correta; nada além dos critérios de aceite; nenhuma dependência nova.
- Devolva cada arquivo inteiro (não diff, não trecho), no formato abaixo, sem cercas de código (```) dentro do bloco.

Formato de saída (obrigatório):
=== FILE: <caminho exatamente como listado> ===
<conteúdo completo do arquivo>
=== END FILE ===

Um bloco por arquivo alterado. Texto fora dos blocos é ignorado.
```

**user** (montado pelo orquestrador, nesta ordem):
1. `# Tarefa`: ID da feature, etapa e "tentativa n de max_attempts".
2. `# Plano`: `plano.md` inteiro.
3. `# Regras do projeto`: os arquivos de `role_context.<papel>`.
4. `# Testes assinados (somente leitura)`: cada arquivo de `tests` (e de `regressionTests`) entre
   `=== TEST: <caminho> ===` e `=== END TEST ===`.
5. `# Arquivos que você pode escrever (conteúdo atual)`: cada arquivo de `sources` no formato de saída
   (`(arquivo novo)` se ainda não existe).
6. Só a partir da 2ª tentativa, `# Falha da tentativa anterior`: o motivo da rejeição ou as últimas 150 linhas
   da saída do comando que falhou.

### Recursos para quem não tem ferramentas
O trabalhador não lê arquivo, skill nem gate; ele só vê o pedido. Todo papel tem direito a todas as skills, gates e
regras ([recursos-compartilhados](../capability/recursos-compartilhados.md)), então o **orquestrador pode incluir no
`user`**, depois do item 3, uma seção `# Skills e regras` com o conteúdo (inteiro ou o trecho que serve) de qualquer
`skill/<nome>/SKILL.md`, `gate/*.md`, `policy/*.md` ou capability de que o papel precisa naquela chamada, cada um entre
`=== RECURSO: <caminho> ===` e `=== END RECURSO ===`. O executor não muda: o pedido já é um arquivo montado pelo
orquestrador. Skill que manda usar ferramenta (ex.: `Edit`) não se aplica ao trabalhador; o orquestrador aplica a
resposta e roda os gates por ele. O recurso incluído é somente leitura e não amplia a lista de arquivos que ele pode
escrever.

Nada de `key_file`, segredo ou arquivo fora dessa lista entra no pedido.

## Resposta
O conteúdo é `choices[0].message.content`. A resposta é **rejeitada inteira** (nada é aplicado) se:
- não há bloco `=== FILE: ... ===` ou algum bloco não fecha com `=== END FILE ===`;
- o caminho não é idêntico a um item de `sources`, ou repete;
- o caminho está em `tests`, `e2e` ou `regressionTests`, ou tem `.test.`, `.spec.` ou `.e2e.` no nome;
- o conteúdo do bloco está vazio ou começa com cerca de código;
- `finish_reason` é `length` (saída cortada).

## Aplicação e gates
1. Gravar cada bloco por inteiro no caminho dele.
2. `git status --porcelain`: só arquivos de `sources` podem ter mudado nesta etapa. Outro caminho = rejeitada
   (restaurar e contar a tentativa).
3. Rodar em ordem, parando no primeiro FAIL: `commands.test` com os arquivos de `tests`, `commands.typecheck`,
   `commands.lint`, `commands.format`, `pnpm gate tests-reviewed <feature>` e `pnpm gate coverage <feature>`.
4. Tudo PASS: a etapa implement está feita e o workflow segue na etapa 5.

## Tentativas e escalada
- Conta como tentativa falha: erro de rede, timeout (`timeout_s`), HTTP diferente de 200 e 429, corpo com `error`,
  resposta vazia, resposta rejeitada e gate FAIL. A próxima tentativa recebe o motivo (item 6 do pedido).
- Escala na hora, sem nova tentativa: HTTP 429, cota abaixo da reserva, chave ausente.
- Escala ao chegar a `max_attempts` falhas.
- Escalar = restaurar `sources` para a cópia guardada antes da primeira tentativa e registrar o motivo. Então o orquestrador assume a etapa
  com o `agent` do papel, ou marca `needs_input` quando a decisão é humana (ex.: cota esgotada e sem outro executor).

## Registro
Em `stages.implement` do `estado.json` ([formato](../workflow/feature-artefatos/estado.md#trabalhador)): `worker`,
`model`, `attempts` (uma entrada por tentativa, com requisições, tokens de `usage` e resultado) e `escalation`.
O texto do pedido e da resposta não vai para o estado nem para o git.
