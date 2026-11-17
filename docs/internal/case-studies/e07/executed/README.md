# E07 — Notebooks executados com SDK 0.1.39

Os dois notebooks foram executados no contêiner com o SDK instalado em
site-packages e os serviços reais de planejamento/features. As cópias nesta
pasta preservam as saídas. As fontes sem saídas ficam em examples/notebooks,
com os mesmos nomes. Os dois notebooks contêm apenas chamadas ao SDK e grafos.

## Demonstração atual

O primeiro projeto define a rede GE instalada nos 13 sites do example_1.
O segundo substitui Barigui por um perfil GE + celular, conectado com custo
inicial 1.0, e desabilita somente a interface GE de Torre.

A receita extension_fixed_links.toml utiliza topology.mode="installed": somente
os enlaces selecionados no primeiro projeto podem ser usados. Enlaces incidentes
à interface de Torre são retirados; nenhuma nova conexão GE é criada.

Resultados da execução:

- Original: 13 interfaces, 33 candidatos, 13 interfaces na solução e 11 enlaces.
- Redundância: 14 interfaces cadastradas, 13 ativas, uma desabilitada (Torre),
  oito enlaces candidatos instalados, 11 interfaces na solução e oito enlaces.
- Barigui mantém suas interfaces GE e celular ativas. Seus seis descendentes
  (copel, decatlon, deneka, saturno, shopping e woodland) preservam os pais GE.
- Tenis e Cemiterio ficam sem caminho, pois pertencem a outros ramos de Torre.
- Itallia continua sendo um acesso conectado independente, atendendo seu ramo.

As verificações da execução confirmaram que os enlaces resultantes pertencem à
solução original e que os pais dos descendentes de Barigui não mudaram.
O inventário desabilitado aparece em cinza no grafo. A classificação magenta de
qualidade é distinta da ausência de caminho, em vermelho. Não há teste de RF da
operadora, falha física de Barigui ou comutação automática de pais GE.
