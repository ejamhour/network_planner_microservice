# E07 — Planejamento de rede com redundância

**21 de outubro de 2026**

## 1. Objetivos da etapa e evolução do escopo

A definição original da E07, registrada em **“project_phases_kb_final.md”**, estabelece:

> Projetar redes com caminhos alternativos em regiões críticas. O deslocamento de equipes para substituição de equipamentos em campo irá tornar a recuperação de falhas muito demorada. É necessário incluir mencamismos que permitam ativar caminhos alternativos de forma remota. O algoritmo de planejamento será modificado para incluir caminhos redundantes no backhaul. O nível de redundância será definido em função do custo adicional máximo tolerado para inclusão de novos equipamentos para fazer a redundância do backhaul. O algoritmo irá privilegiar a redundancia de caminhos que funcionem como tronco no backhaul e afetem mais equipamentos de automação e medição. A forma como os caminhos redundantes serão ativados é dependente da tecnologia do backhaul e será estudada nesta fase do projeto.

O produto final previsto é o **Módulo de planejamento com redundância**. A definição original orienta a identificação de regiões críticas e de elementos que concentram a comunicação de equipamentos de automação e medição, além de reconhecer que a ativação de uma alternativa depende da tecnologia utilizada.

O desenvolvimento do software tratou esse objetivo pela ampliação das possibilidades de configuração e comparação de redes. O modelo permite representar equipamentos com múltiplas interfaces, associar tecnologias e métricas a essas interfaces e preparar variantes de um projeto. A análise da qualidade e das dependências oferece ao engenheiro informações para escolher onde uma conexão complementar deve ser acrescentada.

A concepção inicial relacionava a redundância principalmente à existência de caminhos alternativos utilizando uma mesma tecnologia. Durante o desenvolvimento, essa abordagem foi ampliada para contemplar alternativas de interface, composição de equipamentos e formas de acesso aos serviços. Uma configuração redundante pode combinar diferentes tecnologias, aproveitar equipamentos capazes de encaminhar comunicação entre elas ou utilizar um acesso externo cuja disponibilidade seja informada no cenário.

A priorização prevista originalmente foi tratada por informações de qualidade, filhos e descendentes, com escolha manual dos pontos de proteção. A implementação não realiza otimização automática sob um orçamento máximo de equipamentos. O custo utilizado no rank é um critério de planejamento dos caminhos e não representa, por si só, o custo financeiro da redundância. A configuração e a ativação operacional dos equipamentos permanecem dependentes da tecnologia adotada.

Essa ampliação exigiu que a solução fosse organizada em torno de perfis e instâncias, em vez de pressupor uma combinação específica de equipamentos. As definições compartilhadas descrevem as características dos dispositivos e de suas interfaces; as instâncias representam os equipamentos efetivamente instalados. Dessa forma, a configuração de redundância pode alterar determinados elementos sem modificar todos os equipamentos do mesmo tipo.

O documento de estrutura revisada das etapas define como objetivo revisado desenvolver uma estrutura capaz de representar e avaliar múltiplas alternativas de conectividade, considerando caminhos candidatos, interfaces de comunicação, tecnologias distintas, repetidores e diferentes configurações dos elementos da rede. Para atender a esse escopo, foram desenvolvidos o controle de ativação por interface, a exploração visual das dependências e a preparação de configurações alternativas com possibilidade de preservar a topologia instalada.

Este relatório apresenta primeiro essas capacidades gerais e suas decisões de implementação. A aplicação em uma rede específica aparece na seção de demonstração, permitindo distinguir os recursos do software das escolhas de configuração utilizadas para exercitá-los.

## 2. Requisitos de planejamento com redundância

O planejamento com redundância precisa admitir diferentes composições de rede e diferentes critérios para escolha dos pontos de proteção. O custo de uma conexão adicional, a quantidade de equipamentos dependentes e a importância dos serviços atendidos podem justificar soluções distintas. Por isso, o software deve fornecer informações e meios de configuração, mantendo a decisão de engenharia explícita.

A qualidade e a dependência da topologia são critérios complementares. As métricas existentes permitem identificar caminhos com custo elevado ou qualidade abaixo do limiar. A relação entre pais e filhos permite reconhecer equipamentos que concentram a comunicação de uma sub-rede. Uma folha também pode justificar uma conexão adicional quando atende uma função de automação importante. A quantidade de descendentes, portanto, não é utilizada como uma regra automática para determinar os equipamentos que receberão redundância.

A partir dessas necessidades, o desenvolvimento adotou os seguintes requisitos:

- **Representação multi-interface e multitecnologia:** permitir dispositivos com uma ou mais interfaces, cada uma associada à sua tecnologia e configuração, incluindo equipamentos que encaminham comunicação entre diferentes segmentos.
- **Configuração por instância:** alterar o perfil ou o estado de participação de um elemento específico, preservando as definições dos demais equipamentos que utilizam o mesmo perfil.
- **Análise das dependências:** identificar filhos e descendentes da solução selecionada, com distinção entre interfaces, dispositivos e sites, para apoiar a escolha manual dos pontos de proteção.
- **Avaliação de diferentes configurações:** manter projetos independentes para a rede de referência e suas alternativas, preservando entradas e resultados necessários à comparação.
- **Controle da topologia considerada:** permitir tanto o planejamento a partir de candidatos quanto a avaliação restrita aos enlaces de uma solução tratada como instalada.
- **Interpretação dos resultados:** distinguir existência de caminho, qualidade do caminho e exclusão deliberada de uma interface do cenário.

O controle da topologia é relevante porque o planejamento de uma rede nova e a avaliação de uma rede instalada têm objetivos diferentes. No primeiro, o algoritmo pode escolher entre conexões candidatas. No segundo, quando a configuração operacional exige enlaces fixos, a avaliação deve utilizar apenas as conexões existentes. A preservação de enlaces é, assim, uma opção do fluxo de preparação, aplicável conforme o cenário, e não uma restrição imposta a todo planejamento multitecnologia.

Também é necessário manter separadas conectividade e qualidade. Um dispositivo pode possuir caminho até um ponto conectado e apresentar custo acumulado acima do limite adotado. Da mesma forma, uma interface desabilitada não deve ser contabilizada como uma interface ativa sem atendimento. Essas distinções orientaram tanto a execução do planejamento quanto a apresentação visual dos resultados.

## 3. Modelo de representação e suporte multitecnologia

### 3.1. Perfis, instâncias e composição dos equipamentos

O modelo organiza a rede em sites, dispositivos e interfaces. O site identifica a posição de instalação; o dispositivo representa o equipamento e sua capacidade de encaminhamento; as interfaces descrevem os meios de comunicação disponíveis nesse equipamento. Essa separação permite representar equipamentos com configurações diferentes sem alterar a estrutura do planejador.

Os perfis reúnem configurações reutilizáveis em três níveis. O perfil de antena descreve o tipo e os parâmetros utilizados no cálculo ou na representação do enlace. O perfil de interface referencia a antena e informa tecnologia, frequência, potência e capacidade de repetição, entre outros atributos. O perfil de dispositivo relaciona as interfaces que compõem o equipamento e estabelece sua capacidade de roteamento e suas condições iniciais de conectividade.

O CSV associa os perfis de dispositivos aos registros de instalação e fornece dados como identidade do site, posição e altura de montagem. O SDK expande essas definições em instâncias concretas, com identificadores próprios de dispositivo e interface. O formato também admite instâncias explicitamente descritas no TOML. Assim, perfil e instância têm funções distintas: o perfil fornece a configuração compartilhada, enquanto a instância identifica o elemento que participa do projeto.

Um equipamento com duas interfaces é representado por um perfil de dispositivo que referencia dois perfis de interface. Essas interfaces podem utilizar a mesma tecnologia ou tecnologias diferentes. A substituição de um equipamento por outro com composição ampliada consiste em trocar o perfil aplicado à instância, preservando as identidades necessárias aos enlaces existentes. Não é necessário criar uma classe de dispositivo específica para cada combinação de tecnologias.

Essa estrutura também permite atribuir papéis diferentes aos equipamentos. Um dispositivo pode fornecer conectividade inicial, encaminhar a comunicação de outros elementos ou operar como folha. A combinação entre capacidade de encaminhamento, composição das interfaces e regras do cenário determina como ele participa da topologia.

### 3.2. Tecnologias, enlaces e condições de conectividade

As interfaces são os nós do grafo de planejamento. Os enlaces de rádio são construídos entre interfaces compatíveis, considerando tecnologia, frequência e as demais condições estabelecidas nas regras de conectividade. Interfaces de tecnologias diferentes não são conectadas diretamente como se formassem um mesmo enlace de rádio. Quando a comunicação deve atravessar tecnologias, o modelo utiliza um equipamento com interfaces apropriadas e capacidade de encaminhamento entre elas.

As regras de conexões internas permitem representar essa passagem dentro de um dispositivo. Com isso, o mesmo cenário pode combinar segmentos de acesso e de encaminhamento, como nas configurações LTE e mesh/multi-hop já utilizadas pelo software. O suporte multitecnologia decorre da composição de interfaces, regras e métricas; não pressupõe que todas as tecnologias utilizem os mesmos parâmetros ou critérios de avaliação.

As características ambientais dos enlaces são extraídas pelo microserviço de features. As métricas configuradas para cada tecnologia transformam essas características em custos utilizados pelo planejador. O rank representa o custo acumulado a partir de uma condição de conectividade inicial. Em uma configuração híbrida, a interpretação desse custo depende das métricas adotadas; a soma utilizada para selecionar caminhos não constitui automaticamente uma medida de disponibilidade, vazão ou qualidade de serviço.

O modelo também admite que um acesso seja fornecido como condição de entrada, sem detalhar a infraestrutura externa responsável por ele. Nesse caso, o dispositivo é configurado como conectado e recebe um custo inicial finito. Atualmente, `connected` e o rank inicial são propriedades do dispositivo e são herdados por suas interfaces. A representação desse acesso não exige a geração de enlaces até a infraestrutura externa.

A disponibilidade dos perfis e a possibilidade de combinar tecnologias não equivalem à existência de um modelo de desempenho calibrado para qualquer equipamento. O planejamento detalhado de cada tecnologia depende das métricas, dos parâmetros e dos dados configurados. Já um acesso externo pode ser tratado como premissa explícita, cuja verificação operacional ocorre fora dessa avaliação.

### 3.3. Seleção de caminhos e organização dos projetos em layers

O grafo candidato reúne as alternativas de conexão consideradas pelo projeto. O RPL genérico utiliza os custos dos enlaces e a conectividade inicial para selecionar um pai para cada interface alcançada. A solução registra os caminhos escolhidos, distinguindo-os dos demais enlaces que apenas integraram o conjunto candidato.

A comparação entre alternativas é organizada em projetos independentes, tratados como layers de planejamento. Cada projeto mantém cenário, instâncias, cache e resultados próprios. Uma layer pode representar uma configuração de referência, enquanto outra incorpora novos perfis, altera a participação de interfaces ou utiliza outra condição de acesso. A origem é preservada para que a alteração possa ser examinada e reproduzida.

O conceito de layer pertence a esse fluxo de organização e preparação. Não foi criada uma nova entidade de execução no microserviço nem um mecanismo de fusão automática das soluções. Cada projeto continua executando o planejamento com suas próprias entradas e com um pai selecionado por interface. A relação entre projetos é estabelecida pela origem dos dados, pelas substituições realizadas e, quando aplicável, pela herança dos enlaces.

Essa organização permite utilizar a mesma arquitetura para situações distintas. Um estudo pode explorar uma nova topologia a partir das interfaces habilitadas; outro pode avaliar uma rede instalada, restringindo os candidatos aos enlaces existentes. A escolha do procedimento pertence à configuração do projeto e às condições que o engenheiro pretende representar.

## 4. Funcionalidades de planejamento e análise

### 4.1. Controle das interfaces e ajustes por instância

A implementação já dispunha de perfis reutilizáveis, instanciação por CSV, dispositivos multi-interface, geração de candidatos, métricas por tecnologia e seleção de caminhos pelo RPL genérico. Esses recursos foram utilizados como base para acrescentar o controle de participação das interfaces e a preparação de configurações independentes, sem especializar o modelo em uma família de equipamentos.

Foi acrescentado o campo booleano `enabled` às interfaces concretas, com valor padrão `true`. Uma interface desabilitada permanece cadastrada, mas fica fora dos nós ativos, da geração de enlaces RF e internos, do cálculo de métricas e do RPL. Referências explícitas a interfaces desabilitadas em enlaces manuais ou métricas fornecidas ao serviço são rejeitadas. A ausência do campo preserva o comportamento dos cenários anteriores.

Os ajustes por instância ficam integrados ao `scenario.toml`. O carregamento utiliza os perfis para criar os elementos do CSV e aplica, em seguida, os overrides às interfaces identificadas. Por exemplo:

```toml
[overrides.interfaces."site_001:d0:i0"]
enabled = false
```

O ajuste afeta somente a interface indicada, sem modificar o perfil compartilhado. O carregador verifica os identificadores, os campos permitidos e os valores. Nesta entrega, os overrides suportam `enabled`; parâmetros adicionais por instância, como configurações de antena, são uma possibilidade de evolução da estrutura.

O estado de ativação é preservado na leitura, na gravação e na reabertura do cenário. Sua alteração invalida os candidatos, as métricas e a solução derivados da configuração anterior. O cache externo de features brutas permanece disponível para reutilização quando os parâmetros de extração continuam compatíveis. Dessa forma, a preparação de outra configuração não implica descartar informações ambientais que ainda possam ser aproveitadas.

### 4.2. Exploração visual das dependências e interpretação dos resultados

A análise de dependências foi incorporada ao SDK como uma operação local sobre o resultado salvo. Ela utiliza a relação de pais para identificar filhos diretos e descendentes, sem executar novamente o planejamento. O resultado descreve a dependência dos caminhos selecionados, fornecendo ao engenheiro uma visão do alcance potencial da perda de um elemento de encaminhamento.

A visualização permite selecionar um site e destacar os enlaces de sua sub-rede dependente. O painel apresenta as quantidades de interfaces descendentes, dispositivos e sites distintos. Essa distinção evita que a existência de várias interfaces em um equipamento seja interpretada como a presença de equipamentos adicionais. A consulta pode ser utilizada em conjunto com a qualidade dos caminhos para orientar a escolha de conexões complementares.

A decisão continua manual. O SDK permite registrar o site escolhido e a identificação do resultado que fundamentou essa escolha. O registro não troca perfis, não acrescenta conectividade e não altera enlaces. Ele documenta a seleção para o fluxo de preparação posterior, sem impor que um nó com mais descendentes seja necessariamente mais importante que uma folha.

Os resultados mantêm o inventário completo e distinguem as interfaces ativas das desabilitadas. No grafo, sites desabilitados aparecem em cinza; ausência de caminho aparece em vermelho; qualidade abaixo do limiar aparece em magenta; pontos conectados são identificados em azul. A seleção permite consultar as interfaces e os caminhos associados ao site, mantendo visíveis as diferenças entre cadastro, participação e atendimento.

Os notebooks utilizam essas visualizações e chamadas curtas ao SDK. Os algoritmos de análise, a manipulação dos arquivos e a preparação dos cenários ficam na biblioteca e no serviço. Assim, o exemplo executável documenta o uso das funcionalidades entregues, sem implementar uma solução paralela dentro das células do notebook.

## 5. Construção de configurações alternativas

A preparação de uma configuração alternativa utiliza o mecanismo de regras de substituição já disponível no SDK. As regras atuam sobre os registros do projeto de origem e podem selecionar equipamentos pelo perfil, pelas etiquetas do CSV e pelos critérios de rank suportados. A primeira regra aplicável a cada registro determina sua substituição. A seleção por `site_id` permite restringir a alteração a uma instância escolhida pelo engenheiro.

O perfil de destino determina a composição do equipamento resultante. Uma substituição pode conservar as interfaces existentes e acrescentar outras, ou modificar a composição conforme o objetivo da nova configuração. As condições de conectividade e de custo inicial também podem ser definidas pela regra. Depois de instanciado o novo perfil, os overrides controlam quais de suas interfaces participam do projeto.

O SDK lê a receita de preparação e gera os arquivos efetivos do projeto, incluindo cenário, CSV e relatório das substituições. Os overrides permanecem integrados ao cenário final. A receita descreve como preparar o projeto derivado; ela não introduz outro mecanismo de metadados para competir com os ajustes do `scenario.toml`.

A topologia da configuração alternativa pode ser tratada de duas formas, conforme o estudo:

- **Geração de candidatos:** as regras de conectividade constroem as alternativas disponíveis para as interfaces habilitadas, e o planejador seleciona os caminhos pelos custos configurados.
- **Herança da solução instalada:** somente os enlaces selecionados no projeto de origem podem integrar a avaliação. Esse modo impede que a perda de um recurso seja compensada por uma conexão nova que não pertença à rede considerada instalada.

Para a segunda forma, foi acrescentado o modo de preparação `topology.mode = "installed"`. O SDK valida a correspondência entre os enlaces exportados e a relação de pais da origem, além de verificar a preservação das identidades e dos parâmetros das interfaces instaladas. Os enlaces selecionados tornam-se candidatos manuais; aqueles incidentes a interfaces desabilitadas são excluídos. As regras de geração automática são retiradas do cenário derivado.

O microserviço passou a aceitar esse fluxo exclusivamente manual, inclusive quando o conjunto de enlaces é vazio. A avaliação determina o atendimento na topologia remanescente, sem acrescentar conexões por proximidade. O modo restringe os enlaces disponíveis; a preservação das relações de pais deve ser verificada no cenário concreto, de acordo com suas origens de conectividade e sua organização.

Cada projeto mantém seus arquivos e resultados, e a preparação registra as substituições e os enlaces retidos e excluídos. Ao refazer uma preparação com sobrescrita, os arquivos gerados anteriores são arquivados e os resultados que deixaram de corresponder à configuração atual são retirados da posição ativa. O projeto de origem é preservado, e o cache de features pode ser reaproveitado quando seus parâmetros permanecem válidos. Essa organização fornece a rastreabilidade necessária para examinar a alteração sem substituir a referência utilizada na comparação.

## 6. Demonstração: redundância celular em uma rede GE

A demonstração foi organizada em dois notebooks:

- `e07_01_projeto_original.ipynb`: planejamento da rede GE, inspeção de qualidade e dependências e registro da escolha manual.
- `e07_02_projeto_redundancia.ipynb`: preparação do perfil GE/celular, desativação da interface de Torre e avaliação sobre os enlaces instalados sobreviventes.

Os dois foram executados no contêiner de desenvolvimento com o SDK instalado e os serviços de planejamento e features disponíveis. As versões distribuídas permanecem sem saídas, enquanto cópias executadas preservam os resultados para consulta e preparação dos anexos.

### 6.1. Rede original e escolha do ponto de proteção

O cenário utiliza os 13 sites do `example_1`, mantendo suas posições e alturas. Os perfis LTE do exemplo de origem foram substituídos por perfis mesh. Torre e Itallia representam os pontos inicialmente conectados, e os demais dispositivos podem encaminhar comunicação.

Foram utilizados frequência de 915 MHz, potência de 30 dBm, ganho fixo de antena de 3 dBi e limite de distância de 2.200 m para geração dos candidatos. A métrica `default` utiliza o critério genérico de obstrução de terreno disponível no software. Esses parâmetros são ilustrativos e não constituem uma configuração calibrada de equipamento GE.

O planejamento produziu 33 enlaces candidatos e selecionou 11 enlaces, formando caminhos para os 13 sites. A existência de dois pontos inicialmente conectados resulta em dois ramos de acesso na solução.

O Quadro 1 apresenta um extrato do cenário original. O perfil de dispositivo referencia a interface GE e habilita o encaminhamento, enquanto as instâncias são criadas a partir dos registros do CSV. Essa separação permite reutilizar a configuração de rádio e, posteriormente, substituir somente o equipamento escolhido para receber o acesso complementar.

```toml
[interfaces.ge_mesh_radio]
tech = "ge_mesh"
freq_mhz = 915.0
tx_power_dbm = 30.0
antenna_id = "ge_mesh_omni"
can_relay = true

[devices.ge_mesh_router]
kind = "router"
connected = false
can_route = true
interfaces = ["ge_mesh_radio"]
```

Quadro 1 — Perfis de interface e dispositivo da rede original; extrato de scenario.toml.

A Figura 1 apresenta a solução original na visualização interativa do SDK. Ela permite reconhecer os dois pontos de acesso e os caminhos selecionados antes de definir a proteção. Os sites em magenta possuem caminho, mas ultrapassam o limiar de rank; essa leitura inicial evita confundir uma condição de qualidade insuficiente com ausência de conectividade.

![Solução da rede original](figures/fig01_rede_original.png)

Figura 1 — Rede original: caminhos selecionados e classificação visual dos sites.

A análise da solução mostrou que Barigui possuía seis descendentes: Copel, Decatlon, Deneka, Saturno, Shopping e Woodland. Sua proteção poderia, portanto, oferecer uma saída complementar para sete sites, considerando o próprio Barigui.

Barigui foi escolhido manualmente para demonstrar esse alcance. A seleção não corresponde a uma otimização automática de custo ou a uma determinação de que esse seja o único ponto adequado para proteção. A mesma ferramenta permite selecionar uma folha quando a importância do equipamento atendido justificar uma conexão adicional.

Na topologia original, Barigui utiliza Torre como pai. Seus filhos diretos são Decatlon e Shopping, pelos quais são alcançados os demais descendentes. A análise visual permite inspecionar essa organização antes de preparar o segundo projeto.

A Figura 2 mostra a inspeção de qualidade com Copel selecionado. O painel identifica Saturno como pai, informa rank aproximado de 7,915 diante do limiar 6,0 e registra a existência de atendimento (`served = true`). No fluxo de engenharia, essa consulta permite localizar o caminho que precisa ser examinado e fundamentar uma escolha de proteção por qualidade, independentemente da quantidade de descendentes.

![Inspeção de qualidade de Copel](figures/fig02_inspecao_qualidade.png)

Figura 2 — Inspeção de qualidade: caminho de Copel e atributos do resultado selecionado.

A Figura 3 mostra a consulta de dependências com Barigui selecionado. O destaque da sub-rede e as contagens de dois filhos diretos e seis sites descendentes ajudam o engenheiro a avaliar o alcance da proteção proposta. A consulta descreve a dependência na solução selecionada; a escolha de acrescentar celular continua manual e pode considerar também a importância dos equipamentos atendidos.

![Inspeção da sub-rede dependente de Barigui](figures/fig03_inspecao_dependencias.png)

Figura 3 — Inspeção de dependências: filhos diretos e sub-rede descendente de Barigui.

### 6.2. Configuração alternativa sobre a topologia instalada

No segundo projeto, somente o perfil de Barigui foi substituído pelo perfil duplo GE/celular. Sua interface GE foi mantida ativa e com o mesmo identificador; a interface celular também permaneceu ativa. O dispositivo foi configurado como conectado, com custo inicial 1,0.

O Quadro 2 mostra a composição do perfil duplo na receita de preparação. A interface GE permanece na primeira posição para conservar sua identidade instalada. O acesso celular utiliza meio externo: os valores nulos de frequência e potência são marcadores de configuração, sem cálculo de cobertura celular. A condição de acesso é expressa por `connected` e pelo rank inicial do dispositivo, herdados por suas interfaces. O perfil de antena referenciado está definido no arquivo completo.

```toml
[interfaces.cellular_public]
tech = "cellular_public"
medium = "external"
freq_mhz = 0.0
tx_power_dbm = 0.0
antenna_id = "cellular_external"
can_relay = false

[devices.ge_mesh_cellular_gateway]
kind = "gateway"
connected = true
can_route = true
rank = 1.0
interfaces = ["ge_mesh_radio", "cellular_public"]
```

Quadro 2 — Interface de acesso externo e composição do perfil duplo; extrato de extension_fixed_links.toml.

A indisponibilidade considerada foi a desativação da interface GE de Torre. Barigui não foi desabilitado. Os demais sites também permaneceram habilitados.

O Quadro 3 reúne os comandos declarativos que delimitam a segunda layer: a herança dos enlaces instalados, a substituição restrita ao site Barigui e a desativação da interface de Torre. Esse extrato documenta as três decisões necessárias para reproduzir o estudo sem alterar o projeto original. A regra seleciona uma instância pelo `site_id`, enquanto o override atua sobre uma interface concreta.

```toml
[topology]
mode = "installed"

[[replacement.rules]]
name = "Cellular access at Barigui"
profile = "ge_mesh_router"
tags = { site_id = "barigui" }
replace_with = "ge_mesh_cellular_gateway"
connected = true
rank = 1.0

[overrides.interfaces."torre:d0:i0"]
enabled = false
```

Quadro 3 — Preservação da topologia, substituição por instância e desativação do uplink; extrato de extension_fixed_links.toml.

O modo de topologia instalada herdou os 11 enlaces selecionados no primeiro projeto. Os três enlaces incidentes à interface de Torre foram excluídos, restando oito enlaces. Nenhum novo enlace GE foi gerado.

O projeto resultante contém 14 interfaces cadastradas, das quais 13 permanecem ativas. A solução inclui 11 interfaces com conectividade e oito enlaces. Essas 11 interfaces correspondem a dez sites, pois Barigui possui duas interfaces conectadas.

### 6.3. Resultados e verificação do funcionamento

Barigui e seus seis descendentes mantiveram caminhos de acesso após a desativação da interface de Torre. Os enlaces GE internos dessa sub-rede e as relações de pais de seus descendentes permaneceram iguais aos do projeto original. A alteração ocorreu na condição de saída de Barigui, que passou a representar o acesso celular externo.

Tenis e Cemiterio ficaram sem caminho, pois dependiam de Torre e não receberam uma conexão complementar. O ramo atendido por Itallia permaneceu com seu acesso original. O resultado mostra que a proteção foi localizada e não atribuída automaticamente a todos os equipamentos afetados pela perda de Torre.

A Figura 4 permite conferir visualmente o efeito da configuração alternativa. Barigui aparece como ponto conectado, Torre aparece em cinza por estar desabilitado e Tenis e Cemiterio aparecem em vermelho por ausência de caminho. A comparação com a Figura 1 permite acompanhar a preservação dos enlaces da sub-rede protegida e identificar os ramos que continuam sem proteção, completando a inspeção posterior à preparação da segunda layer.

![Solução com acesso celular em Barigui](figures/fig04_rede_com_redundancia.png)

Figura 4 — Segunda layer: acesso externo em Barigui e resultado após desativar a interface GE de Torre.

A comparação também evidenciou a necessidade de distinguir conectividade e qualidade. O limiar de rank utilizado foi 6,0. Copel e Woodland mantiveram rank aproximadamente igual a 7,915, e Saturno apresentou aproximadamente 6,915. Os três possuem caminho, mas permanecem classificados com qualidade abaixo do limiar.

O custo inicial de Barigui no segundo projeto foi definido como 1,0, igual ao seu rank no projeto original. Por isso, a manutenção dos caminhos da sub-rede também manteve os custos acumulados desses descendentes no exemplo. A demonstração verifica continuidade de acesso na configuração modelada, sem declarar aprovação geral dos requisitos de desempenho.

Os testes verificaram persistência de `enabled`, aplicação de overrides, validação de referências, exclusão de interfaces do planejamento, preparação por substituição, herança dos enlaces instalados e preservação do projeto original.

Também foram verificadas a análise de descendentes, a apresentação visual de subgrafos, a seleção de folhas e a estrutura dos notebooks, que deve conter somente chamadas ao SDK. A execução dos exemplos distribuídos foi testada em workspaces independentes.

Na validação da versão 0.1.39 no contêiner, 110 testes foram aprovados e dois foram ignorados por dependerem da disponibilidade do Node.js naquele ambiente. Os testes de renderização em JavaScript foram executados adicionalmente no ambiente local. Os dois notebooks também foram executados contra os serviços disponíveis, confirmando a preservação dos pais GE dos descendentes de Barigui e a ausência de novos enlaces na segunda configuração.

## 7. Conclusões da etapa e integração com o software

A etapa consolidou um fluxo de planejamento com redundância apoiado na representação genérica de perfis, dispositivos e interfaces. O software permite configurar equipamentos com diferentes combinações de tecnologias, identificar dependências, registrar escolhas manuais e preparar projetos alternativos. Essas capacidades não dependem da marca ou da combinação de interfaces utilizada no exemplo, embora a avaliação de desempenho de cada tecnologia dependa de seus parâmetros e métricas.

O controle de ativação por instância permite delimitar os recursos que participam de cada configuração sem eliminar seu cadastro. A exploração visual oferece informações de qualidade e dependência, enquanto as regras de substituição transformam a escolha de engenharia em uma configuração reproduzível. Quando a avaliação exige preservar uma rede instalada, a herança dos enlaces restringe o cenário derivado às conexões selecionadas na origem.

O caso GE com acesso celular demonstrou uma aplicação desse conjunto de recursos. A desativação de Torre foi representada sem desabilitar Barigui ou reorganizar os enlaces de sua sub-rede. A configuração manteve acesso para seus descendentes e mostrou a perda de atendimento em ramos que não receberam proteção. O exemplo também evidenciou que a existência de caminho e o atendimento ao critério de qualidade devem ser examinados separadamente.

Os resultados têm o alcance definido pelas condições de entrada. Um acesso externo informado como disponível não implica que o software tenha verificado a cobertura da operadora, estabelecido uma VPN ou configurado os mecanismos de comutação do equipamento real. A representação determinística de indisponibilidade também não fornece probabilidades de falha, tempos de recuperação ou disponibilidade estatística. No caso demonstrado, o equipamento que fornece a saída complementar continua sendo uma dependência para seus descendentes.

A E07 reutiliza os mecanismos de métricas e avaliação de qualidade desenvolvidos na E06. A análise das dependências acrescenta a perspectiva de continuidade de acesso, mantendo a distinção entre um caminho existente e um caminho que atende ao desempenho requerido. A E10, definida originalmente como integração dos módulos de monitoramento e planejamento, prevê ajustar projetos e modelos com base nas medições da rede implantada. As configurações alternativas e os recursos de comparação da E07 podem compor esse fluxo, utilizando as métricas e os dados fornecidos pelo restante do sistema.

A E13 foi originalmente denominada **“Ajustes de desempenho e melhorias”** e previa disponibilizar a ferramenta à Copel, avaliar os projetos produzidos e incorporar melhorias, incluindo a possibilidade de projetar redundância em redes já em operação. Na estrutura revisada, ela é apresentada como avaliação do aplicativo de planejamento com garantia de desempenho e tolerância a falhas. Os projetos e notebooks da E07 oferecem casos reproduzíveis para esse trabalho. As execuções aqui apresentadas demonstram as funcionalidades desenvolvidas, enquanto a avaliação integrada do aplicativo pertence ao escopo da E13.

O SDK concentra a preparação, a análise e a visualização, e os notebooks apresentam somente chamadas aos seus recursos públicos. Essa distribuição de responsabilidades permite utilizar os mesmos mecanismos em outros cenários, mantendo as decisões de configuração fora dos algoritmos e a lógica de implementação fora dos exemplos de uso.

## 8. Anexos técnicos

Os anexos reúnem os materiais necessários para consultar e reproduzir a demonstração. As fontes distribuídas dos notebooks permanecem sem saídas para permitir nova execução no workspace do usuário. As cópias executadas preservam os resultados utilizados neste relatório e servirão de base para a preparação dos anexos em PDF, após a revisão do conteúdo e das figuras.

Os notebooks são:

- [Notebook 1 — Projeto original](../../../../examples/notebooks/e07_01_projeto_original.ipynb) e sua [cópia executada](executed/e07_01_projeto_original.ipynb).
- [Notebook 2 — Redundância celular](../../../../examples/notebooks/e07_02_projeto_redundancia.ipynb) e sua [cópia executada](executed/e07_02_projeto_redundancia.ipynb).

Os arquivos de configuração e resultados permitem conferir os perfis, os ajustes por instância e os enlaces considerados em cada projeto:

- [Descrição do projeto original](../../../../examples/projects/e07_ge_mesh_original/README.md), [cenário](../../../../examples/projects/e07_ge_mesh_original/scenario.toml) e [resultado](../../../../examples/projects/e07_ge_mesh_original/result.json).
- [Descrição do projeto de redundância](../../../../examples/projects/e07_ge_mesh_redundancia/README.md), [receita de substituição e enlaces fixos](../../../../examples/projects/e07_ge_mesh_redundancia/extension_fixed_links.toml), [cenário gerado](../../../../examples/projects/e07_ge_mesh_redundancia/scenario.toml) e [resultado](../../../../examples/projects/e07_ge_mesh_redundancia/result.json).

Como documentação complementar, estão disponíveis o [roadmap da E07](roadmap.md), a [configuração de overrides por instância](instance_overrides.md), o [registro de execução dos exemplos](executed/README.md) e as [notas da versão 0.1.39 do SDK](../../../releases/0.1.39-sdk.md). As definições originais das etapas utilizam como fonte **“project_phases_kb_final.md”**, de 29 de abril de 2025. O documento **“estrutura relatorio etapas 07 08 09 13 14.docx”** complementa essa fonte com a descrição revisada dos objetivos e das relações entre as etapas.

Os materiais técnicos complementam a descrição do desenvolvimento e dos resultados, permitindo conferir as configurações e reproduzir os procedimentos sem duplicar arquivos extensos no corpo do relatório.
