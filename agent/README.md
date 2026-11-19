# Agentes

Papéis do harness. Quem executa cada papel é definido pelo adaptador da ferramenta ([adapter/](../adapter/README.md))
e mapeado em `roles` no `harness.project.yaml` do projeto. A definição de cada papel cita as
[policies](../policy/README.md) por caminho, sem copiar o texto delas.

| Papel | Responsabilidade | Etapa do [workflow feature](../workflow/feature.md) |
|---|---|---|
| `planner` | escopo, contrato, critérios de aceite (A<n>) e cenários (C<n>); não escreve código | plan |
| `plan-reviewer` | confere se o plano e os cartões (DoR e DoD) bastam para testar e implementar; somente leitura | plan-review |
| `test-writer` | testes antes do código (skill escrever-teste); não toca código de produção | tests |
| `reviewer` | revisa os testes (skill revisar-teste) e o diff final; somente leitura | review-tests, final-review |
| `implementer` | implementa até os testes revisados passarem; nunca altera teste revisado | implement |

O **orquestrador** é qualquer agente de IA que siga o checklist do workflow: chama os papéis, roda os gates e
grava o estado. Não há outro papel.
