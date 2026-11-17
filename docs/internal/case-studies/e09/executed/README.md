# Execução do exemplo BRO-S

O notebook foi executado em 27/10/2026 no container de desenvolvimento, com
o SDK 0.1.47 instalado a partir do wheel distribuído. As seções de extração
usaram os serviços reais `network-service:8080` e `features:8080`.

Foram importados 25 equipamentos e 24 conexões. A extração gerou 22 entradas
reais no cache de features; duas conexões back-to-back internas não requerem
propagação. O resultado instalado mantém os pais originais e ranks nulos.
A seção 6 gerou `enriched_features.json` com 22 observações medidas pelo enriquecimento da E08 e
excluiu explicitamente os dois registros internos. A repetição da chamada
manteve o arquivo enriquecido inalterado, sem duplicar observações. O cache
puro `features.json` permanece separado e sem observações.
O modelo da E08 foi aplicado com limiar ilustrativo de -80 dBm: 15 enlaces
satisfatórios, dois para investigação de instalação/defeito, quatro com medição
satisfatória apesar da previsão desfavorável e um para revisão de projeto.
As duas conexões internas permanecem sem classificação de RSSI. As vistas
lógica e geográfica coloridas foram verificadas em navegador, incluindo
seleção de caminho, resumo, detalhes individuais e ausência de erros JavaScript.

A construção do inspetor não prepara enlaces. Em verificação adicional foi
acionada a inspeção real do enlace BRO-S-P7-001 → BRO-S-GE-003 e obtido seu
perfil pelo visualizador existente, sem erro. O notebook distribuído mantém
as células sem saídas; esta cópia registra a execução completa.
