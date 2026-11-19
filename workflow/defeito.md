# Workflow: defeito

## Objetivo
Registrar todo defeito encontrado depois do plano, corrigi-lo a partir de um teste vermelho e, no fechamento,
ajustar a regra (plano, checklist ou gate) que o deixou passar.

## Quem participa
O **orquestrador** registra, abre a correção e fecha. A correção usa os mesmos papéis do
[workflow feature](feature.md) (`planner`, `test-writer`, `reviewer`, `implementer`).

## Origem
Um defeito nasce de uma destas fontes:

| Origem | Quando |
|---|---|
| `revisão` | achado da revisão final não corrigido na hora (etapa 7 do workflow feature), inclusive "baixa" ou "risco aceito" |
| `e2e` | falha no gate [e2e](../gate/e2e.md) depois que a feature fechou |
| `mutação` | mutante sobrevivente achado depois que a feature fechou (gate [mutacao](../gate/mutacao.md)) |
| `uso` | comportamento errado visto por quem usa o produto |

## Arquivos
- `docs/defeitos/D<n>.md`: um por defeito ([molde](defeito-artefatos/defeito.md)). `n` é sequencial e não se reusa.
- `docs/defeitos/README.md`: índice de todos os defeitos ([molde](defeito-artefatos/indice.md)).
- `docs/plano/D<n>/`: pasta da correção, com os mesmos artefatos de uma feature.

## Status
`aberto` → `em correção` → `fechado`. Só o orquestrador muda o status, no `D<n>.md` e no índice ao mesmo tempo.

## Lista do orquestrador

### 1. registro
- [ ] Criar `docs/defeitos/D<n>.md` com origem, feature de origem, reprodução (entrada exata, resultado obtido,
      resultado esperado, fonte), severidade, camada que deveria ter pegado e status `aberto`.
- [ ] Acrescentar a linha no índice `docs/defeitos/README.md`.

### 2. correção
- [ ] Status `em correção` (no `D<n>.md` e no índice).
- [ ] Seguir o [workflow feature](feature.md) com `D<n>` como ID, em `docs/plano/D<n>/`. O plano cita a reprodução
      do defeito como critério e acrescenta a linha que faltava na tabela de domínio.
- [ ] A correção **começa por teste vermelho**: o primeiro cenário reproduz o defeito e falha contra o código
      atual (gate [expect-red](../gate/expect-red.md), sem esboço: o código existente faz o papel do esboço).
- [ ] Testes já assinados da feature de origem que a correção roda junto vão em `regressionTests` do
      [estado](feature-artefatos/estado.md). Alterar um teste já assinado de outra feature reabre a revisão dela.

### 3. fechamento
- [ ] Responder no `D<n>.md`: **qual regra deixou passar?** (item do plano, do checklist de bordas ou gate que
      deveria ter pegado e por que não pegou).
- [ ] Ajustar a regra: plano ([planning](../capability/planning.md)), checklist ([testing](../capability/testing.md))
      ou gate ([gates](../gate/README.md)). Se nenhum ajuste couber, justificar por escrito no `D<n>.md`.
      Sem resposta e sem ajuste (ou justificativa), o defeito não fecha.
- [ ] Registrar em "Regra ajustada" o arquivo e o commit do ajuste.
- [ ] Status `fechado` (no `D<n>.md` e no índice), com o commit da correção.

## Concluído quando
- todos os gates do [workflow feature](feature.md#pronto-quando) em PASS para `D<n>`;
- teste de reprodução vermelho antes e verde depois da correção;
- "qual regra deixou passar?" respondida e regra ajustada (ou justificativa registrada);
- `D<n>.md` e índice com status `fechado`.
