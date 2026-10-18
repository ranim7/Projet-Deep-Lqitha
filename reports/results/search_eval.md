# Found-items search (13 queries, Derja / French / English, pool of 209 open found items)

| configuration | Hit@1 | Hit@3 | MRR | empty results |
|---|---|---|---|---|
| R0 dense only | 0.9231 | 0.9231 | 0.9359 | 0.0 |
| R1 dense + strict filters | 0.6154 | 0.6154 | 0.6154 | 0.231 |
| R2 dense + filters with relaxation | 0.8462 | 0.8462 | 0.8462 | 0.0 |
| R3 relaxation + hybrid score | 0.7692 | 0.7692 | 0.7821 | 0.0 |
| R4 dense + soft constraints (production) | 0.8462 | 0.9231 | 0.8846 | 0.0 |
| R5 soft + attribute agreement | 0.8462 | 0.9231 | 0.8846 | 0.0 |
| G1 R3 + answer agent v1 | 0.8462 | 0.8462 | 0.8462 | - |
| G2 R3 + answer agent v2 | 0.8462 | 0.8462 | 0.8462 | - |
| G3 R4 + answer agent v2 (production) | 0.8462 | 0.9231 | 0.8846 | - |

- n_queries: 13
- pool: 209
- intent_accuracy: 0.923
- recall@8 (production retrieval): 0.923
- precision of 'high' labels (v2): 0.9
