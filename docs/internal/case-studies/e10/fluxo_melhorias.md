# Decisões para preparação de melhorias

O diagnóstico e10_diagnostico indica os enlaces para revisão de projeto. Enlaces para inspeção de instalação ou defeito não entram automaticamente como demandas de replanejamento. Um enlace sem medição deve manter essa condição visível na sugestão.

O inventário attached_equipment.csv identifica quais rádios servem equipamentos associados. Esses pontos devem continuar atendidos. A ausência de associação não estabelece removibilidade: o tipo do rádio no inventário e a revisão do engenheiro também importam. AGU-S-GE-058 permanece com associação desconhecida.

As duas intervenções propostas são:

1. Reparar enlace: retirar apenas o enlace selecionado, preservar as conexões dos descendentes e usar LinkPlanner para buscar nova ligação com os extremos definidos pelo engenheiro. Toda a subárvore perde acesso enquanto a ligação está aberta; seus filhos não viram acessos fixos apenas porque seus enlaces foram preservados.
2. Replanejar subárvore: retirar também as conexões internas dos descendentes, manter os equipamentos obrigatórios como demandas e permitir reorganizar os repetidores no planejamento mesh incremental por rodadas. Somente a parte preservada representa a fronteira já conectada.

A lista de sugestões deve explicar diagnóstico, evidência e impacto potencial de cada enlace. A lista de solicitações deve registrar os enlaces escolhidos pelo engenheiro e o modo por enlace. Nenhuma intervenção deve ser aplicada automaticamente por existir na lista de sugestões. Sobreposições entre solicitações de subárvores precisam ser consolidadas antes da execução.

Estado atual: inventário e destaque de equipamentos associados implementados no SDK 0.1.51; revisão interativa e arquivo de reconfiguração implementados no SDK 0.1.52. O usuário escolhe o modo por enlace, revisa o impacto no grafo, identifica repetidores removíveis e confirma o download. O SDK valida o arquivo e recalcula seu impacto antes do uso. A ausência de inventário não autoriza remover um rádio. Consulte `docs/releases/0.1.52-sdk.md` para o fluxo e o contrato do JSON.

Ainda falta gerar um novo projeto a partir da reconfiguração confirmada, preservar os custos e ranks do trecho instalado e integrar a reutilização dos enlaces à construção do grafo candidato. O planejador mesh por rodadas também permanece pendente. Nenhum enlace foi removido do projeto original.
