# Learned WHOIS rules — operator guide

WHOIS responses are not standardized. When a domain's TLD has no static
`parsing.whois_rules` entry and no previously-learned rule matches,
`domain-watcher` can ask an LLM for a `(regex, date_format, timezone)`
triple, validate it through a 6-gate safety pipeline, and persist the
result so the next check on that TLD is deterministic.

This document explains when to enable the fallback and how to inspect,
promote, and disable rules learned at runtime. The threat-model summary
and gate pipeline below capture the full design — read this page once
before turning the fallback on.

## When to enable

Enable fallback if all of the following are true:

- You watch domains across long-tail TLDs whose registries you don't
  want to research by hand.
- You can run a local LiteLLM-compatible model (Ollama is the default;
  `model: ollama/gemma3`) or accept the cost/latency of a hosted
  provider.
- You can review learned rules occasionally — promoting good rules
  into static config closes the loop and removes the LLM from the hot
  path.

Disable fallback if:

- You run a small, well-known TLD set (`.com`, `.ru`, `.uk`, …) — the
  static rules already cover you.
- Your environment cannot expose outbound traffic to a model endpoint
  and you cannot run a local one.

## Threat model summary

The runtime `RuleSuggester` is treated as **untrusted output**.
Everything that gates a learned rule from production is owned by the
host, not the suggester:

- **Gate 1** — regex compiles and contains exactly one capture group.
- **Gate 2** — proposed regex still matches the WHOIS that triggered the
  learn (no overfitting to a future, different reply).
- **Gate 3** — capture group parses to a `datetime` under
  `date_format`/`strptime_format`/`timezone`.
- **Gate 4** — date is in `[now, now + max_age_years]`. No 1970 sentinel,
  no 100-year-future garbage.
- **Gate 5** — cross-check against a known-good domain for that TLD
  (data file at `infrastructure/parsers/data/known_good_domains.json`).
  Skipped with a warning when no known-good is registered for the TLD.
- **Gate 6** — small set of explicit reject patterns
  (`1970-01-01T00:00:00`, etc.) for resilience to obvious garbage.

A rule that fails any gate is dropped. The pipeline version is recorded
on every accepted rule; tightening the pipeline lets you bulk-revalidate
older rules with `domain-watcher rules revalidate --below-pipeline-version`.

## Inspect, promote, disable

```bash
# List every learned rule.
domain-watcher rules learned

# Filter by TLD.
domain-watcher rules learned --tld com

# Show one rule plus its sample-WHOIS hash and pipeline version.
domain-watcher rules show 17

# Convert a learned rule into a YAML diff suitable for
# parsing.whois_rules[]. The CLI prints the diff and exits;
# the operator pastes it into config.yaml manually.
domain-watcher rules promote 17

# Disable a rule without deleting it. Useful when you want to keep
# the row for audit but stop using it for new checks.
domain-watcher rules disable 17

# Remove a rule outright.
domain-watcher rules delete 17

# Force revalidation of one rule or every rule.
domain-watcher rules revalidate 17
domain-watcher rules revalidate --all

# Bulk-revalidate every rule older than the current pipeline version.
domain-watcher rules revalidate --below-pipeline-version 2

# Wipe every auto-learned rule (asks for confirmation).
domain-watcher rules learned --purge-auto --yes
```

`promote` is intentionally a diff, not a write — operators choose where
in `parsing.whois_rules[]` the rule belongs and should commit the
change to source control like any other config.

## Periodic revalidation

The daemon runs a `RevalidationService` job at the interval defined by
`parsing.llm_fallback.safety.revalidate_after` (default `30d`). It
re-runs the pipeline against the current WHOIS for the rule's
TLD-known-good and either:

- marks the rule revalidated (incrementing the counter), or
- disables the rule with a `WhoisRuleInvalidated` event (criticality:
  critical) that surfaces in metrics
  (`domain_watcher_rules_invalidated_total`).

A disabled rule can be re-enabled via `rules revalidate <id>` once
you've fixed the underlying cause.

## Safety rails

- `max_learn_per_hour` (default 5) — global rate limit on learn
  attempts.
- `max_learn_per_tld_per_24h` (default 3) — per-TLD cap; prevents a
  registry transient from rate-limiting itself open by hammering
  a flaky reply.
- `SuggesterCircuitBreaker` (5 consecutive failures inside 5 minutes
  → open circuit for 5 minutes) — short-circuits hot loops on a
  misbehaving backend without wedging the pipeline.

When the rate limit or circuit blocks a learn attempt, the host emits a
`ParseFailed` event (criticality: critical) — operators see it in
metrics and logs.
