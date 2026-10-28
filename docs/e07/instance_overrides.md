# E07 — Ajustes de interfaces por instância

Os ajustes ficam exclusivamente no `scenario.toml`, junto às demais definições
do projeto. Exemplo:

```toml
[overrides.interfaces."GE_042:d0:i0"]
enabled = false
```

A chave identifica a interface concreta, não o perfil nem apenas o site.
Para instâncias geradas do CSV, o formato atual é `<site_id>:d0:i<índice>`.
O índice segue a ordem das interfaces no perfil do dispositivo. Ao mudar essa
composição, revise as referências: IDs inexistentes são rejeitados.

## Ordem de aplicação

1. Carregar perfis e instanciar o CSV, ou carregar os sites explícitos.
2. Validar todos os ajustes e as referências.
3. Aplicar os valores à instância, sem modificar o perfil compartilhado.

O SDK aplica os ajustes antes de enviar o cenário ao serviço. A biblioteca
`PlanningScenario` também os aplica em seus carregadores. Os ajustes permanecem
na exportação, inclusive quando os sites não são incluídos.

`enabled` deve ser um booleano TOML (`true` ou `false`, sem aspas). O valor padrão
é `true`; um override pode habilitar uma interface cujo perfil define `false`.
Nesta entrega, nenhuma outra propriedade é aceita em overrides. Tabelas inválidas,
campos desconhecidos e referências inexistentes geram `ValueError`.

Com `PlanningScenario.from_toml(..., load_instances=False)`, a biblioteca preserva
os ajustes sem resolver os IDs do CSV; eles são aplicados quando
`load_instance_source()` carrega as instâncias. Um carregamento normal rejeita
referências não resolvidas, inclusive se o CSV estiver ausente.

## Efeito no planejamento

Interfaces desabilitadas não participam dos nós ativos, enlaces RF/internos,
métricas ou RPL, tanto no planejador genérico quanto no celular. Enlaces manuais
e métricas fornecidas explicitamente que as referenciem são rejeitados.

O resultado contém `rpl_nodes` apenas com interfaces ativas e
`interface_inventory` com todas as interfaces e seu estado `enabled`.
Assim, interfaces desabilitadas não contam como alvos sem atendimento.

Mudanças de ativação na instância invalidam candidatos, métricas e solução
na próxima operação do planejador. O cache externo de features brutas permanece
reutilizável. Para alterar um projeto remoto, salve e reabra o cenário.

Um grafo sem enlaces pode preservar acessos externos já conectados. Se todas as
interfaces estiverem desabilitadas, a solução é vazia e a avaliação não declara
sucesso de atendimento. Um grafo ativo sem qualquer acesso conectado continua
sujeito à validação de conectividade inicial do RPL.
