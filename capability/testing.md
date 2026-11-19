# Capability: testing

## Objetivo
Definir e validar o comportamento esperado em testes antes de a implementação ser aceita.

## Composição
- skills: `escrever-teste`,
  `revisar-teste`
- papéis: `test-writer`, `reviewer`
- policy: [quality](../policy/quality.md)
- gates: [expect-red](../gate/expect-red.md), [testes-revisados](../gate/testes-revisados.md),
  [cobertura](../gate/cobertura.md), [mutacao](../gate/mutacao.md), [e2e](../gate/e2e.md),
  [auditoria](../gate/auditoria.md)
- artefatos: [testes.md](../workflow/feature-artefatos/testes.md), [auditoria.md](../workflow/feature-artefatos/auditoria.md)

## Checklist de bordas
Para cada entrada em texto ou número da tabela de domínio do plano, o teste cobre (ou o plano justifica por que
não se aplica):

- [ ] vazio (`""`)
- [ ] só espaços (`"   "`)
- [ ] espaços em volta (`" 6 "`)
- [ ] notação exponencial (`"1e1"`)
- [ ] separador decimal ou de milhar errado (`"6.5"` onde se espera vírgula, `"1.000,00"` onde não há milhar)
- [ ] negativo
- [ ] zero
- [ ] limite (mínimo e máximo do domínio)
- [ ] limite + 1 (logo fora do mínimo e do máximo)
- [ ] muito grande (além do inteiro seguro da linguagem, texto longo)
- [ ] caractere inválido (letra, símbolo, caractere de controle)

Cada entrada do plano tem ao menos um caso de rejeição com o erro esperado exato.

## Camadas
| Camada | Quando é obrigatória | Como se prova |
|---|---|---|
| `unit` | todo critério com valor exato | teste unitário com asserção exata |
| `prop` | parser e cálculo | teste de propriedade (biblioteca de geração de casos, do tipo fast-check) sobre o domínio inteiro, além dos exemplos |
| `e2e` | fluxo de usuário descrito no PRD | teste de ponta a ponta no navegador (Playwright ou equivalente), listado em `e2e` do estado; gate [e2e](../gate/e2e.md) |
| `mut` | arquivo de domínio (regra de negócio, cálculo) | score de mutação acima do mínimo do projeto; gate [mutacao](../gate/mutacao.md) |

Cobertura de 100% não prova domínio: ela mostra que a linha rodou, não que a entrada errada foi recusada.
