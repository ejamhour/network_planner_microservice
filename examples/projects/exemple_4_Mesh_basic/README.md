# exemple_4_Mesh_basic

Atualiza os casos de planejamento com postes de tests/jupyter/legacy/testes.ipynb.

## Entradas e papéis

- nodes.csv: somente os equipamentos da rede, com site_id, perfil, coordenadas, altura, connected e can_route. Copel precisa de conexão; Guaíra e Itália são acessos conectados.
- poles.csv: infraestrutura disponível, com pole_id, device_profile, lat, lon e mount_height_m. O perfil descreve o equipamento que poderá ser instalado; não significa que ele já existe. Não há connected, candidate ou rank nesse arquivo.
- scenario.toml: perfis dos rádios/antenas, parâmetros de busca e [mesh.poles] com source="poles.csv", format="csv" e profile_column="device_profile".

O SDK lê os postes localmente somente ao chamar plan_mesh_link e envia sua configuração separadamente na requisição. O cenário armazenado pelo microserviço continua com apenas os equipamentos de nodes.csv. A preparação temporária filtra os candidatos por tecnologia/frequência dos endpoints, interface habilitada, can_relay e can_route. Somente os repetidores escolhidos entram na solução; o mapa de busca preserva também as alternativas avaliadas.

Torre usa outro perfil mesh. Deneka tem duas interfaces; somente a mesh participa. Shopping tem apenas Wi-SUN e é excluído da busca mesh. As configurações são ilustrativas. IDs de postes devem ser únicos e não podem colidir com IDs de equipamentos da rede.

## Execução e inspeção

O notebook usa somente chamadas ao SDK (0.1.50 ou posterior), planeja Copel → Guaíra e Copel → Itália independentemente e mostra dois mapas por estudo:

- show_mesh_candidates(): posições candidatas compatíveis e todos os enlaces efetivamente avaliados, com os custos, inclusive os descartados. O caminho escolhido é laranja; as outras alternativas são cinza. Cada enlace informa evaluated, expanded (segmento explorado para buscar repetidores) e selected. Candidato isolado não foi avaliado nesta busca.
- show_mesh_link(): somente os equipamentos e enlaces do caminho escolhido.

Os HTMLs são separados por estudo e tipo de mapa. result.json contém a última proposta e seu grafo mesh_candidates. features.json contém features brutas reutilizáveis, sem custos derivados. Os arquivos originais nodes.csv, poles.csv e scenario.toml são preservados.

## Métrica e limites

A métrica demonstra custo por obstrução do relevo, não RSSI calibrado. Os perfis mesh usam 900 MHz e parâmetros ilustrativos; posições e alturas vêm dos testes antigos. A busca usa o maior custo dos dois sentidos, com mínimo 1. O limite por segmento é 2 e o máximo é cinco repetidores. mesh_search.meets_segment_threshold informa se a proposta atende ao limite. A busca heurística não avalia todas as combinações possíveis e pode retornar uma proposta que ainda precise de revisão.

Este exemplo planeja enlaces individuais. A incorporação das propostas a uma rede instalada continua sendo uma decisão posterior do engenheiro.
