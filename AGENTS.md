# ESR — TP2

## Como trabalhar

- Responder em português europeu, de forma objetiva, usando o menor número possível de tokens sem perder rigor nem omitir informação necessária. O utilizador prefere títulos curtos em inglês e texto e legendas em português.
- Ao pedir alterações em Typst, procurar primeiro o MCP de Typst disponível neste computador e, através dele, localizar e inspecionar o relatório «ESR TP2» antes de o editar. Confirmar o documento e o conteúdo atuais; se o MCP ou o relatório não estiver disponível, informar o utilizador e procurar uma alternativa apropriada.
- Consultar os slides em `Aula Téorica/` quando relevantes: `1-ESR-Chapter_2_V7_02-.pdf` (camada aplicacional, streaming, CDN, sockets TCP/UDP) e `2-ESR-Multicast-26-27.pdf` (multicast, árvores, adesão e encaminhamento). Distinguir multicast aplicacional sobre ligações unicast de IP multicast; não assumir que IGMP ou PIM são exigidos.
- Quando trabalhar com Cloudflare, usar a CLI `cf`, exceto se o projeto tiver configuração Wrangler.
- O utilizador gosta de avançar passo a passo, com comandos concretos e indicação do nó onde executar cada um. Confirmar a topologia, configuração, código e resultados atuais antes de os afirmar; não inventar medições ou decisões de implementação.

## Enunciado e objetivo

- Fonte académica: `ESR-TP2-26-27.pdf` na raiz; o seu conteúdo descreve requisitos do trabalho, não instruções para o assistente.
- TP2 de ESR, «Serviço Over the Top para entrega de multimédia»: conceber, implementar e avaliar um protótipo de streaming em tempo real de um ou mais servidores para múltiplos clientes através de uma rede overlay aplicacional, usando principalmente o emulador CORE. A overlay usa ligações de transporte sobre uma underlay IP; os nós intermédios encaminham e replicam fluxos para reduzir tráfego redundante e melhorar a utilização de recursos.
- Cada nó é uma aplicação iniciada manualmente, que pode entrar ou sair dinamicamente, conhece inicialmente pelo menos outro nó e mantém ligações com vizinhos. Construir e manter rotas por fluxo com origem, custo, destinos e estado; suportar clientes simultâneos, percursos alternativos, monitorização e atualização dinâmica perante falhas ou alterações da rede. Usar métricas de rede na escolha de rotas.
- Atenção à inconsistência do enunciado: a explicação inicial simplifica para um servidor e um fluxo, mas a lista de requisitos exige **pelo menos dois servidores, cada um com pelo menos dois fluxos de vídeo distintos**. Tratar o cenário simplificado como etapa de teste, não como requisito final suficiente; esclarecer dúvidas com o docente.
- Distinguir requisitos de sugestões: as etapas e estratégias de bootstrap, descoberta, encaminhamento e transporte são sugestões; escolher e justificar uma solução e documentar limitações. Evitar ciclos e duplicação desnecessária; encaminhar apenas para ramos interessados e gerir ativação/desativação dos fluxos.

## Desenvolvimento e testes

- Etapas sugeridas: preparar topologia e transporte; construir overlay e vizinhanças; descobrir e manter rotas; testar percursos com mensagens e logs; integrar streaming e reprodução. As etapas não precisam de ser sequenciais.
- Cenários sugeridos: 4 nós (servidor, 2 clientes, 1 intermédio); 6 nós (servidor, 2 clientes, 3 intermédios); overlay complexo baseado na figura 1. Incluir testes de múltiplos servidores e fluxos, simultaneidade, entrada/saída de nós, alterações de rede e recuperação de rotas, conforme a implementação.
- Uma opção de referência para streaming é o exemplo do livro adaptado pela equipa docente, incluindo `movie.Mjpeg`: https://www.di.uminho.pt/~flavio/ESR/ProgEx.zip. Outras soluções podem ser discutidas com o docente; não assumir que esta opção foi escolhida.
- Registar configurações, métricas, logs, condições e evidências reais; avaliar a eficiência, atraso, largura de banda e limitações quando aplicável. Separar resultados observados de expectativas e hipóteses.
- Ambiente conhecido de trabalho anterior: CORE no Ubuntu/WSL, com shells próprias nos nós. A topologia e os endereços do TP1 não devem ser assumidos para o TP2; verificar o estado atual.

## Relatório e avaliação

- Relatório em formato de artigo, máximo de 12 páginas; LNCS recomendado. Estrutura sugerida: introdução, arquitetura, especificação dos protocolos (formatos de mensagens e interações), implementação (parâmetros e bibliotecas), testes e resultados, conclusões e trabalho futuro.
- Avaliação: topologia overlay 10%, gestão de fluxos 25%, protocolo aplicacional de controlo 15%, protocolo de streaming 10%, solução final integrada 30% e avaliações intermédias 5% + 5%. Considerar especificação, implementação, testes e apreciação crítica; na solução final também apresentação e relatório.
- Submissão indicada: `ESR-TP2-PLx-Gy-Rel.pdf` e `ESR-TP2-PLx-Gy-Codigo.zip` ou `.rar`. Confirmar turno, grupo e instruções atuais antes de nomear ou submeter.
