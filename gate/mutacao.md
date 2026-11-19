# Gate: mutacao

## Objetivo
Garantir que os testes da feature matam as mudanças pequenas no código de domínio (regra de negócio, cálculo).
Cobertura mostra que a linha rodou; mutação mostra que um erro na linha seria pego.

## Comando
`pnpm gate mutation <feature>` (`scripts/gates.mts`). Roda a ferramenta de mutação do projeto (Stryker ou
equivalente; `gates.mutation.tool` no `harness.project.yaml`):
- muta só os arquivos de `domainSources` do [estado](../workflow/feature-artefatos/estado.md);
- usa como suíte só os testes de `tests` (e `regressionTests`, quando houver) da feature;
- compara o score de cada arquivo com `gates.mutation.min_score` do `harness.project.yaml`.

Resultado em `gates.mutation`.

## Critério de sucesso (PASS)
- todo arquivo de `domainSources` tem score maior ou igual a `min_score`, com ao menos um mutante gerado; ou
- `domainSources` vazio e nenhum critério com a camada `mut` (nada a medir; o detalhe diz isso).

## Critério de falha (FAIL)
- `domainSources` ausente;
- `domainSources` vazio com algum critério de camada `mut`;
- arquivo de `domainSources` fora de `sources` ou inexistente;
- `gates.mutation.min_score` ausente no `harness.project.yaml`;
- score abaixo de `min_score` em qualquer arquivo; nenhum mutante gerado num arquivo (0/0 é falha);
- teste da feature falhando antes de mutar; ferramenta saindo com erro ou relatório ausente.

## Evidência esperada
`gates.mutation` no estado com o score de cada arquivo e a lista de mutantes sobreviventes; linha do gate na tabela
"Gates" de `revisao.md` ([modelo](../workflow/feature-artefatos/revisao-final.md)).
