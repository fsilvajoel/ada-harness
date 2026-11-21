# Plano para o Claude finalizar as ondas 3–6 do Ada Harness 2.0

## Instrução de execução

Implemente este plano no repositório `harness-ada`, avançando uma onda por vez, sem parar entre ondas quando os
critérios de aceite estiverem satisfeitos. Não declare autonomia ponta a ponta até passar pelo teste integrado da
Onda 5.

Antes de editar:

1. Leia este plano, [`EvolucaoAdaHarness2.0.md`](../EvolucaoAdaHarness2.0.md) e
   [`progresso-ondas-20.11.md`](./progresso-ondas-20.11.md).
2. Confira o estado atual do repositório e os testes; preserve alterações existentes e não reverta trabalho alheio.
3. Use o código existente como fonte da verdade. Não recrie funcionalidades já implementadas.
4. Mantenha compatibilidade com os formatos e fluxos V1, salvo quando uma mudança versionada e testada for necessária.

## Objetivo final

Permitir que uma feature percorra o fluxo aprovado de SDD até implementação, gates e revisão, com transições
determinísticas, engines configuráveis por papel/etapa, worktrees isoladas, evidências verificáveis e custo mensurável.
O modelo não deve ser chamado para escolher transições simples. Decisões ambíguas, requisitos em aberto, aprovação
de SDD e integração de branch continuam sendo gates humanos explícitos.

Não afirmar que o Harness é autônomo apenas porque existe um dashboard ou um teste de `run_stage`. A prova necessária
é um teste integrado de uma feature, do SDD aprovado à conclusão, usando workers fake e um repositório Git temporário.

## Estado inicial conhecido

Na validação anterior a este plano:

- suíte: **277 passed, 4 skipped**;
- `artifact_lint.py /home/debian/code/pilla --strict`: **PASS**;
- parsing dos JSON schemas, `compileall` e `git diff --check`: **PASS**;
- o runner configurável executa uma etapa por chamada; ainda não há driver de feature ponta a ponta;
- o dashboard é somente leitura e não tem rerun seguro;
- SDD pode ser validado, aprovado por hash e publicar plano/cartões já escritos, mas não os gera;
- a política condicional existe, mas não produz evidência de revisão visual no navegador;
- métricas existem, mas não houve piloto comparativo com engines reais.

Repita as validações antes de trabalhar e atualize este baseline se o repositório tiver mudado.

## Guardrails obrigatórios

- Não gravar secrets, chaves, tokens, cookies ou conteúdo de `.env` em arquivos, eventos, screenshots ou relatórios.
- Testes automatizados não chamam modelos reais, não consomem cota e não dependem de rede; use workers fake.
- Não alterar o projeto Pilla. Use-o somente como fixture de compatibilidade via lint estrito.
- Não mesclar branches `harness/*` automaticamente à branch principal.
- Não permitir commits, gates, transições ou alterações de estado pelo worker.
- Toda escrita do worker precisa estar confinada a `allowedPaths`; falha de escopo bloqueia e descarta a tentativa.
- Não marcar uma onda como concluída apenas porque os arquivos ou schemas existem. Exija comportamento testado e os
  critérios abaixo.
- Não habilitar rerun pela UI se replay, autorização e isolamento não estiverem comprovados. A opção padrão é continuar
  somente leitura.
- Não trocar engine padrão nem afirmar economia de tokens sem dados reais comparáveis.
- Não instalar dependências no Harness sem provar que a funcionalidade não pode reutilizar os comandos/adaptadores
  configurados no projeto alvo. Se uma dependência for necessária, declare-a e documente instalação e versão.
- Não commitar nem fazer push sem pedido explícito do usuário.

## Ordem de trabalho

Dependências:

1. Onda 3 pode ser executada primeiro e isoladamente.
2. Onda 4 depende da persistência de configuração e request introduzida para replay seguro.
3. Onda 5 define o contrato de execução, geração SDD e driver; esse contrato também habilita o rerun seguro da Onda 4.
4. Onda 6 depende dos eventos e do contrato estabilizados nas Ondas 1, 2 e 5. Um piloto real só ocorre com
   autorização e credenciais fornecidas pelo operador.

Ao terminar cada onda:

1. Rode seus testes direcionados e as validações listadas.
2. Corrija falhas antes de seguir.
3. Atualize [`progresso-ondas-20.11.md`](./progresso-ondas-20.11.md) com status, evidência, testes e lacunas reais.
4. Atualize os todos de sessão correspondentes. Não deixe ondas parciais marcadas como `done`.
5. Continue para a onda seguinte sem aguardar confirmação, exceto quando um gate humano ou acesso externo for
   realmente necessário.

## Onda 3 — Evidência de revisão visual

### Problema

`testing_policy.py` decide quando uma revisão visual é aplicável, mas não há integração que execute a revisão ou
registre evidência reproduzível. A classificação visual deve continuar dispensando TDD de layout; ela não deve
dispensar revisão visual. Regra de domínio extraída da tela continua sujeita a TDD.

### Implementar

1. Defina um contrato de revisão visual configurável pelo projeto, ligado aos critérios visuais do plano:
   comando/ferramenta, URL ou alvo, critérios observáveis, diretório de saída e timeout.
2. Reutilize a ferramenta de navegador já configurada no projeto sempre que possível. O ambiente do Harness não tem
   `playwright` instalado por padrão; não o adicione como dependência global sem necessidade comprovada.
3. Execute a revisão apenas quando a política resolver `visual_review=true`. Se houver classificação visual sem
   critérios executáveis, bloqueie com erro claro; não reporte sucesso fictício.
4. Registre evidência estruturada com: feature, critério, comando configurado, resultado, timestamp, hash dos
   screenshots/artefatos e resumo do gate. Não gravar tokens de URL nem dados sensíveis.
5. Mantenha revisão visual distinta de E2E: revisão visual verifica apresentação/estados visuais definidos; E2E prova
   fluxos do PRD.

### Testes

- Feature visual: pula stages TDD não aplicáveis e exige revisão visual configurada/evidenciada.
- Feature sem conteúdo visual: não executa revisão visual.
- Classificação visual com lógica de domínio não extraída: falha no gate correspondente.
- Comando visual ausente, timeout, screenshot ausente ou critério não encontrado: FAIL explícito e sem evidência
  PASS.
- Evidência gerada valida hashes e pode ser lida pelo dashboard.

### Aceite

- Os testes provam que feature puramente visual não recebe expect-red indevido e não passa sem evidência visual.
- O gate preserva a regra de TDD para arquivos de domínio.
- Documentação do fluxo e dos campos de configuração é atualizada.

## Onda 4 — Dashboard e rerun seguro

### Estado existente

O dashboard exibe estado, eventos, métricas e paths/stat de diffs das worktrees, mas não pode rerodar uma etapa. Não
adicionar um botão que reconstrua comandos a partir de dados incompletos.

### Implementar

1. Criar um manifesto imutável de execução por tentativa com versão do schema, `run_id`, feature, etapa, papel,
   request e SHA-256, hash da configuração efetiva, gate, `allowedPaths`, skills, modo, executor resolvido, base SHA e
   identificador da tentativa.
2. Salvar somente referências a variáveis de ambiente para secrets; nunca copiar valores secretos para o manifesto.
3. Fazer replay validar o manifesto e relacioná-lo aos eventos e evidências. Mudança de request/config deve invalidar
   rerun automático, não ser silenciosamente aceita.
4. Habilitar rerun somente para a etapa atual em estado recuperável (`failed`), sem processo/worktree ativo, com
   hashes ainda válidos e transição permitida. Um rerun deve produzir nova tentativa e novas evidências, sem apagar o
   histórico.
5. Se o endpoint HTTP oferecer rerun:
   - manter bind padrão em `127.0.0.1`;
   - exigir POST, validar `Origin`/host e usar token de sessão aleatório não exposto em query string;
   - rejeitar qualquer campo que tente fornecer comando, caminho ou argumento arbitrário;
   - não habilitar por padrão em bind não-local;
   - testar CSRF/origem inválida, etapa ativa, manifesto alterado e tentativa inválida.
6. Mostrar no dashboard o estado do manifesto, tentativa, hashes e motivo de rerun indisponível. A API deve devolver
   erro explícito, não sucesso vazio.

### Aceite

- Rerun válido cria uma nova tentativa no mesmo histórico e preserva todas as anteriores.
- Rerun inválido não executa worker nem gate.
- O dashboard continua somente leitura se qualquer controle de segurança não puder ser comprovado.
- Uma execução pode ser recuperada após reiniciar o servidor usando apenas eventos e manifestos persistidos.

## Onda 5 — Geração SDD e driver de feature

### 5.1 Contrato versionado

Defina e documente um contrato versionado de execução por feature que permita ao Control Plane executar sem
interpretação livre de texto. O contrato deve relacionar, no mínimo:

- IDs e hashes de PRD, ADR e RFC aprovados;
- feature, plano e cartões;
- ordem/dependências entre cartões;
- etapas, papel e worker configurado;
- request de cada etapa ou regra determinística para construí-lo;
- gate/comando, timeout, `allowedPaths`, skills e modo;
- classificação TDD/visual/biblioteca e gates aplicáveis;
- limites de tentativas/fallback e condição de `needs_input`.

Use schema versionado. Rejeite campo obrigatório ausente, ID duplicado, caminho absoluto/traversal, dependência
circular, referência inexistente e comando não configurado. Atualize os linters e exemplos de configuração.

### 5.2 Geração de plano e cartões

1. Use o adaptador/worker configurado para o papel planner; não introduza chamada de provider fora de `dispatch.py`.
2. Gere plano e cartões em staging temporário como rascunhos. Não os publique nem aprove automaticamente.
3. Valide SDD aprovado por hash, schema, `plan_lint`, `card_lint` e `artifact_lint --strict`.
4. Publique atomicamente somente após todos os gates passarem; recuse sobrescrever destinos existentes.
5. Grave hashes de origem e saída. Qualquer edição posterior invalida a aprovação correspondente.
6. Questão aberta, requisito contraditório ou informação não inferível termina em `needs_input`, com perguntas
   específicas e nenhuma implementação iniciada.

### 5.3 Driver sequencial

Implemente um ponto de entrada não interativo, por exemplo `script/run_feature.py`, que:

1. aceite raiz, `run_id` e feature;
2. valide a cadeia SDD, aprovações, contrato e artefatos antes de chamar workers;
3. derive a próxima etapa pelo event log/replay, sem chamada de LLM para transição;
4. rode etapas e cartões em ordem topológica, reaproveitando `dispatch.py`, `stage_runner.py`, `worktree.py`,
   `testing_policy.py` e gates existentes;
5. passe o commit aprovado de cada etapa como base da próxima;
6. pare com resultado explícito em gate FAIL, erro irrecuperável ou `needs_input`;
7. retome uma execução interrompida apenas quando os hashes do contrato/request/config ainda coincidirem;
8. finalize com evidências, estado derivado e branch isolada pronta para revisão humana; nunca faça merge automático.

Não duplicar lógica já existente em `run_card.py`. Se o fluxo por cartão e o novo fluxo não puderem compartilhar o
mesmo mecanismo sem quebrar V1, documente claramente qual fluxo é usado por cada tipo de execução.

### Teste integrado obrigatório

Crie um projeto Git temporário e um worker fake determinístico. O teste deve percorrer desde documentos SDD aprovados
até a última etapa configurada, incluindo ao menos um cartão, gate, commit e replay. Deve confirmar:

- zero chamadas a modelo real ou rede;
- transições e dependências em ordem;
- gate FAIL bloqueia etapas posteriores;
- approval/hash alterado bloqueia antes do worker;
- `allowedPaths` violado não é commitado;
- retry/fallback respeita a cadeia configurada;
- estado é reconstruível a partir do event log;
- segunda execução retoma sem repetir etapas aprovadas;
- branch principal permanece intacta.

### Aceite

Uma feature de teste percorre todo o contrato por um único comando, sem operador chamar manualmente cada etapa. A única
intervenção permitida é um gate humano explícito de aprovação/decisão.

## Onda 6 — Métricas e avaliação de engines

### Implementar

1. Audite se todos os adaptadores registram, quando disponíveis, prompt/completion tokens, duração, custo estimado,
   falha, retries, fallback, gate e resultado final por etapa/feature.
2. Não invente tokens ausentes: diferencie zero de desconhecido e mostre quais engines não fornecem usage.
3. Calcule custo estimado somente quando rates explícitas estiverem configuradas; identifique estimativa como tal.
4. Gere relatório por feature e engine com sucesso na primeira tentativa, taxa de retry/fallback, gates, duração,
   tokens e custo. Use dados agregados sem conteúdo de prompt/resposta.
5. Faça testes com workers fake para garantir agregação, engine sem usage, falha e rate ausente.

### Piloto real — exige autorização

Não envie requests a modelos pagos nem use credenciais presentes na máquina sem autorização explícita. Quando o
operador autorizar, use um projeto não crítico, orçamento/teto definido e chaves fornecidas somente por ambiente.
Compare engines na mesma tarefa e gates; registre condições e limitações, sem alterar o worker padrão.

### Aceite

- Métricas são reproduzíveis e não afirmam custos reais quando só há rate estimada.
- O relatório permite comparar engines na mesma feature sem expor prompts, código privado ou secrets.
- Nenhuma troca de default ocorre sem amostra suficiente, taxa de aprovação e custo por feature comparáveis.
- Se não houver autorização/engine real disponível, marque somente o piloto como bloqueado; finalize a instrumentação
  e os testes locais.

## Validação final do plano

Antes de considerar concluídas as ondas implementáveis:

1. Suíte completa verde; registre contagens exatas.
2. `compileall`, `git diff --check` e parsing de todos os schemas verdes.
3. `artifact_lint.py /home/debian/code/pilla --strict` verde, sem modificar Pilla.
4. Teste integrado de Onda 5 verde com worker fake e Git temporário.
5. Testes de segurança do rerun e dos caminhos/commits verdes.
6. Documentação, exemplos, relatório de progresso e todos refletem o estado observado.
7. Liste explicitamente todo piloto ou validação externa ainda bloqueado.

## Resumo final que o Claude deve apresentar

- Ondas concluídas e evidências por critério;
- comandos executados e resultados;
- arquivos/documentação atualizados;
- lacunas e dependências externas que ainda bloqueiam;
- declaração explícita se o objetivo de execução ponta a ponta foi ou não comprovado.
