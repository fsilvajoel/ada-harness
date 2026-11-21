# Onda 0 — Baseline

## Resultados

- data: 20/11/2026;
- suíte: 210 testes passaram;
- testes ignorados: 4;
- estado da versão atual: preservado;
- ambiente: `.venv/bin/pytest -q`.

## Contratos compatíveis

- `harness.project.yaml`;
- `estado.json`;
- `stages.plan`, `stages.refinement`, `stages.tests`, `stages.testReview`, `stages.implement`, `stages.review`;
- `testShas`;
- `gates`;
- `cards_glob`;
- `status.py`;
- `dispatch.py`;
- `run_card.py`;
- adaptadores `api-chat`, `api-tools` e `cli-agent`;
- gates existentes.

## Invariantes da evolução

1. estados antigos continuam carregáveis;
2. uma stage não é marcada concluída sem evento de resultado;
3. event log é append-only;
4. `estado.json` pode ser reconstruído;
5. gate inválido não aceita implementação;
6. stage escrito fora do scope é rejeitada;
7. fallback não altera gates ou test hashes;
8. nenhum motor muda o event log diretamente sem o pipeline validar o evento.
