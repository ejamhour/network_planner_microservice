# E06 — AGU-S: projeto completo, parte 1

Este projeto aplica o planejador mesh atual a um novo desenho da rede AGU-S.
A topologia instalada é referência de inventário; nenhum enlace instalado ou
repetidor anterior é imposto à solução. Não se trata de replanejar somente os
enlaces classificados no diagnóstico E13.

## Entradas e premissas

- `nodes.csv`: 33 rádios obrigatórios: o acesso AGU-S-P7-001 e 32 rádios com
  equipamentos associados. Apenas o acesso começa conectado, com rank zero.
- `attached_equipment.csv`: 39 equipamentos, sendo 27 DA e 12 AMI. As relações
  equipment_id/equipment_type/device_id são preservadas do inventário AGU-S.
- Os 27 rádios que atendem DA são folhas, inclusive os sete que também atendem
  AMI. Os cinco rádios somente AMI podem retransmitir, por premissa deste projeto.
- `poles.csv`: 22.078 posições candidatas, com perfil de repetidor `ge_radio_1`.
  Os postes são infraestrutura opcional, não nós obrigatórios da rede inicial.
- `scenario.toml`: 900 MHz, potência de 30 dBm, altura de 7 m e ganhos do
  inventário; métrica calibrada na E08, preservada do diagnóstico AGU-S.
  Os novos repetidores usam ganho de 8,15 dBi. Para os terminais YAGI, o ganho
  original é preservado, mas não há diagrama nem orientação; a representação de
  ganho constante não estabelece a orientação das antenas físicas.

Os 32 rádios sem associação informada não são demandas deste novo projeto.
Isso é um recorte baseado no inventário disponível, não uma afirmação de que
podem ser retirados de uma rede instalada. A lista inclui GE-058, cujo cadastro
foi questionado. Os projetos anteriores permanecem intactos.

## Carga simulada para a parte 2

A coluna `traffic_units` atribui 1 unidade de tráfego a cada DA e um inteiro
entre 5 e 20 a cada AMI, conforme confirmado pelo usuário. Os valores AMI são
reproduzíveis: 5 + os quatro primeiros bytes do SHA-256 de equipment_id,
interpretados como inteiro big-endian, módulo 16. São dados sintéticos, sem
pretensão de representar tráfego medido, banda em bit/s ou SLA.

O carregador de equipamentos preserva a coluna adicional. Nesta parte os
valores são somente inventário: não alteram ranks, seleção de pais ou custos.
Limites de comprimento dos caminhos e carga por ramo serão implementados e
avaliados na parte 2. Não há esses limites ativos no projeto atual.

## Execução

Use `e06_01_agu_projeto_completo.ipynb`, SDK 0.1.62 ou superior e os serviços
network e features atualizados. A busca usa dois destinos por terminal,
corredor de 1.500 m e até dois repetidores por estudo. São controles de busca,
não limites de profundidade da árvore final. A métrica máxima por segmento
é 3,25, equivalente ao limiar de -80 dBm na nova conversão não linear.

`add_repeaters(max_rounds=1, progress=True)` estuda uma interface pendente por
chamada. A API agenda o lote e responde com um identificador; o notebook consulta
seu estado por requisições curtas e salva o resultado quando termina. Se a caixa
mostrar **Paused**, use a célula de inspeção das iterações e depois volte à célula
de expansão. O próximo lote retoma o último checkpoint salvo. Apenas os acessos
originais continuam fixos; roteadores alcançáveis podem servir como pais.

Se não houver mais interfaces alcançáveis, o lote termina como **stalled**;
as interfaces pendentes permanecem no inventário. Se os dados ou parâmetros
da busca mudarem, inicie um novo estudo explicitamente com `resume=False`.
Features compatíveis continuam reutilizáveis em `features.json`.

O notebook salva `mesh_iterations`, `mesh_expanded`, `mesh_solution` e o
resultado completo em `result.json`; não altera as entradas originais.
Inspecione o resultado antes de concluir que todas as demandas foram atendidas.

## Rastreabilidade

Rede e associações: `examples/projects/e10_diagnostico`.
Postes: `examples/projects/e13_replanejamento/poles.csv`, convertido de
`home/cisei/projects/teste/poles_geo.json`. Somente os dados de entrada são
copiados; resultados, diagnósticos e enlaces anteriores não são herdados.

Rádios excluídos do inventário inicial deste estudo:

AGU-S-GE-001, AGU-S-GE-002, AGU-S-GE-004, AGU-S-GE-005, AGU-S-GE-006, AGU-S-GE-009, AGU-S-GE-010, AGU-S-GE-011, AGU-S-GE-014, AGU-S-GE-018, AGU-S-GE-019, AGU-S-GE-020, AGU-S-GE-022, AGU-S-GE-023, AGU-S-GE-024, AGU-S-GE-025, AGU-S-GE-027, AGU-S-GE-031, AGU-S-GE-032, AGU-S-GE-033, AGU-S-GE-034, AGU-S-GE-042, AGU-S-GE-043, AGU-S-GE-045, AGU-S-GE-047, AGU-S-GE-049, AGU-S-GE-050, AGU-S-GE-052, AGU-S-GE-062, AGU-S-GE-065, AGU-S-GE-066, AGU-S-GE-058

## Antenas por interface

Com SDK 0.1.63, `antenna_placements.toml` permite sobrescrever a antena e sua instalação por interface. O arquivo inicial contém uma tabela vazia: todos os valores atuais são preservados e nenhuma orientação é inventada. Consulte `project-contracts/antenna-placements.toml.md` no repositório para o contrato.

## Conversão de RSSI em custo

O projeto ativa `assume_symmetric_features = true` em `[mesh]`. Cada avaliação
bidirecional reutiliza a extração do sentido inicial, troca os dados de TX e RX
ao calcular o custo inverso e mantém o pior dos dois custos. O limite de 3,25
continua valendo nos dois sentidos. Desative a opção para métricas que usam
atributos que mudam com o sentido do enlace. Requer SDK 0.1.65 e serviço atualizado.

`metrics/e06_ge_rssi_rank.toml` preserva o estimador de RSSI da E08. A conversão em custo usa dois regimes. Com RSSI previsto de −80 dBm ou melhor, o custo é 1 a partir de −65 dBm e cresce quadraticamente até 3 no limite de −80 dBm. Com RSSI pior que −80 dBm, a segunda equação soma o custo de um salto aceitável e uma parcela quadrática à medida que o sinal piora. O rank soma os custos dos enlaces até o backbone. A expansão inicial usa limite por segmento de 3,0. O notebook carrega o arquivo da métrica antes da expansão, e a mesma especificação está embutida no TOML. Resultados salvos registram os custos da métrica usada na execução.
