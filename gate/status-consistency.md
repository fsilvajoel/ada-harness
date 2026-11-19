# Gate: status-consistency

## Objetivo
Garantir que o `estado.json` (fonte única do andamento) acompanha o que o histórico git diz. O orquestrador só pilota
pelo estado; se um commit entrega uma etapa e o estado não registra, o `status.json` mente.

## Critério de sucesso (PASS)
Para cada feature (dos cartões em `cards_glob` e das pastas com `estado.json`) citada, como palavra inteira, no
assunto de um commit:
- commit `feat`, `fix`, `refactor` ou `perf`: `estado.json` existe e a etapa `implement` está cumprida;
- commit `test`: `estado.json` existe e a etapa `tests` está cumprida;
- nenhuma etapa fica em `running` num `estado.json` já commitado e sem alteração local (`running` só vale com o
  arquivo sujo no worktree, isto é, trabalho em curso); o orquestrador fecha a etapa com `done` ou um
  [status de falha](../workflow/feature-artefatos/estado.md#status-das-etapas) antes de commitar.

Commits `docs`, `chore`, `style` e `build` não exigem nada.

## Critério de falha (FAIL)
Qualquer item acima violado; `cards_glob` ausente; projeto fora de um repositório git (saída 2).

## Evidência esperada
Saída de `python3 script/check_status_consistency.py <raiz do projeto> [--rev <faixa>]`: nada e saída 0, ou uma
linha por problema, com a feature e o commit, e saída 1. Etapa cumprida segue as mesmas regras do comando `status`
([script/README.md](../script/README.md)).
