# Policy: quality

## Objetivo
Garantir que o resultado seja testável, revisável e coerente com o plano.

## Regras
- **Validar antes de concluir.** Nenhum papel declara pronto sem rodar o menor comando que prova o resultado
  (chaves de `commands` do `harness.project.yaml`) e anexar a saída.
- **Escopo explícito.** Toda tarefa diz o que entra, o que fica fora e onde pode escrever (escopo do plano).
- **Evidência acima de suposição.** Afirmação sem saída de comando, arquivo e linha, ou fonte, não conta.
- Preservar o contrato funcional; não inventar comportamento fora dos critérios de aceite.
- Corrigir os próprios erros (lint, tipo, import) antes de passar adiante.
- Critério de aceite é frase verificável.
- **Contrato antes da regra.** Payload ou assinatura muda primeiro no contrato e no teste do contrato
  ([testing](../capability/testing.md#princípios)).
- **Dispensa de teste só declarada.** Arquivo sem TDD tem classificação e motivo no plano; nunca por omissão.
