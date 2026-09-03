# Descricoes revisadas das etapas

Base de referencia: codigo disponivel em `D:\CISEI\TELECOM\microservices`, especialmente os micro-servicos `features-service\planning_service` e `network-service\network_planner_microservice`.

## Micro-servicos

- Extracao de caracteristicas ambientais: consolida dados de relevo, cobertura/vegetacao e edificacoes para caracterizar enlaces de radio em termos de propagacao e interferencias ambientais. A implementacao disponivel no `features-service` calcula perda em espaco livre, difracao, invasoes das zonas de Fresnel, contribuicoes de vegetacao e edificacoes, e disponibiliza essas informacoes por APIs de consulta e visualizacao.
- Planejamento de redes: realiza o planejamento de enlaces e redes usando cenarios configuraveis, nos com uma ou mais interfaces de radio, metricas especificas por tecnologia e algoritmos de roteamento sobre grafos candidatos. A implementacao disponivel no `network-service` permite representar tecnologias como LTE e radios mesh/multi-hop, dispositivos folha, repetidores, pontos conectados ao backbone e redes hibridas.

## E06 - Planejamento de rede com garantias de desempenho

Etapa concluida. A etapa foi implementada por meio de uma arquitetura em que as caracteristicas ambientais dos enlaces sao transformadas em metricas de planejamento configuraveis. O codigo evidencia esse mecanismo no compilador de metricas (`metric_compiler.py`) e na sua integracao com o planejador de grafos (`graph_planner.py`).

Essa abordagem permite que o planejamento deixe de depender exclusivamente de modelos teoricos fixos e passe a usar metricas ajustaveis por tecnologia, por perfil de radio e por criterio de robustez. As metricas disponiveis no repositorio incluem modelos baseados em invasoes de Fresnel, perda de percurso, difracao e configuracoes especificas para enlaces com torres LTE. Essas metricas podem penalizar de forma nao linear enlaces com maior obstrucao ambiental, favorecendo solucoes com maior margem operacional.

Do ponto de vista gerencial, o resultado e uma capacidade de planejamento com garantias parametrizaveis de desempenho: o usuario pode alterar a severidade das penalizacoes, selecionar metricas por tecnologia e calibrar o comportamento do planejador com dados empiricos quando disponiveis. Com isso, a ferramenta passa a suportar planejamentos mais conservadores, mais aderentes ao ambiente real e adaptaveis a diferentes tecnologias de comunicacao.

## E07 - Planejamento de rede com redundancia

Etapa concluida. A funcionalidade foi implementada pela evolucao do modelo de planejamento para representar redes com multiplas alternativas de conexao, multiplas interfaces por dispositivo e diferentes papeis de nos na topologia. O suporte tecnico aparece nos modulos de modelagem de cenarios (`planner_classes.py` e `planning_scenario.py`) e no algoritmo de propagacao RPL generico (`geo_rpl_agnostic.py`).

O modelo permite definir quais pontos ja estao conectados ao backbone, quais equipamentos podem atuar como repetidores e quais devem operar apenas como folhas. Tambem permite configurar dispositivos com mais de uma interface de radio, criando condicoes para planejamentos hibridos, por exemplo combinando acesso LTE e radios mesh/multi-hop. A redundancia pode ser avaliada por alternativas de caminho, por tecnologias habilitadas e por diferentes configuracoes de nos fixos ou interfaces ativas.

Em termos de capacidade entregue, a ferramenta consegue construir cenarios em que a rede nao depende de uma unica forma de conectividade. O planejamento pode explorar caminhos candidatos, selecionar rotas de menor custo e permitir comparacoes entre configuracoes redundantes ou hibridas. Ajustes operacionais, como manter alternativas de pais no RPL ou repetir o planejamento com conjuntos diferentes de interfaces habilitadas, ficam naturalmente acomodados pela estrutura de grafo e pelo modelo de cenarios.

## E13 - Avaliacao do aplicativo de planejamento com garantia de desempenho e tolerancia a falhas

Etapa em andamento. A avaliacao vem sendo conduzida a partir de cenarios simulados e notebooks de validacao que exercitam a criacao de cenarios, a configuracao de perfis de radio, a geracao de enlaces candidatos, o calculo de metricas e a execucao do planejamento. O repositorio inclui exemplos com uma interface LTE e exemplos com dispositivos de duas interfaces combinando LTE e radio mesh/multi-hop.

Esses testes demonstram que a ferramenta ja possui base funcional para avaliar desempenho e tolerancia a falhas em diferentes arranjos de rede. Como as metricas sao configuraveis, e possivel variar o nivel de exigencia sobre invasoes de Fresnel e outras caracteristicas ambientais, comparando o impacto dessas escolhas na topologia planejada. Como os nos e interfaces tambem sao configuraveis, e possivel comparar alternativas com diferentes graus de redundancia.

O trabalho atual deve ser entendido como uma etapa de consolidacao e melhoria de usabilidade. A capacidade tecnica principal ja esta presente; o foco passa a ser simplificar a configuracao para o usuario, organizar fluxos de avaliacao e transformar a flexibilidade do modelo em procedimentos de planejamento mais diretos e repetiveis.

## E08 - Criacao dos modelos de baseline para diagnosticos corretivos e preditivos

Etapa concluida. Foi implementada uma camada de modelagem empirica para estabelecer baselines de desempenho de enlaces a partir de dados medidos em campo. A base tecnica dessa etapa esta no modulo `rssi_core.py`, que define modelos parametrizaveis por expressoes, rotinas de treinamento, predicao, explicacao de contribuicoes e calculo de residuais.

O baseline e construido comparando medidas observadas, principalmente RSSI, com caracteristicas do enlace e do ambiente. O RSSI foi adotado como variavel central por representar uma medida individual do enlace, mais adequada para diagnostico pontual do que metricas acumuladas de caminho. O modelo padrao disponivel no repositorio (`default_900.json`) registra treinamento com 1890 enlaces e utiliza termos associados a potencia, ganhos de antena, perda em espaco livre, difracao, vegetacao e edificacoes.

Na pratica, essa etapa entrega a capacidade de identificar o desempenho esperado para enlaces com caracteristicas semelhantes e medir o quanto cada enlace se desvia desse comportamento. Enlaces com residuais excessivos podem ser priorizados para investigacao, indicando possiveis problemas de projeto, cadastro, instalacao, alinhamento de antenas, defeitos de equipamento ou obstrucoes locais nao capturadas plenamente pelos dados ambientais.

## E09 - Deteccao de causa raiz em diagnostico corretivo

Etapa concluida. A deteccao de causa raiz foi estruturada como uma comparacao entre desempenho medido e baseline esperado, usando modelos empiricos treinados com caracteristicas ambientais e parametros de enlace. Essa abordagem substitui a dependencia de uma integracao continua com sistemas externos por um fluxo mais flexivel, capaz de operar com dados fornecidos em arquivos ou bases exportadas.

O mecanismo implementado permite estimar o RSSI esperado, calcular residuais e decompor a previsao em contribuicoes associadas aos principais fatores do enlace. Com isso, a ferramenta nao apenas aponta quais enlaces estao fora do comportamento esperado, mas tambem oferece indicios sobre quais componentes mais influenciaram a previsao, como perda de percurso, difracao, vegetacao ou edificacoes.

Do ponto de vista operacional, a ferramenta apoia diagnosticos corretivos ao priorizar enlaces anormais e orientar a investigacao de campo. A conclusao definitiva da causa raiz ainda depende da analise tecnica local e da qualidade dos dados disponiveis, mas o sistema reduz o espaco de busca e torna a triagem mais objetiva.

## E14 - Avaliacao da ferramenta de monitoramento com acompanhamento de baseline e deteccao de causa raiz

Etapa em andamento. A avaliacao da ferramenta de monitoramento esta apoiada em dois blocos: o acompanhamento de baselines empiricos de RSSI e o processamento de metricas operacionais importadas para analise offline. O codigo disponivel inclui rotinas para importar series de metricas, aplicar regras de transformacao, persistir os dados em DuckDB e mesclar dados de SCADA quando houver base exportada compativel (`monitoring\import_metrics.py`).

Essa estrategia permite continuar avaliando o metodo mesmo quando o acesso automatizado a sistemas externos de monitoramento esta indisponivel. Os dados podem ser fornecidos por exportacoes locais, processados em formato padronizado e comparados com os modelos de baseline. O modelo tambem pode ser ajustado para criterios mais otimistas ou pessimistas, permitindo controlar o nivel de confianca exigido para marcar uma anomalia.

Em nivel gerencial, a etapa demonstra a viabilidade de manter uma ferramenta de acompanhamento baseada em dados historicos e exportados, sem depender exclusivamente de integracoes online. Quando o fluxo de dados monitorados for restabelecido, a mesma base conceitual pode ser reaproveitada para acompanhamento temporal continuo, preservando o nucleo de predicao, comparacao e deteccao de anomalias.

## E10 - Integracao dos modulos de monitoramento e planejamento

Etapa concluida. A integracao foi implementada por desacoplamento entre tres responsabilidades: extrair caracteristicas ambientais, transformar essas caracteristicas em metricas de desempenho e usar as metricas no planejamento da rede. Essa separacao aparece no codigo pela combinacao do micro-servico de extracao de caracteristicas, dos modelos de RSSI e metricas configuraveis, e do planejador baseado em grafos.

Com essa arquitetura, o planejamento nao fica preso a uma unica formula teorica de RSSI, SNR ou MCS. As mesmas caracteristicas ambientais podem alimentar metricas analiticas, modelos empiricos treinados com dados de campo ou criterios especificos por tecnologia. O planejador usa essas metricas para ponderar as alternativas de conexao e construir topologias que equilibram desempenho, robustez e disponibilidade de interfaces.

O modulo de monitoramento complementa essa integracao ao fornecer dados medidos para calibracao, validacao e diagnostico. Mesmo quando os dados chegam por arquivos ou bancos exportados, eles podem alimentar o ciclo de melhoria das metricas e dos baselines. Assim, planejamento e monitoramento passam a formar um fluxo unico: o ambiente e descrito pelo micro-servico de caracteristicas, o desempenho esperado e aprendido pelos modelos de baseline, e o planejamento usa essas informacoes para propor redes mais robustas e adaptadas ao comportamento real observado.
