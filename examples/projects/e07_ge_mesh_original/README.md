# E07 — GE mesh, layer original

Derivado de example_1_LTE_planning_and_link_inspection: 13 sites, com os mesmos
IDs, posições e alturas. Os dois antigos sites celulares (torre e itallia)
passam a ser pontos de acesso mesh conectados. Os demais equipamentos podem
encaminhar tráfego. Nenhum arquivo do exemplo LTE é alterado.

Parâmetros ilustrativos: 915 MHz, 30 dBm, ganho fixo de 3 dBi e limite de 2200 m
para geração de candidatos. Não são especificações verificadas de equipamento GE.
A métrica default é o custo genérico de obstrução de terreno existente, sem
calibração GE nem garantia de capacidade, cobertura RF ou estabilidade operacional.

Executar notebooks/e07_01_projeto_original.ipynb. Os resultados usam features reais
extraídas pelo serviço quando disponíveis, mas continuam sujeitos a essas hipóteses.
O notebook apresenta dependências e permite a escolha manual do site a proteger.
