# E08 Modelo de referência da tecnologia

Este projeto demonstra o ajuste do RSSI com medições dos enlaces da Fase 1 do
projeto Copel. Requer o SDK 0.1.46 com extras `notebook` (ou `baseline` para
treinar sem visualização).

O notebook é `notebooks/e08_baseline_tecnologia.ipynb`, aberto a partir da pasta
`notebooks` do workspace. Ele contém somente chamadas ao SDK.

Em workspaces que já tinham a versão 0.1.41 do exemplo, atualize também os
campos `data` e `measurements` do `baseline.json` conforme a versão distribuída.
O preparo do workspace preserva arquivos de projeto existentes e não altera
automaticamente essa configuração. O `data` agora aponta para
`projects/e08_baseline_copel/features.json` e `measurements` para
`data/e08/measurements.json`.

## Dados e configuração

- `data/e08/measured_links.json`: cópia de `records2.json`, com as medições e
  configurações originais, para consulta.
- `features.json`: cache padrão com as características já extraídas dos enlaces.
- `enriched_features.json`: cópia derivada com as observações, consumida pelo
  treinamento e pela avaliação. O enriquecimento não altera o cache puro.
- `data/e08/measurements.json`: configurações e medições a associar ao cache.
- `data/e08/features.json`: cópia das features antes da associação, para consulta.
- `data/e08/preparation_report.json`: exclusões explícitas na preparação manual.
- `baseline.json`: filtros, expressões e sequência de ajustes. Os caminhos são
  relativos ao workspace. As expressões são configuração Python confiável do
  usuário, como no núcleo de cálculo original.

Esta execução reutiliza as features existentes e não solicita nova extração.
Primeiro execute `project.enrich_features()` para associar as medições. O método
valida os parâmetros efetivos da extração e grava as observações no arquivo
enriquecido, sem modificar o cache original. Pode ser repetido sem duplicar medições.
Não foi incluído um importador de arquivos legados no SDK. A extração de novos
enlaces continua pertencendo ao fluxo do serviço de planejamento.

Na preparação manual foram mantidas 2.468 entradas de features e 2.278 medições
com os campos de rádio obrigatórios. Foram excluídas 190 medições com ganhos de
antena ausentes ou não numéricos, registradas em `preparation_report.json`.
Os filtros do treinamento continuam selecionando os mesmos 1.890 registros.
O namespace de origem `legacy://copel-phase1-rf-engine` identifica a extração
local original; não atribui esses resultados ao serviço de features atual.
Os arquivos antigos `measured_links.json` e `measured_features.json` ficam como
referência da preparação manual e não são lidos pelo notebook atualizado.

## Saídas

- `metrics/e08_copel_rssi.json`: estimador de sinal, no formato do modelo RF.
- `metrics/e08_copel.toml`: métrica de planejamento com previsão de RSSI e custo
  positivo. O custo utiliza o limiar e a escala explicitamente escolhidos.
- `training_result.json`: quantidades, coeficientes e erros por etapa.
- `evaluation.json`: previsões, medições, contribuições e classificação dos enlaces.
- `training.html` e `evaluation.html`: visualizações locais, sem tabelas ou
  painéis de JSON puro. O gráfico de avaliação permite selecionar cada enlace.

O limiar de -80 dBm e a escala de 10 dB por unidade de custo são escolhas
ilustrativas do exemplo. Devem ser substituídos pelos critérios do projeto.
As estatísticas são calculadas sobre os registros usados no ajuste; não
representam validação em amostras independentes. Os quatro grupos orientam
investigação, atendimento satisfatório, ausência de correção quando a medição
é boa e revisão de projeto quando previsão e medição são insuficientes.

## Inspeção dos enlaces divergentes

A seção 5 do notebook reutiliza o visualizador do serviço de features. Ela
requer esse serviço disponível e uma sessão `FeaturesNotebook` fornecida pelo
usuário. `inspect_divergent_links` ordena os enlaces pela diferença absoluta e
permite definir diferença mínima, limite de enlaces e direção do desvio:
`both`, `below_expected` ou `above_expected`.

O painel apresenta RSSI previsto, medido e diferença no seletor. A inspeção
oferece os perfis, mapas, edificações e demais vistas já existentes. Nenhuma
extração é solicitada ao criar o painel: a seleção é preparada ao inspecionar
um enlace. A frequência e a altura mínima informadas devem reproduzir a
extração original; neste conjunto foram utilizados 900 MHz e mínimo de 7 m.
