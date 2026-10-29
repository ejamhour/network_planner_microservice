# Rede mesh com inclusão de repetidores

Use SDK >= 0.1.60 e o microserviço atualizado. O cenário começa com dois acessos fixos (Guaira e Italia) e três terminais (Copel, Woodland e Cemiterio). Os terminais não encaminham tráfego; os postes ficam separados em poles.csv, com seus perfis de rádio candidatos.

Execute o notebook example_5_Mesh_network.ipynb:

1. Abrir com client.open_project(project, planner="mesh").
2. add_repeaters: executar o laço de inclusão de repetidores com LinkPlanner.
3. build_candidate_edges: construir o grafo comum dos rádios presentes.
4. compute_metrics: calcular os custos pelo GraphPlanner e reutilizar o cache.
5. solve: selecionar os caminhos sem alterar o conjunto de rádios.
6. remove_unused_repeaters: retirar os repetidores propostos sem uso.
7. save_result: salvar a configuração e a solução finais.

As entradas originais são preservadas. mesh_iterations contém as rodadas, mesh_expanded o cenário ampliado e mesh_solution o projeto final. O TOML de saída tem as instâncias completas e é a definição autoritativa; o CSV acompanha o inventário por site quando possível. Somente os acessos originais são fixos, inclusive na solução final.

A métrica e os limites configurados determinam as propostas. Não há balanceamento por carga ou penalização adicional por saltos nesta versão. Consulte docs/mesh_network_planning.md no código-fonte do serviço.
