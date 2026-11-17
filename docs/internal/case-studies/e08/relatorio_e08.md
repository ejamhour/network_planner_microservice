# E08 — Criação dos modelos de baseline para diagnósticos corretivos e preditivos

**26 de outubro de 2026**

## 1. Objetivos da etapa e escopo desenvolvido

A definição original da E08, registrada em “project_phases_kb_final.md”, estabelece:

> Determinar os limites entre comportamento normal e anormal dos mais diferentes parâmetros da rede. Baselines são uma técnica comumente utilizada para diagnósticar problemas em uma rede. Com baselines individualizados para cada parte da rede e para a camada de aplicação será possivel correlacionar problemas. nas aplicações e em segmentos específicos da rede. Uma vez que a rede tenha sido implantada e homologada e as aplicações estejam operando em parâmetros satisfatórios, os níveis de tráfego e desempenho em termos de sinal de RF, taxa de retransmissão e perda de pacotes será medido para cada segmento da rede. Serão definidos também modelos de relacionamento entre baselines, como a relação entre os comandos enviados para rede (como corte ou religamento) e a variação do tráfego transmistido em cada enlace. Esses modelos serão usados para determinar qualquer anomalia na rede que aconteça de forma abrupta ou gradual.

O produto previsto para a etapa é o módulo de análise de enlaces por baseline. Seu desenvolvimento foi orientado à construção de uma referência de comportamento para cada tecnologia, utilizando medições dos enlaces em campo e informações sobre as condições em que foram instalados.

Neste contexto, baseline significa o desempenho esperado da tecnologia nas condições de instalação de um enlace. A referência é representada por um modelo que relaciona as configurações dos rádios e as características físicas e ambientais do enlace à intensidade do sinal recebido. Esse modelo permite estimar como outro enlace da mesma tecnologia deveria se comportar e comparar a estimativa com uma medição de campo.

A implementação utiliza RSSI, medida de intensidade do sinal recebido, por ser um dos indicadores mais frequentemente disponibilizados pelos equipamentos de rádio em operação. A escolha permite trabalhar com dados já obtidos pelas concessionárias, sem exigir a implantação de uma nova estrutura de coleta. O resultado da etapa é um estimador de RSSI que pode ser utilizado na composição da métrica da tecnologia e na avaliação dos enlaces.

O fluxo recebe arquivos com medições exportadas do monitoramento. Essa forma de integração atende às condições de acesso aos dados das concessionárias e permite executar a análise fora do ambiente operacional. A criação do modelo e a comparação com as medições são realizadas sobre o conjunto de dados fornecido, sem depender de uma conexão permanente com os sistemas de supervisão.

O escopo desenvolvido concentra-se na referência de sinal dos enlaces. A relação com os indicadores das aplicações é estabelecida posteriormente, cruzando o desempenho da comunicação até a aplicação com a avaliação dos segmentos do caminho. A E08 fornece a estimativa do comportamento esperado e os desvios observados, que apoiam a investigação dos problemas e a definição das intervenções.

## 2. Dados de entrada e caracterização dos enlaces

O ponto de partida é um arquivo com as medições e as configurações dos enlaces de uma tecnologia. Cada registro identifica as duas extremidades, suas posições, as alturas de instalação, a potência de transmissão, os ganhos das antenas e o RSSI medido no receptor. Outros indicadores disponíveis podem acompanhar o registro, mas o ajuste apresentado nesta etapa utiliza o RSSI como variável a estimar.

A associação entre medição e configuração é necessária porque um mesmo valor de sinal pode ter significados diferentes conforme a potência, as antenas e as condições de propagação. O modelo deve aprender o comportamento da tecnologia considerando essas diferenças. Por isso, o arquivo reúne tanto o resultado medido quanto as informações que descrevem a instalação correspondente.

O SDK organiza os arquivos nas pastas do workspace. A pasta `data/` armazena os dados de entrada e os registros acrescidos das características extraídas. A pasta `metrics/` recebe o modelo ajustado para utilização na métrica da tecnologia. Essa organização permite manter os dados usados na construção do modelo e reutilizar as características dos enlaces em novos ajustes.

Antes do treinamento, os registros são selecionados conforme os critérios definidos para o conjunto de dados. Podem ser aplicados limites de RSSI, potência e ganho de antena, além da verificação da presença das informações necessárias. Esses critérios devem refletir os equipamentos e as condições da tecnologia estudada. Valores ausentes ou usados pelo sistema de origem para indicar falta de medição precisam ser tratados antes de serem interpretados como observações de campo.

O SDK envia os enlaces medidos ao serviço de planejamento (network), que coordena a extração das características de propagação pelo serviço de features, utilizando o mesmo procedimento aplicado aos enlaces candidatos. O processamento utiliza as posições e as alturas das extremidades, a frequência informada e os dados geográficos disponíveis. O resultado acrescenta ao registro informações sobre distância, perda em espaço livre, difração e presença de obstáculos associados ao terreno, à vegetação e às edificações.

As features descrevem as condições em que o sinal se propaga. Sua associação com as medições permite distinguir, durante o ajuste, enlaces com condições físicas diferentes. Um enlace com obstrução por vegetação, por exemplo, contribui para estimar o efeito dessa condição, enquanto um enlace mais livre de obstáculos ajuda a ajustar os componentes básicos de propagação.

O processamento em lotes já está implementado no serviço de planejamento e é reutilizado para os enlaces medidos. O SDK recebe os resultados e os associa às medições para o treinamento. As features são armazenadas em data/, permitindo repetir o ajuste do modelo sem executar novamente a extração.

## 3. Construção do modelo da tecnologia pelo SDK

O SDK disponibiliza o fluxo de importação dos enlaces, extração das features, ajuste do modelo e gravação do resultado. O notebook apresenta as chamadas para executar essas operações e os parâmetros escolhidos para o estudo. O SDK organiza a leitura dos arquivos, o ajuste e a gravação do modelo, enquanto o serviço de planejamento coordena a extração das features em lotes. Essa divisão permite aplicar o mesmo procedimento a outros conjuntos de medições.

O estimador representa o RSSI pela soma de contribuições das configurações dos equipamentos e das condições de propagação. Potência de transmissão e ganhos das antenas contribuem para o sinal recebido; as perdas de propagação e os efeitos dos obstáculos reduzem esse valor. A participação de cada componente é descrita por expressões e coeficientes, registrados no próprio modelo.

A implementação distingue componentes com coeficientes fixos e componentes cujos coeficientes são ajustados a partir dos dados. Essa separação permite conservar relações conhecidas, como a contribuição da potência informada, e estimar o peso das características ambientais para a tecnologia avaliada. As expressões podem ser alteradas para representar outras relações entre as features e o desempenho observado.

O ajuste também admite valores iniciais e limites para os coeficientes. Esses parâmetros permitem orientar o treinamento e restringir soluções incompatíveis com a interpretação escolhida para os componentes. Na configuração utilizada na demonstração, as expressões de perda carregam sinal negativo e seus coeficientes são limitados a valores não negativos. Dessa forma, o ajuste dessas parcelas representa perdas adicionais.

O treinamento utiliza regressão por quantis. O quantil define a posição da estimativa em relação às medições: o valor 0,5 busca representar o comportamento mediano, enquanto outros valores permitem ajustar uma referência mais alta ou mais baixa. Essa escolha faz parte da configuração do modelo e deve ser considerada ao interpretar a previsão e os desvios de cada enlace.

O procedimento permite ajustar componentes em etapas. Na demonstração, um subconjunto de enlaces com menor presença de obstáculos foi utilizado no ajuste inicial dos componentes de propagação. Outros subconjuntos permitiram estimar as contribuições de vegetação e edificações. Esses resultados forneceram os valores iniciais para o ajuste conjunto dos componentes ambientais, mantendo as contribuições básicas já definidas.

No exemplo do notebook, as chamadas ao SDK ajustam primeiro os componentes de propagação e, em seguida, os componentes ambientais. A saída do ajuste inicial registra a quantidade de enlaces de cada subconjunto e os coeficientes obtidos:

```text
Propagação: 243 enlaces
  fspl = 1,1143; difração = 1,0000
Vegetação: 188 enlaces
  boundary = 4,4818; fresnel = 4,0423; core = 2,6839
Edificações: 521 enlaces
  boundary = 3,0128; fresnel = 2,0746; core = 1,3731
```

Os nomes boundary, fresnel e core identificam as features de obstrução utilizadas nas expressões do modelo. Os valores apresentados são coeficientes dessas expressões, e não perdas em dB aplicadas diretamente a qualquer enlace. A contribuição depende também do valor da feature no enlace avaliado.

Na etapa final executada por `project.train()`, os coeficientes ambientais anteriores são usados como valores iniciais para o ajuste conjunto com os 1.890 registros selecionados. A saída registrada no notebook apresenta:

```text
Modelo final — coeficientes ajustados
Vegetação:
  boundary = 3,8600; fresnel = 3,5390; core = 2,3298
Edificações:
  boundary = 1,9408; fresnel = 1,1953; core = 1,0582
```

A mudança dos coeficientes mostra o efeito do ajuste conjunto sobre os valores obtidos nos subconjuntos. Os componentes básicos de propagação são mantidos fixos nessa etapa. A gravação pelo SDK conserva as expressões e os coeficientes no arquivo do modelo, permitindo reproduzir a previsão e examinar suas contribuições para cada enlace.

Essa sequência é uma configuração de treinamento utilizada no exemplo. O recurso geral é a possibilidade de selecionar registros, definir expressões, fixar ou ajustar coeficientes e salvar o estimador resultante. A aplicação a outra tecnologia exige escolher os dados e os parâmetros correspondentes aos equipamentos e às condições que se pretende representar.

O modelo é gravado em JSON na pasta `metrics/`, contendo as expressões, os coeficientes fixos e ajustados, o termo independente e informações do treinamento. Essas informações permitem carregar o estimador posteriormente, calcular previsões e examinar a contribuição de cada componente. O arquivo preserva o resultado do ajuste sem exigir novo treinamento a cada avaliação.

O modelo de RSSI fornece uma previsão de sinal. Para uso na seleção de caminhos, essa previsão é convertida em custo pela métrica da tecnologia, conforme o critério de qualidade configurado. A previsão de RSSI e o custo do enlace têm funções diferentes: a primeira descreve o sinal esperado; o segundo permite comparar e acumular custos ao longo dos caminhos de rede.

## 4. Avaliação dos enlaces e orientação das correções

Depois de definido o modelo, o software calcula o RSSI esperado dos enlaces a partir de suas configurações e features. Para os enlaces que possuem medição, a previsão pode ser comparada diretamente com o sinal observado. A interpretação considera se cada um atende ao limite de qualidade adotado para a tecnologia e para o projeto.

O limite de qualidade deve ser definido de acordo com os requisitos de operação. A diferença numérica entre previsão e medição, isoladamente, não determina a necessidade de intervenção. Um enlace pode apresentar diferença em relação ao modelo e ainda funcionar satisfatoriamente. A comparação entre qualidade prevista e medida orienta as seguintes situações:

- **Previsão boa e desempenho medido ruim:** o enlace deveria atender ao critério de qualidade nas condições informadas, mas o resultado de campo é insuficiente. A situação indica um possível problema de instalação ou defeito e justifica a investigação do enlace.
- **Previsão boa e desempenho medido bom:** o enlace apresenta comportamento adequado e não requer correção segundo esse critério.
- **Previsão ruim e desempenho medido bom:** a medição confirma funcionamento satisfatório. A indicação desfavorável do modelo pode ser ignorada para a decisão de corrigir esse enlace.
- **Previsão ruim e desempenho medido ruim:** as condições representadas pelo modelo já indicam desempenho insuficiente. O enlace precisa ser melhorado por revisão do projeto, avaliando alterações nas condições de instalação ou na solução de conectividade.

A comparação também pode ser expressa pelo residual, calculado como RSSI medido menos RSSI previsto. Um residual negativo indica que o sinal observado foi inferior ao estimado; um residual positivo indica sinal superior. Esse valor ajuda a identificar os enlaces que mais se afastam da referência, enquanto a classificação de qualidade informa a necessidade e o tipo de ação.

Para apoiar a inspeção, o modelo permite decompor a previsão em suas contribuições. O usuário pode verificar os valores associados à potência, aos ganhos das antenas e aos componentes de perda. Essa consulta ajuda a entender por que o estimador prevê sinal suficiente ou insuficiente e quais condições merecem atenção na revisão do enlace.

O resultado oferece uma orientação de investigação. Quando previsão e medição divergem desfavoravelmente, a análise de campo pode verificar instalação, antenas, equipamentos e informações de cadastro. Quando ambas indicam qualidade insuficiente, a revisão do projeto pode avaliar mudanças de altura, antena, posição ou alternativas de conexão. A identificação da causa específica utiliza essas informações em conjunto com os demais dados disponíveis sobre a rede.

## 5. Uso do SDK e inspeção dos resultados

### 5.1. Configuração do projeto de treinamento

O uso do SDK, biblioteca Python que disponibiliza as operações do software nos notebooks, começa pela abertura de um projeto de baseline. O notebook de demonstração é `e08_baseline_tecnologia.ipynb`, e seu projeto é `projects/e08_baseline_copel`. A classe `BaselineProject` lê o arquivo obrigatório `baseline.json` nessa pasta. Esse arquivo define os dados de entrada, os arquivos de saída, os filtros e a sequência de ajustes. Os caminhos declarados são relativos à raiz do workspace, permitindo utilizar as pastas compartilhadas `data/` e `metrics/`.

O Quadro 1 apresenta um extrato da configuração utilizada no exemplo, com os caminhos e um dos filtros. O campo `data` aponta para o `features.json` padrão do projeto e `measurements` aponta para as medições a associar. O arquivo completo inclui também os demais filtros e a lista `stages`, que descreve cada etapa de treinamento. Assim, as escolhas do ajuste ficam registradas fora das células do notebook e podem ser revistas antes da execução.

```json
{
  "version": 1,
  "data": "projects/e08_baseline_copel/features.json",
  "measurements": "data/e08/measurements.json",
  "model": "metrics/e08_copel_rssi.json",
  "metric": "metrics/e08_copel.toml",
  "filters": [
    {
      "feature_path": "measures.rssi",
      "min_value": -120,
      "max_value": -35,
      "include_max": true
    }
  ]
}
```

Quadro 1 — Extrato de baseline.json com caminhos de entrada e saída e filtro de RSSI; as etapas de treinamento constam do arquivo completo.

Cada elemento de `stages` possui um nome, os filtros específicos do subconjunto e a configuração `fit`. Nela são declaradas as expressões fixas e ajustáveis, os coeficientes fixos, os valores iniciais, os limites dos coeficientes e os parâmetros de treinamento. Uma etapa pode utilizar o coeficiente obtido em outra: a referência `vegetation.veg_boundary`, por exemplo, recupera o valor ajustado na etapa `vegetation` para inicializar esse componente no ajuste final.

O arquivo pode ser elaborado pelo usuário ou preparado com auxílio de uma ferramenta de inteligência artificial. Em ambos os casos, ele permite examinar quais características serão utilizadas, como os registros serão selecionados e quais coeficientes serão aprendidos. O engenheiro define a configuração adequada à tecnologia e aos dados; o SDK executa o procedimento declarado. A geração assistida do arquivo não modifica essa divisão de responsabilidades.

O arquivo `features.json` mantém o formato `cisei-feature-cache`, versão 1, utilizado pelo planejamento. Cada entrada conserva a origem (`source`), os parâmetros efetivos da extração (`request`) e as características calculadas (`features`). O enriquecimento acrescenta uma lista `observations` com as configurações de rádio e as medições correspondentes, sem alterar os valores já extraídos. Uma mesma entrada pode receber várias observações.

O arquivo de medições utiliza o formato `cisei-link-measurements`, versão 1, com uma lista `records`. Cada observação possui identificador único (`id`), a origem e os parâmetros da entrada de features correspondente, os nomes dos equipamentos, a potência transmitida em dBm, os ganhos de antena em dBi e o RSSI medido em dBm. As posições, as alturas efetivas em metros e a frequência em MHz são reaproveitadas da requisição de extração. A associação confere também o sentido do enlace e as opções de extração; nomes de equipamentos, isoladamente, não determinam a correspondência. Campos obrigatórios ausentes, identificadores duplicados e correspondências conflitantes são informados antes da gravação.

### 5.2. Execução e gravação do modelo

Com o notebook aberto na pasta `notebooks` do workspace, a sequência de uso consiste em abrir o projeto, executar o treinamento e apresentar o resumo visual. A chamada `train()` não recebe parâmetros porque utiliza a configuração já carregada de `baseline.json`. Se o arquivo for alterado, o projeto deve ser aberto novamente para carregar a nova configuração.

```python
from cisei_planning_sdk import BaselineProject

project = BaselineProject("../projects/e08_baseline_copel")
project.enrich_features();
project.train();
project.show_training();
```

A chamada `enrich_features()` lê o cache existente e o arquivo de medições indicado na receita, valida as associações e acrescenta as observações ao `features.json`. A operação não solicita extração geográfica e pode ser repetida com os mesmos dados sem duplicar medições. Uma alteração nos valores de uma observação já registrada exige `replace=True`. Depois dessa preparação, o treinamento lê as observações e as features correspondentes, aplica os filtros e executa os ajustes na ordem declarada. No projeto de demonstração, as quatro etapas são propagação básica, vegetação, edificações e modelo final. A operação grava o estimador em `metrics/e08_copel_rssi.json` e o resumo em `training_result.json`, na pasta do projeto. O treinamento utiliza as features existentes e não solicita nova extração.

A Figura 1 mostra o resultado de `show_training()`. As barras representam os coeficientes das expressões ajustadas em cada etapa, acompanhados da quantidade de medições utilizada. Os nomes `base`, `vegetation`, `buildings` e `final` correspondem às etapas da receita; `fspl` e `diff` identificam os componentes de perda em espaço livre e difração. Os demais nomes identificam as características de obstrução por vegetação e edificações. A visualização permite conferir a execução da sequência e os valores que compõem o modelo salvo; os comprimentos das barras são normalizados dentro de cada etapa.

![Resumo visual do treinamento](figures/fig01_ajuste_modelo.png)

Figura 1 — Resumo do treinamento pelo SDK, com quantidades de medições e coeficientes ajustados em cada etapa.

Para disponibilizar o resultado ao planejamento, o usuário escolhe o sinal requerido e a escala de conversão em custo. A chamada seguinte grava a métrica em `metrics/e08_copel.toml`. Nesse exemplo, -80 dBm e 10 dB por unidade de custo são parâmetros ilustrativos definidos para a demonstração, e não valores aprendidos pelo modelo.

```python
project.export_metric(required_rssi_dbm=-80.0, db_per_cost=10.0);
```

O JSON conserva o estimador de sinal; o TOML contém a expressão de previsão e sua conversão em custo positivo para utilização no planejamento. Os arquivos do projeto têm funções distintas: `baseline.json` configura o treinamento, `training_result.json` registra seu resultado e `evaluation.json` reúne a comparação dos enlaces. As features e suas observações ficam no `features.json` do projeto; o arquivo de medições permanece em `data/e08/measurements.json`. Portanto, `evaluation.json` não substitui um arquivo de features.

### 5.3. Comparação visual entre previsão e medição

Depois do treinamento, o usuário informa o limiar de qualidade para comparar o sinal previsto com o medido. A operação `evaluate()` grava as previsões, os residuais, as classificações e as contribuições em `evaluation.json`. A chamada `show_evaluation()` apresenta o gráfico interativo e salva uma versão HTML na pasta do projeto, permitindo consultar os resultados sem repetir o ajuste.

```python
project.evaluate(rssi_threshold_dbm=-80.0);
project.show_evaluation();
```

![Dispersão de RSSI com enlace selecionado](figures/fig02_dispersao_rssi.png)

Figura 2 — Dispersão do RSSI previsto e medido, classificação por cores e contribuições do enlace selecionado.

A Figura 2 apresenta essa visualização. Cada ponto corresponde a um enlace: o eixo horizontal mostra o RSSI previsto e o vertical mostra o RSSI medido, ambos em dBm. As linhas contínuas marcam o limiar de -80 dBm nos dois eixos e dividem o gráfico em quatro regiões. As cores relacionam essas regiões às ações de engenharia:

- **Verde, região superior direita:** previsão e medição atendem ao limiar; o enlace apresenta atendimento satisfatório.
- **Vermelho, região inferior direita:** a previsão atende ao limiar, mas a medição não; há indicação para investigar instalação ou defeito.
- **Azul, região superior esquerda:** a previsão não atende ao limiar, mas a medição é satisfatória; o enlace não recebe indicação de correção por esse critério.
- **Marrom, região inferior esquerda:** previsão e medição não atendem ao limiar; há indicação para revisar o projeto.

A linha diagonal tracejada representa a igualdade entre previsão e medição. Pontos acima dela possuem sinal medido superior ao previsto; pontos abaixo possuem sinal inferior. Como o residual é medição menos previsão, essas posições correspondem, respectivamente, a diferenças positivas e negativas. O afastamento da diagonal evidencia a divergência, enquanto a posição em relação ao limiar informa a situação de qualidade.

Ao selecionar um ponto, o painel lateral apresenta os equipamentos das duas extremidades, o sinal previsto, o medido, a diferença e a contribuição de cada componente. Na Figura 2, o enlace BIT-R-P7-001 → BIT-R-GE-023 apresenta previsão aproximada de -120,63 dBm e medição de -56,00 dBm, com diferença positiva de 64,63 dB. Ele aparece em azul: apesar da divergência elevada, a medição atende ao critério utilizado. O exemplo mostra por que uma diferença grande, isoladamente, não deve ser interpretada como necessidade de corrigir o enlace.

A dispersão oferece uma leitura do conjunto e permite passar diretamente a um enlace de interesse. A versão inserida no relatório é uma captura estática; no notebook e no HTML os pontos permanecem selecionáveis. A análise pode começar tanto pelos enlaces abaixo do critério de qualidade quanto pelos casos em que o comportamento observado diverge da referência, mantendo a distinção entre os dois objetivos.

### 5.4. Inspeção integrada dos enlaces divergentes

Para investigar as condições físicas dos enlaces, o SDK reutiliza o visualizador do serviço de features. A operação `inspect_divergent_links()` recebe uma sessão desse serviço e monta uma lista ordenada pelo valor absoluto do residual. O usuário pode definir a diferença mínima, a quantidade máxima de enlaces e a direção do desvio. O seletor apresenta os equipamentos, a previsão, a medição e a diferença, permitindo identificar o caso antes de abrir seus perfis ou mapas.

```python
from cisei_features_sdk.notebook import FeaturesNotebook

geo = FeaturesNotebook("http://features:8080", user=None)
inspector = project.inspect_divergent_links(
    geo, freq_mhz=900, minimum_height_m=7,
    min_abs_error_db=20, direction="both", limit=50,
);
```

Nessa chamada, `min_abs_error_db` seleciona diferenças de pelo menos 20 dB e `limit` restringe a lista aos 50 maiores casos. O valor `both` inclui os dois sentidos; `below_expected` seleciona somente medições inferiores à previsão e `above_expected` seleciona medições superiores. A frequência e a altura mínima devem corresponder às condições utilizadas na extração. No conjunto demonstrado, foram utilizados 900 MHz e altura mínima de 7 m.

O painel oferece perfil do enlace, mapas de terreno e cobertura, perfis de superfície e consulta de edificações, entre as visualizações já disponíveis. A criação da lista utiliza os resultados salvos. A inspeção geográfica requer o serviço de features e prepara o enlace selecionado, sem executar novamente o treinamento nem extrair as características de todo o conjunto.

A Figura 3 mostra o perfil do mesmo enlace selecionado na Figura 2, obtido pelo visualizador de features. O eixo horizontal representa a distância ao longo do enlace e o vertical representa a elevação. Na legenda do perfil, a linha preta contínua indica o terreno, a verde tracejada indica a vegetação, a vermelha tracejada indica as edificações e a azul tracejada representa a linha de visada entre as extremidades. As faixas coloridas junto ao perfil indicam as classes de cobertura; a escala à direita identifica vegetação e área construída. Essa vista permite relacionar a divergência observada às condições geográficas e verificar as informações utilizadas na análise, sem atribuir automaticamente uma causa física à diferença.

![Perfil geográfico do enlace divergente](figures/fig03_perfil_enlace.png)

Figura 3 — Perfil de elevação e cobertura de BIT-R-P7-001 → BIT-R-GE-023, consultado pelo visualizador de features na inspeção integrada.

O fluxo reúne a visão do conjunto e a inspeção detalhada: a dispersão mostra a distribuição das previsões e medições; a seleção apresenta os componentes do cálculo; e o visualizador geográfico permite examinar o enlace escolhido. O usuário pode, assim, avaliar os dados e as condições de instalação antes de decidir por investigação, revisão de projeto ou ausência de intervenção.

## 6. Demonstração com os enlaces da Fase 1 do projeto Copel

O fluxo foi testado com medições e configurações dos enlaces da Fase 1 do projeto Copel. O arquivo `records2.json` reúne os registros utilizados na preparação dos dados. A extração acrescentou as características físicas e ambientais dos enlaces, e os resultados foram utilizados no notebook de treinamento do modelo de RSSI.

O notebook distribuído utiliza o `features.json` padrão já calculado, associa as configurações e medições de `data/e08/measurements.json` por meio de `enrich_features()` e executa a seleção dos registros, o ajuste dos componentes e a gravação do modelo em `metrics/`, sem repetir a extração geográfica. Os arquivos intermediários permitem repetir o treinamento a partir das features já calculadas. O objetivo do exemplo é mostrar como criar uma referência de comportamento para a tecnologia a partir dos dados obtidos em campo.

O conjunto original continha 2.468 registros com features. Na preparação manual do exemplo para o formato padrão, foram mantidas todas essas entradas de features e associadas 2.278 medições com os campos obrigatórios de rádio. Outras 190 apresentavam ganhos de antena ausentes ou não numéricos e foram registradas no relatório de preparação, sem preenchimento de valores. Os filtros configurados continuaram selecionando os mesmos 1.890 registros para o ajuste final. Os componentes considerados incluíram potência, ganhos das antenas, perda em espaço livre, difração e efeitos de vegetação e edificações. As quantidades descrevem o conjunto empregado na demonstração; o fluxo permite utilizar outros arquivos e critérios de seleção.

O notebook comparou as previsões com as medições e calculou os desvios para o conjunto utilizado. Na saída registrada, o erro absoluto médio do modelo final foi aproximadamente 8,62 dB, frente a 9,25 dB do modelo básico usado na comparação. Esses valores descrevem o ajuste sobre os próprios dados empregados no procedimento, e não uma avaliação em um conjunto independente de enlaces.

A execução original também avaliou subconjuntos definidos pelo RSSI medido. Os resultados registrados nesse notebook fornecem a seguinte comparação do erro absoluto médio, em dB:

```text
Conjunto                  Enlaces   Básico   Final
Todos os selecionados       1.890     9,25    8,62
RSSI entre -80 e -70 dBm       518    10,96    8,49
RSSI entre -120 e -80 dBm      252    19,22   12,47
```

Os subconjuntos foram definidos por intervalos abertos de RSSI, conforme os filtros do notebook. A comparação mostra redução do erro médio com a inclusão dos componentes ambientais, inclusive no grupo com sinal mais baixo. Esse resultado avalia a aproximação das medições pelo modelo; a decisão de corrigir cada enlace continua dependendo da qualidade prevista, da qualidade medida e, no cruzamento posterior, da condição da aplicação.

Também foram inspecionados os enlaces com maiores diferenças entre sinal previsto e medido. Para cada um, a decomposição da previsão permite examinar os componentes que levaram à estimativa. Essa inspeção exemplifica o uso do baseline: relacionar o desempenho observado às condições representadas no modelo e orientar a avaliação do enlace, considerando o critério de qualidade definido pelo engenheiro.

O resultado principal da demonstração é a execução do fluxo de criação e utilização de um estimador de desempenho com dados reais. O modelo gerado pode ser carregado para avaliar enlaces com as configurações e features necessárias, sem repetir seu treinamento. A aplicação a outra tecnologia utiliza o mesmo procedimento, com as medições e a parametrização apropriadas.

## 7. Relação entre a avaliação dos enlaces e o desempenho da aplicação

O uso dos indicadores da aplicação permite verificar se o caminho de comunicação atende à sua finalidade. Do ponto de vista do software, a aplicação é representada por um nó específico no grafo, ao qual os equipamentos de campo estão conectados por seus caminhos de rede. Essa representação permite associar os indicadores de comunicação com a aplicação aos segmentos percorridos, sem vincular o método a um comando ou a um sistema de automação específico.

A avaliação do baseline é realizada sobre os enlaces. Em um cruzamento posterior, seus resultados são relacionados aos indicadores da aplicação para interpretar a condição do caminho completo. Assim, o modelo de sinal não precisa estimar diretamente a taxa de sucesso de cada operação da aplicação. Ele indica quais segmentos apresentam condições insuficientes ou comportamento abaixo do esperado, enquanto os indicadores da aplicação permitem verificar se há impacto no serviço atendido.

Nos estudos realizados, foi avaliada a relação entre as condições da rede e os resultados dos pings do SCADA para os equipamentos de automação. Esses testes forneceram uma indicação da comunicação com os equipamentos. Os dados de operações de corte e religamento, citadas como exemplo na definição original da etapa, não foram disponibilizados. O procedimento do software permanece genérico e permite realizar o mesmo cruzamento com outros indicadores fornecidos pelo usuário.

A interpretação conjunta distingue duas situações:

- **A aplicação apresenta problemas de comunicação:** a avaliação dos enlaces ajuda a localizar segmentos que possam explicar o resultado. Quando a previsão é suficiente e o desempenho medido é ruim, há indicação de comportamento abaixo do esperado, orientando a investigação de instalação ou defeito. Quando a previsão e a medição são insuficientes, há indicação de condições inadequadas de projeto, orientando sua revisão. Essas informações apoiam a investigação da relação entre os segmentos e o problema observado na aplicação.
- **A aplicação não apresenta problemas de comunicação:** as indicações desfavoráveis da avaliação dos enlaces são tratadas como oportunidades de manutenção preventiva ou de melhoria de robustez. Um segmento pode apresentar sinal abaixo do esperado ou condições previstas insuficientes sem que isso tenha comprometido os indicadores disponíveis da aplicação. Nesse caso, a intervenção é avaliada para reduzir a exposição a problemas futuros e melhorar as condições de operação do caminho.

Esse cruzamento permite atribuir finalidade às recomendações. A mesma condição de um enlace pode justificar uma investigação corretiva quando há impacto na aplicação ou uma ação preventiva quando o serviço permanece satisfatório. A avaliação dos segmentos identifica onde atuar e que tipo de alteração considerar; a situação da aplicação ajuda a definir a prioridade e o objetivo dessa atuação.

## 8. Conclusões e integração com o diagnóstico e o planejamento

A E08 disponibiliza um procedimento para transformar medições de campo em uma referência de comportamento da tecnologia. O SDK recebe os enlaces e as configurações dos rádios, encaminha a extração das features ao serviço de planejamento e ajusta um modelo de RSSI com os resultados. O serviço de planejamento reutiliza o processamento em lotes já empregado na avaliação dos enlaces candidatos. O resultado é salvo para uso posterior na métrica da tecnologia e na avaliação dos enlaces.

A organização em arquivos permite utilizar exportações dos sistemas de monitoramento, conservar os dados empregados no ajuste e repetir o processamento conforme a necessidade. A integração ao SDK mantém a preparação e o treinamento acessíveis pelo mesmo ambiente utilizado nos projetos de planejamento. O notebook documenta o uso dessas operações e os parâmetros adotados no exemplo.

A comparação entre previsão e medição permite distinguir enlaces que deveriam apresentar bom desempenho daqueles cujas condições de instalação já explicam um resultado insuficiente. Essa distinção orienta a investigação de possíveis problemas de instalação ou defeitos e a escolha dos casos que exigem revisão de projeto. Enlaces com desempenho medido satisfatório não recebem indicação de correção apenas porque a previsão foi desfavorável.

O mesmo modelo permite prever o sinal de enlaces ainda sem medição, usando suas configurações e features. Assim, a avaliação de conexões existentes e o planejamento de novas conexões utilizam uma referência obtida em campo.

Os desvios em relação ao baseline apoiam o diagnóstico da E09 e, combinados aos indicadores da aplicação, orientam ações corretivas ou preventivas. Para o planejamento, a previsão de sinal compõe a avaliação da qualidade e dos custos dos enlaces.
