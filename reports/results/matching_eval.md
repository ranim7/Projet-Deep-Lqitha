# Matching & verification evaluation


## set_A - 40 lost reports vs 40 found reports

| matcher | Hit@1 | Hit@3 | Hit@5 | MRR |
|---|---|---|---|---|
| M0 raw message, dense | 0.325 | 0.575 | 0.675 | 0.4863 |
| M1 + perception normalisation | 0.4 | 0.75 | 0.85 | 0.5902 |
| M2 + hard filters (category, date) | 0.575 | 0.7 | 0.7 | 0.6333 |
| M3 full matcher (+ attribute re-scoring) | 0.675 | 0.7 | 0.7 | 0.6875 |

| language | n | Hit@1 | Hit@3 | MRR |
|---|---|---|---|---|
| derja | 35 | 0.6286 | 0.6571 | 0.6429 |
| fr | 5 | 1.0 | 1.0 | 1.0 |


## set_B - 40 lost reports vs 1540 found reports

| matcher | Hit@1 | Hit@3 | Hit@5 | MRR |
|---|---|---|---|---|
| M0 raw message, dense | 0.375 | 0.45 | 0.475 | 0.4331 |
| M1 + perception normalisation | 0.275 | 0.4 | 0.4 | 0.3562 |
| M2 + hard filters (category, date) | 0.225 | 0.3 | 0.325 | 0.2842 |
| M3 full matcher (+ attribute re-scoring) | 0.375 | 0.375 | 0.425 | 0.3911 |

| language | n | Hit@1 | Hit@3 | MRR |
|---|---|---|---|---|
| ar | 6 | 0.5 | 0.5 | 0.5 |
| derja | 15 | 0.4667 | 0.4667 | 0.4907 |
| en | 4 | 0.75 | 0.75 | 0.75 |
| fr | 15 | 0.1333 | 0.1333 | 0.1522 |


## Verifier v1 vs v2 (top-2 candidates of the full matcher, Set A)

| version | pairs | true_pairs | match_precision | true_pairs_kept (match or possible) | wrong_pairs_called_match | wrong_pairs_rejected | wrong_pairs_possible |
|---|---|---|---|---|---|---|---|
| v1 | 22 | 10 | 1.0 | 0.2 | 0 | 12 | 0 |
| v2 | 22 | 10 | 1.0 | 0.6 | 0 | 10 | 2 |