# Live-model admission limits and accounting

The live client never initiates a request merely by loading configuration. The
user-selected provider, endpoint and model remain explicit configuration. No
credential values are written to the configuration or ledger.

## Per-run limits

`ModelConfig` accepts three optional fields, all defaulting to `null` for backward
compatibility:

| Field | Meaning |
|---|---|
| `max_model_calls` | Maximum physical HTTP attempts, including failed attempts and retries. |
| `max_total_tokens` | Maximum admitted total-token spend for the ledger. |
| `request_token_reserve` | Documented maximum input plus output tokens for a single request; required with a token-spend limit. |

For a provider whose documented model context window is 1,048,576 tokens, the
following configuration reserves that entire window before each HTTP request:

```json
{
  "max_model_calls": 1000,
  "max_total_tokens": 5000000,
  "request_token_reserve": 1048576,
  "max_output_tokens": 2048,
  "retries": 0
}
```

Immediately before **each** HTTP attempt, the client verifies that the call limit
has room and that `known_total_tokens + request_token_reserve <= max_total_tokens`.
Admission is serialized across clients sharing the same `UsageLedger` object.
Reloading an existing ledger retains its spent allowance. Separate processes must
not write the same ledger; this is not an interprocess accounting service.

A known response releases unused reservation, replacing it with actual provider
usage. Any sent attempt without reported total usage stops a token-limited run
immediately; the measured total stays `null`, while the budget report conservatively
retains the full reservation. This applies to transport failures and HTTP errors
as well as successful responses. There is no automatic unbounded retry. A blocked
attempt has `request_sent=false` and zero tokens and dollars because no HTTP
request occurred.

The reserve is an upper bound, **not an average or a tokenizer estimate**. Using a
small guessed reserve cannot guarantee a token cap. The caller must use the
provider's documented input-plus-output bound. As an additional admission guard,
the serialized request's UTF-8 byte length plus maximum output tokens cannot
exceed the reserve; this covers builder/reflection prompts as well as actors.
This byte check is conservative and is not claimed to be exact tokenization.
Provider-reported usage exceeding the configured bound is preserved as a failed
`token_reservation_exceeded` attempt and prevents further requests. The client
cannot undo an already completed charge or guarantee a dishonest provider's
billing. Unpriced gateway or external charges are outside token cost estimates.

With the example configuration, admission can stop with approximately one
context window of unused allowance. A run stopped by a limit writes a failed
`run_status.json`, preserves all usage and episode evidence, and does not create a
successful summary. Failed builder/reflection attempts receive explicit ownership
in `failed_operations.jsonl`; they do not become frozen harnesses or training
labels. The final status includes `spending_limits`, `stopped_for_budget` and
`stop_reason`.

## Micro-study request envelope

The frozen micro-study has three source tasks from one source generator and one
target domain containing three support tasks and three query tasks. The support
and query generators are distinct. With all eleven module candidates, one
construction seed and one execution seed, the seven-condition protocol has
33 source episodes, at most 24 support episodes for `R@4`, and 21 query episodes:
at most 78 episodes. Each of the two optimized methods can perform four candidate
evaluations across three support tasks. The whole-round reservation permits at
most one reflection round per method at `R@4`.

With twelve actor turns per episode, the envelope is 936 actor requests, two D/S
builder requests and two GEPA reflection requests: **940 study requests** before
retries. These are ceilings, not predicted token spend or success. Rejected
proposals and early finishes can reduce physical execution. A 1,000-request cap
leaves 60 requests above this envelope, and `retries=0` preserves the calculation.

An optional preflight is a separate, explicitly invoked single request asking for
the exact text `OK`, with a small output cap, no tools and zero retries. Record
the actual response model and all usage. It does not test the actor/tool cycle,
does not count as a benchmark result, and must have its own ledger if kept
outside study spend. The separately recorded environment calibration permits
four actor requests; together with the single preflight probe, these are at most
**five additional calls outside the study ledger**. Their usage and cost must be
disclosed alongside study spend. No such network request is part of the unit
tests.

## DeepSeek usage normalization

DeepSeek documents `prompt_tokens` as cache-hit plus cache-miss input tokens;
`prompt_cache_hit_tokens` is also exposed as
`prompt_tokens_details.cached_tokens`. The client accepts either representation
and verifies both when present. It rejects conflicting hit counts, hit/miss sums
inconsistent with input, and total counts inconsistent with input plus output.
Missing cache or reasoning breakdowns remain unknown rather than becoming zero.
[Official Chat Completions schema](https://api-docs.deepseek.com/api/create-chat-completion/),
[official context-cache guide](https://api-docs.deepseek.com/guides/kv_cache/).

When all configured rates and necessary counts are present, estimated cost is:

```text
((input_tokens - cached_input_tokens) * input_rate
 + cached_input_tokens * cached_input_rate
 + output_tokens * output_rate) / 1,000,000
```

The cached count is already part of input tokens; it is never added to total
tokens a second time. No rates are supplied automatically. `pricing_version`
should identify the selected tariff, date, timezone and currency. The current
implementation applies one configured rate table throughout a run; a run crossing
a time-of-day tariff boundary must be split or repriced from timestamped ledger
entries before financial reporting. Without prices, dollar cost remains `null`.

Current DeepSeek API documentation describes thinking as enabled by default and
`reasoning_effort="none"` as disabling it. The present executor does not preserve
provider `reasoning_content` between tool turns, so the non-thinking protocol is
the validated choice for this runtime. A matching response alias confirms that
the provider returned that alias; it does not independently establish immutable
model weights. These limitations must accompany any research interpretation.
[Official request and response fields](https://api-docs.deepseek.com/api/create-chat-completion/).

Local tests in `tests/test_live_budget.py` exercise actual localhost HTTP,
including pre-dispatch blocking, retries, missing usage, shared-ledger admission,
restart accounting, DeepSeek cache fields, and study abort ownership. Their
scripted responses are engineering validation only.
