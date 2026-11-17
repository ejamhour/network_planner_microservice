# Features e medições para treinamento

## Fluxo

O SDK utiliza o mesmo `features.json` dos projetos de planejamento, no formato
`cisei-feature-cache`, versão 1. A extração pelo serviço de planejamento já
preenche `source`, `request` e `features` em cada entrada. O treinamento não
requer nova extração nem outro formato de features.

`BaselineProject.enrich_features()` associa as medições às entradas existentes
e grava um arquivo separado, `enriched_features.json`. O `features.json`
permanece como cache de extração e não é modificado. O arquivo derivado contém
uma lista `observations` em cada entrada correspondente, mantendo origem,
requisição e valores calculados. `train()` e `evaluate()` leem o arquivo
enriquecido. As posições, as
alturas efetivas e a frequência vêm de `request`.

## Arquivo de medições

JSON UTF-8, com a seguinte estrutura mínima:

```json
{
  "format": "cisei-link-measurements",
  "version": 1,
  "records": [
    {
      "id": "amostra-001",
      "source": "http://features:8080",
      "request": {
        "tx": [-25.9, -53.4],
        "rx": [-25.91, -53.41],
        "tx_ha": 7,
        "rx_ha": 9,
        "freq_mhz": 900,
        "options": {}
      },
      "tx": {"name": "radio_A", "pw": 30, "ant_gain": 8.15},
      "rx": {"name": "radio_B", "ant_gain": 14},
      "measures": {"rssi": -75}
    }
  ]
}
```

Campos obrigatórios de cada registro:

- `id`: string não vazia, única por observação no conjunto. Observações em
  momentos diferentes do mesmo enlace precisam de identificadores distintos.
- `source`: campo legado opcional. Novos arquivos de medições não precisam
  informar o endereço do serviço. A associação usa os parâmetros de `request`
  e exige uma entrada única no cache. O campo continua aceito para manter
  compatibilidade com os arquivos anteriores.
- `request`: parâmetros efetivos da extração da entrada correspondente.
  `tx` e `rx` são pares latitude/longitude em graus. `tx_ha` e `rx_ha` são
  alturas acima do solo, em metros, não negativas. `freq_mhz` é positiva.
  `options` é um objeto e sua omissão equivale a `{}`.
- `tx.name` e `rx.name`: nomes dos equipamentos, strings não vazias.
- `tx.pw`: potência transmitida, em dBm.
- `tx.ant_gain` e `rx.ant_gain`: ganhos das antenas, em dBi.
- `measures.rssi`: sinal recebido em `rx` na transmissão `tx → rx`, em dBm.

Os números devem ser finitos, sem strings numéricas, booleanos, valores nulos
ou valores não numéricos. Não há conversão de unidades ou preenchimento de
potências, ganhos ou alturas ausentes. Outros indicadores e metadados podem
acompanhar a observação, inclusive horário e tipo de antena.

As coordenadas e alturas não precisam ser repetidas em `tx` e `rx`. Se forem,
devem coincidir com `request`; conflitos são rejeitados. Os parâmetros de
extração devem ser copiados da entrada efetivamente utilizada, incluindo
opções, sem aplicar uma nova altura mínima durante a associação.

## Associação e persistência

A correspondência considera direção, coordenadas, alturas, frequência
e opções. Sem `source`, entradas equivalentes de origens diferentes são
rejeitadas como ambíguas. Não depende apenas dos nomes dos equipamentos. Entradas duplicadas
de extração, identificadores repetidos, associações ausentes ou parâmetros
conflitantes geram erro com a posição do registro antes de gravar qualquer
alteração no arquivo enriquecido.

Uma entrada de features pode receber várias observações, sem duplicar as
features. Repetir o mesmo arquivo não duplica observações. Uma alteração nos
valores de um identificador já existente exige `replace=True`. O arquivo
enriquecido é gravado de forma atômica após a validação de todas as associações.
Entradas sem observações não entram no treinamento.

O arquivo derivado mantém a estrutura do cache, mas o fluxo de planejamento
utiliza apenas `features.json`. O SDK não inclui importação dos formatos
legados usados para preparar a demonstração.

Para uma rede instalada, `InstalledNetworkProject.enrich_features()` reutiliza
esse mesmo contrato. Cada observação deve informar o `link_id` de uma conexão
explícita do cenário e os nomes dos sites em `tx.name` e `rx.name`; os
`interface_id`, quando presentes, também são conferidos. O SDK verifica o
sentido contra a conexão instalada e exclui os registros de conexões
`internal` antes de delegar à associação descrita acima. O relatório informa
as exclusões. Métricas de caminho permanecem separadas das observações de RF.

## Receita e chamadas

Campos do `baseline.json` do exemplo:

```json
{
  "version": 1,
  "data": "projects/e08_baseline_copel/features.json",
  "enriched": "projects/e08_baseline_copel/enriched_features.json",
  "measurements": "data/e08/measurements.json",
  "model": "metrics/e08_copel_rssi.json",
  "metric": "metrics/e08_copel.toml"
}
```

Este extrato não inclui os filtros e as etapas `stages` obrigatórias para o
treinamento. Os caminhos são relativos à raiz do workspace. Também é possível
passar um caminho relativo ou um documento de medições diretamente ao método.
`data` indica o cache puro de entrada; `enriched` indica a saída derivada e é
opcional, com padrão `enriched_features.json` na pasta do projeto. Cache e
saída devem ser arquivos distintos. Os relatórios de treinamento e avaliação
são vinculados ao hash do arquivo enriquecido.

```python
project.enrich_features()
project.train()
project.evaluate(rssi_threshold_dbm=-80)
project.show_evaluation()
```

`evaluation.json` é a saída da avaliação, não a entrada de features. Se as
observações forem alteradas, treinamento e avaliação precisam ser executados
novamente para corresponder ao arquivo enriquecido atual.
