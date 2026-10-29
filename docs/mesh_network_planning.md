# Planejamento mesh com inclusão explícita de repetidores

O `MeshPlanner` coordena a inclusão progressiva de repetidores usando
`MeshLinkPlanner`. O `GraphPlanner` continua responsável pelas operações comuns
sobre o cenário ampliado. Os arquivos originais do projeto são preservados.

## Sequência do SDK

```python
scenario = client.open_project(project, planner="mesh")
repeaters = scenario.add_repeaters(geo_base_url="http://features:8080")
candidates = scenario.build_candidate_edges()
metrics = scenario.compute_metrics(geo_base_url="http://features:8080")
solution = scenario.solve()
pruning = scenario.remove_unused_repeaters()
scenario.save_result()
```

1. `add_repeaters` executa o laço especializado: escolhe o próximo terminal,
   testa pontos servidos próximos com o planejador de enlaces, incorpora os
   repetidores da melhor proposta aceitável e atualiza a conectividade
   temporária para a rodada seguinte. O custo do destino até o backbone entra
   na comparação. Um terminal pendente pode ser tentado novamente depois que
   outro estudo acrescentar um repetidor. Não é construído o grafo final nessa
   chamada. O conjunto de postes não é inserido globalmente como rádios.
2. `build_candidate_edges` utiliza as regras de conectividade do GraphPlanner
   sobre os terminais, acessos originais e repetidores propostos. Não consulta
   features nem acrescenta rádios. Se o cenário não fornece regras RF, o
   planejador mesh fornece regras de proximidade com `final_degree`.
3. `compute_metrics` avalia somente os enlaces desse grafo, sem mudar os
   equipamentos ou executar a inclusão de repetidores. A implementação é a
   comum do GraphPlanner e aceita os lotes convencionais. O SDK mesh usa
   `bidirectional=True` para conservar o custo pelo pior sentido. Para graph e
   cell, o padrão continua sendo avaliar o sentido informado. As features
   brutas são reaproveitadas do cache.
4. `solve` é a operação comum de seleção de caminhos. Não adiciona nem remove
   rádios. O argumento opcional `max_edge_metric` limita a elegibilidade por
   segmento sem retirar os candidatos ou métricas da inspeção. Sem esse
   argumento, a API conserva o comportamento original. O SDK mesh reutiliza o
   `segment_threshold` do estudo de inclusão quando o argumento é omitido.
5. `remove_unused_repeaters` é a pós-etapa mesh: percorre os caminhos dos
   terminais obrigatórios e retira apenas repetidores propostos sem uso.
   Preserva todos os terminais e acessos originais, sem novas extrações RF.
6. `save_result` grava o resultado e os artefatos correspondentes.

Somente os acessos originais mantêm `connected=true` na seleção final. Os
ranks de entrada desses acessos são preservados. Um repetidor que ficou
servido durante a construção não se transforma em uma nova saída do backbone.

## Configuração

O SDK lê os postes de `[mesh.poles]`, os controles dos estudos de enlace em
`[mesh]` e os controles da inclusão em `[mesh.network]`:

```toml
[mesh.network]
parent_candidates = 3
corridor_m = 1500.0
final_degree = 3
```

O corredor limita as posições oferecidas a cada estudo. A busca filtra por
tecnologia, frequência, interface habilitada e capacidade de encaminhamento.
Na inclusão, o custo de cada segmento é o maior dos dois sentidos e deve
atender a `segment_threshold`. As buscas exploram todos os setores, inclusive
posições colineares com os extremos. Os ajustes de carga e profundidade da
árvore ficam para evolução posterior.

## Endpoints e responsabilidades

- `POST /planning/scenarios/{id}/mesh/add_repeaters`: inclusão de repetidores.
- `POST /planning/scenarios/{id}/candidate_edges/build`: construção comum dos
  candidatos; o seletor mesh utiliza o cenário ampliado já preparado.
- `POST /planning/scenarios/{id}/metrics/compute`: cálculo comum de métricas,
  sem ramo mesh executando rodadas.
- `POST /planning/scenarios/{id}/solution/solve`: solução comum do grafo,
  sem remoção automática de rádios.
- `POST /planning/scenarios/{id}/mesh/remove_unused_repeaters`: retirada dos
  repetidores propostos sem uso, depois da solução.

O serviço decide a topologia; o SDK lê arquivos, faz chamadas e salva os
resultados. Nenhum endpoint recebe caminhos locais do usuário. O atalho
`plan_mesh_network` permanece compatível, executando explicitamente essas
operações no núcleo do serviço. O fluxo recomendado é o exposto no notebook.

## Artefatos e inspeção

- As entradas `scenario.toml`, `nodes.csv` e `poles.csv` permanecem intactas.
- `features.json` contém features brutas reutilizáveis, sem custos derivados.
- `mesh_iterations/0000`, `0001` etc. registram os estados da inclusão.
- `mesh_expanded` recebe o cenário e inventário ampliados depois da inclusão.
  Esses arquivos podem ser examinados antes das operações comuns.
- `mesh_solution` recebe a configuração final e o inventário correspondente
  quando o resultado solucionado é salvo. O TOML com sites explícitos é
  autoritativo; o CSV é um inventário por site no fluxo de perfis. Sites
  complexos sem perfil único são representados somente no TOML.
- `result.json` informa a fase, as propostas, os repetidores selecionados,
  os efetivamente retirados e as pendências após a solução.

`show_mesh_study(step, attempt=1, kind="candidate")` inspeciona as alternativas
calculadas durante a inclusão. Todos os postes disponíveis no corredor são
exibidos; somente os enlaces avaliados têm linhas. Essa inspeção já está
 disponível depois de `add_repeaters`. `show_network(kind="candidate")` mostra
os candidatos globais depois da construção comum; `show_network()` mostra a
solução. O notebook `example_5_Mesh_network.ipynb` usa somente chamadas ao SDK.

Uma falha de extração é propagada, sem apresentar uma execução parcial como
concluída. As extrações concluídas ficam no cache para repetição. Os snapshots
são gravados depois da resposta da inclusão; não constituem retomada de job em
execução. Para comparar configurações, criar uma cópia do projeto antes de
alterar entradas. As saídas da própria pasta são atualizadas em nova execução.
