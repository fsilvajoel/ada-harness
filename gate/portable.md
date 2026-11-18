# Gate: portable

## Objetivo
Garantir que o harness possa ser levado para outro projeto sem referência fixa ao projeto original nem ao ambiente.

## Critério de sucesso (PASS)
Nenhum arquivo do harness contém um dos textos de `portable.forbidden` do `harness.project.yaml` do projeto alvo
(nome do projeto, caminhos absolutos de máquina, caminhos de pastas do projeto). Esse yaml fica no projeto, fora
do harness.

## Critério de falha (FAIL)
- algum arquivo contém um texto proibido;
- o yaml não declara `portable.forbidden` (lista vazia não conta como aprovação).

## Evidência esperada
Saída de `python3 script/check_portable.py <harness.project.yaml do projeto>`: "Gate portable passou." ou uma linha por ocorrência
(`<arquivo> contém texto proibido: <texto>`).
