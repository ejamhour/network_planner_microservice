# Execução da demonstração E08

`e08_baseline_tecnologia.ipynb` é a cópia executada do notebook distribuído,
validada em 26 de outubro de 2026 com o SDK 0.1.42 no contêiner.
O notebook distribuído em `examples/notebooks` permanece sem saídas.

O cache padrão foi preparado manualmente a partir do arquivo existente
`all_chunks.json`, fora do SDK. Os dados originais são os enlaces da Fase 1
do projeto Copel. A primeira operação do notebook associa as medições ao
`features.json` existente. Nenhuma extração geográfica foi solicitada.

O cache mantém 2.468 entradas. Foram associadas 2.278 observações com os campos
obrigatórios; 190 registros sem ganhos numéricos foram explicitamente excluídos
na preparação manual, conforme `data/e08/preparation_report.json`.

O treinamento reproduziu os coeficientes e o erro absoluto médio registrados
no notebook original: 1.890 registros no ajuste final e erro de 8,6198685095 dB.
O limiar ilustrativo de -80 dBm classificou 157 enlaces para investigação,
1.463 como satisfatórios, 175 com medição satisfatória apesar da previsão
desfavorável e 95 para revisão do projeto.

Os resultados e HTML interativos estão em `examples/projects/e08_baseline_copel`;
o modelo JSON e a métrica TOML estão em `examples/metrics`.

A seção opcional de inspeção de divergências foi adicionada ao notebook.
A célula `requires-features-service` foi omitida desta execução para validar
o fluxo offline. A seleção, a ordenação e a integração com
o painel existente foram testadas com uma sessão simulada, incluindo os
parâmetros geográficos enviados para a inspeção do enlace escolhido.

A versão instalada da wheel foi utilizada na execução. Os 71 testes de
baseline, cache, lotes, visualização, preparação e distribuição passaram.
