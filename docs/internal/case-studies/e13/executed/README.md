# Validação E13 — Replanejamento AGU-S

O notebook `e13_replanejamento.ipynb` foi executado integralmente em 28/10/2026
com os serviços reais `network-service:8080` e `features:8080`. A cópia executada
também está no workspace do usuário. Os oito arquivos de origem conferidos por
SHA-256 permaneceram idênticos: TOML, CSV, resultado instalado, features brutas,
features enriquecidas, avaliação, inventário de equipamentos e reconfiguração.

A escolha confirmada continha quatro enlaces em modo `link`, sem remoção de
rádios. A preparação preservou 65 rádios e 60 enlaces; 54 interfaces continuaram
conectadas e 11 ficaram aguardando reconexão. A métrica já calibrada da E08 foi
reutilizada, sem treinamento adicional. Os custos dos enlaces preservados foram
mantidos na consolidação.

Condições da busca: rádio candidato GE em 900 MHz, potência 30 dBm, ganho
8,15 dBi, altura 7 m; corredor de 1.500 m por enlace; até cinco repetidores e
três caminhos por rodada; custo por segmento até 2, calculado pelo pior sentido.
As antenas terminais mantêm os ganhos representados no diagnóstico, sem inferir
diagramas direcionais que não foram fornecidos.

- **GE-014–GE-058:** 1.904 postes no corredor, 42 enlaces avaliados. Nenhum caminho
  avaliado atendeu ao limite nas condições da busca. O enlace permaneceu pendente.
- **GE-015–GE-066:** 296 postes no corredor, 28 enlaces avaliados. Caminho aceito
  com um repetidor no poste 917784800; custo total 2,9290. Cada segmento atende ao
  limite, embora o custo total seja maior que o da ligação direta inadequada.
- **GE-022–GE-031:** 325 postes no corredor, 39 enlaces avaliados. Caminho aceito
  com um repetidor no poste 923060901; custo total 2,9625. Todos os segmentos
  atendem ao limite.
- **GE-024–GE-025:** 575 postes no corredor, 68 enlaces avaliados. Nenhum caminho
  avaliado atendeu ao limite nas condições da busca. O enlace permaneceu pendente.

A consolidação contém os 65 rádios originais e dois repetidores propostos.
64 das 67 interfaces alcançam o único acesso original, AGU-S-P7-001. GE-025,
GE-026 e GE-058 continuam sem serviço. A conexão instalada GE-025–GE-026 continua
preservada e visível, apesar de ambos os rádios estarem desconectados do backbone.
O cache resultante tem 409 entradas brutas reutilizáveis.

O notebook mostra os grafos efetivamente avaliados, as quatro propostas e a rede
consolidada. A busca é heurística e limitada pelas premissas: um resultado pendente
não prova inviabilidade física em outras configurações. Os resultados são propostas
de planejamento, sem alteração de equipamentos ou enlaces reais.

A validação também revelou a necessidade de priorizar a qualidade por segmento
na escolha do caminho final. A correção correspondente está descrita em
`docs/releases/0.1.56-sdk.md` e possui teste regressivo específico.
