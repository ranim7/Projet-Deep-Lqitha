# Board translator - BLEU / ROUGE by round-trip translation (30 found items)

Each translation is translated back into English by an independent model and compared with the original English description (the reference). Scores on lower-cased, accent-free words; BLEU-4 is a corpus score (x100).

| language | system | translated | BLEU-4 | BLEU-4 (sacreBLEU) | ROUGE-1 F1 | ROUGE-L F1 | copied English words | right script/language |
|---|---|---|---|---|---|---|---|---|
| fr | S0 local fallback (gemma3:4b) | 30/30 | 25.0 | 23.2 | 0.631 | 0.597 | 0.188 | 1.0 |
| fr | S1 gpt-oss-120b, no style guide | 30/30 | 31.3 | 22.5 | 0.676 | 0.635 | 0.214 | 1.0 |
| fr | S2 gpt-oss-120b + style guide (production) | 30/30 | 32.9 | 20.0 | 0.651 | 0.628 | 0.19 | 1.0 |
| derja | S0 local fallback (gemma3:4b) | 29/30 | 11.6 | 4.2 | 0.417 | 0.371 | 0.36 | 0.967 |
| derja | S1 gpt-oss-120b, no style guide | 30/30 | 25.6 | 12.3 | 0.599 | 0.577 | 0.485 | 1.0 |
| derja | S2 gpt-oss-120b + style guide (production) | 30/30 | 19.7 | 8.3 | 0.499 | 0.491 | 0.339 | 1.0 |
