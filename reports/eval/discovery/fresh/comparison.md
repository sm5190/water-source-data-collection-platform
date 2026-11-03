# Fresh discovery: baseline (e0c8320) vs improved (Milestone 3)

Discovery-only runs (real discover + acquire nodes, every field open each round, no extraction or model calls), A-B-B-A per city in one session, same providers and budgets. The improved arm used the drafted city identity (as if approved). Labels and utility expectations are UNCONFIRMED drafts unless stated, so every figure below is provisional. Pooled recall = relevant documents found / relevant documents in the city's pool (everything any run acquired + the stored runs' labelled documents), not absolute recall.

| run | searches | site: (non-serving host) | docs OK | about elsewhere | relevant / irrelevant / uncertain / unlabelled | precision | pooled recall | steering hosts | registry verifies (new rules, this evidence) |
|---|---|---|---|---|---|---|---|---|---|
| Houston-baseline-1 | 29 | 0 (0) | 8 | 0 | 5 / 2 / 1 / 0 | 5/7 | 5/27 | — | correct [], wrong [] |
| Houston-baseline-2 | 33 | 4 (4) | 17 | 0 | 5 / 12 / 0 / 0 | 5/17 | 5/27 | mywater.us, account.mywater.us, nhcrwa.com | correct [], wrong ['NFBWA Internal Distribution Water'] |
| Houston-improved-1 | 33 | 0 (0) | 12 | 0 | 1 / 8 / 3 / 0 | 1/9 | 1/27 | — | correct [], wrong [] |
| Houston-improved-2 | 33 | 0 (0) | 16 | 0 | 11 / 4 / 1 / 0 | 11/15 | 11/27 | — | correct [], wrong [] |
| Santiago-baseline-1 | 34 | 0 (0) | 5 | 0 | 0 / 2 / 3 / 0 | 0/2 | 0/10 | fondodeaguasantiago.cl | correct [], wrong [] |
| Santiago-baseline-2 | 38 | 4 (4) | 9 | 2 | 3 / 5 / 1 / 0 | 3/8 | 3/10 | fondodeaguasantiago.cl, aguasdesantiago.com.ar, pozosdeagua.org | correct [], wrong [] |
| Santiago-improved-1 | 38 | 0 (0) | 7 | 0 | 3 / 0 / 4 / 0 | 3/3 | 3/10 | — | correct [], wrong [] |
| Santiago-improved-2 | 37 | 0 (0) | 4 | 0 | 0 / 1 / 3 / 0 | 0/1 | 0/10 | — | correct [], wrong [] |
