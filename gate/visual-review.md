# Gate: visual-review

**Objetivo:** provar com evidência verificável que cada critério visual do plano foi conferido na tela, sem
exigir TDD em fontes `dispensa-estilo`/`dispensa-biblioteca`.

**Quando roda:** automaticamente no `finalReview` (`script/stage_runner.py`) quando
`resolve_feature_gates(plan)["visual_review"]` é verdadeiro, depois do gate configurado da etapa. Feature sem fonte
visual: `NOT_APPLICABLE`, nada é executado.

**Como roda:** `visual_review.command` do `harness.project.yaml` (ferramenta do próprio projeto) uma vez por
critério com layer `visual`. O Harness não instala navegador.

**Sucesso:** para todo critério visual, a ferramenta sai com 0, grava screenshot não vazio e relatório
`{"criterion": "<id>", "found": true, "summary": "<texto>"}`.

**Falha explícita (sem PASS fictício):** configuração ausente, executável inexistente, timeout, saída não zero,
screenshot ausente/vazio, relatório ausente/ilegível/de outro critério, `found` diferente de `true`, classificação
visual sem critério visual, plano inválido (ex.: fonte visual com `domain: true`).

**Evidência:** `.harness/runs/<run>/<feature>/evidence/finalReview/<tentativa>/visual-review/visual-review.json`
com `schemaVersion`, `feature`, `status`, `target` sanitizado (sem userinfo, query e fragmento), e por critério:
comando (com target sanitizado), exit code, screenshot e relatório com SHA-256. `validate_visual_evidence`
(e `visual_review.py --validate`) recalcula os hashes; o dashboard mostra status, hashes e erros de validação.
