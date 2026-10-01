# Evaluation results: finding duplicates and related requests

Generated 2026-10-01 01:03 UTC by `make eval` (`python -m ai.evals.run`). Regenerate it after changing a prompt, model, embedding model or threshold.

## What this measures

The `find_related` step of the triage workflow, run on every card of the seeded data in a throwaway database: the similarity search proposes candidates, the `judge_duplicate` prompt labels each as duplicate / related / distinct with a confidence, and pairs at or above the link threshold would become suggested links. That is compared with `data/seed/labels.json`: 5 groups of true duplicates (7 duplicate pairs), 3 related-but-distinct pairs, and every other pair of cards on the same board counted as distinct.

**How far to trust it.** There are only 7 duplicate and 3 related pairs, so one pair is 14% of duplicate recall; treat differences of a pair or two as noise. The seed requests were written by hand for this purpose. A single run of a language model at temperature 0 is close to, but not exactly, repeatable. These numbers say whether the workflow is wired up and roughly how good it is; they do not predict accuracy on real customer data.

## Mock mode

| Setting | Value |
|---|---|
| Judge | `mock` with prompt `judge_duplicate@2` |
| Embeddings | `mock-hash-256` |
| Candidates per card (`top_k`) | 5 (similarity at least 0.2) |
| Link threshold / merge threshold | 0.6 / 0.85 |
| Cards run | 30; 10 pairs judged; 219 labelled pairs scored |
| Time | 5 s |

| Class | Precision | Recall | F1 | Correct | Predicted | Actual |
|---|---|---|---|---|---|---|
| duplicate (linked) | n/a | 0.00 | n/a | 0 | 0 | 7 |
| duplicate, merge-grade | n/a | 0.00 | n/a | 0 | 0 | 7 |
| related (linked) | n/a | 0.00 | n/a | 0 | 0 | 3 |

Retrieval recall (labelled pairs the similarity search surfaced at all, before any judging): duplicates **0.00**, related **0.33**. False merges (a confident duplicate verdict on a pair that is not a duplicate): **0**.

Confusion matrix (rows: truth, columns: what the workflow did with the pair):

| truth \ workflow | duplicate | related | none | not_retrieved |
|---|---|---|---|---|
| **duplicate** | 0 | 0 | 0 | 7 |
| **related** | 0 | 0 | 1 | 2 |
| **distinct** | 0 | 0 | 4 | 205 |

`none` = looked at but not linked (judged distinct, or below the link threshold); `not_retrieved` = never looked at.

Sensitivity to the link threshold (the same judgements scored at other thresholds; the merge threshold stays at least as high):

| Link threshold | Duplicate precision | Duplicate recall | Related precision | Related recall | Links suggested | Wrong links |
|---|---|---|---|---|---|---|
| 0.3 | n/a | 0.00 | n/a | 0.00 | 0 | 0 |
| 0.4 | n/a | 0.00 | n/a | 0.00 | 0 | 0 |
| 0.5 | n/a | 0.00 | n/a | 0.00 | 0 | 0 |
| 0.6 | n/a | 0.00 | n/a | 0.00 | 0 | 0 |
| 0.7 | n/a | 0.00 | n/a | 0.00 | 0 | 0 |
| 0.8 | n/a | 0.00 | n/a | 0.00 | 0 | 0 |

Choosing a threshold from this table fits it to these same 10 labelled pairs, so the best-looking row is optimistic. Use it to see the trade-off, then confirm on data the model has not been tuned against.

Labelled pairs the workflow got wrong or missed:

- **duplicate** #1 Support SAML single sign-on / #2 Let our employees log in with their Okta accounts: not retrieved by similarity search
- **duplicate** #1 Support SAML single sign-on / #3 Azure AD / Entra sign-in for our whole org: not retrieved by similarity search
- **duplicate** #2 Let our employees log in with their Okta accounts / #3 Azure AD / Entra sign-in for our whole org: not retrieved by similarity search
- **duplicate** #4 Export report tables to CSV / #5 Get dashboard numbers into Excel for my monthly review: not retrieved by similarity search
- **related** #4 Export report tables to CSV / #10 Email reports automatically every Monday: not retrieved by similarity search
- **duplicate** #6 Dark mode / #7 A night theme, the bright UI is tiring for our late-shift operators: not retrieved by similarity search
- **duplicate** #8 Audit log of admin actions / #9 Need a history of who changed what, for our SOC 2 audit: not retrieved by similarity search
- **related** #8 Audit log of admin actions / #11 Custom permission roles beyond Admin and Member: judged distinct at 0.59
- **duplicate** #19 Slack integration for alerts / #20 Post notifications to our team chat channel: not retrieved by similarity search
- **related** #19 Slack integration for alerts / #21 Webhooks for record events: not retrieved by similarity search

## Real mode

| Setting | Value |
|---|---|
| Judge | `llama3.2:3b` with prompt `judge_duplicate@2` |
| Embeddings | `nomic-embed-text-v2-moe:latest` |
| Candidates per card (`top_k`) | 5 (similarity at least 0.2) |
| Link threshold / merge threshold | 0.6 / 0.85 |
| Cards run | 30; 150 pairs judged; 219 labelled pairs scored |
| Time | 128 s |

| Class | Precision | Recall | F1 | Correct | Predicted | Actual |
|---|---|---|---|---|---|---|
| duplicate (linked) | 1.00 | 0.43 | 0.60 | 3 | 3 | 7 |
| duplicate, merge-grade | n/a | 0.00 | n/a | 0 | 0 | 7 |
| related (linked) | 0.17 | 0.33 | 0.22 | 1 | 6 | 3 |

Retrieval recall (labelled pairs the similarity search surfaced at all, before any judging): duplicates **1.00**, related **1.00**. False merges (a confident duplicate verdict on a pair that is not a duplicate): **0**.

Confusion matrix (rows: truth, columns: what the workflow did with the pair):

| truth \ workflow | duplicate | related | none | not_retrieved |
|---|---|---|---|---|
| **duplicate** | 3 | 1 | 3 | 0 |
| **related** | 0 | 1 | 2 | 0 |
| **distinct** | 0 | 4 | 81 | 124 |

`none` = looked at but not linked (judged distinct, or below the link threshold); `not_retrieved` = never looked at.

Sensitivity to the link threshold (the same judgements scored at other thresholds; the merge threshold stays at least as high):

| Link threshold | Duplicate precision | Duplicate recall | Related precision | Related recall | Links suggested | Wrong links |
|---|---|---|---|---|---|---|
| 0.3 | 1.00 | 0.43 | 0.07 | 1.00 | 46 | 40 |
| 0.4 | 1.00 | 0.43 | 0.08 | 1.00 | 40 | 34 |
| 0.5 | 1.00 | 0.43 | 0.14 | 0.33 | 10 | 6 |
| 0.6 | 1.00 | 0.43 | 0.17 | 0.33 | 9 | 5 |
| 0.7 | n/a | 0.00 | n/a | 0.00 | 0 | 0 |
| 0.8 | n/a | 0.00 | n/a | 0.00 | 0 | 0 |

Choosing a threshold from this table fits it to these same 10 labelled pairs, so the best-looking row is optimistic. Use it to see the trade-off, then confirm on data the model has not been tuned against.

Labelled pairs the workflow got wrong or missed:

- **duplicate** #1 Support SAML single sign-on / #2 Let our employees log in with their Okta accounts: judged related at 0.45
- **duplicate** #1 Support SAML single sign-on / #3 Azure AD / Entra sign-in for our whole org: judged related at 0.42
- **duplicate** #4 Export report tables to CSV / #5 Get dashboard numbers into Excel for my monthly review: judged related at 0.42
- **related** #4 Export report tables to CSV / #10 Email reports automatically every Monday: judged related at 0.45
- **duplicate** #19 Slack integration for alerts / #20 Post notifications to our team chat channel: judged related at 0.63
- **related** #19 Slack integration for alerts / #21 Webhooks for record events: judged related at 0.45

Links it would suggest that are wrong:

- predicted **related**, truth **distinct**: #5 Get dashboard numbers into Excel for my monthly review / #12 Improve the dashboard
- predicted **related**, truth **distinct**: #8 Audit log of admin actions / #15 Custom fields on records
- predicted **related**, truth **distinct**: #10 Email reports automatically every Monday / #13 Make the app better for managers
- predicted **related**, truth **duplicate**: #19 Slack integration for alerts / #20 Post notifications to our team chat channel
- predicted **related**, truth **distinct**: #21 Webhooks for record events / #29 Link pull requests to records

## Reading the mock numbers

Mock mode replaces the model with deterministic rules (word overlap) and the embeddings with a hashed bag of words. It exists so the whole system runs with no model, and this table shows what that costs: it finds duplicates only when they share words. It is a wiring test, not a quality measure.
