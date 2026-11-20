# Gate: e2e

## Objetivo
Garantir que os fluxos de usuário descritos no PRD passam de ponta a ponta, no navegador, com a aplicação real.

## Comando
`pnpm gate e2e <feature>` (`scripts/gates.mts`). Roda a ferramenta e2e do projeto (Playwright ou equivalente;
`gates.e2e.tool` no `harness.project.yaml`) só com os arquivos de `e2e` do
[estado](../workflow/feature-artefatos/estado.md).

Resultado em `gates.e2e`.

## Critério de sucesso (PASS)
- todos os testes de `e2e` rodam e passam, nenhum pulado, e cada cenário de camada `e2e` aparece no relatório; ou
- `e2e` vazio e nenhum critério com a camada `e2e` (nada a rodar; o detalhe diz isso).

Dispensa de TDD numa tela (`exemptSources`) não dispensa o e2e do fluxo do PRD que passa por ela.

## Critério de falha (FAIL)
- `e2e` ausente, ou vazio com algum critério de camada `e2e`;
- arquivo de `e2e` inexistente;
- nenhum teste executado; teste falhando, pulado ou `todo` (**pular é FAIL**);
- cenário de camada `e2e` fora do relatório;
- `gates.e2e.tool` ausente no `harness.project.yaml` quando há teste e2e.

## Evidência esperada
`gates.e2e` no estado com a contagem de testes e os nomes dos que falharam; linha do gate na tabela "Gates" de
`revisao.md` ([modelo](../workflow/feature-artefatos/revisao-final.md)).
