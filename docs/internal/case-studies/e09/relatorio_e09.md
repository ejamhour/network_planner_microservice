# E09 Detecção de causa raiz em diagnóstico corretivo

**27 de outubro de 2026**

## 1 Objetivos da etapa e escopo desenvolvido

A E09 desenvolveu um método geral de diagnóstico corretivo para redes de comunicação. O método combina a topologia da rede com a avaliação individual dos enlaces da E08 para identificar os segmentos que precisam de inspeção ou melhoria de projeto ao longo dos caminhos até os pontos de acesso à rede de transporte, denominados backbone. Sua aplicação utiliza os dados da rede examinada e o modelo da tecnologia correspondente. A rede BRO-S foi utilizada para validar o método e demonstrar sua implementação no software.

A definição original da etapa estabelece:

> Localizar automaticamente a causa raiz quando uma falha acontece na rede. Durante o processo de monitoramento de uma rede, uma falha pode fazer com que vários indicadores gerem alarmes simultaneamente. É necessário estabelecer relacionamento de causa e efeito entre esses indicadores e apontar a causa raiz desses alarmes. Os diversos indicadores da rede precisam ser agrupados em relacionamentos de causa e efeito. Esse relacionamento deve ser estabelecido na forma de um sistema especialista. Por exemplo, variações abruptas no nível de RF serão acompanhadas no aumento de número de retransmissões, na perda de pacotes, aumento de atraso e diminuição na taxa de sucesso dos indicados dos aplicavos de automação e medição. Nesse exemplo, a causa raiz é o RF, podendo ser causado por problemas nas antenas ou crecimento de vegetação ou outro fatores físicos. Através da comparação dos baselines dos níveis de RF será possível deteminar o enlace que sofreu alteração, e se essa alteração foi transitória ou permanente. Esse relacionamento entre os diferentes níveis de baseline será feito por regras em um sistema especialista.

O produto previsto é o módulo de análise de causa raiz. Na implementação desenvolvida, o diagnóstico é construído pela combinação da topologia instalada com a comparação entre o desempenho esperado e o desempenho medido de cada enlace. O modelo da tecnologia, desenvolvido na E08, fornece a referência para essa comparação. A E09 acrescenta a interpretação dos resultados ao longo do caminho e sua apresentação nas visualizações da rede.

Essa análise é especialmente útil em redes com vários saltos, nas quais a comunicação de um equipamento depende de rádios intermediários. Uma indicação desfavorável no destino pode estar associada ao enlace de acesso desse equipamento ou a outro segmento compartilhado do caminho. A topologia permite localizar essas dependências. Em uma conexão direta com o backbone, o mesmo procedimento se reduz à avaliação do enlace de acesso.

O diagnóstico indica onde investigar e qual intervenção considerar. A comparação entre previsão e medição não distingue, sozinha, um defeito de rádio de uma instalação inadequada ou de outra alteração das condições físicas. A investigação em campo pode utilizar o segmento apontado pelo software como ponto de partida. A análise temporal das medições e o cruzamento com indicadores da aplicação ficam para as etapas posteriores; a entrega da E09 concentra-se nos enlaces e nos caminhos da rede instalada.

## 2 Representação da rede instalada e dados de entrada

O desenvolvimento reutiliza a organização dos projetos por sites, dispositivos e interfaces. Um site informa a localização; um dispositivo representa o equipamento instalado; as interfaces descrevem suas possibilidades de comunicação. Essa estrutura permite representar diferentes tecnologias e equipamentos com várias interfaces. O diagnóstico trabalha sobre as conexões efetivamente informadas, sem precisar gerar uma nova topologia para a rede em operação.

As entradas utilizam os formatos já adotados pelo software. O arquivo `scenario.toml` contém os perfis de equipamentos e antenas, as configurações das interfaces e as conexões explícitas. O `nodes.csv` contém as instâncias e suas posições. Os arquivos de métricas mantêm uma separação entre o comportamento de cada salto e o comportamento do caminho completo:

- `hop_metrics.json` reúne as observações dos enlaces, identificando transmissor, receptor, configuração dos rádios e intensidade do sinal recebido, ou RSSI, em dBm. Outros indicadores disponíveis podem acompanhar cada observação.
- `path_metrics.json` preserva as métricas do caminho completo de cada equipamento, como tempo de resposta e perda de pacotes, para o cruzamento posterior com a aplicação.

Cada conexão possui um identificador `link_id`, compartilhado com suas observações. O sentido da medição é explícito: o RSSI informado em um registro corresponde ao sinal recebido em `rx`, transmitido por `tx`. No exemplo BRO-S, as medições correspondem à transmissão do pai para o filho. O caminho de saída do equipamento é percorrido no sentido inverso, do filho em direção ao backbone. A separação evita atribuir a uma medição a potência ou o ganho de antena da extremidade errada.

A conectividade é definida pelos enlaces explícitos do TOML. O campo `next_hop`, quando presente no CSV, é conservado como informação auxiliar. O método de importação identifica os pais a partir das conexões e dos pontos declarados como conectados ao backbone. Na versão desenvolvida, cada componente deve formar uma árvore com um único acesso ao backbone. Ciclos, interfaces desconhecidas e componentes com acesso ausente ou ambíguo são rejeitados, em vez de provocar uma escolha automática de novas rotas.

As conexões por cabo também fazem parte do caminho. Dois rádios instalados no mesmo local e interligados por cabo podem ser representados por uma conexão interna, mesmo quando suas funções na rede são diferentes. O trecho é mantido na topologia e na inspeção das dependências, mas não recebe extração de propagação ou classificação de RSSI. O extrato abaixo apresenta a distinção entre um enlace de rádio e uma ligação interna do exemplo.

```toml
[[net.candidate_edges]]
src = "BRO-S-GE-017:d0:i0"
dst = "BRO-S-GE-027:d0:i0"
kind = "rf"
link_id = "BRO-S-GE-017__BRO-S-GE-027"

[[net.candidate_edges]]
src = "BRO-S-GE-026:d0:i0"
dst = "BRO-S-GE-025:d0:i0"
kind = "internal"
metric = 0.0
connection_type = "back_to_back"
link_id = "BRO-S-GE-026__BRO-S-GE-025"
```

Embora o formato existente utilize o nome `candidate_edges`, neste fluxo a lista contém as conexões instaladas. Não são acrescentadas regras de geração de enlaces. A importação conserva a rede fornecida e não executa uma seleção de caminhos alternativos.

## 3 Avaliação dos enlaces e diagnóstico do caminho

A E08 definiu o baseline como o comportamento esperado de uma tecnologia nas condições em que o enlace foi instalado. O modelo estima o RSSI a partir das configurações dos rádios e das características de propagação, incluindo as informações de terreno, vegetação e edificações disponíveis. Na E09, esse modelo é aplicado aos enlaces instalados, sem novo treinamento com a própria rede que está sendo diagnosticada.

O engenheiro define um limiar de RSSI para distinguir uma condição satisfatória de uma condição insuficiente. A previsão e a medição são comparadas com o mesmo limiar, mantendo as quatro classificações da E08:

- **Previsão boa e medição ruim:** investigar instalação ou possível defeito. O modelo indica que o enlace deveria atender ao critério escolhido, mas o comportamento medido está abaixo desse critério.
- **Previsão boa e medição boa:** atendimento satisfatório. A comparação não indica necessidade de correção do enlace.
- **Previsão ruim e medição boa:** medição satisfatória, sem indicação de correção pela previsão desfavorável. O resultado medido tem prioridade nessa decisão.
- **Previsão ruim e medição ruim:** revisar o projeto. As condições representadas pelo modelo já indicam insuficiência, acompanhada pelo resultado medido.

A classificação depende do modelo, das configurações informadas e do limiar escolhido. Ela não é uma probabilidade de falha. Um enlace situado próximo ao limiar pode mudar de classe se o engenheiro alterar esse critério. Por isso, a visualização mantém os valores previstos e medidos acessíveis, permitindo examinar a indicação sem depender apenas da cor atribuída ao enlace.

A ausência de medição não impede a análise quando existem features e configurações suficientes para calcular a previsão. Uma previsão insuficiente permite indicar melhoria de projeto; uma previsão satisfatória é apresentada como tal, acompanhada da informação de que não há medição. Sem os dados necessários à previsão, o trecho aparece como dados insuficientes. A falta de medição não é convertida em indicação de defeito.

O diagnóstico do caminho reúne as classificações dos segmentos entre o equipamento selecionado e o backbone. O sumário informa o ponto de saída, a quantidade de conexões e os enlaces indicados para inspeção ou revisão de projeto. Os detalhes apresentam as extremidades e os valores de cada trecho. As ligações internas aparecem separadamente, sem serem interpretadas como enlaces de rádio com sinal zero.

Essa composição permite identificar um segmento intermediário que participa da comunicação de vários equipamentos. Também permite reconhecer que um mesmo caminho contém mais de um enlace com indicação de intervenção. O software apresenta essas evidências ao engenheiro, sem reduzir automaticamente todos os resultados a um único defeito físico. Nesta versão, o diagnóstico utiliza uma observação selecionada por enlace; múltiplas observações precisam ser selecionadas previamente, sem agregação temporal implícita.

## 4 Implementação no SDK e fluxo de uso

As operações foram incorporadas ao SDK, a biblioteca Python utilizada pelos notebooks do workspace. A classe `InstalledNetworkProject` organiza a leitura da rede instalada, a extração de features, o enriquecimento das observações e a avaliação dos caminhos. O notebook contém chamadas à biblioteca; a lógica de importação, validação, classificação e apresentação permanece no software.

O primeiro passo é importar a topologia. A operação lê o TOML e o CSV, estabelece a relação de pais nas conexões existentes e grava `result.json`. A estrutura é compatível com os resultados já utilizados pelos visualizadores. O número de saltos descreve a posição na topologia; não é tratado como rank de qualidade. Nenhum custo acumulado de desempenho é atribuído durante essa importação.

```python
from cisei_planning_sdk import InstalledNetworkProject

project = InstalledNetworkProject(
    "../projects/e09_diagnostico_bro"
)
project.import_topology()
```

A chamada `extract_features()` reutiliza o processamento em lotes do serviço de planejamento, que coordena as requisições ao serviço de features. O procedimento é aplicado somente às conexões de rádio existentes. As ligações internas permanecem na topologia e são excluídas da extração geográfica. O fluxo existente do serviço também calcula os custos configurados, mas não executa a seleção de rotas nem substitui o diagnóstico pela comparação desses custos.

```python
from cisei_planning_sdk import PlanningClient

client = PlanningClient(
    "http://network-service:8080", workspace="..", timeout=1900.0
)
project.extract_features(
    client, geo_base_url="http://features:8080", batch_size=10
)
```

Os dados calculados ficam no `features.json`, usado exclusivamente como cache de extração. O enriquecimento lê esse cache e as métricas por salto e gera `enriched_features.json`, contendo as observações associadas. Treinamento e avaliação consomem o arquivo enriquecido. Essa separação impede que a gravação posterior de um cache de extração apague as medições utilizadas no diagnóstico.

A receita `baseline.json` indica os arquivos envolvidos. O campo `data` mantém o caminho do cache puro; `enriched` indica o arquivo derivado. Os caminhos são relativos à raiz do workspace. O modelo aponta para o estimador treinado na E08, que é reutilizado sem alteração. As métricas de caminho continuam em arquivo separado.

```json
{
  "data": "projects/e09_diagnostico_bro/features.json",
  "enriched": "projects/e09_diagnostico_bro/enriched_features.json",
  "measurements": "data/e09/hop_metrics.json",
  "model": "metrics/e08_copel_rssi.json"
}
```

O SDK verifica os identificadores dos enlaces, o sentido das observações e os parâmetros de extração antes de gravar o enriquecimento. Repetir a chamada com os mesmos dados não duplica as observações. A alteração de uma observação já existente exige substituição explícita. A saída é gravada após a validação, preservando o arquivo anterior quando os dados de entrada são rejeitados.

```python
project.enrich_features()
project.evaluate_links(rssi_threshold_dbm=-80)
project.show_topology(layout="logical", diagnosis=True)
project.show_topology(layout="geographic", diagnosis=True)
```

A avaliação grava `evaluation.json`. As vistas coloridas ficam em arquivos HTML próprios, mantendo também as vistas da topologia sem diagnóstico. Ao selecionar um nó, o painel apresenta o diagnóstico do caminho; ao selecionar um enlace, apresenta sua classificação individual. O método `diagnose_path()` disponibiliza os mesmos dados para outros consumidores da biblioteca. Alterações no modelo, no conjunto enriquecido ou na topologia exigem nova avaliação antes de exibir um diagnóstico associado às entradas anteriores.

## 5 Demonstração com a rede BRO S

A validação do método utilizou os dados reais da rede BRO-S fornecidos no arquivo de entrada do ambiente anterior. A preparação para os formatos atuais foi feita fora do SDK. O exemplo contém 25 equipamentos e 24 conexões instaladas, com um ponto de acesso ao backbone identificado como `BRO-S-P7-001`. As relações entre os equipamentos foram preservadas. Equipamentos classificados como pontos de acesso que possuem pai na rede não foram transformados em novas saídas para o backbone.

Foram utilizados 900 MHz e o sentido de medição confirmado para os dados: o nó recebe o sinal transmitido por seu pai. As configurações originais de potência, ganho e altura foram mantidas em quatro perfis distintos. As antenas direcionais conservam o ganho informado; o arquivo original não fornece os diagramas e azimutes necessários para representar sua direcionalidade completa. Essa condição faz parte da configuração disponível para a demonstração.

Das 24 conexões, 22 são enlaces de rádio e duas são ligações por cabo entre rádios no mesmo local. Os pares GE-026/GE-025 e GE-001/GE-028 estavam identificados como back-to-back nos dados de origem. Eles foram representados como conexões internas. Os valores zero de RSSI desses registros não foram utilizados como medições de propagação. A extração gerou 22 entradas de features e o enriquecimento associou 22 observações de rádio.

O modelo da E08 foi aplicado com limiar ilustrativo de −80 dBm. O resultado foi de 15 enlaces com atendimento satisfatório, dois para investigação de instalação ou defeito, quatro com medição satisfatória apesar da previsão desfavorável e um para revisão de projeto. As duas conexões internas permaneceram sem classificação de RSSI. A Figura 1 mostra a distribuição dessas classificações na topologia lógica. A visão conjunta permite localizar os trechos que merecem atenção e observar sua posição em relação às ramificações da rede.

![Topologia lógica classificada da rede BRO-S](figures/fig01_topologia_logica.png)
Figura 1 — Topologia lógica da rede BRO-S com as classificações dos enlaces. Os rótulos omitem o prefixo comum BRO-S para facilitar a leitura.

As cores seguem a nomenclatura da E08: verde para atendimento satisfatório; vermelho para investigação de instalação ou defeito; azul para medição satisfatória apesar da previsão; marrom para revisão de projeto. As ligações por cabo aparecem em cinza tracejado. Os nós mantêm cor neutra, pois a classificação corresponde ao enlace. Selecionar um caminho aumenta sua espessura e esmaece os demais trechos, preservando as cores do diagnóstico.

A Figura 2 apresenta a seleção de `BRO-S-GE-030`. Seu caminho até o backbone contém sete conexões, incluindo uma ligação interna e dois enlaces com indicação de inspeção. O resumo permite perceber que a investigação não deve se limitar ao rádio do equipamento selecionado: há também um trecho intermediário com comportamento inferior ao previsto. No notebook, os detalhes abaixo do sumário permitem consultar cada enlace do percurso.

![Caminho selecionado e sumário do diagnóstico](figures/fig02_logical_caminho.png)
Figura 2 — Caminho de BRO-S-GE-030 até o backbone e sumário do diagnóstico no painel de seleção.

O enlace GE-017 → GE-027 apresentou previsão de aproximadamente −72,0 dBm e medição de −84,0 dBm. O enlace GE-023 → GE-030 apresentou previsão de aproximadamente −65,4 dBm e medição de −82,8 dBm. Nos dois casos, o modelo indica atendimento ao limiar adotado, enquanto a medição fica abaixo dele. Ambos são classificados para investigação de instalação ou possível defeito. O resultado aponta segmentos a examinar, sem afirmar que as duas diferenças têm a mesma causa física.

A Figura 3 mostra o mesmo caminho nas posições geográficas dos equipamentos. Essa vista utiliza coordenadas projetadas, com o norte para cima e sem mapa de fundo. Ela complementa a hierarquia lógica ao permitir localizar os enlaces no espaço, conservando a identidade dos equipamentos e suas classificações. As ligações entre rádios em posições coincidentes podem ficar sobrepostas nessa representação; sua presença é explicitada no diagnóstico e na vista lógica.

![Caminho classificado em coordenadas geográficas](figures/fig03_geographic_caminho.png)
Figura 3 — Localização geográfica do caminho selecionado na rede BRO-S, com as cores de classificação preservadas.

O exemplo também permite comparar uma indicação de inspeção com uma indicação de revisão de projeto. O enlace GE-017 → GE-016 apresentou previsão de aproximadamente −80,4 dBm e medição de −86,2 dBm. Como ambos os valores ficam abaixo do limiar de −80 dBm, a classificação indica revisão do projeto. A apresentação dos valores permite ao engenheiro observar que a previsão está próxima do limiar e interpretar a decisão conforme o critério escolhido.

A inspeção física do trecho reutiliza o visualizador de features já disponível. A Figura 4 foi obtida pelo mesmo painel do notebook para o enlace GE-017 → GE-027, indicado para investigação. O eixo horizontal apresenta a distância ao longo do enlace e o vertical apresenta a elevação. A linha preta representa o terreno; as linhas tracejadas verde e vermelha representam as informações de árvores e construções; a linha azul representa a linha de visada. As cores junto ao perfil e a legenda lateral identificam classes de cobertura do solo. O perfil ajuda a examinar as condições utilizadas na previsão e a orientar a investigação do trecho selecionado.

![Perfil físico do enlace GE-017 para GE-027](figures/fig04_inspecao_enlace.png)
Figura 4 — Perfil de terreno e cobertura do enlace BRO-S-GE-017 → BRO-S-GE-027, obtido pelo inspetor de features do SDK.

A demonstração está organizada no notebook `e09_diagnostico_bro.ipynb`, disponível entre os exemplos do workspace do software de planejamento em nuvem do CISEI. Ele apresenta, em sequência, a importação da topologia, as vistas lógica e geográfica, a extração em lotes, a inspeção dos enlaces, o enriquecimento e a classificação com o modelo da E08. As figuras deste relatório registram estados das mesmas visualizações. No notebook, o engenheiro pode selecionar outros equipamentos, percorrer os detalhes e repetir a avaliação com um critério adequado ao seu estudo.

## 6 Procedimento de validação do software

O método desenvolvido foi validado com os dados reais da rede BRO-S, utilizando o SDK distribuído. O notebook foi executado no container de desenvolvimento com a versão 0.1.47, utilizando os serviços de planejamento e de features para a extração. O conjunto de conexões enviado ao processamento foi conferido contra a topologia de entrada, incluindo o sentido de transmissão utilizado para associar as observações. A aplicação do modelo manteve os pais da rede instalada e não executou novo planejamento.

O tratamento das ligações back-to-back foi verificado durante a execução. Elas foram conservadas como parte dos caminhos, sem gerar requisições de propagação para extremidades coincidentes. O cache resultante contém somente os 22 trechos de rádio. O enriquecimento exclui explicitamente os dois registros internos e mantém essa informação em seu resultado, evitando interpretar seus valores zero como intensidade de sinal.

Também foi verificada a separação entre o cache puro e o conjunto enriquecido. Repetir o enriquecimento não duplica observações. A gravação posterior de um cache de extração carregado antes do enriquecimento não remove os dados do arquivo derivado. Entradas inválidas, identificadores conflitantes e alterações sem substituição explícita são rejeitados antes da gravação. O treinamento e a avaliação da E08 continuam utilizando os dados enriquecidos em arquivo separado.

Os testes automatizados abrangem as quatro condições de classificação, os caminhos ascendentes, as ligações internas, a falta de medições, os dados insuficientes e a rejeição de diagnósticos associados a entradas alteradas. A execução de verificação reuniu 58 testes aprovados, com um teste não executado nessa configuração. As vistas foram conferidas em navegador, incluindo seleção de nó, preservação das cores do caminho, consulta de um enlace e ausência de erros de execução na interface. A inspeção de um perfil real também foi realizada pelo painel existente.

A validação com BRO-S verificou a implementação e a aplicação do método desenvolvido. A confirmação da causa física de uma anomalia exige evidências complementares de operação ou de campo. A análise histórica das medições pode ampliar essa validação nas etapas seguintes, verificando a persistência e a coincidência dos eventos. Na E09, o resultado verificável é a localização dos segmentos classificados e sua associação correta aos caminhos dos equipamentos.

## 7 Integração com as demais etapas

A E08 fornece o modelo de referência da tecnologia e a metodologia de comparação entre previsão e medição. A E09 utiliza esse resultado como entrada, acrescentando a topologia instalada, a composição do diagnóstico por caminho e as visualizações que permitem localizar os segmentos indicados para intervenção. A separação entre criação do modelo e aplicação do diagnóstico permite reutilizar o estimador sem ajustá-lo novamente à rede examinada.

As indicações de revisão de projeto podem orientar o trabalho de integração entre medições e planejamento previsto na E10. O engenheiro pode utilizar os enlaces e caminhos identificados para preparar um projeto de melhoria e avaliar as alterações propostas. As funcionalidades de representação de tecnologias, interfaces e acessos alternativos já desenvolvidas no planejamento fornecem a base para esse trabalho, respeitando as características da tecnologia instalada.

A avaliação dos diagnósticos em redes em operação, prevista na E14, poderá utilizar esses mesmos exemplos e as evidências adicionais disponíveis. As métricas de caminho foram preservadas para o cruzamento com os indicadores da aplicação. A análise temporal e os componentes anteriores que forem úteis podem ser incorporados a esse trabalho conforme a necessidade de validação, mantendo o foco da E09 no diagnóstico dos enlaces ao longo do percurso.

## 8 Resultados e conclusões da etapa

A etapa resultou em um método geral de diagnóstico corretivo, implementado no SDK e aplicável a diferentes redes a partir de suas topologias, configurações e medições. A implementação importa as conexões existentes, reutiliza a extração de features, associa as métricas por salto e aplica o modelo da E08. O resultado distingue enlaces satisfatórios, trechos que precisam de inspeção e condições que indicam revisão de projeto, mantendo também o caso em que a medição satisfatória não justifica correção apesar da previsão desfavorável.

As vistas lógica e geográfica relacionam as classificações aos caminhos até o backbone e apresentam os valores que sustentam cada indicação. A validação com BRO-S demonstrou a preservação das conexões, o tratamento dos trechos internos e a identificação de múltiplos enlaces para investigação no mesmo caminho.
