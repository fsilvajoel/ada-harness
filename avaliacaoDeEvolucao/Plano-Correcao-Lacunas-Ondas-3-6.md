# Plano de correção das lacunas das ondas 3–6 do Ada Harness 2.0

## Instrução de execução

Este documento é **só o plano**. Nada aqui foi implementado. Execute um item por vez, na ordem recomendada, e só
marque um item como concluído quando o critério de aceite tiver passado com evidência (saída de comando, teste).

Antes de editar:

1. Leia este plano, [`Plano-Claude-Finalizacao-Ondas-3-6.md`](./Plano-Claude-Finalizacao-Ondas-3-6.md) e
   [`progresso-ondas-20.11.md`](./progresso-ondas-20.11.md).
2. Confira o estado do repositório (`git status`, `git log --oneline -10`, suíte). Preserve o trabalho existente.
3. O código é a fonte da verdade. As linhas citadas abaixo valem para o commit `8ec7812` (= `14ea662` em conteúdo);
   se mudarem, localize pelo nome da função.

## Estado verificado (21/11)

- Branch local `feat/ondas-3-6-harness-2` em `14ea662`, igual à origin; working tree limpa; uma worktree.
- `main` local em `429ae29`, **7 commits atrás** de `origin/main` (`8ec7812`); fast-forward possível.
- `8ec7812` é o merge commit da PR #1 (pais `5cd8152` e `14ea662`); `git diff 14ea662 8ec7812` vazio; PR **MERGED**
  em 2026-11-21T03:41:02Z.
- Commits da PR (`5cd8152..14ea662`): `429ae29` run_card glob, `b6528bb` base 2.0, `480e67d` visual,
  `44278b1` dashboard/rerun, `839f9e3` feature/run_feature, `d65fda3` metrics, `14ea662` docs.
- Não há `.git/hooks` ativos, `.github/`, `pyproject.toml`, `requirements*.txt`, `pytest.ini` nem `conftest.py`.
- `.venv`: pytest 9.2.0 e PyYAML 6.0.3 (usado, mas não declarado). **jsonschema e playwright ausentes.**
- Sistema: `/usr/bin/chromium` 156.0.8078.79, `node` e `npx` disponíveis.
- Suíte: **321 passed, 4 skipped** (os 4 skips são `tests/test_render_issue.py`, exigem `ADA_TEST_PROJECT`).

## Guardrails obrigatórios

- **Nada de `--no-verify`**, `core.hooksPath`, `LEFTHOOK=0` ou qualquer forma de desligar hooks. Falhou hook:
  corrigir a causa.
- **Segredos fora do git**: nenhuma chave, token, cookie ou conteúdo de `.env` em arquivo, evento, manifesto,
  screenshot, relatório ou log de teste. Só nomes de variáveis.
- **Tipagem forte**: anotações em toda função nova; nada de `Any`/`object` genérico onde um tipo concreto
  (`TypedDict`, `dataclass`, `Literal`) cabe.
- **Sem ampliar escopo**: cada item corrige só a sua lacuna. Melhorias vistas no caminho viram nota, não código.
- Testes automatizados não chamam modelo real, não consomem cota e não usam rede externa (loopback local é aceito).
- Não alterar o Pilla (`/home/debian/code/pilla` e `pilla-wt-*`); usá-lo só como fixture de `artifact_lint --strict`.
- Produção (srv51, srv126, VM 260, VM 257, CTs 253/254) é só leitura.
- Não mesclar `harness/*` automaticamente. Não commitar nem dar push sem pedido explícito do Joel (exceção
  delimitada: item 6, ver lá).
- **Piloto real (item 5) não é executado** por este plano: só preparado.

## Ordem de execução recomendada

| Ordem | Item | Tamanho | Modelo sugerido | Depende de |
|---|---|---|---|---|
| 1 | 6 — Sincronizar `main` e apagar branch | pequeno | haiku | — |
| 2 | 3 — YAML de exemplo válido | pequeno | haiku | 6 |
| 3 | 4 — Verificar commits intermediários | pequeno | haiku (sonnet se algo falhar) | 6 |
| 4 | 1 — Lock órfão / recuperação de crash | médio | opus (desenho) + sonnet (código) | 6 |
| 5 | 2 — Playwright e jsonschema opcionais | médio | sonnet | 6, 3 |
| 6 | 5 — Preparar o piloto da Onda 6 | médio (só doc/checklist) | opus | 1, 2, 3 |

Motivo: o item 6 põe a `main` local no estado real, e todos os demais partem dela (branch nova por item, ex.:
`fix/lacuna-3-example-yaml`). Os itens 3 e 4 são baratos e não tocam código de runtime. O item 1 mexe no núcleo de
execução e precisa vir antes do piloto. O item 2 depende do 3 porque o teste do exemplo deve validar a seção
`visual_review` corrigida. O item 5 só faz sentido com 1–3 resolvidos (piloto sem recuperação de crash é arriscado).
Os itens 3, 4 e 1 podem rodar em paralelo em worktrees separadas, porque não tocam os mesmos arquivos.

---

## Item 6 — Sincronizar a `main` local e apagar `feat/ondas-3-6-harness-2`

### Diagnóstico

- `main` local `429ae29` está 7 commits atrás de `origin/main` `8ec7812`; `git merge-base --is-ancestor` confirma
  fast-forward.
- `14ea662` (ponta da branch) é ancestral de `origin/main`, e `git diff 14ea662 8ec7812` é vazio: apagar a branch
  não perde commit nem conteúdo.

### Solução

```bash
cd /home/debian/code/harness-ada
git fetch origin --prune
git status --porcelain                                  # precisa estar vazio
git merge-base --is-ancestor origin/feat/ondas-3-6-harness-2 origin/main && echo merged-ok
git switch main
git merge --ff-only origin/main                         # nunca merge com commit nem reset --hard
git branch -d feat/ondas-3-6-harness-2                  # -d (não -D): o git recusa se não estiver mesclada
git push origin --delete feat/ondas-3-6-harness-2       # remoto: só com o pedido do Joel (ver Risco)
git fetch --prune
```

### Testes / aceite

- `git rev-parse main` = `git rev-parse origin/main` (= `8ec7812` ou mais novo).
- `git branch -a --list '*ondas-3-6*'` vazio.
- `.venv/bin/python -m pytest -q` na `main`: **321 passed, 4 skipped**.

### Risco

- Baixo. `-d` e `--ff-only` falham com segurança em vez de perder trabalho.
- Apagar a branch remota é irreversível do lado do GitHub (dá para recriar a partir de `14ea662`, que está na
  `main`). O item 6 da tarefa pede isso explicitamente, mas o executor deve confirmar a linha `merged-ok` antes do
  `push --delete`. Se o pedido do Joel não cobrir o remoto, parar após o `branch -d` local.

---

## Item 3 — `harness.project.example.yaml` não é YAML válido

### Diagnóstico

- `yaml.safe_load` falha com "mapping values are not allowed here" na l.59, col. 47 (em `429ae29` era l.55; o defeito
  existe em `429ae29`, `b6528bb` e `8ec7812`).
- Causa: `": "` dentro de placeholders `<...>` sem aspas, em 4 linhas:
  - l.59 `cards_glob: <glob dos arquivos de cartões, ex.: docs/plano/E*.yaml>`
  - l.61 `- <prefixo de caminho, ex.: o app web>`
  - l.92 `command: <comando, ex.: node scripts/visual-check.mjs ...>`
  - l.93 `target: <URL local da tela, ex.: http://localhost:5173>`
- O parser simples do Harness (`parse_simple_yaml`, fallback quando não há PyYAML) também falha na l.83
  `mutation: {tool: ..., min_score: <score mínimo, 0 a 100>}`: vírgula dentro de mapa inline.
- O `README.md` l.15 manda copiar este molde. Nenhum teste carrega o exemplo, por isso o defeito passou.
- Já validado numa cópia (scratchpad, arquivo real intacto): com aspas duplas nas 4 linhas e
  `min_score: <score mínimo de 0 a 100>`, PyYAML e `parse_simple_yaml` passam, `load_visual_config` lê a seção, e
  `load_contract_config(cfg, "E1-F01")` devolve as 7 stages.

### Solução

1. Pôr aspas duplas nos 4 valores (ex.: `cards_glob: "<glob dos arquivos de cartões, ex.: docs/plano/E*.yaml>"`).
2. l.83: trocar para `min_score: <score mínimo de 0 a 100>` (sem vírgula).
3. Não mudar chaves nem semântica; os comentários continuam iguais.

### Testes

`tests/test_example_config.py` (novo, tipado):

- `yaml.safe_load` do exemplo devolve `dict`;
- `parse_simple_yaml` do exemplo devolve o mesmo conjunto de chaves de topo;
- `load_visual_config` devolve `command`, `target` e `timeout_s == 120`;
- `load_contract_config(..., "E1-F01")` devolve exatamente as stages de `STAGES[1:]`, na ordem, com `{feature}`
  substituído;
- regressão: o teste falha se uma linha nova do exemplo reintroduzir `": "` sem aspas (o próprio `safe_load` garante).

### Aceite

- Os testes novos passam; suíte completa sem regressão (≥ 321 passed).
- `git diff` toca só `harness.project.example.yaml` e o teste novo.

### Risco

Mínimo (arquivo de molde). Projetos que copiaram o molde antigo já teriam quebrado no load; não há compatibilidade a
preservar.

---

## Item 4 — Commits intermediários da PR sem teste individual

### Diagnóstico

- Só a ponta `14ea662` foi validada (321/4). Os 6 commits anteriores de `5cd8152..14ea662` nunca rodaram a suíte
  isoladamente. Como a PR entrou por **merge commit** (`8ec7812`), todos eles estão na história da `main` e um
  `git bisect` futuro pode cair num commit quebrado.
- Indício concreto: o exemplo YAML está inválido desde `429ae29` (item 3); a correção de `commit_attempt`
  (`git add` só dos paths alterados) veio em `839f9e3`, então commits anteriores podem falhar testes de integração.
- Não há CI (`.github/` ausente) nem hook para impedir isso.

### Solução (só leitura sobre a história; nada é reescrito)

1. Worktree descartável fora do repo:
   `git worktree add --detach ../.verify-ondas-3-6 5cd8152`.
2. Laço com o Python absoluto do `.venv` principal:

   ```bash
   PY=/home/debian/code/harness-ada/.venv/bin/python
   for c in $(git rev-list --reverse 5cd8152..14ea662); do
     git -C ../.verify-ondas-3-6 checkout -q --detach "$c"
     ( cd ../.verify-ondas-3-6 && $PY -m pytest -q -p no:cacheprovider \
       && $PY -m compileall -q script tests \
       && git diff --check "$c^" "$c" \
       && $PY script/artifact_lint.py /home/debian/code/pilla --strict ) > "$SCRATCH/verify-$c.log" 2>&1
     echo "$c $?"
   done
   git worktree remove ../.verify-ondas-3-6
   ```

3. Registrar a tabela commit → PASS/FAIL (com o nome do teste que falhou) na seção de validação de
   `progresso-ondas-20.11.md`. **Não reescrever a `main`**: história publicada fica como está. Se algum commit falhar,
   anotar como "não bisectável" e, para bisect, usar `git bisect skip` nesses commits.
4. Opcional, dentro do escopo: `script/verify_commits.sh <base> <head>` com o mesmo laço, para PRs futuras. Política
   no plano: PR multi-commit roda o script antes do merge, ou é mesclada com squash. CI no GitHub só com ok do Joel.

### Testes / aceite

- Os 7 commits têm resultado registrado (PASS/FAIL e motivo).
- A worktree temporária foi removida; `git worktree list` mostra só a principal.
- Nenhum commit novo na `main` por causa deste item (exceto a nota de progresso e o script opcional, em branch própria).

### Risco

- Baixo. O laço não toca a `main`.
- Commits antigos podem gravar `.harness/` ou `__pycache__` na worktree temporária; ela é descartada.
- `artifact_lint` lê o Pilla, sem escrever nele.

---

## Item 1 — Lock órfão após crash de rerun

### Diagnóstico

Em `script/attempt_manifest.py`:

- l.29 `LOCK_NAME = "rerun.lock"`.
- `rerun_eligibility` (l.221–277): exige `current_stage == stage` e status `failed`; `active_stage_worktrees`
  (l.216–218) bloqueia se o diretório da stage tiver **qualquer** entrada; l.275 bloqueia com
  "rerun já em andamento" se `(base / LOCK_NAME).exists()`.
- `rerun_stage` (l.283–325): chama a elegibilidade **antes** do lock (l.293), cria o lock com
  `os.open(O_CREAT | O_EXCL)` (l.299), grava o PID (l.303) e faz `unlink` no `finally` (l.322).

Problemas:

1. **Lock órfão**: SIGKILL, OOM ou queda da VM pulam o `finally`, e o arquivo fica. Toda chamada seguinte recebe
   "rerun já em andamento" para sempre. Hoje a saída é remover à mão.
2. **Remover só o lock não basta**: `run_stage` (`script/stage_runner.py`, ~l.279) já gravou `stage_started`, então o
   replay deixa a stage em `running`, e a elegibilidade exige `failed`. Fica também o diretório da worktree em
   `../.harness-worktrees/<repo>/<run>/<feature>/<stage>/<attempt>`, que bloqueia por `active_stage_worktrees`.
3. **TOCTOU**: a elegibilidade não é rechecada sob o lock. Dois pedidos quase simultâneos podem passar pela checagem;
   um perde no `O_EXCL`, mas o estado lido antes do lock pode já ter mudado.
4. **`run_feature.py` não usa lock nenhum**, então pode rodar junto com um rerun do dashboard no mesmo run/feature.
   As mensagens l.371 e l.474 ("stage X ficou running; recuperação manual necessária") não dizem como recuperar.
5. Já existe o padrão certo no repo: `script/run_card.py` (l.34, l.77, l.526–535) usa `fcntl.flock(LOCK_EX | LOCK_NB)`,
   que o kernel libera quando o processo morre.

### Solução

1. **Lock único por run/feature** em `script/run_lock.py` (novo, tipado):
   - arquivo `.harness/runs/<run>/<feature>/run.lock`; `fcntl.flock(fd, LOCK_EX | LOCK_NB)`; context manager
     `hold_run_lock(base) -> Iterator[LockInfo]`;
   - o arquivo **nunca é apagado**: apagar abre uma corrida de inode, em que dois processos travam arquivos
     diferentes;
   - metadados só para diagnóstico, regravados após adquirir: `pid`, `hostname`, `boot_id`
     (`/proc/sys/kernel/random/boot_id`), `started_at`, `command` (`rerun` ou `run_feature`). Sem argumentos, env nem
     caminhos de segredo;
   - `LockBusy` com o holder lido do arquivo, para a mensagem.
2. **`rerun_stage`**: adquirir o lock primeiro e só então rodar `rerun_eligibility` (recheck sob o lock, o que fecha
   o TOCTOU). Remover o `O_EXCL` e o `unlink`.
3. **`rerun_eligibility`**: trocar a checagem `exists()` (l.275) por uma tentativa não bloqueante de `flock`
   (`probe_run_lock(base) -> LockInfo | None`); arquivo existente sem holder **não** bloqueia.
4. **`run_feature.py`**: envolver a execução de cada run/feature com `hold_run_lock`; se ocupado, sair com exit 1 e
   uma mensagem citando o holder.
5. **Compatibilidade com o `rerun.lock` legado** (O_EXCL de versões anteriores): bloquear só se o PID gravado estiver
   vivo **e** o `boot_id` for o mesmo (o legado não tem boot_id, então só PID vivo). Se o PID estiver morto, tratar
   como órfão e mostrar o diagnóstico, sem apagar automaticamente; a remoção é feita pelo comando abaixo.
6. **Recuperação explícita**: `script/recover_run.py <raiz> --run <id> --feature <ID> [--apply]`:
   - dry-run por padrão: lista o que faria;
   - adquire o `run.lock` e recusa se houver holder vivo;
   - para cada stage em `running` no replay: grava `stage_failed` com `reason="interrupted"` via
     `pipeline.finish_stage` (l.196–240, já aceita `running → failed`);
   - remove a worktree órfã da tentativa com `worktree.remove_attempt_worktree(repo, dest, force=True,
     delete_branch=False)` (l.149–172, já valida namespace e caminho) e roda `git worktree prune`. A branch
     `harness/*` é preservada como evidência;
   - remove o `rerun.lock` legado órfão;
   - grava o evento `run_recovered` com stage, attempt e o que foi limpo;
   - idempotente: rodar duas vezes não gera eventos duplicados.
7. As mensagens de `run_feature.py` (l.371, l.474) e o motivo no dashboard (`script/dashboard.py` l.192–207) passam a
   citar `script/recover_run.py ... --apply`. O dashboard **não** ganha botão de recuperação (sem ampliar escopo).

### Testes

`tests/test_run_lock.py` + ajustes em `tests/test_rerun.py`:

- **Crash real**: um subprocess adquire o lock e entra em `stage_started`, depois toma `SIGKILL`. Em seguida o lock está
  livre (`probe` devolve `None`), a stage está `running`, e `rerun_eligibility` diz "use recover_run". Depois do
  `recover_run --apply`, a stage fica `failed/interrupted`, a worktree some, a branch fica, e o rerun é elegível e
  cria a tentativa N+1.
- **Concorrência**: dois processos `rerun_stage` sincronizados por barreira; exatamente um roda e o outro recebe
  `LockBusy`.
- **`run_feature` × rerun**: com o lock segurado por `run_feature`, o rerun recusa.
- **Holder vivo**: `recover_run` recusa e não grava evento.
- **Legado**: `rerun.lock` com PID vivo bloqueia; com PID morto, é diagnosticado como órfão e removido só pelo
  `recover_run --apply`.
- **TOCTOU**: o estado muda entre a checagem do dashboard e a aquisição do lock (o teste injeta uma aprovação), e o
  recheck sob o lock bloqueia.
- **Dry-run** não altera eventos nem disco; a segunda execução de `--apply` não faz nada.
- Ajustar `test_rerun.py` l.139 (hoje exige que o lock suma) e l.185–193 (o lock passa a ser `flock`, não a
  existência do arquivo).

### Aceite

- Nenhum caminho de código depende de `unlink` em `finally` para liberar exclusão.
- Os testes novos e os 11 de `test_rerun.py` (ajustados) passam; os 15 de `test_run_feature.py` continuam passando;
  suíte sem regressão.
- Mutação manual: remover o recheck sob o lock derruba o teste de TOCTOU; voltar ao `exists()` derruba o de crash.

### Risco

- Médio: mexe no caminho de execução de stages. `flock` é local (não vale em NFS), e isso deve ficar documentado em
  comentário; `.harness/` é local.
- A recuperação marca como falha uma stage que pode ter terminado e só não registrado. É aceitável (o rerun refaz),
  e o dry-run mostra antes.
- Não fazer recuperação automática no startup: só o comando explícito.

---

## Item 2 — Ferramenta visual real (Playwright) e jsonschema

### Diagnóstico

- `script/visual_review.py` (398 linhas) delega ao comando do projeto (`VisualConfig`: `command`, `target`,
  `timeout_s`; `load_visual_config` l.77–99). `_review_criterion` (l.140–222) dá FAIL explícito para executável
  ausente, timeout, exit ≠ 0, screenshot ausente ou vazio, relatório ausente, ilegível ou sem critério,
  `found != true` e falta de `summary`.
- Os 12 testes de `tests/test_visual_review.py` usam só `fake_browser.py`; nenhum navegador real foi exercitado.
- A checagem do screenshot só olha tamanho > 0, sem assinatura PNG (o fake grava `b"\x89PNG fake"`).
- `schema/*.json` (approval, card, engine-result, evidence, feature-contract, plan, state) são Draft 2020-12, mas
  nada os valida com `jsonschema`; os linters próprios (`lint_contract`, `artifact_lint`) são a validação canônica e
  podem divergir dos schemas sem ninguém notar.
- O guardrail do plano original ("não instalar dependências no Harness sem provar necessidade") continua valendo: o
  runtime não passa a depender de nada; só testes e o molde opcional.

### Solução

1. **Declarar dependências** (nada disso existe hoje):
   - `requirements-dev.txt`: `pytest==9.2.0`, `PyYAML==6.0.3`, `jsonschema==<versão fixada>`;
   - `requirements-visual.txt` (opcional): `playwright==<versão fixada>`. Sem `playwright install`: usar o
     `/usr/bin/chromium` do sistema via `executable_path` (configurável por `ADA_CHROMIUM`), sem download de navegador;
   - README: duas linhas de instalação (`.venv/bin/pip install -r ...`).
2. **Ferramenta de referência** `tests/fixtures/visual_check_playwright.py` (tipada), que cumpre o contrato
   existente sem mudar o `visual_review.py`: recebe `--url --criterion --shot --report`, abre a página headless, tira
   o screenshot, procura o critério (texto ou seletor) e grava `{"criterion", "found", "summary"}`.
3. **Fallback sem Python-playwright**: a mesma fixture com `--engine chromium-cli` usa
   `chromium --headless --screenshot=<shot> --dump-dom <url>`, procurando o critério no DOM. Documentar os dois
   caminhos em `gate/visual-review.md`, como exemplo de `visual_review.command` para o projeto alvo.
4. **Endurecimento mínimo** (dentro do escopo): `_review_criterion` exige a assinatura PNG
   (`\x89PNG\r\n\x1a\n`) no screenshot. O `fake_browser.py` passa a gravar um PNG mínimo válido.
5. **Teste de integração marcado**:
   - `pytest.ini` com `markers = visual_real: usa navegador real` e `schema_real: usa jsonschema`;
   - `tests/test_visual_real.py` sobe `http.server` em `127.0.0.1` numa porta aleatória, servindo um HTML fixo
     (sem rede externa), roda `visual_review` com a fixture e verifica PASS, hash e um PNG real;
   - roda um caso FAIL (critério ausente na página);
   - skip limpo se faltar playwright ou chromium (`pytest.importorskip` / `shutil.which`), com
     `ADA_REQUIRE_OPTIONAL=1` transformando o skip em falha (para rodar de propósito).
6. **jsonschema** em `tests/test_schemas_jsonschema.py` (`importorskip("jsonschema")`):
   - `Draft202012Validator.check_schema` em todos os `schema/*.json`;
   - valida os artefatos que os testes existentes já geram (contrato de feature, evidência, engine-result, state)
     contra o schema correspondente;
   - casos negativos: um artefato que `lint_contract` rejeita também deve ser rejeitado pelo schema, quando a regra
     for estrutural.
   - Divergência encontrada: o **linter é canônico**; corrigir o schema, com o caso registrado no teste. Não colocar
     `jsonschema` no runtime.

### Testes / aceite

- Sem as dependências opcionais: a suíte passa, com os novos testes em skip (contagem de skips documentada).
- Com `requirements-dev.txt` e `requirements-visual.txt` instalados e `ADA_REQUIRE_OPTIONAL=1`, os testes
  `visual_real` e `schema_real` passam, sem skip e sem rede externa.
- O PNG falso (`b"\x89PNG fake"`) agora falha; os 12 testes visuais existentes passam com o fake atualizado.
- `progresso-ondas-20.11.md`: as lacunas 2 e 3 ficam "fechadas (opcional, testado)".

### Risco

- Médio-baixo. A versão do Chromium do sistema pode não casar com a do Playwright fixado: por isso existe o fallback
  `chromium-cli`, e o teste aceita qualquer dos dois.
- O teste visual é mais lento (segundos); fica atrás do marker.
- jsonschema pode revelar divergências schema × linter, o que pode crescer de escopo: corrigir só o schema e listar
  o restante como nota.

---

## Item 5 — Preparar o piloto real da Onda 6 (NÃO executar)

> Este item **não autoriza nem propõe executar** o piloto. Entrega só a preparação. A execução exige ok explícito do
> Joel, dado depois de ler o checklist abaixo.

### Diagnóstico

- `script/run_feature.py` (CLI l.527–530: `project_root --run --feature --retry-failed`) roda a feature inteira, mas
  **não tem teto de custo ou orçamento por feature**. Os limites existentes vêm da config: `max_attempts`,
  `timeout_s`, `fallbacks`, e `quota_reserve` nos adapters api-chat/api-tools (`script/api_chat.py` l.289–290,
  `script/api_tools.py`).
- `script/metrics.py <raiz> [--config]` só estima custo com `workers.<nome>.rates`; `cli_agent` só mede consumo com
  `usage_jq`. Sem isso, o consumo aparece como desconhecido (correto, mas inútil para comparar).
- Há a baseline em `avaliacaoDeEvolucao/onda-0-baseline.md` e a Alice (Ollama local, custo zero, só com a VM 110
  ligada).
- O Pilla e os `pilla-wt-*` não podem ser usados.

### Preparação (entregáveis, sem rodar engine real)

1. **Projeto sandbox não crítico**: repo novo `~/code/ada-piloto` (local, sem remoto ou com remoto privado), com uma
   feature pequena e visual-free (ex.: CLI de conversão com 2–3 cartões), SDD (PRD/ADR/RFC) aprovado por hash,
   `harness.project.yaml` derivado do exemplo corrigido (item 3) e comandos `verify/test` reais e rápidos.
2. **Ensaio com fake** no sandbox: `run_feature.py` ponta a ponta com o worker fake, para provar config, gates e
   métricas antes de gastar cota. Isso pode ser feito sem autorização (não chama modelo).
3. **Config de engines do piloto** (proposta, sem chaves): primeiro a Alice (custo zero), depois uma engine paga por
   papel. Para cada worker: `max_attempts: 1`, `fallbacks: []`, `timeout_s` curto, `quota_reserve` conservador,
   `rates` preenchidas, `usage_jq` para cli-agent. Chaves só por `key_env`, carregadas pelo Joel no ambiente; nunca
   em arquivo do repo.
4. **Teto de custo**: como o Harness não tem orçamento, o teto é imposto **fora** dele: limite de gasto no painel do
   provider ou chave dedicada com cota baixa, mais o número máximo de stages (7) × `max_attempts=1`. Não implementar
   orçamento no Harness neste item (seria ampliar escopo; vira proposta separada se o Joel quiser).
5. **Roteiro de medição**: `metrics.py` após cada run; comparar com `onda-0-baseline.md` em: sucesso na primeira
   tentativa, retries, gates falhos, duração, tokens conhecidos e custo estimado. Planilha de resultado em
   `progresso-ondas-20.11.md` (seção nova, preenchida só após o piloto).

### Checklist de autorização para o Joel

- [ ] Itens 1, 2 e 3 deste plano concluídos (recuperação de crash, exemplo válido).
- [ ] Ensaio com fake no sandbox passou de ponta a ponta.
- [ ] Projeto do piloto: `~/code/ada-piloto` (ou outro indicado pelo Joel), fora do Pilla e de produção.
- [ ] Engines e modelos autorizados: ______ (Alice: VM 110 ligada? sim/não).
- [ ] Teto de gasto por provider: R$/US$ ______, configurado no painel do provider (ou chave dedicada).
- [ ] Máximo de runs: ______ (sugestão: 1 com Alice + 1 por engine paga).
- [ ] Chaves carregadas só no ambiente da sessão; nenhuma em arquivo versionado.
- [ ] Janela de execução e quem acompanha.
- [ ] Autorizo a execução: ______ (data/hora).

### Aceite do piloto (quando autorizado e executado)

- Cada run termina em `done` ou `blocked` com evento explicável; nada é mesclado em branch principal.
- `metrics.py` tem tokens conhecidos para 100% das tentativas das engines medidas (senão o run não conta para
  comparação).
- Gasto real ≤ teto autorizado (conferido no painel do provider).
- Relatório comparativo com a baseline; **nenhuma troca de engine padrão** sem decisão do Joel.

### Rollback

- Cancelar: Ctrl-C ou kill do `run_feature`; depois `recover_run.py --apply` (item 1).
- Limpar: `git worktree prune`, apagar as branches `harness/*` do sandbox e o próprio `~/code/ada-piloto`, se
  descartado.
- Revogar ou rotacionar a chave dedicada do piloto.
- O default de engine do Harness não muda, então não há rollback de config no Harness.

### Risco

- Custo e cota (mitigado pelo teto externo + `max_attempts=1`).
- Vazamento de segredo em evidência (mitigado pelas regras existentes: manifesto só com nomes de variáveis; conferir
  com `grep` pelo prefixo da chave em `.harness/` antes de compartilhar resultados).
- Dados enviesados por uma feature só: deixar claro no relatório que é amostra mínima.

---

## Validação final do plano

Ao concluir os itens 6, 3, 4, 1 e 2:

- `.venv/bin/python -m pytest -q`: passed ≥ 321 + novos, sem falhas; skips explicados (4 de `ADA_TEST_PROJECT` + os
  opcionais).
- Com as dependências opcionais e `ADA_REQUIRE_OPTIONAL=1`: zero skips de `visual_real` e `schema_real`.
- `artifact_lint.py /home/debian/code/pilla --strict`: PASS; Pilla sem alteração (`git -C pilla status` limpo).
- `compileall`, `git diff --check` e `yaml.safe_load(harness.project.example.yaml)`: PASS.
- `progresso-ondas-20.11.md` atualizado: lacunas 2, 3 e 4 fechadas; lacuna 1 (piloto) continua **BLOQUEADA**, com
  checklist pronto; lacuna 5 (engines sem `usage_jq`) inalterada e fora de escopo.
- Commits só com pedido do Joel, um por item, mensagem em inglês, com hooks ativos.

## Resumo que o executor deve apresentar

Por item: status (feito, parcial ou bloqueado), arquivos tocados (diff-stat), testes adicionados e contagem da suíte,
riscos remanescentes. Para o item 5: só "preparado, aguardando autorização", com o link do checklist.
