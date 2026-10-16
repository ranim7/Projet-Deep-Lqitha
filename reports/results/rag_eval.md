# Retrieval evaluation

32 test queries (French, Arabic, Derja, English) with gold documents + key phrases.
Knowledge base: 16 documents, 157,412 characters, embeddings bge-m3 (Ollama, local).

| strategy | n_chunks | avg_chars | min_chars | max_chars |
|---|---|---|---|---|
| structure | 204 | 811 | 82 | 2092 |
| recursive | 196 | 935 | 35 | 1148 |

| chunking | retrieval | item_type filter | Hit@1 | Hit@3 | Hit@5 | MRR | ms/query | Hit@3 ar | Hit@3 derja | Hit@3 en | Hit@3 fr |
|---|---|---|---|---|---|---|---|---|---|---|---|
| structure | dense | no | 0.75 | 0.9375 | 0.9375 | 0.8333 | 2236 | 1.0 | 0.5 | 1.0 | 0.9615 |
| structure | dense | yes | 0.75 | 0.9375 | 0.9375 | 0.8333 | 2222 | 1.0 | 0.5 | 1.0 | 0.9615 |
| structure | hybrid | no | 0.625 | 0.9688 | 0.9688 | 0.7708 | 2241 | 1.0 | 0.5 | 1.0 | 1.0 |
| structure | hybrid | yes | 0.6562 | 0.9375 | 0.9688 | 0.7875 | 2228 | 0.5 | 0.5 | 1.0 | 1.0 |
| recursive | dense | no | 0.8438 | 0.9688 | 1.0 | 0.9125 | 2235 | 1.0 | 0.5 | 1.0 | 1.0 |
| recursive | dense | yes | 0.875 | 1.0 | 1.0 | 0.9375 | 2230 | 1.0 | 1.0 | 1.0 | 1.0 |
| recursive | hybrid | no | 0.6875 | 0.9688 | 0.9688 | 0.8281 | 2242 | 1.0 | 0.5 | 1.0 | 1.0 |
| recursive | hybrid | yes | 0.7188 | 1.0 | 1.0 | 0.8594 | 2226 | 1.0 | 1.0 | 1.0 | 1.0 |
| recursive | dense + query rewriting | no | 0.8125 | 0.9688 | 1.0 | 0.8865 | None | 1.0 | 0.5 | 1.0 | 1.0 |
| recursive | dense + query rewriting | yes | 0.8438 | 1.0 | 1.0 | 0.9115 | None | 1.0 | 1.0 | 1.0 | 1.0 |

**Best configuration:** recursive chunking + dense retrieval, filter=yes (Hit@3 1.0, MRR 0.9375).

## Failures of the best configuration (relevant chunk not in top-3)
