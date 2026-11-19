# Revisão dos testes <ID da feature>

revisor: reviewer

`pnpm gate expect-red <feature>`: <PASS/FAIL>

- [x] C1 — <evidência: arquivo:linhas e o que mostram>
- [ ] C2 — <o que falta>

## Plano e camadas
- [ ] Toda entrada externa tem linha na tabela de domínio (formato, mínimo, máximo, exemplos rejeitados, erro, fonte).
- [ ] Todo `A<n>` está no molde fixo (entrada, domínio, saída, erro, fonte, camada); critério vago reprova.
- [ ] Todo critério tem teste em cada camada declarada (`unit`, `prop`, `e2e`); parser e cálculo têm `prop`.
- [ ] Checklist de bordas preenchido por entrada; ao menos uma rejeição por entrada, com o erro exato.

## Veredito
<aprovado | reprovado>

## Lacunas
- <critério ou cenário sem teste, camada sem teste, borda sem caso; "nenhuma" se zero>
