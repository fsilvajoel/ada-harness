# Gate: cobertura

## Objetivo
Garantir que o código de produção da feature foi exercitado por inteiro pelos testes da feature. A suíte
completa de cada pacote não entra aqui: ela é do gate de conclusão (`pnpm verify`). Assim, testes vermelhos
de outras features ainda em esboço (o teste vem antes do código) não travam a feature em andamento.

## Comando
`pnpm gate coverage <feature>` (`scripts/gates.mts`). Em cada pacote que contém testes listados em `tests` (e em
`regressionTests`, quando houver) do [estado](../workflow/feature-artefatos/estado.md):
- roda só esses testes no vitest, com cobertura ligada;
- mede só os arquivos de produção da feature, um `--coverage.include` por arquivo, com os caracteres de glob
  escapados. Os arquivos vêm de `sources` do estado. **Compatibilidade:** só quando `sources` está ausente o gate
  cai na regra do nome (cada teste sem o `.test`: `x.test.ts` mede `x.ts`);
- anula o `coverage.exclude` do `vitest.config` (`--coverage.exclude=` vazio), para que um arquivo da feature
  excluído ali não suma da medição;
- exige 100% de linhas, ramos, funções e statements em cada arquivo (limites passados na linha de comando e
  conferidos arquivo a arquivo no `coverage-summary.json`).

Resultado em `gates.coverage`.

## Critério de sucesso (PASS)
Vitest sai com código 0, todos os testes da feature passam, nenhum é pulado, todo teste da feature aparece no
relatório e cada arquivo de produção da feature aparece no relatório de cobertura com linhas medidas e 100%
nas quatro métricas.

## Critério de falha (FAIL)
- `sources` presente mas vazio, ou com arquivo inexistente ou fora de um pacote com testes da feature;
- sem `sources`: arquivo de produção deduzido inexistente, ou teste sem o padrão `.test.<ext>`;
- nenhum teste executado; teste falhando; teste pulado ou `todo` (**pular é FAIL**);
- teste da feature fora do relatório do vitest;
- `coverage-summary.json` ausente;
- arquivo da feature fora do relatório de cobertura (excluído ou não carregado): é FAIL, nunca 0/0 PASS;
- cobertura vazia: **0/0 é falha**, nunca sucesso;
- qualquer métrica abaixo de 100% num arquivo da feature, ou vitest saindo com código diferente de 0.

## Limites
Cobertura mostra que a linha rodou, não que a entrada fora do domínio foi recusada. O domínio é provado pelos
cenários de rejeição ([testing](../capability/testing.md)) e pelo gate [mutacao](mutacao.md).

## Evidência esperada
`gates.coverage` no estado e a linha do gate na tabela "Gates" de `revisao.md`
([modelo](../workflow/feature-artefatos/revisao-final.md)).
