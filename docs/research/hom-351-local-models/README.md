# HOM-351: local model measurements

This folder holds the raw inputs and outputs behind ADR 0047, Amendment 1 (HOM-351). No number
goes into the ADR without a file here.

Status 2026-11-10: only `results/bge-m3-q8_0/` and `results/qwen3-reranker-0.6b-q8_0/`
exist. The other runs are pending: the Mac did not have the free disk for them. Their cells in
the ADR say "not measured, disk" (the LLM cells: "not measured on the Mac (disk); real
qualification on the P40 in a later reviewed-script step").
In each `server.log`, the local models folder is shown as `<models>`.

- `fixtures/calls.json`: 12 synthetic calls (8 Persian, 4 English). Each call has gold intent,
  outcome, entities, key facts and 2 QA pairs.
- `fixtures/retrieval.json`: 20 short passages and 12 queries (8 Persian, 4 English; with
  mixed-script queries and Persian digits) with graded relevance.
- `run_eval.py`: the harness. It starts nothing. It calls a running OpenAI-compatible server
  and writes the HOM-69 evaluator inputs and outputs.
- `results/<candidate>/`: one folder per measured run. `result.json` is the summary.
  `eval/*.json` are the raw outputs of `python -m backend.evaluation`, and the `.jsonl` files
  next to them are their inputs. Only an `llm` run writes `raw.jsonl` (every model answer, with
  its time in seconds). An `embed` or `rerank` run saves the rankings, not the raw
  `/v1/embeddings` vectors or `/v1/rerank` responses. `server.log` is the `llama-server` log of
  that run.

The fixtures are synthetic and small on purpose. No real customer data, no real names, phone
numbers or ids (phones use `0999 000 xxxx` and the `555-01xx` fiction range, e-mails use
`example.com`). The real WP13 gold corpus replaces them later. Until then, every quality number
here is a smoke test on 12 calls, not a qualification.

## How each number is made

The LLM run sends each call through the real product adapter,
`OpenAICompatibleIntelligenceProvider` (`Homayar/backend/providers/openai_compatible.py`):
the same system prompt, `response_format` json_schema (strict) and parser as production. Then
it asks 2 QA questions per call with a small harness prompt, because the product has no QA
prompt yet.

| Number | Evaluator (HOM-69) | How the input is built |
| --- | --- | --- |
| schema valid rate | none (harness count) | calls where the product parser accepted the answer / calls |
| intent accuracy | `intent` | gold intent vs the first `stated` intent label; a missing extraction counts as an abstention |
| outcome accuracy | `intent` | same, for the outcome dimension |
| entity recall and precision | `entities`, `--normalize fa` or `en` | gold entities vs the extraction entity values (`label`, `text`), exact value match after normalisation |
| key fact support rate | `factual_support` | each gold key fact is one claim, labelled `supported` when all its keyword groups appear in the summary, summary fields, decisions or action items (normalised match), else `unsupported`. **Auto-labelled by string match, not a human audit.** |
| citation validity | `factual_support` | the summary's source turn indexes become citations; an index outside the call is an invalid citation |
| QA accuracy | `qa_score` | score 1 when the answer contains an accepted answer (normalised), else 0; reference 1. `qa_accuracy = 1 - score_mae` |
| retrieval MRR, nDCG@5, recall@3, hit@1 | `retrieval`, `--ks 1,3,5` | dense: cosine over `/v1/embeddings`; rerank: `/v1/rerank` over all 20 passages, and over the dense top 10 |

Limits of these rules:

- A string match misses a correct paraphrase and can accept a wrong sentence that happens to
  hold the keyword. It is the same rule for every candidate, so it ranks them, but the absolute
  values are rough.
- Entity value match is strict: `۳۲۰ هزار تومان` and `۳۲۰ هزار` do not match. Recall per type
  is what the F27 critical-entity gate reads; on 12 calls it is only a signal.
- 12 calls and 12 queries give wide error bars. HOM-70 owns confidence intervals.

## Speed and memory

- Tokens per second come from the `timings` object that `llama-server` returns with each chat
  completion (`prompt_n`, `prompt_ms`, `predicted_n`, `predicted_ms`), summed over the run.
- Embedding and rerank speed are wall-clock times of the requests, after one warm-up request.
- Memory: the harness samples the server PID with `ps -o rss=` (resident memory) and `top -stats
  mem` (memory footprint) during the run and keeps the peaks. The two `server.log` files
  here (default log level) have no model, KV or compute buffer sizes. The compute buffer sizes in the ADR's VRAM plan are
  estimates with no Mac evidence.
- Product timeout (harness version 2): the product chat adapter gives up after 60 seconds
  (`Homayar/backend/providers/openai_compatible.py:339`). The `llm` mode keeps a longer
  transport timeout (`--timeout`, default 600) so that a slow call still finishes and is
  measured. It records each call's seconds in `raw.jsonl`, marks each analysis call over 60
  seconds `would_time_out_in_product`, and counts them in `result.json`
  (`summary.analysis_seconds.over_60s`, `summary.qa_seconds.over_60s`,
  `summary.product_timeout`). The two retrieval runs were made with harness version 1; the
  `embed` and `rerank` code did not change in version 2.
- Every speed and memory number here is **measured on Apple M-series with Metal, proxy only**.
  This Mac is not a Tesla P40. Nothing here is a P40 number.

## Re-running one check

Start one server at a time on `127.0.0.1` (see the commands in each `result.json` `note`), then
run from `Homayar/`:

```bash
uv run python ../docs/research/hom-351-local-models/run_eval.py llm \
  --base-url http://127.0.0.1:8080/v1 --label <name> --out <dir> --server-pid <pid>
```

Stop the server by its PID afterwards.
