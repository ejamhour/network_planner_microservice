# E07 — GE instalado e redundância celular

Execute notebooks/e07_01_projeto_original.ipynb e depois
notebooks/e07_02_projeto_redundancia.ipynb com SDK 0.1.39+.

A receita ativa é extension_fixed_links.toml. Ela substitui somente Barigui pelo
perfil ge_mesh_cellular_gateway, com interfaces ge_mesh_radio e cellular_public.
A interface GE continua na primeira posição, preservando seu ID instalado.
O dispositivo torna-se conectado com rank inicial 1.0; ambas as interfaces herdam
essa condição, conforme o modelo atual. A rede da operadora não é planejada.

O override desabilita somente torre:d0:i0. Os demais sites continuam habilitados.
No modo topology.mode = "installed", o SDK herda os enlaces selecionados no
resultado original, exclui aqueles incidentes à interface desabilitada e elimina
as regras de geração geométrica. O serviço avalia apenas os enlaces sobreviventes.
Os pais GE dos descendentes de Barigui permanecem os originais.

O notebook contém somente chamadas ao SDK e grafos, sem tabelas ou algoritmos
locais. Torre aparece em cinza, nós sem caminho em vermelho e qualidade abaixo
do limiar em magenta. Selecione Barigui para consultar suas duas interfaces e
explorar seus descendentes. A interface celular usa medium="external" e campos
RF nulos (0.0), apenas para satisfazer o formato; não gera candidatos RF.

A preparação sobrescrita arquiva os arquivos gerados anteriores em
preparation_backups e preserva o projeto original. A receita tem novo nome para
ser recebida pelo prepare sem substituir receitas editadas do exemplo anterior.
