# Validação do diagnóstico AGU-S — 28/10/2026

O notebook e10_diagnostico reutiliza somente as funções existentes da E09, com os parâmetros confirmados pelo usuário: 900 MHz, potência 30 dBm, altura 7 m e modelo da E08. O teste do notebook com o microserviço em processo e features controladas passou. Isso valida o fluxo e os formatos; não constitui diagnóstico real da rede.

A execução com os serviços reais network-service:8080 e features:8080 importou a topologia e gerou os mapas lógico e geográfico. Na extração de propagação, parou no enlace AGU-S-P7-001:d0:i0 → AGU-S-GE-018:d0:i0. prepare_profiles retornou: index 1 is out of bounds for axis 1 with size 1.

Requisição: tx=(-25.988,-49.3342), rx=(-26.00089,-49.33383), tx_ha=7, rx_ha=7, freq_mhz=900. O cache local preservou nove extrações válidas. Não houve alteração de coordenadas, remoção do enlace, produção de features artificiais ou alteração do SDK/microserviços para contornar a falha.

O código existente do features-service, cisei_lib/dem/dem_utils.py, calcula resolução projetada acessando X_m[0,1] e X_m[1,0]. Um recorte de uma única coluna/linha é uma hipótese compatível com o erro observado, ainda sem stack trace interno da rota, que devolve apenas a mensagem da exceção.

A cópia e10_diagnostico.interrupted.ipynb preserva a execução interrompida. O notebook distribuído em examples/notebooks não tem saídas salvas. A classificação real dos enlaces e as vistas coloridas ainda não foram produzidas. O software foi mantido intacto, conforme o escopo solicitado.

Fonte do inventário: bhnodes_geo.json, SHA256 2211d043b5adee307f6337276fce4a798a13a12bc2c6f28360c8c1a1eac5fcb4. Modelo compartilhado: e08_copel_rssi.json, SHA256 637e4760b283faa8b5d7eebea69a1a693b84c73aa789dca60eaca3e9bdffc1fd.
