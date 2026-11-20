# Workflows

Processos de ponta a ponta: sequência de etapas, papéis, skills, gates e critério de conclusão.
Workflow é orquestração, não conhecimento de implementação. Formato: [_modelo.md](_modelo.md).

| Workflow | Objetivo |
|---|---|
| [feature](feature.md) | entregar uma feature: plan → tests → revisão dos testes → implement → mutação e e2e → revisão final → fechamento |
| [defeito](defeito.md) | registrar um defeito, corrigi-lo a partir de um teste vermelho e ajustar a regra que o deixou passar |

Quem conduz é o orquestrador: qualquer agente de IA que siga o workflow. Moldes dos artefatos da feature:
[feature-artefatos/](feature-artefatos/README.md). Moldes do defeito: [defeito-artefatos/](defeito-artefatos/README.md).

**Fora do fluxo.** Trabalho em que todo arquivo de produção seria dispensado de TDD (só layout e estilo, ou só
componente de biblioteca) não é feature: não tem expect-red nem cobertura para satisfazer. Vira tarefa fora do
fluxo, com revisão visual no navegador. Se aparecer condição, cálculo ou validação, extraia a lógica para um arquivo
`tdd` e a parte com lógica entra no fluxo de feature
([classificação](../capability/testing.md#classificação-tdd)).
