# E07 — Roadmap de desenvolvimento

Data: 21/10/2026. Estado: modelo/persistência, overrides e filtragem implementados; dois exemplos GE executados. Relatório e exportação PDF/Word pendentes.

### Progresso — primeira etapa

Implementado o campo booleano `enabled` na interface concreta, com padrão `true`,
propagação de perfis/patterns e persistência no TOML e no payload do SDK.
Validação específica: 15 testes aprovados em `tests/test_interface_enabled.py`.
O campo ainda não exclui interfaces do planejamento.
Validação no contêiner de desenvolvimento: 19 testes aprovados, incluindo os
15 testes específicos e a suíte `tests/test_scenario_configuration.py` do serviço.

### Progresso — segunda etapa

Overrides integrados ao TOML aplicados por ID concreto após a expansão do CSV
ou sobre sites explícitos. Suporte a `enabled`, precedência sobre a instância,
validação de campos/valores/referências e preservação na exportação.
Validado no contêiner: 52 testes aprovados nas suítes de interfaces,
configuração do serviço e projetos por substituição. A etapa seguinte é
excluir interfaces desabilitadas do planejamento.

## Objetivo e escopo

### Progresso — terceira etapa

Interfaces inativas excluídas dos candidatos RF/internos, métricas e RPL em
graph/cell. Referências manuais a interfaces inativas são rejeitadas.
Inventário completo exportado separadamente dos nós ativos. Mudanças de ativação
invalidam os resultados derivados. Grafos vazios e acessos conectados isolados
são tratados explicitamente, sem reconstruir candidatos indevidamente.
Validação no contêiner: 83 testes aprovados e um ignorado nas suítes selecionadas.

Representar a conectividade em dois projetos independentes: a rede GE instalada
e sua configuração com redundância celular. A demonstração desabilita a interface
do nó de saída original, sem gerar novos enlaces GE nem alterar os pais dos
descendentes do ponto protegido. Não implementa comutação operacional de pais.

Os notebooks são exemplos de desenvolvimento e futura documentação do relatório E07. A camada complementar não deve ser confundida com substituição da solução original nem com garantia operacional de failover.

## Decisões

- Um único método de configuração por instância: overrides integrados ao scenario.toml. Não haverá metadata.json, metadata.toml separado ou JSON dentro do CSV.
- Perfis definem valores padrão; CSV instancia os elementos; overrides ajustam as instâncias concretas.
- enabled é booleano e vale true quando omitido. Interfaces desabilitadas permanecem no cenário, mas não participam de candidatos, métricas ou RPL.
- Endereçar interfaces por interface_id concreto, evitando ambiguidade em sites com vários dispositivos. Os IDs devem ser obtidos após a expansão/substituição e validados; índices gerados podem mudar quando muda a ordem das interfaces do perfil.
- Replacement rules continuam preparando perfis e configurações do segundo projeto. O extension.toml existente é uma receita de preparação, não um segundo mecanismo de metadados. A configuração final de instâncias fica no scenario.toml gerado.
- A análise de descendentes é um recurso local do SDK, acessível visualmente por
  `PlanningProject.show_dependencies()`. O notebook 1 contém apenas chamadas
  curtas ao SDK, sem cálculos locais nem tabelas.

### Revisão do exemplo — SDK 0.1.38

O notebook 1 foi simplificado e executado com o SDK instalado. O grafo permite
selecionar sites e destacar todos os descendentes; o painel distingue filhos,
interfaces, dispositivos e sites dependentes. A escolha manual é persistida por
`save_redundancy_selection(site_id)`, sem modificar a topologia.

No SDK 0.1.39, o notebook 2 também contém apenas chamadas ao SDK e grafos.
A receita oferece interfaces GE/celular a Barigui e desabilita somente Torre.
O modo de preparação `topology.mode = "installed"` herda os enlaces da solução
original, excluindo apenas os incidentes à interface desabilitada.

### Exemplo consolidado

Os notebooks usam os 13 sites do example_1 com perfis GE mesh, em dois projetos
independentes. Barigui foi escolhido manualmente após examinar a solução e seus
seis descendentes. Seu perfil passa a ter duas interfaces, GE e celular, e o
dispositivo torna-se conectado com custo inicial 1.0. A segunda layer desabilita
somente a interface GE de Torre. Os seis descendentes de Barigui mantêm seus
pais instalados; Tenis e Cemiterio ficam sem caminho. Os dois notebooks foram
executados com os serviços reais e passaram também nos testes de distribuição.
Os registros com saídas estão em `docs/internal/case-studies/e07/executed`; as fontes executáveis,
sem saídas, ficam em `examples/notebooks`.

## Contrato proposto

```toml
[overrides.interfaces."8187902144:d0:i0"]
enabled = false
```

Este contrato já é interpretado pelos carregadores do SDK e da biblioteca.
Só enabled integra esta entrega; seu efeito sobre o planejamento já está
implementado. Configuração de antenas é uma extensão posterior, com campos
permitidos e sem mutação do perfil compartilhado.

## 1. Estado e serialização das interfaces

- [ ] Acrescentar enabled à interface concreta e preservar o valor na importação/exportação, no SDK e no serviço.
- [ ] Validar booleanos; rejeitar valores inválidos, IDs desconhecidos e propriedades não suportadas.
- [ ] Preservar a identidade e o cadastro das interfaces desabilitadas. Relatórios devem distinguir desabilitada de não atendida.
- [ ] Manter compatibilidade dos cenários existentes, que não declaram enabled.

Componentes: planner_classes.py, planning_scenario.py, local_io.py e serialização de cenários/resultados.

## 2. Aplicação dos overrides

- [ ] Aplicar após a expansão do CSV ou leitura de sites explícitos, com a mesma semântica nos caminhos de carga SDK e biblioteca.
- [ ] Resolver o endereço pelo interface_id concreto; validar antes de abrir o cenário remoto ou gravar a preparação.
- [ ] Preservar os overrides na preparação por replacement rules e na reabertura do projeto.
- [ ] Definir comportamento quando uma substituição elimina ou renomeia uma interface referenciada: erro explícito, sem ignorar ajustes.
- [ ] Invalidar resultados derivados quando a ativação mudar; preservar features brutas reutilizáveis.

## 3. Participação no planejamento

- [ ] Excluir interfaces desabilitadas dos nós ativos, dos enlaces RF e internos e do cálculo de métricas.
- [ ] Rejeitar enlaces manuais que referenciem interfaces desabilitadas, com diagnóstico claro.
- [ ] Aplicar a regra aos planejadores graph e cell; impedir que fluxos especializados reintroduzam interfaces inativas.
- [ ] Tratar explicitamente cenários sem interfaces ativas ou sem enlaces, incluindo acessos externos já conectados.
- [ ] Excluir interfaces desabilitadas dos denominadores de atendimento; identificá-las nos relatórios de inventário.

## 4. Report de dependências por subgrafo

- [ ] Criar report local no SDK a partir do resultado salvo, sem reexecutar planejamento.
- [ ] Identificar filhos e descendentes pela relação parent, preservando a orientação lógica.
- [ ] Retornar interfaces e contagens distintas de dispositivos e sites; excluir o elemento analisado da contagem de descendentes.
- [ ] Diferenciar o subgrafo de dependência selecionado das alternativas do grafo candidato.
- [ ] Definir o contrato para análise por interface, dispositivo e site; não assumir equivalência entre essas unidades.

## 5. Preparação e comparação dos projetos

- [x] Notebook original: executar o caso GE mesh derivado do example_1 e salvar resultado e dependências.
- [x] Notebook redundância: substituir Barigui por perfil duplo GE/celular e torná-lo conectado.
- [x] Desabilitar somente a interface GE de Torre; manter os demais sites habilitados.
- [x] Herdar somente os enlaces instalados e verificar a preservação dos pais dos descendentes de Barigui.
- [ ] Garantir que o segundo layer não reutilize implicitamente caminhos do primeiro nos nós escolhidos para acesso alternativo.
- [ ] Comparar atendimento por identidade estável de site/dispositivo, sem somar ranks de métricas diferentes e sem confundir complementação de cobertura com redundância.
- [ ] Documentar acessos externos como premissas, sem modelar VPN ou cobertura de operadora como recurso implementado.

## 6. Validação e entrega

- [ ] Testes de carga/round-trip, precedência, erros de referência e compatibilidade sem overrides.
- [ ] Testes de exclusão em candidatos RF/internos/manuais, métricas, RPL e relatórios.
- [ ] Testar preservação de overrides e integridade da origem em replacement rules e reexecuções.
- [ ] Validar contagens de subgrafos com ramificações e múltiplas interfaces por equipamento.
- [x] Executar os dois notebooks com os serviços disponíveis e revisar os resultados reais.
- [x] Atualizar versão/distribuição do SDK (0.1.39) e coleção de exemplos conforme o fluxo do repositório.
- [ ] Redigir relatório em Markdown a partir das funcionalidades verificadas; exportar Word e notebook PDF posteriormente.

## Critério de conclusão

Dois projetos reproduzíveis, report visual de dependências e perfil duplo no nó
escolhido. A perda da saída original é representada por um override de enabled,
sem reorganizar os enlaces GE instalados nem modificar o projeto de origem.

## Artefatos iniciais

- examples/notebooks/e07_01_projeto_original.ipynb
- examples/notebooks/e07_02_projeto_redundancia.ipynb

Ambos usam as APIs existentes e o suporte a enabled/overrides implementado nesta
sequência. Foram executados com os serviços disponíveis; os resultados e suas
limitações estão registrados em `docs/internal/case-studies/e07/executed/README.md`.
