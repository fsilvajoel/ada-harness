# Artefatos de uma feature

Moldes dos arquivos que o workflow [feature](../feature.md) produz em `docs/plano/<feature>/`.
Copie o molde, troque os `<...>` e apague o que não se aplica.

| Etapa | Arquivo | Quem escreve |
|---|---|---|
| plan | [plano.md](plano.md) | planner |
| refinamento funcional | `revisao.md` ([refinamento](refinamento.md)); ajustes em `plano.md`, nos cartões e na fila `pm_queue` | refiner (ajustes) e orquestrador (parecer) |
| refinamento funcional (fila do projeto, fora da pasta da feature) | arquivo `pm_queue` ([molde](pendencias-pm.md)) | refiner; PM responde |
| tests | [testes.md](testes.md) | test-writer |
| revisão dos testes, revisão final | `revisao.md` ([testes](auditoria.md), [final](revisao-final.md)) | orquestrador, com a saída do reviewer |
| todas | `estado.json` ([estado.md](estado.md)) | orquestrador e `pnpm gate` |

Ordem: `plan → refinamento funcional → tests → revisão dos testes → implement → mutação e e2e → revisão final → fechamento`. A implementação só começa com
os testes prontos e revisados (skill `revisar-teste`). Achado da revisão final não corrigido na hora vira defeito
([workflow defeito](../defeito.md)).
