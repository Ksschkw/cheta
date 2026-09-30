# Reproducible evidence: the offline demo run

This file records a run that anyone can reproduce with no API keys and no Walrus
Memory account. It is **not** the real-user evidence the submission needs; the
real-user numbers go in the article once real people have used the bot. It is the
proof that the consolidation and recovery behaviour is real and deterministic
rather than a claim in a README.

## How to reproduce

```
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
PYTHONPATH=src .venv/bin/python -m uvicorn main:app --port 8080 --app-dir src
PYTHONPATH=src .venv/bin/python scripts/seed_demo_user.py --strict
```

With no credentials the app reports `"memory": {"mode": "mock"}` and
`"llm": {"providers": ["offline"]}`. The offline model is a deterministic stub,
not a language model: it extracts first-person facts by clause and adjudicates
new facts against their nearest neighbours by predicate. Answer quality is not
demonstrated by this run. Memory behaviour is.

## What the scripted run produced

Eight of eight scripted expectations met, exit code 0:

| Step | Behaviour | Result |
| --- | --- | --- |
| a | a durable fact is learned and written | 2 facts stored |
| b | the fact is recalled on a later turn | 2 memories recalled |
| c | a changed value supersedes the old one | verdict `updates`, old memory superseded |
| d | a conflicting statement is flagged | verdict `contradicts`, contradiction opened |
| - | stats report the real counts | 6 active, 1 superseded, 1 open contradiction |
| - | counterfactual replay contrasts both answers | `recalled_count` 2, `reply_changed` true |

The counterfactual pair the script generates:

```
with_memory:    From what I remember about you: The user is allergic to peanuts;
                The user always takes their coffee black.
                You said: Peanuts and coffee

without_memory: I do not have any memories about you yet, so I can only answer
                from this message. You said: Peanuts and coffee
```

That difference is produced by re-running the same stored turn with memory
switched off. It is not hand-written.

## The wipe-and-recover proof

Walrus Memory has no API that lists individual memories, so the local index
cannot be rebuilt by enumeration. The index is therefore persisted as a compact
snapshot memory in a companion namespace. Verified live against a running server:

```
1. local index wiped          -> GET /memories/{user} returned 0 memories
2. POST /memories/{user}/rebuild-index
   {"snapshots_scanned":5,"newest_sequence":12,"records_recovered":7,
    "records_already_present":0,"namespace":"ranti.user.cli-demo-final.idx"}
3. GET /memories/{user}       -> 5 active memories restored, with their
                                 original importance values
```

The seven recovered records are the five active memories plus the superseded and
contradicted entries, so the consolidation state survives the loss of the app,
not just the raw text.

## Honest limits of this evidence

- The offline memory client keeps its data in process, so a full process restart
  does not survive here. The wipe above removes the application index while the
  client is alive. Against the hosted relayer the memory itself is durable.
- Recall in the offline client matches on query-token coverage, which is much
  harsher than a real embedding model. Step b deliberately keeps its query short.
  Against the relayer the same step works with natural phrasing.
- Only the duplicate, update and contradiction paths are exercised. The salience
  ranking is exercised but its ordering is not meaningful with a lexical mock.
