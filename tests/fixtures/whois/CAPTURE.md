# WHOIS fixture capture log

These fixtures are **synthetic**: structurally faithful to the real
registry responses but stripped of any registrant PII and using
deterministic dates so the regex tests stay stable across years. When a
registry changes its WHOIS format (new field, removed field, reordered
sections) we re-capture from a public domain we own permission to query
and overwrite the relevant file here.

| File                  | Source `whois <fqdn>` | Captured (UTC) | Notes |
| --------------------- | --------------------- | -------------- | ----- |
| `example.com.txt`     | `whois example.com`   | 2024-08-15     | Verisign Registry format. Contains both `Updated Date` and `Registry Expiry Date`; the parser MUST pick the expiry. |
| `example.ru.txt`      | `whois example.ru`    | 2024-08-15     | Coordination Center for TLD .RU format; key field is `paid-till:`. |
| `example.co.uk.txt`   | `whois example.co.uk` | 2024-08-15     | Nominet format; double-TLD; date as `dd-mmm-yyyy`. |
| `example.app.txt`     | `whois example.app`   | 2024-08-15     | Google Registry; same shape as Verisign. |
| `example.io.txt`      | `whois example.io`    | 2024-08-15     | Internet Computer Bureau format; same shape as Verisign. |

## Regenerating

```bash
whois -H example.com > tests/fixtures/whois/example.com.txt
```

Then scrub any registrant contact info (emails, phone numbers, addresses)
before committing — these tests are public and we do not need real PII
to exercise the parser.
