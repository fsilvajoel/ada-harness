# Progresso verificável da evolução 2.0

Plano para retomar e finalizar as ondas faltantes: [Plano do Claude — Ondas 3–6](./Plano-Claude-Finalizacao-Ondas-3-6.md).

## Status executivo

Ondas 3–5 concluídas e Onda 6 concluída em código (21/11). Com **worker fake e repositório Git temporário**, um único
comando (`script/run_feature.py`) leva uma feature de SDD aprovado até `closing`, parando apenas no gate humano de
aprovação do plano. **Nenhuma engine real foi exercitada**: o piloto da Onda 6 está **BLOQUEADO** (exige autorização
do operador, cota e projeto não crítico), então não há dado para recomendar engine nem afirmar economia de tokens.

| Onda | Status | Evidência / lacuna |
|---|---|---|
| 0 — Baseline | Concluída | Baseline histórica: 210 aprovados, 4 ignorados; compatibilidade V1 registrada. |
| 1 — Pipeline determinístico | Núcleo concluído | Event log, replay, transições, retry/fallback, rollback e worktrees testados; teste real despacha worker fake, roda gate e commita numa branch isolada. A execução configurada ainda é uma etapa por chamada. |
| 2 — Schemas e linters | Núcleo concluído | Schemas, linters e preflight integrados ao runner; resultado de engine normalizado/validado e salvo como evidência; Pilla passa em `artifact_lint --strict`. Não há ainda scheduler que consuma esses artefatos de ponta a ponta. |
| 3 — Política condicional de testes | Concluída (20/11) | `script/visual_review.py` + gate `visual-review` no `finalReview` quando a política resolve `visual_review=true`. Ferramenta do projeto via `visual_review` no `harness.project.yaml` (Harness não instala navegador). Evidência `visual-review.json` com target sanitizado, comando, exit code e SHA-256 de screenshot/relatório; `validate_visual_evidence` recalcula hashes; dashboard mostra. `tests/test_visual_review.py`: 12 testes (skip TDD visual, PASS com hash, adulteração, não visual sem execução, `domain:true` falha, classificação sem critério visual, config/executável ausente, timeout, sem screenshot, sem relatório, critério não encontrado, crash, integração `finalReview` FAIL→PASS em Git temporário). Correção: `run_stage` numera tentativas globalmente pelo replay — um novo run não sobrescreve evidência de tentativa anterior. Lacuna: nenhuma ferramenta visual real foi exercitada (só fake). |
| 4 — Dashboard | Concluída (21/11) | `script/attempt_manifest.py`: manifesto imutável por tentativa (papel, executor/cadeia, modo, gate, `allowedPaths`, skills, SHA-256 de request e config, base SHA, só nomes de variáveis de segredo) + evento `attempt_manifest` com hash. `run_configured_stage` grava o manifesto antes do preflight. Dashboard mostra manifestos e elegibilidade; padrão somente leitura; `--enable-rerun` só em loopback, token por processo, Host/Origin, Content-Type, corpo limitado, campos exatos, elegibilidade recalculada do disco. `tests/test_rerun.py`: 11 testes (manifesto completo sem valor de segredo; rerun válido cria tentativa 2 e preserva a 1, sem lock; config/request/manifesto alterados bloqueiam sem chamar worker; base mudou; stage errada/aprovada; worktree ativa e lock; run inexistente/ID inválido; HTTP desabilitado por padrão=405; token/Origin/Host/Content-Type/campo extra/traversal rejeitados; 404/409; rerun 200 após "reinício" só com estado em disco; bind não-loopback recusado). Lacuna: lock órfão após crash exige remoção manual. |
| 5 — SDD completo | Concluída (21/11) | `schema/feature-contract.schema.json` + `script/feature_contract.py` (contrato versionado: hashes de PRD/ADR/RFC, plano, cartões, config e request por stage; papel, cadeia de executores, gate, `allowedPaths`, skills, modo, cartões, aplicabilidade; `lint_contract` rejeita cadeia/path/gate/cartão/ordem/ciclo/campos extras). `script/run_feature.py` (CLI; exit 0 done, 1 blocked, 2 uso, 3 needs_input): verifica aprovação SDD por hash, planner gera plano/cartões em staging, lint e publicação sem sobrescrever, `questions.json` → needs_input, aprovação humana do plano, sequência refinement→tests→testReview→implement→mutation_e2e (pulado se a política não exigir)→finalReview→closing encadeando commits em `harness/*`, nada é mesclado; re-verifica hashes do contrato/SDD/config/request antes de cada stage; retomada idempotente; `--retry-failed`. `run_card.py` segue como fluxo V1, não duplicado. Correção em `worktree.commit_attempt`: `git add` só dos paths alterados (já validados), um `allowedPath` inexistente quebrava o commit. `tests/test_run_feature.py`: 15 testes (fluxo completo com só o gate de aprovação; perguntas→regeneração; SDD não aprovado; planner fora de escopo/inválido; falha e retomada com `--retry-failed`; adulteração de SDD/config/request e do hash do contrato; fallback configurado; escrita fora de `allowedPaths` bloqueia e descarta; lint do contrato; contrato sem `closing`; determinismo). Lacuna: só worker fake. |
| 6 — Otimização | Código concluído; **piloto BLOQUEADO** | Auditoria: consumo vem só de `attempt_result.usage` (worker `cli-agent` só preenche com `usage_jq`); ausência = desconhecido. Correção no legado `summarize_events`: engine com tentativa sem `usage` ia a custo 0 — agora fica em `unpriced_engines` e o custo total é `null`. Novo `build_report` + CLI `script/metrics.py <raiz> [--config]`: por feature (run/feature) e por engine — tentativas, taxa de sucesso na 1ª tentativa, retries/taxa, fallbacks de/para/taxa, gates executados/falhos, tipos de falha, duração, tokens conhecidos vs `usage_unknown_attempts` (desconhecido ≠ zero), `estimated_cost` só com `workers.<nome>.rates` explícitas e consumo completo, rotulado `cost_basis: estimate`, senão `null` + `cost_unavailable`. Sem texto de prompt/reason/saída. `tests/test_metrics_report.py`: 5 testes (agregação; engine sem usage; falha com tipo; taxa ausente; CLI lendo `.harness/runs` sem vazar texto); mutação manual da regra de desconhecido derruba o teste. Default de engine inalterado. **Piloto real: BLOQUEADO** (autorização do operador). |

## Validação desta execução

- Suíte completa (21/11): **321 passed, 4 skipped** (`.venv/bin/python -m pytest -q`, 43,6 s).
- `artifact_lint.py /home/debian/code/pilla --strict`: **PASS**.
- `compileall`, parsing dos JSON schemas e `git diff --check`: **PASS**.
- Testes direcionados: `test_run_feature.py` + `test_rerun.py` **26 passed**; seleção path/commit/allowed/scope/rerun **39 passed**; métricas + dashboard **11 passed**.
- `/home/debian/code/pilla` não foi alterado (só fixture de lint).
- A integração usa um worker de teste local; nenhum modelo real foi chamado.

## O que já pode ser executado

`run_feature.py <raiz> --run <id> --feature <ID>` executa a feature inteira pelo contrato (ver Onda 5).
`stage_runner.py` resolve o worker pelo `harness.project.yaml`, despacha via `dispatch.py`, cria worktree isolada,
executa gate configurado, grava evidência/resultado e commita o sucesso em branch `harness/*`. A branch não é mesclada
automaticamente. `run_card.py` continua sendo um fluxo separado para cartões `test-author`/`implement`; ele não
registra o pipeline 2.0 nem cria commits.

## Bloqueios e lacunas restantes

Itens 1–5 da lista anterior (contrato, geração a partir do SDD, driver sequencial, snapshot/hash antes de rerun,
revisão visual) estão atendidos e testados com fakes. Resta:

1. **BLOQUEADO — piloto real da Onda 6**: rodar `run_feature.py` com engines reais em projeto não crítico, medir
   qualidade/custo/latência com `metrics.py` e comparar com a baseline antes de qualquer troca de engine padrão.
   Requer autorização do operador e cota.
2. ~~Ferramenta de revisão visual real~~ — **fechada (opcional, testado)**: `tests/fixtures/visual_check_playwright.py` (motores `playwright`, `node-playwright` com o Playwright do projeto alvo, `chromium-cli`), `tests/test_visual_real.py` (marker `visual_real`, servidor local, PASS/FAIL, PNG real); o Harness agora rejeita screenshot sem assinatura PNG. Documentado em `gate/visual-review.md`.
3. ~~Validação por `jsonschema`~~ — **fechada (opcional, testado)**: `tests/test_schemas_jsonschema.py` (marker `schema_real`) valida os 7 schemas (Draft 2020-12) e os artefatos gerados (cartão, plano, state, evidência, engine-result, aprovação, contrato) contra o linter; nenhuma divergência schema × linter encontrada. O linter segue canônico; `jsonschema` só em `requirements-dev.txt`.

   Suíte: 349 passed/4 skipped com `ADA_REQUIRE_OPTIONAL=1` e dependências instaladas (os 4 skips são anteriores e não opcionais); sem as opcionais, `test_visual_real` (7) e `test_schemas_jsonschema` (15) ficam em skip.
4. Lock órfão de rerun após crash exige remoção manual.
5. Engines sem `usage_jq` aparecem como consumo desconhecido; custo dessas engines fica indisponível.
