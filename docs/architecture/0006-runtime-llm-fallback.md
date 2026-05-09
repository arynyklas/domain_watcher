# ADR 0006 — Runtime LLM WHOIS Rule Fallback

> Status: **DRAFT**, awaiting approval.
> Related: [0001 Overview](./0001-overview.md) §11(5),
> [0003 Config Schema](./0003-config-schema.md) §3 (`parsing.llm_fallback`),
> [0004 Plugin Protocol](./0004-plugin-protocol.md) §4.4.
> Embedded data: `src/domain_watcher/infrastructure/parsers/data/known_good_domains.json`.

## 1. Context

The hot WHOIS-parsing path is regex-driven. When a TLD has no static rule
in `parsing.whois_rules` and no learned rule in the state DB, we have two
choices:

A. Emit `ParseFailed` and ask the operator to add a rule manually.
B. Ask an LLM to suggest a rule, validate it, persist it as "learned",
   and continue.

The user has chosen (B). This document specifies the exact safety
contract that makes (B) acceptable in a high-reliability monitoring
system. Without these rails, an LLM-generated regex that silently
extracts the *registration* date (or the *registrar's update timestamp*)
instead of the expiration date would cause us to never warn about
expiring domains. That is exactly the failure mode this system exists
to prevent.

## 2. Threat model

| Threat                                                 | Mitigation               |
| ------------------------------------------------------ | ------------------------ |
| LLM picks a regex matching a wrong field               | §4 self-validation + range check |
| LLM hallucinates a regex that doesn't compile          | §4 step 1 (compile check) |
| LLM regex matches but yields garbage on parse          | §4 step 2 (parse check)  |
| LLM fits a one-shot example but breaks on real WHOIS   | §4 step 3 (re-validation on next run) |
| Bad rule cached forever                                | §5 revalidation; §6 demotion |
| LLM backend becomes a denial-of-service vector         | §7 rate limit + circuit breaker |
| Operator never learns a rule was auto-learned          | §8 audit log + CLI surface |
| Quietly wrong rule produces plausible-but-wrong dates  | §4 cross-check with a second WHOIS run, §5 revalidation |

## 3. Flow

```
┌──────────────────────────────────────────────────────────────────────┐
│ check_domain(d)                                                      │
│   ├─ checker.check(d) → CheckResult(raw=<whois text>)                │
│   └─ parser.parse(raw, d, rules):                                    │
│       1. try static rules from config                                │
│       2. try learned rules from state DB                             │
│       3. if still no match AND llm_fallback.enabled:                 │
│             ├─ rate-limit check (§7)                                 │
│             ├─ rule = await suggester.suggest(raw, d)                │
│             ├─ validation pipeline (§4) — REJECT or ACCEPT           │
│             ├─ on ACCEPT:                                            │
│             │     persist as learned_rule(auto_learned=True)         │
│             │     emit WhoisRuleLearned event                        │
│             │     return parsed datetime                             │
│             └─ on REJECT:                                            │
│                   emit ParseFailed(reason)                           │
│                   return error                                       │
│       4. if llm_fallback disabled and no rule: ParseFailed           │
└──────────────────────────────────────────────────────────────────────┘
```

Step "validation pipeline" may itself perform a network call (gate 5 cross-check); the result is cached for `safety.revalidate_after` so subsequent learn attempts in the same TLD do not re-fetch.

The application layer owns this orchestration; the `RuleSuggester` plugin
only produces a candidate. The plugin **cannot** persist on its own.

## 4. Validation pipeline

Every suggested rule passes through these gates in order. Any failure
rejects the rule.

1. **Compile.** `re.compile(rule.expires_regex)` must succeed and the
   pattern must contain exactly one capture group.

2. **Match.** Applying the rule to the *same* WHOIS text the LLM was
   given must produce a non-empty match. (LLMs sometimes invent
   plausible regex for text they didn't actually inspect.)

3. **Parse.** The captured string must parse to a tz-aware `datetime`
   under the suggested `date_format` and `timezone`.

4. **Range check.** The parsed datetime must satisfy:
   - `now < parsed_at <= now + max_age_years`
     (rejects dates in the past and absurdly far in the future)
   - `parsed_at != registration date heuristics` — if the WHOIS text
     contains a "Registered" / "created" line whose date matches our
     parsed value, reject. We require the rule to extract the
     *expiration*, not the *registration*.

5. **Cross-check (key safety net).** Fetch WHOIS for one *known-good*
   domain in the same TLD and re-apply the rule. If the second domain
   doesn't match the rule, reject — the regex was overfitted to one
   string. The "known-good" domain comes from a small static list per
   TLD (e.g. `iana.org`, `verisign.com`, `nic.ru`) embedded in the
   parser package. If the TLD is too obscure to have a known-good
   domain, we skip step 5 and rely more heavily on §5 revalidation.

   **Caching.** The known-good WHOIS fetch is cached per
   `(tld, known_good_domain)` for the duration of
   `safety.revalidate_after` (default 30 days). The cache is in-process —
   revalidation drives it, and a process restart costs at most one extra
   fetch per learning attempt. Without caching, every learn attempt for a
   TLD performs two WHOIS fetches: this is unacceptable on the hot path.

   **Transient cross-check failures are not rule failures.** If the
   cross-check WHOIS fetch raises `TransientCheckError` (registry timeout,
   5xx, connection reset), the gate raises `SuggestionError(transient=True)`
   — NOT `RuleValidationError`. The caller treats this as "try again later",
   not "rule is bad". Otherwise a flaky known-good registry permanently
   rejects every learned rule for that TLD. The pipeline counter
   `domain_watcher_pipeline_gate5_skipped_total{reason}` distinguishes
   `no_known_good` (data file lookup miss) from `cross_check_unavailable`
   (transient transport failure).

6. **Operator hint check (cheap, last).** If the parsed string is
   suspiciously round (`00:00:00` is fine; but `1970-01-01` is rejected),
   reject. Catches LLM defaults.

A rule that survives all six steps is persisted with the metadata that
identifies which version of the validation pipeline approved it
(`validated_by_pipeline_version`). When we tighten the pipeline later,
we can revalidate or invalidate older learned rules in bulk.

## 5. Periodic revalidation

Every `parsing.llm_fallback.safety.revalidate_after` (default 30 days),
a background task runs the **same validation pipeline** against fresh
WHOIS data for one representative domain per learned rule. If the rule
no longer validates:

- The rule is demoted to `disabled = true`.
- A `WhoisRuleInvalidated` event is emitted.
- The next domain check for that TLD will trigger a new LLM suggestion
  (with the rate limit applying).

This is the rail that catches "LLM was right at learn time, registry
changed its WHOIS format six months later".

## 6. Manual operator surface

The CLI exposes:

```bash
domain-watcher rules learned                # list learned rules
domain-watcher rules learned --tld xyz      # filter
domain-watcher rules show <id>              # show full rule + raw WHOIS sample
domain-watcher rules promote <id>           # write to YAML, drop from learned table
domain-watcher rules disable <id>           # set disabled=true
domain-watcher rules delete <id>            # purge
domain-watcher rules revalidate [--all|<id>] # force a revalidation pass now
domain-watcher rules revalidate --below-pipeline-version <N>  # revalidate everything older than N
domain-watcher rules learned --purge-auto --yes               # delete all auto-learned rules
```

When the validation pipeline tightens (a new gate, stricter range checks)
we bump the constant `PIPELINE_VERSION` in
`infrastructure/parsers/validation_pipeline.py`. Operators run
`rules revalidate --below-pipeline-version <new>` to force re-validation
of every rule learned under an older pipeline. Failed rules are demoted to
`disabled = true`; passing rules have their
`validated_by_pipeline_version` bumped. `learned --purge-auto --yes` is
the rollback button: it deletes every auto-learned rule, leaving only
operator-promoted rules in the YAML. Use it when disabling
`parsing.llm_fallback.enabled` and you do not want stale learned rules to
keep matching.

`promote` is the path from "auto-learned, working fine" to "blessed
config, audited in git". Operators are encouraged to run
`domain-watcher rules learned` weekly and promote the rules they trust.

## 7. Rate limit and circuit breaker

The two concerns are separated by ownership:

- **Rate limits live in `ParsingService` (application layer).** They are policy.
  - Per-host: no more than `safety.max_learn_per_hour` (default 5) suggestion calls per running process.
  - Per-TLD: no more than `safety.max_learn_per_tld_per_24h` (default 3) suggestion calls per TLD per 24h. A TLD that consistently can't be learned is logged and surfaced via the health endpoint, not retried in a hot loop.
  - Excess requests yield `ParseFailed` immediately; we do not queue.

- **The circuit breaker lives in `infrastructure/parsers/safety.py` as `SuggesterCircuitBreaker`.** It is transport health.
  - After 5 consecutive `SuggestionError`s from a backend in a 5-minute window, the circuit opens for 5 minutes.
  - While open, calls return `SuggestionError("circuit_open", transient=True)` without invoking the backend.
  - This avoids cascades when the LLM backend is wedged.

The split is deliberate: rate limit values change via config (operator decision); circuit-breaker tuning is internal (engineering decision). Mixing them under one knob ties operator surface to internal heuristics.

## 8. Audit log

Every learn / revalidation / demotion produces a `WhoisRuleLearned`,
`WhoisRuleRevalidated`, or `WhoisRuleInvalidated` event. These flow
through the standard event bus. Operators can:

- Subscribe a notifier to them (e.g. `discord-eng` channel for "auto-learned a new rule").
- Query the state DB directly (`learned_rules` table has `created_at`,
  `created_by_suggester_id`, `validated_by_pipeline_version`,
  `last_revalidated_at`, `revalidation_count`, `disabled`).

## 9. State schema

```sql
-- learned_rules — owned by the standalone state DB, mirrored to bot's PG
CREATE TABLE learned_rules (
    id                            BIGSERIAL PRIMARY KEY,
    tld                           TEXT NOT NULL,
    expires_regex                 TEXT NOT NULL,
    date_format                   TEXT NOT NULL,
    strptime_format               TEXT,
    timezone                      TEXT NOT NULL DEFAULT 'UTC',
    auto_learned                  BOOLEAN NOT NULL DEFAULT true,
    disabled                      BOOLEAN NOT NULL DEFAULT false,
    created_by_suggester_id       TEXT NOT NULL,         -- e.g. "litellm:ollama/gemma3"
    validated_by_pipeline_version INT NOT NULL,
    sample_whois_sha256           TEXT NOT NULL,         -- the text the LLM saw
    sample_domain                 TEXT NOT NULL,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_revalidated_at           TIMESTAMPTZ,
    revalidation_count            INT NOT NULL DEFAULT 0,
    UNIQUE (tld, expires_regex)
);

CREATE INDEX learned_rules_tld_active
    ON learned_rules(tld) WHERE disabled = false;
```

We hash the source WHOIS text rather than store it (could be PII / quota
on some registries). The hash lets us notice "same pattern, learned
again" and dedup.

```python
# core/parsing/ports.py — extension to LearnedRulesRepository

class LearnedRulesRepository(Protocol):
    # ... existing methods ...
    async def list_below_pipeline_version(self, v: int) -> Sequence[LearnedRule]:
        """Used by `rules revalidate --below-pipeline-version`."""
    async def delete_auto_learned(self) -> int:
        """Used by `rules learned --purge-auto`. Returns row count."""
```

## 10. Why this is acceptable

- The hot path is **deterministic per (TLD, learned regex)**. The LLM
  result is materialized; the runtime applies a regex like any other.
- The validation pipeline is **the same code path** at learn time and
  at revalidation time. There is exactly one definition of "is this
  rule sane?".
- Failures are **loud**: `ParseFailed` events, audit log, CLI surface.
  A silently wrong rule survives at most `revalidate_after` days.
- The plugin protocol **cannot bypass** validation. A new backend
  (e.g. OpenAI) inherits the same rails.
- Operators retain control: opt-out (`enabled: false`), inspect, demote,
  promote — all are first-class CLI commands.

## 11. Explicit non-rails (residual risk)

These risks are **not** mitigated and are accepted:

- **Adversarial WHOIS.** A registrar that deliberately serves a fake
  expiration date will fool us, just as it would fool any parser.
- **Two-of-three failures.** If the static rules are stale, the LLM
  hallucinates a wrong-but-plausible field, *and* the cross-check
  domain happens to match by coincidence — we will learn a wrong rule
  for ≤30 days. Mitigations: §5 revalidation drift will eventually
  catch it; alerts include the source rule id so operators reviewing
  a missed-expiration incident can trace it to the rule and disable.
- **Non-Latin date formats.** Small local models may not always produce
  right `date_format` for, e.g., Persian calendars. If users need
  exotic locales they should add static rules.

These are documented; users read this ADR before enabling the feature.

## 12. Default

`parsing.llm_fallback.enabled` defaults to **`false`** in the shipped
example config. Users opt in, and they read this ADR before flipping it.
