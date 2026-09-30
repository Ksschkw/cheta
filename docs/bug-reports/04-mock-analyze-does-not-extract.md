# [WITHDRAWN] MemWalMock.analyze stores the raw input as one fact instead of extracting atomic facts

Reproduced locally: yes. WITHDRAWN: the behaviour is real and reproducible, but it is explicitly documented as intentional, so it is not an accurate bug report against the maintainers. Do not file this one.

## Labels

`bug`, `python-sdk`, `testing` (proposed; withdrawn)

## Environment

- OS: Linux Mint 22.3 (Zena), kernel `Linux 7.0.0-29-generic x86_64`
- Python: `Python 3.12.3` (tested with `/home/ksschkw/kss/IDK/.research/venv/bin/python`)
- SDK: `memwal==0.1.11`
- Relayer: `https://relayer.memory.walrus.xyz`, `GET /health` HTTP 200 with `"prompt_versions":{"extract":"extract.v6","ask":"ask.v2"}`

## Summary

`MemWalMock.analyze` calls `_store(text, namespace)` once and returns a single `AnalyzedFact` whose `text` equals the entire input, with `fact_count=1`. The real `POST /api/analyze` runs the `extract.v6` LLM extractor and returns one fact per atomic fact. This divergence means a test written against the mock cannot catch extractor-dependent behaviour. However, the SDK README documents this exact behaviour: "For deterministic behavior, `analyze` stores its full input as one fact instead of invoking an LLM extractor" (`packages/python-sdk-memwal/README.md:107`). Because it is documented by design, this report is withdrawn as a bug and should instead be a documentation/test-coverage discussion, not an issue.

## Steps to reproduce

1. Run:

```python
import asyncio
from memwal import MemWalMock

async def main():
    m = MemWalMock()
    text = ("My name is Alice. I live in Berlin and I am allergic to peanuts. "
            "I work at Acme.")
    res = await m.analyze(text)
    print("fact_count:", res.fact_count)
    for f in res.facts:
        print("fact text equals full input:", f.text == text)
        print("fact text:", repr(f.text))

asyncio.run(main())
```

2. Compare with the documented server behaviour: `docs/relayer/api-reference.md:399-433` shows an input `"I live in Hanoi and prefer dark mode."` producing two facts, `"User lives in Hanoi"` and `"User prefers dark mode"`, and the prompt version on the live relayer is `extract.v6` (`services/server/src/services/extractor.rs:203`).

3. Read the mock README statement at `packages/python-sdk-memwal/README.md:107`.

## Expected behaviour

(As filed, before withdrawals.) A mock named `MemWalMock` that implements the "analyze" method would split the input into atomic facts so tests can exercise multi-fact paths and importance handling.

## Actual behaviour

```
fact_count: 1
fact text equals full input: True
fact text: 'My name is Alice. I live in Berlin and I am allergic to peanuts. I work at Acme.'
```

The mock's `analyze` (`memwal/mock.py:290-308`) passes the whole string to `_store` and returns one `AnalyzedFact(text=text, ...)`. The real relayer extracts because `services/server/src/routes/analyze.rs:132-137` documents the flow as "2. Call LLM to extract memorable facts from text" using `FACT_EXTRACTION_PROMPT_VERSION = "extract.v6"`.

## Impact

Low, and mitigated by documentation. The real impact is a test-fidelity gap: a test suite that relies on `MemWalMock.analyze` will never exercise multi-fact extraction, importance buckets, dedup, or cap behaviour, so regressions in those areas pass CI. This is worth a documented testing note, but calling it a product bug would be inaccurate.

## Suggested fix or API shape

If the maintainers want to close the fidelity gap, add an opt-in injectable extractor:

```python
class MemWalMock:
    def __init__(self, ..., extractor: Optional[Callable[[str], list[str]]] = None):
        self._extractor = extractor
```

where the default stays "one fact = full input" (preserving the documented deterministic behaviour) and tests can pass a fake extractor to simulate multiple facts. No change is needed if the current documentation is the intended contract.

## Evidence

Files read (installed 0.1.11 wheel, `/home/ksschkw/kss/IDK/.research/venv/lib/python3.12/site-packages/memwal/`):

- `memwal/mock.py:290-308` - `analyze` stores `text` and returns exactly one fact.
- `memwal/mock.py:481-503` - `_store`; `memwal/mock.py:99-104` - mock docstring ("designed for application tests, not relevance benchmarking").

Repository files read (`/home/ksschkw/kss/IDK/.research/MemWal`):

- `packages/python-sdk-memwal/README.md:107` - documents "analyze stores its full input as one fact instead of invoking an LLM extractor". This is the reason for withdrawal.
- `services/server/src/services/extractor.rs:203` - `FACT_EXTRACTION_PROMPT_VERSION = "extract.v6"`.
- `services/server/src/routes/analyze.rs:132-137` - analyze flow calls the extractor.
- `docs/relayer/api-reference.md:399-433` - worked two-fact example.

Commands run:

- The mock script above via `/home/ksschkw/kss/IDK/.research/venv/bin/python` (observed output quoted verbatim).
- `curl -sS https://relayer.memory.walrus.xyz/health` - shows `prompt_versions.extract = extract.v6`.
