# Guia de estudo: Salesforce AI Copilot

Esse guia é pra você entender o projeto de ponta a ponta e conseguir explicar cada decisão numa entrevista. Lê na ordem, roda cada etapa e faz os exercícios. É isso que torna o projeto seu.

---

## 1. A ideia em uma frase

Um assistente que conversa com a sua org Salesforce: ele consulta dados (SOQL), lê o schema dos objetos e procura no código Apex pra responder perguntas em linguagem natural.

## 2. Os 5 conceitos que você precisa dominar

### 2.1 Tool use (function calling)
Um LLM sozinho só gera texto. No **tool use**, você passa pro modelo uma lista de ferramentas (nome, descrição e um JSON Schema dos parâmetros). Quando ele precisa de algo, em vez de responder, devolve um pedido tipo "chama `soql_query` com `{query: ...}`". **Seu código** executa e devolve o resultado, e o modelo continua. Esse vai e volta é o **agent loop** (`sf_copilot/agent.py`).

> Na entrevista: "O modelo não executa nada. Ele só decide qual ferramenta chamar e com quais argumentos. Quem executa é a aplicação, e é aí que entram validação e segurança."

### 2.2 MCP (Model Context Protocol)
Um **padrão aberto** pra expor ferramentas a qualquer cliente de IA. Pensa num "REST pra ferramentas de IA": você escreve o servidor uma vez (`mcp_server.py`) e ele funciona no Claude Desktop, no Claude Code, no Cursor etc. A comunicação aqui é via **stdio**: o cliente sobe o processo `sf-copilot serve` e troca mensagens JSON-RPC pela entrada e saída padrão.

Três peças do MCP que aparecem no código:
- **Tools**: funções que o modelo pode chamar (`@server.tool`)
- **Annotations**: metadados como `read_only_hint`, que avisam ao cliente se a tool só lê ou também escreve
- **Instructions**: um texto que o servidor manda pro cliente explicando como usar as tools

### 2.3 Embeddings
Um modelo transforma texto num vetor de números, tipo `[0.12, -0.03, ...]`, em que **textos com significado parecido ficam próximos**. "enviar e-mail de fatura" e "sendInvoiceEmail" acabam perto um do outro, mesmo sem nenhuma palavra igual. O Chroma usa por padrão o modelo `all-MiniLM-L6-v2`, que roda local e é gratuito.

### 2.4 Vector database
É onde ficam os vetores. Ele responde a pergunta "quais são os N vetores mais parecidos com este?" usando **similaridade de cosseno**. Aqui é o **ChromaDB**, salvo na pasta `.chroma/`.

### 2.5 RAG (Retrieval-Augmented Generation)
1. **Indexar** (`sf-copilot index`): busca o código Apex, quebra em pedaços (chunks), gera os embeddings e salva.
2. **Recuperar**: na hora da pergunta, gera o embedding dela e busca os chunks mais parecidos.
3. **Gerar**: o modelo responde usando esses chunks como contexto, citando arquivo e linhas.

Por que RAG em vez de colar o código inteiro no prompt? Porque uma org tem centenas de classes, que não cabem no contexto, e mesmo se coubessem sairia caro e lento. O RAG manda só o que é relevante.

## 3. Arquitetura

```
você ──► agent.py (Claude) ──tool calls──► mcp_server.py ──► tools.py ──► Salesforce
                                                   └──────► rag.py ──► ChromaDB
```

Decisão importante: **o agente não define as tools, ele pede a lista pro servidor MCP** (`server.list_tools()`). Uma única fonte da verdade, que é exatamente como o Claude Desktop funciona.

## 4. Tour pelo código (lê nessa ordem)

1. **`tools.py`**: lógica pura. Repara que toda função recebe `sf` como parâmetro. Isso é **injeção de dependência**: nos testes a gente passa um Salesforce falso.
   - `run_soql`: só aceita `SELECT` e adiciona `LIMIT` se faltar. Por quê? Uma query sem limite pode trazer 50 mil registros e estourar o contexto do modelo.
   - `_clean`: remove o campo `attributes` que o Salesforce coloca em todo registro. É ruído que só gasta token.
   - `describe_object`: devolve um schema **compacto**. Um describe cru do Account tem centenas de KB.
   - `create_record` / `update_record`: **human in the loop**. Sem `confirm=true` só mostram uma prévia, e com `SF_ALLOW_WRITES=false` nem isso.
2. **`salesforce.py`**: autenticação. O jeito mais prático pra dev é reaproveitar o login do Salesforce CLI (`sf org display --json`).
3. **`rag.py`**:
   - `fetch_apex_sources`: query na **Tooling API** (`ApexClass.Body`), já tratando paginação (`nextRecordsUrl`).
   - `chunk_code`: janelas de 60 linhas com 10 de sobreposição. A sobreposição evita cortar um método no meio e perder o contexto. Cada chunk guarda as linhas, pra citação.
4. **`mcp_server.py`**: registra as tools. Repara no `model_visible_errors`: o MCP esconde o texto de erros inesperados (segurança), mas um erro tipo "No such column 'Foo'" **precisa** chegar ao modelo, pra ele corrigir a query sozinho.
5. **`agent.py`**: o loop. Chama o Claude e, se `stop_reason == "tool_use"`, executa as tools, devolve os `tool_result` e repete. Tem um limite de 10 rodadas pra não entrar em loop infinito.
6. **`tests/`**: 20 testes sem internet. O `HashEmbedding` é um embedding falso e determinístico, e o `FakeMessages` simula o Claude.

## 5. Rodando passo a passo

1. Cria uma **Developer Edition grátis**: https://developer.salesforce.com/signup
2. Instala o Salesforce CLI e faz login: `sf org login web --alias my-dev-org`
3. Cria umas classes Apex de exemplo na org (ou faz deploy de um projeto seu), pra ter o que indexar.
4. Sobe o projeto:
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   cp .env.example .env    # coloca SF_TARGET_ORG e ANTHROPIC_API_KEY
   pytest                  # tudo verde?
   sf-copilot index
   sf-copilot chat
   ```
5. Pergunta coisas como: "quais objetos custom eu tenho?", "quantas contas por indústria?", "onde no código a gente valida CPF?"
6. Depois conecta no Claude Desktop (config no README).

## 6. Exercícios: é aqui que o projeto vira seu

Faz um por vez, com um commit pra cada. Isso também deixa o histórico do GitHub vivo.

1. **Fácil**: adiciona uma tool `count_records(object_name, where)` que usa `SELECT COUNT()`.
2. **Fácil**: no `chat`, imprime quantos tokens cada resposta gastou (`response.usage`).
3. **Médio**: indexa também os **Flows**. Dica: query de `FlowDefinitionView` / metadata na Tooling API.
4. **Médio**: busca híbrida. Junta a busca vetorial com uma busca por palavra exata no nome da classe e compara os resultados.
5. **Médio**: escreve um **eval**, uma lista de 10 perguntas com a resposta esperada e um script que roda o agente e mede quantas ele acerta. Saber avaliar é o que mais diferencia um AI engineer.
6. **Avançado**: troca o transporte de stdio por `streamable-http` e faz deploy num servidor.
7. **Avançado**: compara o projeto com o **Agentforce**. O que o Agentforce já faz pronto? Onde uma solução própria faz sentido? Isso dá um ótimo post no LinkedIn.

## 7. Perguntas de entrevista (treina responder em voz alta, em português E em inglês)

**O que é MCP e por que usar?**
Um protocolo padrão pra expor ferramentas e dados a modelos de IA. Escrevo o servidor uma vez e qualquer cliente compatível usa, sem integração customizada pra cada um.

**Como funciona o seu RAG?**
Busco o código Apex pela Tooling API, quebro em chunks de 60 linhas com sobreposição, gero embeddings e salvo no ChromaDB. Na pergunta, busco os chunks mais similares por cosseno e o modelo responde citando arquivo e linha.

**Como você evita que a IA faça besteira na org?**
SOQL só leitura, LIMIT automático, escrita desligada por padrão, prévia antes de qualquer escrita com confirmação humana, validação de nomes e Ids, e annotations MCP marcando o que é escrita.

**Como você testa algo que depende de LLM?**
Separei a lógica em funções puras com dependências injetadas. Nos testes, o Salesforce, o Claude e o modelo de embedding são fakes, e eu testo o loop do agente inteiro, inclusive erro voltando pro modelo. Pra qualidade das respostas o caminho é eval (exercício 5).

**Qual o limite do projeto?**
Chunk por número de linhas pode cortar um método no meio (o ideal seria chunk por método). Só indexa Apex. Não tem eval de qualidade ainda. Saber dizer isso mostra maturidade.

**English version, practice out loud:**
- *"It's an MCP server that exposes a Salesforce org as tools: SOQL queries, schema describes and record updates, plus a RAG pipeline over the org's Apex code."*
- *"Writes are disabled by default, and every write returns a dry-run preview first, so there's always a human in the loop."*

## 8. Como colocar no LinkedIn e no CV

Só coloca o que você consegue explicar. Sugestão depois de rodar e fazer pelo menos 2 exercícios:

> **Salesforce AI Copilot** (Python, MCP, RAG, ChromaDB, Claude API): servidor MCP que expõe uma org Salesforce como ferramentas para agentes de IA, com RAG sobre código Apex via Tooling API, guardrails de segurança (SOQL somente leitura, confirmação humana para escrita) e 20+ testes automatizados com CI.
