# Adaptador: modelo por API de chat (sem ferramentas)

Um **trabalhador** é um modelo acessado por uma API de chat compatível com OpenAI (`POST <base_url>/chat/completions`).
Ele não tem ferramentas: recebe um pedido em texto e devolve texto. O orquestrador monta o pedido, aplica a resposta
e roda os gates. **Quem aprova é o gate, nunca a palavra do modelo.**

## Escopo
- Hoje cobre só o papel `implementer` (etapa 4 do [workflow feature](../workflow/feature.md)), e só quando o plano e os
  testes já estão assinados.
- O trabalhador não escreve nem altera teste, plano ou `estado.json` e não faz commit. Isso fica com o orquestrador.

## Configuração (no `harness.project.yaml` do projeto)
- `workers.<nome>`: `adapter: api-chat`, `base_url`, `model`, `key_env` (variável de ambiente com a chave),
  `key_file` (de onde o humano carrega a chave), `quota_url` e `quota_remaining` (caminho jq até as requisições
  restantes no dia), `quota_reserve` (folga mínima), `max_attempts` (padrão 6) e `timeout_s`.
- `roles.<papel>.worker: <nome>`: o papel pode ser executado por esse trabalhador. Sem a chave, o papel segue
  com o `agent` de sempre.
- `role_context.<papel>`: arquivos do projeto com as regras de tipagem e lint que vão no pedido.

## Chave
- O orquestrador usa a chave só pela variável `key_env` do ambiente dele, dentro do comando
  (`-H "Authorization: Bearer $<key_env>"`). Não lê `key_file`, não imprime, não loga e não grava o valor
  ([security](../policy/security.md), [edit-guard](../gate/edit-guard.md)).
- Quem carrega a variável é o humano, ao abrir a sessão, exportando **só ela** a partir de `key_file`.
- Variável vazia = escalada (`no-key`).

## Antes de cada requisição
1. Pré-condição (só na primeira): `testShas` gravado e `gates.tests-reviewed.status = PASS` no estado.
2. Cota: `GET <quota_url>` com a chave; ler `quota_remaining`. Se o valor for menor ou igual a `quota_reserve`,
   ou se a consulta falhar, escala (`quota`). Não espera a cota voltar.
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
