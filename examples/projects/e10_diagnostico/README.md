# E10 — Diagnóstico da rede AGU-S

Este projeto aplica a metodologia já implementada na E09 à rede instalada AGU-S. Não altera o SDK ou o microserviço, não treina outro modelo, não escolhe novos caminhos e não propõe melhorias. O replanejamento ficará para e10_melhorias.

## Origem e preparação

Entradas: D:/CISEI/TELECOM/old_code/PlanApp/PlanApp/home/cisei/monitoring/agu-s/inbox/bhnodes_geo.json. A versão com nome bhnodess_geo.json não foi utilizada. São 65 equipamentos, 64 relações pai–filho, um único acesso ao backbone (AGU-S-P7-001), sem ciclos ou pais ausentes. O maior caminho tem 12 conexões.

A conversão foi feita fora do SDK, para os formatos já aceitos. nodes.csv preserva todos os equipamentos, IDs, coordenadas, pais informativos e modos originais. Somente o nó sem pai é connected=true; os demais access points mantêm sua dependência original. Todos permanecem aptos a retransmitir para preservar a topologia instalada. Essa configuração não determina quais poderão ser removidos em uma melhoria posterior.

scenario.toml contém exclusivamente enlaces instalados explícitos, sem geração automática de candidatos. A orientação pai → filho acompanha a medição Parent Node Avg Rssi: sinal recebido no filho, transmitido pelo pai. A rota de saída é percorrida no sentido inverso.

## Parâmetros

O usuário confirmou em 28/10/2026 o uso de 900 MHz e do modelo compartilhado da E08 (metrics/e08_copel_rssi.json), como no exemplo BRO. Potência de 30 dBm e altura de 7 m constam na representação legada current/graph.json de todos os equipamentos. A configuração 01-import.toml também declara esses valores como padrões. Não foram tratados como medições individuais de instalação.

Os ganhos 8,15 dBi, 14 dBi e 2,5 dBi vêm do inventário. As 36 antenas YAGI preservam o ganho declarado, mas não há diagramas ou orientação para representá-las direcionalmente. O perfil de ganho constante não afirma que essas antenas físicas sejam omni. As coordenadas projetadas usam EPSG:31982, como a E09.

## Medições e conexões internas

Cinco relações pai–filho estão na mesma posição e são explicitamente b2b: GE-009/010, GE-019/020, GE-032/033, GE-042/043 e GE-049/062. Permanecem como conexões internas, com custo zero e sem avaliação de propagação ou classificação de RSSI.

Restam 59 enlaces de rádio. data/e10/agu_s/hop_metrics.json contém 57 observações válidas no contrato cisei-link-measurements, versão 1. Os enlaces AGU-S-GE-025 → AGU-S-GE-026 e AGU-S-GE-014 → AGU-S-GE-058 têm RSSI zero na origem: são mantidos na topologia, mas sem observação de rádio. O método existente usa somente a previsão nesses casos. Não se interpreta RSSI zero como sinal válido nem se atribui defeito sem medição.

data/e10/agu_s/path_metrics.json preserva a perda de pacotes e sua contagem por equipamento, referentes ao caminho. Não entra na classificação individual por salto deste notebook. Não foram inventados horários, séries temporais ou métricas de RTT.

## Uso

Abrir notebooks/e13_diagnostico.ipynb no workspace, com o mesmo SDK e serviços usados pela E09. O fluxo importa a topologia, mostra vistas lógica e geográfica, extrai features apenas dos enlaces RF, permite sua inspeção, enriquece as features, aplica o modelo da E08 e mostra o diagnóstico de cada caminho. O limiar demonstrativo é -80 dBm.

features.json é o cache bruto; enriched_features.json contém as medições associadas; evaluation.json contém as classificações e previsões; result.json conserva a topologia instalada. Os quatro mapas HTML são gravados pelo visualizador existente. O notebook distribuído não inclui saídas salvas.

Interpretação: investigar instalação/defeito quando a previsão é boa e a medição ruim; revisar projeto quando ambas são ruins; nenhuma correção quando a medição é satisfatória. A indicação de inspeção não comprova um defeito físico específico.

Os 22.078 postes fornecidos pelo usuário não são usados nesta etapa de diagnóstico. Serão entrada independente do planejamento de melhorias.

## Equipamentos associados e inspeção

attached_equipment.csv relaciona equipment_id e equipment_type ao device_id do rádio, com uma linha por equipamento. A referência fica em [attachments] do TOML. O inventário complementar vem de home/cisei/projects/teste/bhns_geo.json: 39 associações em 32 rádios, sendo 27 DA e 12 AMI. Um rádio pode ter vários equipamentos. AGU-S-GE-058 não aparece nesse inventário; ausência de linha significa associação não informada, e não equipamento dispensável.

Com SDK 0.1.51, show_topology destaca em roxo os rádios com equipamento associado e lista os equipamentos ao selecionar o nó, nas vistas lógica e geográfica, com ou sem diagnóstico. As cores dos enlaces continuam representando as categorias E08. O inventário é descritivo: não altera os enlaces, features, medições, modelo ou classificação, nem exige refazer a extração.

A execução real foi concluída pelo usuário após a correção do processamento de rasters estreitos no serviço de features. O resultado salvo contém dez enlaces para inspeção e quatro para revisão de projeto, sendo três com medição ruim e um sem medição. O próximo estudo distinguirá reparo de enlaces individuais (LinkPlanner) e replanejamento da subárvore (planejador mesh por rodadas).


## Revisão da reconfiguração (SDK 0.1.55)

A seção 9 do notebook abre `project.review_reconfiguration(layout="logical")`.
Escolha enlace ou subárvore antes de clicar. O botão de sugestões seleciona os
quatro enlaces para revisar projeto; a escolha continua sendo do engenheiro.
Abra **Seleções e repetidores** para identificar os rádios removíveis. Rádios com
equipamentos associados são mantidos, e a ausência no inventário não autoriza
removê-los. Revise, marque a confirmação e salve `reconfiguration.json` diretamente no projeto.

No Jupyter, o botão de confirmação grava o arquivo pela API de arquivos da sessão e mostra o caminho salvo.
`project.load_reconfiguration(project.path / "reconfiguration.json")` verifica a
confirmação, a origem e o impacto. A revisão não altera a rede instalada. A geração
do projeto de melhorias será feita separadamente.

Os enlaces recomendados aparecem em âmbar antes da seleção. Vermelho tracejado
indica remoção na prévia; a legenda distingue recomendação de decisão do engenheiro.

O JSON registra somente origem, escolhas e os enlaces/rádios a remover. No modo
subárvore, a interface lista os repetidores marcados para retirada e informa
quando nenhum rádio foi marcado. A última célula do notebook lê e valida o
arquivo salvo, exibindo o resultado para revisão.
