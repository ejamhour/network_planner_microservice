# E13 — Replanejamento de enlaces AGU-S

Execute `notebooks/e13_replanejamento.ipynb` depois de revisar e confirmar a
seleção em `e13_diagnostico.ipynb`, usando somente o modo enlace. O projeto de
origem permanece em `projects/e10_diagnostico`; este diretório recebe as propostas
e o cenário de replanejamento. Requer SDK 0.1.56 com extras notebook e os serviços
de rede e features já disponíveis.

As únicas entradas distribuídas deste projeto são `replanning.toml` e
`poles.csv`. Os 22.078 postes foram convertidos uma única vez do arquivo
`home/cisei/projects/teste/poles_geo.json` fornecido pelo usuário. O CSV contém
ID e coordenadas geográficas/projetadas em EPSG:31982. O SDK consome esse formato
canônico; não incorpora a importação do formato legado. SHA-256 do GeoJSON de
origem: `ecac145299016448825bbebd9009b0511a9d2a0beccdbae518aa792a527b5748`.

A preparação valida a reconfiguração confirmada, preserva os 65 rádios e retira
somente os enlaces indicados. Calcula os custos com a métrica já existente e
reutiliza o cache bruto de features. Não altera os arquivos do diagnóstico.
Os pontos ainda conectados ficam fixos com seus ranks acumulados no novo
nodes.csv. Os outros enlaces continuam explícitos no scenario.toml, mesmo em
componentes que perderam acesso ao backbone.

Cada par removido é estudado com o LinkPlanner já exposto pelo serviço. Somente
os postes num corredor de 1.500 m ao redor daquele segmento são oferecidos como
candidatos. O perfil proposto é ge_radio_1, 900 MHz, 30 dBm, ganho de 8,15 dBi e
altura de 7 m. A busca admite cinco repetidores e três caminhos por rodada.
Esses parâmetros são premissas do exemplo, editáveis em replanning.toml.

O custo por segmento usa o maior custo dos dois sentidos. Só entram na rede
consolidada propostas em que todos os segmentos atendem ao limite configurado
(2 nesta métrica, correspondente a RSSI previsto de -80 dBm). Uma busca limitada
pode não encontrar uma alternativa viável. As propostas rejeitadas continuam
disponíveis para inspeção; as demandas correspondentes podem permanecer sem
serviço. Os estudos não compartilham automaticamente os rádios novos: um mesmo
poste usado em dois estudos recebe identidades de rádio diferentes.

Arquivos gerados:

- `scenario.toml` e `nodes.csv`: cenário após a retirada dos enlaces.
- `reconfiguration.json`: decisão confirmada aplicada à preparação.
- `installed_network.json`: referência da rede instalada, configuração e custos.
- `prepared_network.json`: rede preservada antes das novas ligações.
- `features.json`: cache bruto, reutilizado e ampliado pelas buscas.
- `link_studies/`: proposta e grafo avaliado de cada par.
- `replanning_result.json`: atendimento ao limite em cada estudo.
- `result.json`: rede consolidada, com somente as propostas aceitas.
- `solution_scenario.toml`: configuração completa da solução consolidada.

A consolidação usa apenas os enlaces preservados e as propostas aceitas, sem
gerar uma nova malha de alternativas sobre os rádios existentes. Os únicos
acessos externos são os originais. A versão inicial exige um dispositivo e uma
interface por site instalado; rejeita entradas ambíguas e solicitações de
subárvore, em vez de modificar silenciosamente sua interpretação.
