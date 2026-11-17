# Figuras do relatório E09

As figuras foram preparadas em 27/10/2026 a partir da execução real do notebook
BRO-S, com SDK 0.1.47 e limiar de RSSI de −80 dBm.

- `fig01_topologia_logica.png`: saída HTML de `show_topology` com diagnóstico,
  na vista lógica, com todos os rótulos habilitados.
- `fig02_logical_caminho.png`: a mesma vista com BRO-S-GE-030 selecionado,
  incluindo o sumário do diagnóstico no painel. Para a captura editorial,
  o painel foi limitado ao sumário; os detalhes por enlace continuam no HTML.
- `fig03_geographic_caminho.png`: vista geográfica do mesmo caminho, capturada
  sem o painel lateral para dar espaço à geometria. Não contém mapa de fundo.
- `fig04_inspecao_enlace.png`: imagem real de `link_profile()` para
  BRO-S-GE-017 → BRO-S-GE-027, obtida pelo inspetor utilizado no notebook.

As capturas HTML usam fontes ampliadas para impressão. O prefixo comum BRO-S
foi omitido apenas nos rótulos desenhados no grafo para evitar sobreposição;
os identificadores, conexões, valores e classificações não foram alterados.
O perfil físico foi preservado tal como retornado pelo serviço de features.

A rotina de captura está em `.codex_tmp/e09_docx/capture_figures.cjs` e a
captura do perfil em `.codex_tmp/e09_docx/capture_profile.py`, na raiz do
repositório. A cópia executada do notebook está em `docs/internal/case-studies/e09/executed`.
