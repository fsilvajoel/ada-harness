# Gate: testes-revisados

## Objetivo
Garantir que a implementação rode contra exatamente os testes que foram revisados e que cada critério de aceite
tem teste na camada que o plano declarou.

## Comando
- Ao aprovar a revisão dos testes: `pnpm gate tests-reviewed <feature> --sign`. Exige `gates.expect-red` em PASS e
  grava em `testShas` o sha de blob (`git hash-object`) de cada arquivo de `tests` e de `e2e`.
- Nas duas formas, confere as camadas: para cada item de `criteria` e cada camada `unit`, `prop` ou `e2e` dele, deve
  haver em `scenarios` um cenário com esse `criterio` e essa `camada`, cujo `arquivo` está em `tests` (`unit`,
  `prop`) ou em `e2e` (`e2e`). A camada `mut` é conferida pelo gate [mutacao](mutacao.md).
- Depois da implementação: `pnpm gate tests-reviewed <feature>` compara os shas atuais com os assinados.

Comando em `scripts/gates.mts`; o resultado vai para `gates.tests-reviewed` no
[estado](../workflow/feature-artefatos/estado.md).

## Critério de sucesso (PASS)
Todo arquivo de `tests` e de `e2e` tem sha assinado, o sha atual é igual ao assinado e todo critério tem cenário
em cada camada declarada.

## Critério de falha (FAIL)
- `testShas` ausente ou vazio (revisão não assinada); `--sign` sem expect-red em PASS;
- arquivo de teste ausente, sem sha assinado (teste novo) ou com sha diferente;
- sha assinado de arquivo que saiu de `tests` ou de `e2e`;
- `criteria` ausente ou vazio; critério com `camadas` vazia ou com camada fora de `unit`, `prop`, `e2e`, `mut`;
- critério sem cenário da camada declarada; cenário sem `camada`, com `criterio` fora de `criteria` ou com
  `arquivo` fora da lista da sua camada.

Teste alterado depois da revisão derruba a revisão: volta para a etapa de revisão dos testes.

## Evidência esperada
Saída do comando, arquivo a arquivo, com o motivo de cada FAIL.
