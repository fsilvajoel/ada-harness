# Capability: planning

## Objetivo
Definir escopo, domínio das entradas, critérios de aceite, cenários e contrato antes da execução.

## Composição
- papel: `planner`
- policy: [quality](../policy/quality.md)
- gate: no refinamento funcional, o `refiner` confere o plano (regras abaixo) e os cartões antes da etapa tests,
  completa o que não depende do PM e põe o resto na fila de PM; o `reviewer` reprova critério vago na revisão
  dos testes
- artefato: [plano.md](../workflow/feature-artefatos/plano.md)
- workflow: [feature](../workflow/feature.md), etapa plan

## Regras do plano
- **Domínio das entradas.** Toda entrada externa tem uma linha na tabela de domínio: formato aceito, mínimo,
  máximo, exemplos rejeitados e erro esperado, com fonte. Entrada sem linha, ou limite sem fonte, é pendência.
- **Critério com molde fixo.** Todo `A<n>` traz `entrada`, `domínio`, `saída`, `erro`, `fonte` e `camada`
  (`unit`, `prop`, `e2e` ou `mut`). Critério vago ("valor válido", "lança erro" sem dizer qual, "formato
  correto" sem o formato) é reprovado.
- **Critério é comportamento.** Cada `A<n>` descreve comportamento observável e tem ao menos um cenário `C<n>`.
  Qualidade geral (cobertura, typecheck, lint, formatação) não vira `A<n>`: já é cobrada pelos gates de
  cobertura e pelos checks do `done`.
- **Rejeição planejada.** Toda entrada da tabela tem ao menos um cenário de rejeição (exemplo rejeitado → erro).
- **Arquivos de produção.** O plano lista os arquivos de produção da feature e marca os de domínio; a lista vira
  `sources` e `domainSources` no [estado](../workflow/feature-artefatos/estado.md).
- **Pendência para a feature.** Qualquer pendência, inclusive no domínio, para o workflow até ser respondida.
- **Tela é tipo de cartão.** Cartão que cria ou altera página, layout ou composição visual do frontend é marcado
  como **tela** no plano, e só ele recebe `shadcn` e `tailwind-css` em `skills:`
  ([tabela](../workflow/feature-artefatos/cartao.md#tabela-padrão-tipo-de-cartão--extras)). O plano de uma tela
  cita os componentes shadcn previstos (nomes do registro) e os estados a cobrir (vazio, carregando, erro).
