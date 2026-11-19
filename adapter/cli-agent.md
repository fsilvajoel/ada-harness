# Adaptador: agente de linha de comando

Um **trabalhador** é um agente de IA de linha de comando: um programa que recebe um briefing, lê e edita arquivos
sozinho num diretório e devolve um texto no stdout. O orquestrador monta o briefing, roda o comando numa **cópia de
trabalho**, confere o que mudou e roda os gates. **Quem aprova é o gate, nunca a palavra do agente.**

Diferença para o [api-chat](api-chat.md): lá o orquestrador aplica a resposta em texto; aqui o agente edita a cópia
e o orquestrador só traz de volta o que está no escopo do papel.

## Escopo
- Papel `refiner` (refinamento funcional), em **modo escrita restrita**: edita só o `plano.md` da feature, os
  cartões dela e a fila `pm_queue`; o parecer volta pelo stdout no
  [molde](../workflow/feature-artefatos/refinamento.md).
- Papel `reviewer` (revisão dos testes e revisão final), em **modo leitura**: não edita arquivo nenhum; o relatório
  volta pelo stdout no formato que o papel já usa.
- Papel `implementer` (etapa 4 do [workflow feature](../workflow/feature.md)), em **modo escrita**, nas mesmas
  condições do api-chat: plano e testes já assinados.
- O trabalhador não escreve nem altera teste, `revisao.md` ou `estado.json` e não faz commit; plano, cartões e fila
  só o `refiner`. O resto fica com o orquestrador.

## Configuração (no `harness.project.yaml` do projeto)
- `workers.<nome>`: `adapter: cli-agent` e
  - `command`: o programa;
  - `read_args`: argumentos do modo leitura. Precisam negar escrita, shell e memória na própria ferramenta;
  - `write_args`: argumentos do modo escrita. Liberam só escrita de arquivo; shell e memória continuam negados;
  - `output_jq` (opcional): filtro jq aplicado com `jq -rs` ao stdout para extrair a resposta final, quando a saída
    é JSON por linha. Sem ele, o stdout inteiro é a resposta;
  - `usage_jq` (opcional): filtro jq (também `-rs`) que extrai o consumo da execução, gravado no registro;
  - `roles`: papéis permitidos, separados por espaço (ex.: `"reviewer implementer"`). Papel fora da lista não vai
    para o trabalhador;
  - `max_attempts` (padrão 6) e `timeout_s`.
- `roles.implementer.worker: <nome>`: a etapa implement pode ir para esse trabalhador (como no api-chat).
- `roles.refiner.worker: <nome>`: o refinamento funcional vai para esse trabalhador, em modo escrita restrita
  (`write_args`).
- `pm_queue`: caminho da fila de pendências de PM ([molde](../workflow/feature-artefatos/pendencias-pm.md)).
- `roles.reviewer.second_opinion: <nome>`: além do `agent` do reviewer, que continua rodando, esse trabalhador faz
  uma **segunda revisão** nas mesmas etapas ([Segunda opinião](#segunda-opinião)).
- `role_context.<papel>`: arquivos do projeto com regras que o briefing manda ler.

## Cópia de trabalho
O agente nunca roda na pasta do projeto. Antes de cada tentativa, o orquestrador:
1. Cria uma pasta nova no temp dele e copia para lá os arquivos de `git ls-files -co --exclude-standard`
   (versionados e novos não ignorados). Arquivo ignorado (segredos, dependências, build) não vai para a cópia.
2. Guarda o sha de cada arquivo da cópia (`git hash-object`).
3. Na primeira tentativa do implementer, guarda também uma cópia dos arquivos de `sources` do projeto como estavam.

Os argumentos limitam o acesso da ferramenta à pasta corrente; a cópia é essa pasta. Nenhum segredo vai no briefing
nem nos argumentos ([security](../policy/security.md)).

## Chamada
Na pasta da cópia: `<command> <read_args | write_args>`, com o briefing no **stdin** (sem limite de tamanho de
argumento) e `timeout_s` como limite. Cada tentativa é uma execução nova, sem sessão anterior.

## Briefing
Montado pelo orquestrador, nesta ordem:
1. `# Tarefa`: ID da feature, etapa, papel, modo (leitura ou escrita) e "tentativa n de max_attempts".
2. `# Papel`: o texto da definição do papel (`roles.<papel>.agent`), com a regra de que, em modo leitura, não se
   cria, edita nem apaga arquivo e não se roda comando.
3. `# Regras do projeto`: os caminhos de `role_context.<papel>`, para ler na cópia.
4. `# Feature`: caminho de `plano.md`, `estado.json`, `tests` e `e2e` na cópia; no refinamento, também o arquivo
   dos cartões citado no plano, os IDs deles e o caminho de `pm_queue`.
5. Revisão: `# Gates`, o último resultado de cada gate no `estado.json` (o agente não roda comando); só na revisão
   final, `# Diff`, a saída de `git diff` da feature. Por fim, `# Saída`: o molde do registro
   ([testes](../workflow/feature-artefatos/auditoria.md) ou [final](../workflow/feature-artefatos/revisao-final.md)),
   a devolver inteiro no stdout com o veredito. No refinamento: só `# Saída`, com o
   [molde do refinamento](../workflow/feature-artefatos/refinamento.md) e a rodada (1 ou 2); na rodada 2, também
   as entradas da fila respondidas pelo PM.
6. Implementer: `# Arquivos que você pode escrever`, a lista de `sources`, e a regra de que teste é somente leitura.
   Refiner: `# Arquivos que você pode escrever`, o `plano.md`, o arquivo dos cartões (só os IDs da feature) e
   `pm_queue`, e a regra de que teste, código, PRD, RFC e ADR são somente leitura.
7. `# Skills e regras`: as skills do papel (`role_skills.<papel>`) e as do campo `skills` do cartão, **incluídas no
   briefing pelo harness, não escolhidas pelo orquestrador**, no mesmo contrato do
   [api-chat](api-chat.md#skills-e-regras-no-pedido): cada recurso entre `=== RECURSO: <caminho> ===` e
   `=== END RECURSO ===`, `nome` = só o `SKILL.md`, `nome#regra` = índice + `rules/<regra>.md`, nunca o `AGENTS.md`
   compilado, sem repetição, erro claro se faltar recurso ou passar de `skills_max_bytes`. Não há executor próprio do
   cli-agent: o bloco sai de `python3 <harness>/script/skill_inject.py <raiz do projeto> --role <papel> --skills <itens
   do cartão>` (stdout) e é anexado ao briefing; o tamanho por recurso sai no stderr e vai para o registro. O agente
   também pode ler os arquivos na cópia de trabalho, mas não depende disso.
8. Só a partir da 2ª tentativa, `# Falha da tentativa anterior`: o motivo da rejeição ou as últimas 150 linhas da
   saída do comando que falhou.

## Conferência
Depois de cada execução, o orquestrador compara os sha da cópia com os guardados:
- **Modo leitura:** qualquer arquivo criado, alterado ou apagado = tentativa rejeitada. A resposta precisa trazer o
  veredito (`aprovado`, `reprovado` ou `needs_input`); sem ele, rejeitada.
- **Modo escrita, refiner:** só podem ter mudado o `plano.md` da feature, o arquivo dos cartões e `pm_queue`. No
  arquivo dos cartões, os cartões que não são da feature ficam idênticos; na fila, só entradas desses cartões.
  Outra mudança = rejeitada, e nada é trazido. A resposta precisa trazer o veredito (`pronto`, `ajustado` ou
  `pendencia-pm`); `pronto` com arquivo alterado, ou `pendencia-pm` sem entrada aberta da feature na fila, = rejeitada.
  Aceita: o orquestrador copia esses arquivos para o projeto.
- **Modo escrita, implementer:** só arquivos de `sources` podem ter mudado. Caminho fora de `sources`, ou em `tests`, `e2e` ou
  `regressionTests`, ou com `.test.`, `.spec.` ou `.e2e.` no nome = rejeitada, e nada é trazido. Aceita: o
  orquestrador copia os arquivos de `sources` alterados para o projeto.
- Em todos os modos, `git status --porcelain` do projeto não pode mudar durante a execução; se mudar, restaurar
  ([edit-guard](../gate/edit-guard.md)) e escalar.
- A cópia é apagada no fim da tentativa.

## Gates do implementer
Iguais aos do [api-chat](api-chat.md#aplicação-e-gates), rodados no projeto: `commands.test` com os arquivos de
`tests`, `commands.typecheck`, `commands.lint`, `commands.format`, `pnpm gate tests-reviewed <feature>` e
`pnpm gate coverage <feature>`, parando no primeiro FAIL.

## Segunda opinião
- Roda depois do `agent` do reviewer, com o mesmo pedido da etapa (revisão dos testes ou revisão final).
- A saída vai para `revisao.md` como seção própria, com `revisor: <nome do trabalhador>`.
- Veredito da etapa: `aprovado` só se os dois aprovarem. Reprovação de qualquer um devolve a etapa com a lista de
  achados dos dois; `needs_input` de qualquer um vira `needs_input`.
- Escalada da segunda opinião não bloqueia: a etapa segue com o veredito do `agent` e a escalada fica registrada.

## Tentativas e escalada
- Conta como tentativa falha: saída diferente de 0, timeout (`timeout_s`), resposta vazia, resposta rejeitada e, no
  implementer, gate FAIL. A próxima tentativa recebe o motivo (item 8 do briefing).
- Escala na hora: comando ausente ou não autenticado, limite de uso da ferramenta, projeto alterado fora da cópia.
- Escala ao chegar a `max_attempts` falhas.
- Escalar = restaurar `sources` para a cópia guardada antes da primeira tentativa (implementer) e registrar o motivo.
  Então o orquestrador assume a etapa com o `agent` do papel, ou marca `needs_input` quando a decisão é humana.

## Registro
Em `stages.<etapa>` do `estado.json` ([formato](../workflow/feature-artefatos/estado.md#trabalhador)): `worker`,
`attempts` (uma entrada por tentativa, com `usage` quando houver `usage_jq`, e resultado) e `escalation`. Na
segunda opinião, o mesmo registro fica em `stages.<etapa>.secondOpinion`, junto com o `verdict` dela. O texto do
briefing e da resposta não vai para o estado nem para o git (o relatório do reviewer vai para `revisao.md`).
