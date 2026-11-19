# Refinamento funcional <ID da feature>

refinador: refiner
rodada: <1 | 2>

## Requisitos
- [ ] Todo `A<n>` é observável e está no molde fixo (entrada, domínio, saída, erro, fonte, camada).
- [ ] Toda regra tem fonte; toda entrada externa tem linha na tabela de domínio, com rejeição e erro exato.
- [ ] Contrato, arquivos de produção, dependências e arquivos de teste previstos bastam para escrever os testes e
      implementar sem adivinhar.

## Cartões
| Cartão | DoR | DoD |
|---|---|---|
| <ID> | ok / ajustado / pendência | ok / ajustado / pendência |

## Veredito
<pronto | ajustado | pendencia-pm>

## Mudanças
- <arquivo> (<cartão ou A<n>>): <o que foi completado> (fonte: <doc aprovado, seção>); "nenhuma" se `pronto`

## Pendências de PM
- <cartão>: <pergunta> (entrada gravada na fila `pm_queue`); "nenhuma" se zero
