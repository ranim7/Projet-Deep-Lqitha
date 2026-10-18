# Prompt evaluation - v1 vs v2

Model: openai/gpt-oss-120b (vision: qwen/qwen3.8-27b), Groq free tier.


## intake

| version | n_cases | json_valid | intent | intent_semantic (lenient) | next_action | language | one_question | category | date | no_color_q_with_photo | all_checks_passed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| v1 | 30 | 1.0 | 0.067 | 0.7 | 0.0 | 0.6 | 0.9 | 0.0 | 0.0 | 1.0 | 0.0 |
| v2 | 30 | 1.0 | 0.967 | 0.967 | 0.867 | 0.967 | 0.867 | 0.944 | 1.0 | 0.75 | 0.7 |
| v3 | 30 | 1.0 | 1.0 | 0.933 | 0.867 | 1.0 | 0.967 | 0.944 | 1.0 | 0.75 | 0.733 |


## perception

| version | n_images (paired, Qwen-VL) | json_valid | category | color_family | brand_given | english_output | brand_wrong_when_given | privacy_no_number_leak | text_category | text_english_normalised |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | 10 | 1.0 | 0.0 | 0.429 | 0.5 | 1.0 | 0.4 | 0.0 | 0.333 | 1.0 |
| v2 | 10 | 1.0 | 0.8 | 0.714 | 0.4 | 1.0 | 0.25 | 1.0 | 1.0 | 1.0 |


## claim_check

| version | accuracy | false_accepts (security) | false_rejects (usability) | n |
|---|---|---|---|---|
| v1 | 0.688 | 0 | 5 | 16 |
| v2 | 1.0 | 0 | 0 | 16 |


## notifier

| version | n | valid_json | question_present | secret_leaked_in_question | yes_no_questions | leaks_after_n8n_guard |
|---|---|---|---|---|---|---|
| v1 | 10 | 0 | 10 | 10 | 0 | 0 |
| v2 | 10 | 10 | 10 | 2 | 0 | 0 |


## procedure

| version | citation_rate_in_scope | out_of_scope_admitted | answers_with_invented_numbers | n |
|---|---|---|---|---|
| v1 | 0.0 | 1/2 | 2 | 8 |
| v2 | 1.0 | 2/2 | 0 | 8 |
