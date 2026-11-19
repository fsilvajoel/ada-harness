# Pendências de PM

Fila única do projeto (caminho em `pm_queue` do `harness.project.yaml`), escrita pelo `refiner` no
refinamento funcional ([workflow feature](../feature.md), etapa 1b). Uma entrada por pergunta. O PM responde em
`resposta`; o refiner aplica na rodada 2 e marca `resolvida`. Entrada `aberta` deixa a feature em `needs_input`.

## <ID do cartão> (<ID da feature>)
- status: <aberta | resolvida>
- data: <AAAA-MM-DD>
- pergunta: <decisão de produto que os docs aprovados não respondem>
- opções: <a) ...; b) ...>
- sugestão padrão: <opção e por quê>
- resposta: <preenchida pelo PM>
