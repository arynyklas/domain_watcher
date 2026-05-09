# LLM fixture capture log

Each JSON file below mirrors the **shape** ``litellm.acompletion``
returns. Capturing the full response object (instead of just the
content string) preserves the test's coverage of ``_extract_content``.

| File                          | Scenario                                  | Source / synthesis |
| ----------------------------- | ----------------------------------------- | ------------------ |
| `unknown_tld_ok.json`         | model emits a clean JSON rule             | `ollama/gemma3` against the WHOIS sample below; sha256(prompt)=`<sha>` |
| `bad_json_response.json`      | model wraps JSON in prose / code fences   | observed sporadically with small local models |
| `missing_capture_group.json`  | model omits the capture group             | observed when the prompt is shortened |
| `auth_failure.json`           | marker only                               | tests synthesise `litellm.exceptions.AuthenticationError` |

## Regenerating

The capture command (use the same prompt your code emits — see
``infrastructure/parsers/_prompts.build_messages``):

```bash
uv run python - <<'PY'
import asyncio, json, litellm
from domain_watcher.infrastructure.parsers._prompts import build_messages

async def main():
    msgs = build_messages(
        raw_whois=open("tests/fixtures/whois/example.com.txt").read(),
        tld="com",
        domain="example.com",
    )
    r = await litellm.acompletion(
        model="ollama/gemma3",
        api_base="http://localhost:11434",
        messages=msgs,
        temperature=0,
        response_format={"type": "json_object"},
    )
    print(json.dumps(r.model_dump(), indent=2))

asyncio.run(main())
PY
```

When you bump ``infrastructure/parsers/_prompts.PROMPT_TEMPLATE_VERSION``
you MUST re-record every fixture above and confirm the recorded-fixture
test still asserts the same behavior — the prompt change is the contract.
