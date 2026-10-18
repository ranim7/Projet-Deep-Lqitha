# Agentic RAG vs single-query RAG (12 situations, 4 languages)

| pipeline | need recall@context | context precision | passages | need coverage | cited steps | faithful steps | supported steps | letter type |
|---|---|---|---|---|---|---|---|---|
| Single-query RAG (previous) | 0.799 | 0.708 | 4.0 | 0.868 | 0.729 | 0.522 | 0.268 | - |
| Agentic: decomposition + routed retrieval | 0.972 | 0.607 | 7.333 | - | - | - | - | - |
| Agentic: + LLM re-ranking | 0.972 | 0.823 | 3.833 | - | - | - | - | - |
| Agentic + writer v1 | - | - | - | 0.722 | 0.986 | 0.839 | 0.36 | 0.917 |
| Agentic + writer v2 (production) | - | - | - | 0.944 | 0.955 | 0.863 | 0.374 | 0.917 |

Retrieval metrics (need recall, precision, passages) describe the context given to the writer; generation metrics (coverage, citations, faithfulness) describe the answer delivered to the user (unsupported plan steps removed).
