# estado.json de uma feature

Fica em `docs/plano/<feature>/estado.json` e diz em que ponto a feature está. Quem grava: o **orquestrador**
(ao fechar cada etapa) e o **script de gates** (`pnpm gate <nome> <feature>`,
nos campos `gates` e `testShas`). Os outros papéis não editam este arquivo.

```json
{
  "feature": "E4-F04",
  "stages": {
    "plan":       {"status": "done"},
    "refinement": {"status": "done", "verdict": "ajustado", "round": 1},
    "tests":      {"status": "done"},
    "testReview": {"status": "done", "verdict": "aprovado"},
    "implement":  {"status": "running"},
    "review":     {"status": "pending"}
  },
  "sources": ["<pacote>/src/log/redact.ts", "<pacote>/src/log/redact-form.ts"],
  "domainSources": ["<pacote>/src/log/redact.ts"],
  "criteria": [
    {"id": "A1", "camadas": ["unit", "prop", "mut"]},
    {"id": "A2", "camadas": ["e2e"]}
  ],
  "tests": ["<pacote>/src/log/redact.test.ts"],
  "e2e": ["<pacote>/e2e/redact.e2e.ts"],
  "regressionTests": [],
  "scenarios": [
    {"id": "C1", "criterio": "A1", "camada": "unit", "arquivo": "<pacote>/src/log/redact.test.ts",
     "teste": "C1 (A1): mascara o CPF no log"},
    {"id": "C2", "criterio": "A1", "camada": "prop", "arquivo": "<pacote>/src/log/redact.test.ts",
     "teste": "C2 (A1): nenhum CPF gerado sobra no texto"},
    {"id": "C3", "criterio": "A2", "camada": "e2e", "arquivo": "<pacote>/e2e/redact.e2e.ts",
     "teste": "C3 (A2): a tela de log mostra o CPF mascarado"}
  ],
  "testShas": {"<pacote>/src/log/redact.test.ts": "eb4282ab...", "<pacote>/e2e/redact.e2e.ts": "91c0d2fe..."},
  "gates": {
    "expect-red": {"status": "PASS", "at": "2026-11-17T12:00:00.000Z", "detail": "3 de 3 testes falharam"}
  }
}
```

## Campos
Caminhos sempre relativos à raiz do repositório. Listas são de texto, sem repetição.

- `feature`: ID da feature (igual ao nome da pasta). Num defeito, o ID do defeito (`D<n>`,
  [workflow defeito](../defeito.md)).
- `stages.<etapa>.status`: `pending`, `running`, `done` ou um estado de falha (abaixo). Etapas: `plan`, `refinement`,
  `tests`, `testReview`, `implement`, `review`. `testReview` e `review` guardam também o `verdict` do reviewer;
  `refinement` guarda o `verdict` do refiner (`pronto`, `ajustado` ou `pendencia-pm`) e o `round` (1 ou 2,
  [refinamento funcional](../feature.md)).
- `sources`: `string[]`, arquivos de produção da feature (seção "Arquivos de produção" do plano). É o que o gate
  [cobertura](../../gate/cobertura.md) mede. Ausente = estado antigo (o gate cai na regra do nome).
- `domainSources`: `string[]`, subconjunto de `sources` com regra de negócio ou cálculo (coluna "Domínio" do
  plano). É o que o gate [mutacao](../../gate/mutacao.md) mede. `[]` quando a feature não tem arquivo de domínio.
- `criteria`: `{id: "A<n>", camadas: ("unit" | "prop" | "e2e" | "mut")[]}[]`, um item por critério do plano, com
  as camadas declaradas nele. `camadas` não é vazia.
- `tests`: `string[]`, arquivos de teste unitário e de propriedade da feature. É a lista que expect-red,
  testes-revisados e cobertura usam.
- `e2e`: `string[]`, arquivos de teste e2e da feature; `[]` quando nenhum critério tem a camada `e2e`. Gate
  [e2e](../../gate/e2e.md).
- `regressionTests`: `string[]` opcional, testes já assinados em outra feature que cobertura e mutacao rodam junto
  com `tests` (uso: correção de defeito). Expect-red e testes-revisados não os olham.
- `scenarios`: `{id: "C<n>", criterio: "A<n>", camada: "unit" | "prop" | "e2e", arquivo: string, teste: string}[]`,
  ligação cenário → critério → camada → arquivo → nome exato do teste. `arquivo` está em `tests` (camadas `unit` e
  `prop`) ou em `e2e` (camada `e2e`). A camada `mut` não tem cenário próprio: é provada pelo gate mutacao sobre os
  testes `unit`/`prop` do critério.
- `testShas`: sha do blob (`git hash-object`) de cada arquivo de `tests` e de `e2e`, gravado por
  `pnpm gate tests-reviewed <feature> --sign` depois da aprovação do reviewer. Se um sha mudar, a revisão cai.
- `gates.<nome>`: último resultado de cada gate (`PASS`/`FAIL`, horário ISO e detalhe), gravado pelo script.
  Nomes: `expect-red`, `tests-reviewed`, `coverage`, `mutation`, `e2e`.

## Trabalhador
Quando a etapa vai para um trabalhador ([api-chat](../../adapter/api-chat.md) ou
[cli-agent](../../adapter/cli-agent.md)), o orquestrador grava em `stages.<etapa>`:

```json
"implement": {
  "status": "done",
  "worker": "<nome>",
  "model": "<modelo>",
  "attempts": [
    {"n": 1, "at": "2026-11-19T00:22:00.000Z", "requests": 1, "promptTokens": 909, "completionTokens": 384,
     "result": "gate-fail", "detail": "commands.test: 2 de 6 testes falharam"},
    {"n": 2, "at": "2026-11-19T00:25:00.000Z", "requests": 1, "promptTokens": 1210, "completionTokens": 402,
     "result": "PASS", "detail": "test, typecheck, lint, format, tests-reviewed e coverage PASS"}
  ],
  "escalation": null
}
```

- `attempts[].result`: `PASS`, `gate-fail` (comando ou gate em FAIL), `rejected` (resposta fora do formato ou
  caminho proibido) ou `error` (rede, timeout, HTTP, corpo com erro ou vazio).
- `attempts[].requests`: requisições de chat feitas na tentativa (a consulta de cota não conta).
- `escalation`: `null` ou `{"at", "reason", "detail"}`, com `reason` igual a `attempts`, `quota`, `429` ou `no-key`.
  Depois da escalada, a etapa segue com o `agent` do papel ou fica `needs_input`.
- cli-agent: `model`, `requests` e tokens só quando a ferramenta informa; `attempts[].usage` guarda o que o
  `usage_jq` extrair. `escalation.reason` pode ser também `tool` (comando ausente, sem login, limite de uso) ou
  `outside-copy` (projeto alterado fora da cópia de trabalho).
- Segunda opinião do reviewer: o mesmo registro em `stages.testReview.secondOpinion` ou
  `stages.review.secondOpinion`, com o `verdict` dela.

## Status das etapas
| Status | Significado | O que fazer |
|---|---|---|
| `needs_input` | o papel ou o plano pediu decisão humana (inclusive pendência no domínio e `pendencia-pm` do refinamento) | responder e retomar a etapa |
| `failed` | o reviewer reprovou, ou o papel não entregou | refazer a etapa com a lista de achados |
| `error` | gate FAIL (ex.: teste passou antes da hora, sha mudou, cobertura ou mutação abaixo do limite, 0/0, teste pulado, critério sem teste da camada) | ler a mensagem do gate; não é sucesso |
