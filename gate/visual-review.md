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

## Ferramenta de referência (opcional)

`tests/fixtures/visual_check_playwright.py` cumpre o contrato acima e serve de exemplo de `visual_review.command`.
O `{criterion}` do Harness é o id do critério; o que procurar na tela vai em `--expect` (texto visível, ou
`css=<seletor>`). Três motores, em `--engine`:

```yaml
visual_review:
  target: http://127.0.0.1:3000/painel
  timeout_s: 120
  # 1) Playwright do Python (pip install -r requirements-visual.txt; usa o Chromium do sistema, ADA_CHROMIUM)
  command: python tests/fixtures/visual_check_playwright.py --url {target} --criterion {criterion} --shot {screenshot} --report {report} --expect "Saldo atual"
  # 2) Playwright do próprio projeto alvo via node (ex.: pilla, pnpm em apps/web)
  #    ... --engine node-playwright --project-dir apps/web
  # 3) Fallback sem Playwright: chromium --headless --screenshot --dump-dom (só texto no DOM)
  #    ... --engine chromium-cli
```

O Harness exige que o screenshot seja um PNG de verdade (assinatura `\x89PNG\r\n\x1a\n`); arquivo com outro
conteúdo falha. Teste real: `tests/test_visual_real.py` (marker `visual_real`, servidor `http.server` local; skip se
faltar playwright/chromium/node; `ADA_REQUIRE_OPTIONAL=1` torna o skip uma falha; `ADA_PROJECT_WEB` aponta o
`apps/web` do projeto).
