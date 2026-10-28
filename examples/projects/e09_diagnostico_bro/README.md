# E09 — Diagnóstico de caminhos da rede BRO-S

Projeto preparado a partir dos 25 equipamentos de `bro-s/inbox/bhns_geo.json`.
A conversão foi feita fora do SDK. O projeto utiliza os formatos existentes:
TOML, CSV de instâncias e JSON de medições da E08. Não recebe GeoJSON nem
MessagePack e não incorpora análise temporal.

## Arquivos de entrada

- `scenario.toml`: perfis de rádio e antena e 24 conexões instaladas explícitas
  em `net.candidate_edges`. Não há regras de geração automática de enlaces.
  São 22 trechos de rádio e duas conexões internas back-to-back. Os pares
  GE-026/GE-025 e GE-001/GE-028 estão nas mesmas coordenadas e são marcados
  como `b2b` na origem; não recebem extração de propagação. O RSSI zero
  desses dois registros originais não representa sinal de rádio medido.
- `nodes.csv`: 25 sites com um dispositivo e uma interface por site, posições,
  perfis e identificadores de automação/medição disponíveis. `next_hop` é
  preservado como informação auxiliar.
- `data/e09/hop_metrics.json`: 24 observações no contrato
  `cisei-link-measurements`, versão 1, já aceito por `enrich_features()`.
- `baseline.json`: configuração para avaliar os enlaces com o modelo da E08,
  `metrics/e08_copel_rssi.json`. Não é uma receita de novo treinamento.
- `data/e09/path_metrics.json`: métricas dos caminhos completos, preservadas
  como entrada para cruzamento com a aplicação. Não são métricas de um único salto.

Os caminhos `data/...` e `metrics/...` são relativos à raiz do workspace.
Os exemplos da E08 devem estar disponíveis no mesmo workspace para fornecer
o modelo. Reutilizar esse modelo é uma escolha de demonstração, cuja
adequação à tecnologia deve ser avaliada pelo engenheiro.

## Topologia e sentido das medições

O único ponto sem pai, `BRO-S-P7-001`, foi representado como acesso ao backbone.
Os outros dois equipamentos classificados como access point no arquivo
original conservam seus pais e não são convertidos em novas saídas.
Todos os equipamentos são mantidos aptos a encaminhar tráfego; isso preserva
as relações instaladas, inclusive quando o papel nominal não é repetidor.

As interfaces usam o identificador atual `<site_id>:d0:i0`. Cada conexão tem
um `link_id` compartilhado com sua observação. A orientação das conexões no
TOML é pai → filho, para acompanhar a medição de sinal; o caminho de saída
do equipamento percorre essas relações em direção ao pai. A lista explícita
é a fonte da conectividade, e não o campo informativo do CSV.

O usuário confirmou frequência de 900 MHz e que `next_hop_rssi` representa
o sinal recebido no nó, transmitido por seu pai. A observação usa, portanto,
a potência e o ganho do pai em `tx`, e o ganho do filho em `rx`.
RSSI está em dBm, potência em dBm, ganhos em dBi, alturas em metros e posições
da requisição em latitude/longitude. As configurações originais geraram
quatro perfis distintos. Não foram inventados horários de medição.

O arquivo original informa antenas YAGI, mas não seus diagramas e azimutes.
Os perfis preservam o ganho declarado e registram essa limitação na descrição;
o perfil `omni` serve apenas à representação de ganho constante, sem afirmar
que a antena física é omnidirecional.

## Estado da preparação

O notebook `notebooks/e09_diagnostico_bro.ipynb` requer SDK 0.1.47 e executa
a importação local da topologia e sua inspeção lógica e geográfica.
`InstalledNetworkProject.import_topology()` registra os pais em `result.json`,
sem selecionar rotas e sem atribuir rank de qualidade. Esta versão aceita
uma floresta com um acesso ao backbone por componente, rejeitando ciclos e
acessos ambíguos. O SDK reconstrói os caminhos a partir dos enlaces explícitos,
sem depender do `next_hop` informativo. Os caminhos antigos `nic_path` e as
contagens antigas de saltos não são usados para substituir essas conexões.

As cores dessas vistas identificam acesso ao backbone e seleção de caminho;
não representam qualidade. As seções 4 e 5 executam a extração de features
e abrem a inspeção interativa dos enlaces. Não há avaliação de medições nesta
fase de extração. A seção 6 associa as medições por salto ao cache, sem
executar ainda a avaliação do modelo.

`project.extract_features(client, geo_base_url=..., batch_size=10)` reutiliza
a etapa existente de extração/cálculo de custos do serviço de planejamento,
sem executar seleção de rotas ou alterar o resultado instalado. O cache fica
no `features.json` do projeto e é reutilizado nas próximas execuções.
`project.inspect_links(geo)` abre o painel existente para perfis, mapas e
edificações. A sessão de inspeção prepara somente o enlace selecionado.

`project.enrich_features()` gera `enriched_features.json`, preservando
`features.json` como cache puro de extração. Reutiliza o enriquecimento da E08 após validar
o `link_id` e o sentido de cada observação contra as conexões instaladas.
Associa 22 observações de rádio e informa a exclusão dos dois registros
internos, preservados em `hop_metrics.json`. A avaliação lerá o arquivo
enriquecido. Salvar novamente o cache de extração não altera as observações.
Para encontrar
as entradas, o sentido pai → filho, coordenadas,
alturas, frequência e opções precisam coincidir com `hop_metrics.json`.
O arquivo de medições não exige `source` nem endereço do serviço. A origem
permanece no cache de features como informação da extração. Se houver mais
de uma entrada com a mesma requisição em origens distintas, a associação
é rejeitada como ambígua, sem escolher uma delas silenciosamente.

O notebook aplica o modelo da E08 na seção 7 e abre as vistas coloridas na
seção 8. O limiar é escolhido pelo engenheiro:

```python
project.evaluate_links(rssi_threshold_dbm=-80)
project.show_topology(layout="logical", diagnosis=True)
project.show_topology(layout="geographic", diagnosis=True)
```

O limiar é ilustrativo. Não executar `train()` neste projeto: o objetivo é
aplicar o modelo existente, sem recalibrá-lo com a própria rede diagnosticada.
Também não executar `export_metric()` sobre o arquivo compartilhado da E08.

`evaluation.json` contém as classificações individuais e as conexões instaladas.
Ao selecionar um nó, o painel resume seu caminho, indica quantos enlaces
precisam de inspeção ou revisão de projeto e apresenta previsão e medição
de cada trecho. `diagnose_path(interface_id)` retorna os mesmos dados.
As cores são as da E08: verde satisfatório, vermelho investigação, azul medição
satisfatória apesar da previsão, marrom revisão de projeto. Cabo é cinza
tracejado; os nós são neutros. Selecionar um caminho preserva suas cores.
Sem medição, a previsão pode indicar revisão de projeto, mas não defeito.
Dados insuficientes são identificados. Não há cruzamento com aplicação nesta
revisão. Modelo/dados/topologia alterados exigem nova avaliação antes de usar
a vista colorida. A importação e os visualizadores não replanejam a rede.
