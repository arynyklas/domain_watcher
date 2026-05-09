"""Prompt templates for the LiteLLM rule suggester.

Pinning these as named constants — not f-strings buried inside the
suggester — makes prompt changes a deliberate event with a code review.
Bump ``PROMPT_TEMPLATE_VERSION`` and re-run the recorded-fixture LLM
tests when the prompt is edited.
"""

from __future__ import annotations

PROMPT_TEMPLATE_VERSION = 1

SYSTEM_PROMPT = """\
You are a strict information-extraction component. Your only job is to
look at a WHOIS response and produce a JSON object describing how to
extract the *expiration* (or "paid-till") date from it.

You MUST respond with a single JSON object, nothing else. The object has
the following keys:

  - "expires_regex":  Python regular expression with EXACTLY ONE capture
    group, applied to the WHOIS body via re.search. The capture group
    contains the raw expiration date string.
  - "date_format":  one of "iso8601", "rfc3339", "yyyy-mm-dd",
    "dd-mmm-yyyy", "epoch", "custom".
  - "timezone":  IANA tz name. Use "UTC" if the WHOIS body specifies an
    explicit offset (Z, +00:00, etc).
  - "strptime_format":  required iff "date_format" is "custom"; a Python
    strptime format string. Otherwise omit or set to null.

Critical constraints:

  - The regex MUST extract the *expiration* date, NOT the registration,
    creation, last-updated, or queried-at date.
  - The regex MUST have exactly one capture group.
  - Do not output prose, code fences, or anything outside the JSON object.
"""

USER_PROMPT_TEMPLATE = """\
TLD: {tld}
Domain: {domain}

Here is the raw WHOIS response (truncated to 4 KiB):
---
{raw_whois}
---

Output the JSON object now.
"""

EXAMPLE_USER = """\
TLD: com
Domain: example.com

Here is the raw WHOIS response (truncated to 4 KiB):
---
   Domain Name: EXAMPLE.COM
   Updated Date: 2024-08-14T07:01:34Z
   Creation Date: 1995-08-14T04:00:00Z
   Registry Expiry Date: 2025-08-13T04:00:00Z
   Registrar: ICANN
---

Output the JSON object now.
"""

EXAMPLE_ASSISTANT = (
    '{"expires_regex": "Registry Expiry Date:\\\\s+(\\\\S+)",'
    ' "date_format": "iso8601",'
    ' "timezone": "UTC"}'
)

RAW_TRUNCATE_LIMIT = 4096


def truncate(raw_whois: str, *, limit: int = RAW_TRUNCATE_LIMIT) -> str:
    if len(raw_whois) <= limit:
        return raw_whois
    return raw_whois[:limit] + "\n...[truncated]"


def build_messages(*, raw_whois: str, tld: str, domain: str) -> list[dict[str, str]]:
    """One-shot prompt: system + example user + example assistant + real user."""
    user = USER_PROMPT_TEMPLATE.format(tld=tld, domain=domain, raw_whois=truncate(raw_whois))
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": EXAMPLE_USER},
        {"role": "assistant", "content": EXAMPLE_ASSISTANT},
        {"role": "user", "content": user},
    ]


__all__ = [
    "EXAMPLE_ASSISTANT",
    "EXAMPLE_USER",
    "PROMPT_TEMPLATE_VERSION",
    "RAW_TRUNCATE_LIMIT",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
    "build_messages",
    "truncate",
]
