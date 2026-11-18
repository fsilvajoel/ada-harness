# Capability: planning

## Objetivo
Definir escopo, critérios de aceite, cenários e contrato antes da execução.

## Composição
- papel: `planner`
- policy: [quality](../policy/quality.md)
- gate: o orquestrador confere o plano (critérios `A<n>`, cenários `C<n>`, contrato) antes da etapa tests
- artefato: [plano.md](../workflow/feature-artefatos/plano.md)
- workflow: [feature](../workflow/feature.md), etapa plan
