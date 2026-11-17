# E13 Ajustes de desempenho e melhorias

**29 de outubro de 2026**

## 1 Objetivos da etapa e escopo desenvolvido

A E13 desenvolveu um fluxo para transformar o diagnóstico de uma rede existente em propostas de melhoria do projeto. O software permite examinar os enlaces, revisar as intervenções sugeridas e planejar novas conexões preservando a parte da rede que deve permanecer instalada. O método utiliza a representação da rede, os modelos de desempenho e os recursos de planejamento construídos nas etapas anteriores. A rede AGU-S foi utilizada para validar esse fluxo e produzir os exemplos apresentados neste relatório.

A definição original da etapa estabelece:

> Instalar o aplicativo na Copel e fazer os ajustes necessários. Avaliar juntamente com a Copel o resultado obtidos com a ferramenta de planejamento. Esta etapa irá disponibilizar a ferramenta de planejamento para Copel. Os resultado dos projetos elaborados serão discutidos e possíveis melhorias serão feitas na ferramenta. Uma possibilidade é utilizar a ferramenta para projetar caminhos redundantes em redes problemáticas que já estão em operação.

O desenvolvimento aqui descrito concentra-se nos recursos que permitem avaliar uma rede em operação e elaborar um projeto de melhoria a partir dos problemas identificados. A redundância é uma das intervenções possíveis nesse conjunto de ferramentas, tratada na E07. Neste relatório, a demonstração acompanha a substituição de enlaces inadequados por conexões planejadas entre os mesmos extremos, com a possibilidade de instalar repetidores em postes disponíveis.

O diagnóstico oferece duas formas de intervenção. A primeira remove apenas os enlaces escolhidos, mantendo os rádios e as demais conexões. A segunda remove os enlaces de uma subárvore e permite retirar seus repetidores, preservando os equipamentos que precisam ser reconectados. Essa segunda opção transforma a região afetada em um caso de planejamento incremental, cujo procedimento é tratado no relatório da E10. A E13 acompanha a primeira opção, denominada replanejamento de enlaces.

O resultado da etapa é um procedimento de software aplicável a diferentes projetos. As decisões continuam sob responsabilidade do engenheiro: o diagnóstico apresenta evidências, a interface permite revisar o que será alterado e o planejador calcula as alternativas nas condições informadas. As operações produzem arquivos de projeto e propostas de conexão para análise antes da implantação.

## 2 Representação da rede e informações utilizadas

A representação utiliza sites, dispositivos e interfaces. O site fornece a localização; o dispositivo representa o equipamento; as interfaces descrevem os recursos de comunicação. Os perfis permitem especificar tecnologias, potência, antenas e funções de encaminhamento. Essa organização é comum ao planejamento e ao diagnóstico, de modo que uma rede instalada pode ser avaliada com os mesmos modelos usados para planejar suas alternativas de conexão.

O arquivo `scenario.toml` reúne os perfis e a configuração da rede. O `nodes.csv` descreve as instâncias e suas posições. No diagnóstico, os enlaces são informados explicitamente no cenário: o software importa a topologia existente, identifica os caminhos até os pontos com acesso ao backbone e associa as medições aos respectivos segmentos. O backbone corresponde à rede de transporte pela qual os equipamentos alcançam os sistemas de destino. Conexões internas por cabo também podem integrar os caminhos, inclusive entre rádios instalados no mesmo local.

As medições são fornecidas por arquivos. O `hop_metrics.json` contém os indicadores de cada enlace, com identificação das interfaces transmissora e receptora. O `path_metrics.json` mantém as observações do caminho completo, para o cruzamento com o comportamento da aplicação. A E13 utiliza a avaliação por enlace para orientar o replanejamento. Os dados de caminho permanecem associados ao projeto para as análises em que forem necessários.

A avaliação reaproveita o modelo da tecnologia definido na E08. Esse modelo representa como os enlaces deveriam se comportar nas condições de instalação, a partir das configurações de rádio e das características físicas dos percursos. O indicador utilizado é a intensidade do sinal recebido, conhecida como RSSI, expressa em dBm. A comparação entre a previsão e a medição fornece a indicação de manutenção ou revisão de projeto.

O inventário de equipamentos associados é mantido em `attached_equipment.csv`. A separação permite representar mais de um equipamento de automação conectado ao mesmo rádio. Essa informação é importante para distinguir um repetidor de um rádio que atende uma carga de interesse. A visualização identifica os rádios com equipamentos associados em roxo e apresenta os registros ao selecionar o nó. A ausência de um equipamento no inventário, por si só, não autoriza considerar o rádio removível.

Os postes disponíveis são fornecidos separadamente em `poles.csv`. Eles representam posições onde um repetidor pode ser instalado, sem serem inicialmente dispositivos da rede. O perfil do rádio candidato e as condições de busca são definidos no projeto de replanejamento. Somente os repetidores selecionados em propostas aceitas passam a integrar a configuração consolidada.

## 3 Diagnóstico e identificação dos enlaces para revisão

O fluxo inicia com a importação da topologia e a extração das características físicas dos enlaces, chamadas de features. O enriquecimento associa a essas características as medições e configurações necessárias para aplicar o modelo da E08. As features originais permanecem em `features.json`, enquanto os registros enriquecidos são salvos em `enriched_features.json`. A avaliação gera `evaluation.json` e fornece as classificações utilizadas na inspeção da topologia.

A interpretação distingue quatro situações. Quando a previsão é boa e a medição é ruim, a indicação é investigar a instalação ou um possível defeito. Quando ambas são boas, o enlace apresenta atendimento satisfatório. Se a medição é boa apesar de uma previsão ruim, não há indicação de correção com base nessa comparação. Quando a previsão e a medição são ruins, o enlace é recomendado para revisão de projeto. Sem medição, o software pode indicar uma deficiência prevista pelo modelo, identificando que a recomendação se baseia somente no projeto.

Essa distinção orienta a intervenção. Um enlace com comportamento medido abaixo do esperado pode exigir manutenção em campo; refazer seu projeto não é a primeira conclusão do método. O replanejamento demonstrado concentra-se nos enlaces classificados como revisar projeto e nas indicações equivalentes baseadas apenas na previsão, sempre sujeitas à revisão do engenheiro.

A Figura 1 mostra a topologia lógica com o caminho de AGU-S-GE-025 destacado e o diagnóstico do enlace GE-024 — GE-025 no painel lateral. O RSSI previsto é aproximadamente −97,1 dBm, e o medido é −84,5 dBm. Ambos ficam abaixo do limiar de −80 dBm usado na avaliação. A diferença entre os valores não elimina a indicação de revisão, pois o comportamento previsto e o observado são insuficientes segundo esse critério.

![Diagnóstico do enlace na topologia lógica](figures/fig01_diagnostico.png)

Figura 1 — Inspeção do enlace GE-024 — GE-025 na topologia instalada. O painel apresenta previsão, medição e classificação, enquanto o grafo conserva o caminho até o backbone.

As cores dos enlaces representam o diagnóstico: verde para atendimento satisfatório, vermelho para investigação de instalação ou defeito, azul para medição satisfatória apesar da previsão e âmbar para revisão de projeto. As conexões internas por cabo não recebem avaliação de propagação. Ao selecionar um equipamento, o visualizador destaca seu caminho até o backbone sem substituir as cores do diagnóstico. Isso permite examinar se o problema está no enlace de acesso ou em outro segmento compartilhado por vários descendentes.

O mesmo resultado pode ser inspecionado em disposição geográfica. A visão lógica facilita acompanhar a hierarquia e as dependências; a geográfica permite relacionar os enlaces às posições cadastradas. Ambas consultam a mesma avaliação. A inspeção individual dos percursos também fica disponível no notebook, para examinar as informações físicas que sustentam a previsão.

## 4 Revisão e confirmação da intervenção

Depois do diagnóstico, o engenheiro abre a revisão de reconfiguração. Os enlaces recomendados aparecem em âmbar, mas não são selecionados automaticamente. É possível selecionar sugestões em conjunto ou clicar em cada enlace. O painel informa o diagnóstico do segmento e mantém uma prévia do efeito da intervenção antes da confirmação.

No modo somente enlace, a conexão escolhida é retirada do cenário seguinte, mas todos os rádios e as demais conexões permanecem. Um equipamento pode perder o caminho até o backbone como consequência dessa escolha, sem que seu próprio enlace com um descendente seja eliminado. Esse comportamento preserva a instalação que deverá ser aproveitada após o replanejamento.

No modo subárvore, a intervenção abrange o enlace escolhido e os enlaces descendentes. Os rádios identificados como repetidores removíveis podem ser retirados, enquanto os equipamentos que precisam de comunicação permanecem como pontos a reconectar. A interface apresenta a seleção de repetidores e considera o inventário de equipamentos associados. Esse procedimento prepara o problema de planejamento incremental apresentado na E10. O estudo desenvolvido nas seções seguintes utiliza somente a remoção de enlaces.

A Figura 2 apresenta a seleção de GE-024 — GE-025. O vermelho tracejado identifica o enlace escolhido para remoção; os demais enlaces recomendados continuam em âmbar. O resumo mostra uma conexão removida, nenhum rádio removido e duas interfaces que perderiam acesso ao backbone. Essas interfaces correspondem a GE-025 e ao seu descendente GE-026. A conexão entre ambos é preservada, embora o conjunto fique temporariamente desconectado da saída da rede.

![Seleção do enlace para remoção](figures/fig02_selecao_enlace.png)

Figura 2 — Prévia da remoção de GE-024 — GE-025 no modo somente enlace. O grafo distingue recomendação e seleção, e o resumo informa o efeito sobre a conectividade.

A confirmação registra uma decisão explícita. O engenheiro pode desfazer itens, revisar o modo de intervenção e conferir o impacto antes de marcar que revisou os enlaces e repetidores destacados. A Figura 3 mostra esse ponto do procedimento com apenas GE-024 — GE-025 selecionado. A imagem ilustra uma escolha individual; a execução completa de validação utilizou quatro enlaces confirmados no modo somente enlace.

![Confirmação da reconfiguração](figures/fig03_confirmacao.png)

Figura 3 — Controles de revisão e confirmação. A seleção identifica o enlace, o modo aplicado e a ausência de remoção de rádios antes de gerar reconfiguration.json.

O arquivo `reconfiguration.json` contém as solicitações confirmadas, os identificadores dos enlaces e dos dispositivos a remover e as referências que vinculam a escolha ao projeto de origem. As listas extensas do que permanece na rede são reconstruídas a partir do projeto. No Jupyter, a confirmação salva o arquivo na pasta do projeto. Na visualização HTML externa, utilizada para capturar a Figura 3, o botão oferece o download; nesse caso, o arquivo deve ser colocado na pasta do projeto antes da preparação.

A revisão produz a solicitação de mudança. A criação do novo projeto ocorre na etapa seguinte, utilizando essa solicitação e preservando os arquivos da rede diagnosticada. Essa separação permite verificar a decisão antes de executar os cálculos de replanejamento e comparar os resultados com a condição original.

## 5 Preparação do projeto e preservação da rede existente

O novo projeto utiliza a rede diagnosticada como origem e recebe a reconfiguração confirmada. A preparação verifica a correspondência entre os arquivos e a escolha do usuário, calcula os custos da rede instalada com a métrica da tecnologia e identifica quais interfaces continuam alcançando o acesso original ao backbone após a retirada dos enlaces.

As interfaces que continuam conectadas são mantidas como pontos de conexão disponíveis, com seus ranks acumulados preservados. O rank expressa o custo do caminho até um acesso ao backbone; os custos dos segmentos são obtidos da métrica configurada para a tecnologia. Assim, conectar um trecho a um ponto intermediário não equivale a criar uma nova saída de custo zero. O caminho já existente acima desse ponto continua participando da avaliação da rede completa.

As interfaces que perderam a saída ficam disponíveis para reconexão. Seus enlaces preservados também permanecem no cenário, mesmo quando pertencem a um componente temporariamente desconectado. No exemplo de GE-025, a retirada do enlace com GE-024 não elimina a conexão com GE-026. O replanejamento trata a ligação retirada e, quando uma proposta é aceita, a consolidação verifica novamente o acesso dos equipamentos ao backbone.

A chamada `project.prepare(...)` gera `nodes.csv` e `scenario.toml` na pasta do novo projeto. O CSV conserva os rádios e atualiza as informações de conectividade e rank. O TOML mantém os enlaces preservados. São salvos também a reconfiguração utilizada, o registro da rede original e o resultado da preparação, permitindo inspecionar a rede antes das novas conexões.

Os parâmetros de busca ficam em `replanning.toml`. O extrato abaixo reproduz a configuração utilizada na validação. Os postes são lidos de um arquivo independente e recebem o perfil de rádio informado apenas quando considerados como candidatos a repetidores.

```toml
[poles]
source = "poles.csv"
device_profile = "ge_radio_1"
mount_height_m = 7.0
corridor_m = 1500.0

[search]
max_repeaters = 5
max_candidates = 3
segment_threshold = 2.0
search_midpoint = true
```

O corredor é a região situada a uma distância máxima do segmento de reta que liga os extremos do enlace. Na demonstração, são oferecidos os postes a até 1.500 m desse segmento, incluindo as proximidades dos extremos. Esse recorte reduz o conjunto de posições disponíveis para cada estudo, sem exigir que todos os postes da área sejam inseridos como rádios na rede.

## 6 Replanejamento dos enlaces e consolidação das propostas

Cada enlace removido dá origem a um estudo entre seus dois extremos. O planejador avalia a conexão direta e procura alternativas com repetidores nas posições disponíveis. A busca utiliza os recursos de planejamento de enlaces e expansão de caminhos, com limites de candidatos e quantidade de repetidores. Os cálculos de propagação reutilizam as features já disponíveis quando as condições do enlace são compatíveis com o registro salvo.

Selecionar um poste para o corredor não significa avaliar todas as suas possíveis conexões. Os postes formam o conjunto de posições disponíveis; os enlaces são avaliados à medida que a busca explora alternativas. O visualizador distingue esses dois conjuntos. Um poste isolado no desenho pode não ter sido explorado, enquanto uma linha representa uma conexão efetivamente avaliada.

A Figura 4 mostra esse resultado para GE-024 — GE-025. O estudo reuniu 575 postes e os dois extremos, totalizando 577 posições, e avaliou 68 enlaces. O painel informa o custo da conexão selecionada e se ela foi expandida durante a procura de repetidores. O desenho permite conferir o que foi testado sem confundir todos os postes disponíveis com alternativas de rádio já calculadas.

![Postes disponíveis e enlaces avaliados](figures/fig04_candidatos.png)

Figura 4 — Grafo do estudo de GE-024 — GE-025. Os pontos representam posições disponíveis e as linhas representam os 68 enlaces avaliados; a proposta selecionada aparece em laranja.

A implementação utilizada na validação procura primeiro caminhos em que todos os segmentos atendam ao custo máximo configurado. Entre esses caminhos, utiliza o custo acumulado para escolher a proposta. Quando nenhum atende ao limite, mantém a melhor proposta encontrada para inspeção, mas não a incorpora à rede consolidada. O custo de cada segmento considera o pior dos dois sentidos de comunicação. O limite é uma condição do estudo e pode ser ajustado pelo usuário.

Nesse exemplo, a proposta de GE-024 — GE-025 permaneceu como enlace direto, com custo aproximado de 3,73, acima do limite de 2 por segmento. Por isso, o enlace continuou pendente na consolidação. A exibição de uma proposta selecionada no estudo não significa que ela foi aceita para o projeto final. O resultado informa a condição de atendimento separadamente, evitando tratar um caminho calculado como uma melhoria já obtida.

A Figura 5 apresenta uma proposta aceita em outro trecho da mesma validação, GE-015 — GE-066. O planejador inseriu um repetidor no poste 917784800 e obteve dois segmentos dentro do limite. O custo total do caminho é aproximadamente 2,93. O valor acumulado pode ser maior que o custo do enlace direto inadequado, pois o critério utilizado exige que cada salto atenda à condição de qualidade.

![Proposta com um repetidor](figures/fig05_proposta_aceita.png)

Figura 5 — Proposta aceita para GE-015 — GE-066, com repetidor no poste 917784800. O painel apresenta o custo do segmento inspecionado e os ranks locais do estudo.

O extremo azul na Figura 5 é a referência do estudo entre dois pontos. Seu rank local é zero para calcular os custos do trecho, sem criar um novo acesso físico ao backbone. Na consolidação, os novos segmentos são combinados com os enlaces preservados e a conectividade é avaliada em relação aos acessos originais. Os ranks finais passam a incluir o percurso completo da rede.

Ao terminar, `project.replan_links(...)` salva as propostas por enlace, o resumo de aceitação e o resultado consolidado em `result.json`. A configuração completa fica em `solution_scenario.toml`, incluindo os repetidores das propostas aceitas. O par `nodes.csv` e `scenario.toml` permanece como entrada preparada; os novos sites são representados na configuração consolidada. A visualização final identifica em cinza os enlaces preservados e em laranja os novos segmentos aceitos, mantendo visíveis os componentes ainda desconectados.

## 7 Validação com AGU S e utilização pelo SDK

A validação utilizou o cadastro da rede AGU-S, suas medições por enlace e os postes disponíveis na região. A rede importada contém 65 rádios e 64 conexões, das quais cinco são internas por cabo. O modelo da E08 foi reutilizado sem novo treinamento. Foram adotados 900 MHz, potência de 30 dBm e altura de 7 m; os ganhos das antenas terminais foram preservados conforme o inventário. O rádio candidato a repetidor utiliza ganho de 8,15 dBi.

A comparação com medições classificou 57 enlaces de rádio: 40 com atendimento satisfatório, dez para investigação de instalação ou defeito, quatro com medição satisfatória apesar da previsão e três para revisão de projeto. Entre os dois enlaces de rádio sem medição utilizável, um recebeu indicação de revisão baseada na previsão e outro apresentou previsão satisfatória. As cinco conexões por cabo completam a topologia e não recebem diagnóstico de RSSI.

Na execução completa foram confirmadas quatro remoções de enlaces, sem retirada de rádios. A preparação preservou 60 conexões e todos os 65 rádios. Desse conjunto, 54 interfaces continuaram conectadas ao backbone e onze ficaram aguardando reconexão. O replanejamento aceitou duas propostas, cada uma com um repetidor: GE-015 — GE-066 e GE-022 — GE-031. A segunda utiliza o poste 923060901 e apresenta custo total aproximado de 2,96, com os dois segmentos dentro do limite.

A rede consolidada contém 67 interfaces, das quais 64 alcançam o acesso original. GE-025 e GE-026 continuam sem serviço porque a proposta GE-024 — GE-025 não atendeu ao limite. A terceira interface pendente corresponde a um registro com suspeita de inconsistência de posição no cadastro. Esse registro demanda conferência e não é utilizado como evidência de desempenho do planejador. Os resultados indicam, portanto, quais intervenções foram aceitas e quais continuam abertas para análise.

Os notebooks `e13_diagnostico.ipynb` e `e13_replanejamento.ipynb` estão disponíveis como exemplos no workspace Jupyter do software. O primeiro importa a rede, extrai e enriquece as features, aplica a avaliação e apresenta a revisão da intervenção. O segundo cria o projeto derivado, executa os estudos e mostra os candidatos, as propostas e a rede consolidada. As figuras deste relatório foram obtidas das visualizações geradas por esses exemplos, com ajuste de tamanho para leitura no documento.

As células dos notebooks utilizam chamadas ao SDK, o kit de desenvolvimento que permite operar os serviços a partir do ambiente Python. A sequência principal do diagnóstico é apresentada a seguir. O objeto `client` fornece o acesso aos serviços configurados no notebook.

```python
project.import_topology()
project.extract_features(client, geo_base_url="http://features:8080")
project.enrich_features()
project.evaluate_links(rssi_threshold_dbm=-80)
project.show_topology(layout="logical", diagnosis=True)
project.review_reconfiguration(layout="logical")
```

Após a revisão e confirmação, o projeto de replanejamento é associado à origem por `LinkReplanningProject`. Na demonstração, o projeto de diagnóstico mantém o nome de pasta `e10_diagnostico`, enquanto os notebooks e o projeto de replanejamento identificam a E13. As chamadas seguintes executam o fluxo descrito no relatório.

```python
source = InstalledNetworkProject("../projects/e10_diagnostico")
project = LinkReplanningProject("../projects/e13_replanejamento", source=source)
project.prepare(client, geo_base_url="http://features:8080")
project.show_prepared_network()
project.replan_links(client, geo_base_url="http://features:8080")
project.show_link_candidates("AGU-S-GE-024__AGU-S-GE-025")
project.show_link_solution("AGU-S-GE-024__AGU-S-GE-025")
project.show_network()
```

A execução integral verificou a preservação dos arquivos de origem, incluindo cenário, instâncias, resultado, features, avaliação, inventário e reconfiguração. Os cálculos e arquivos produzidos pelo replanejamento permaneceram no projeto derivado. A validação também verificou a seleção de caminhos com qualidade suficiente por segmento e a apresentação separada dos candidatos, das propostas e dos resultados aceitos.

## 8 Resultados do desenvolvimento e integração com as demais etapas

A E13 integrou o diagnóstico da rede instalada à preparação e execução de projetos de melhoria. O software passou a oferecer uma sequência verificável entre a identificação de um enlace inadequado, a decisão do engenheiro, o cálculo das alternativas e a consolidação da rede proposta. O registro da reconfiguração e a manutenção do projeto original permitem repetir o estudo e comparar seus resultados com a condição diagnosticada.

O desenvolvimento reutiliza os modelos da E08 para estimar o comportamento dos enlaces e a interpretação de caminhos da E09 para localizar os segmentos que justificam intervenção. Os mecanismos de conectividade e planejamento das etapas anteriores são utilizados para calcular as alternativas. A E07 complementa esse conjunto ao tratar a redundância, inclusive por outras interfaces e tecnologias. A E10 apresenta a aplicação do fluxo ao planejamento incremental, incluindo o caso em que a retirada de uma subárvore libera a região para novo planejamento.

A validação com AGU-S demonstrou a preservação da rede existente, o aproveitamento de postes para propor repetidores e a distinção entre resultados aceitos e pendentes. O caso GE-024 — GE-025 mostrou que o procedimento também precisa representar uma busca sem proposta aceitável: o engenheiro consegue examinar o diagnóstico, a seleção confirmada e as alternativas avaliadas sem receber uma indicação incorreta de que o problema foi resolvido.

O método fornece propostas de projeto nas condições dos dados e parâmetros utilizados. A busca limitada não demonstra a inexistência de outras soluções, e os novos enlaces são previsões que podem ser confrontadas com medições após a implantação. A organização desenvolvida permite incorporar essas observações ao processo de avaliação e manter a relação entre o diagnóstico, a intervenção escolhida e o projeto resultante.
