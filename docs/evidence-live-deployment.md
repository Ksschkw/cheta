# Live deployment evidence

Recorded against the deployed instance at https://ranti-gkn7.onrender.com with
the real hosted Walrus Memory relayer and the real Groq model. Not a local run,
not a mock.

## The deployed service is genuinely wired to the real services

```
GET /health
{"status":"ok","memory":{"mode":"walrus","degraded":false},
 "llm":{"providers":["groq","gemini"]},"telegram":{"configured":true}}
```

A full turn on the deployed instance, with real extraction and real writes:

```
POST /chat/turn                       http=200   time=6.5s
provider: groq
namespace: ranti.user.web-deploy-probe
stored:  "The user prefers dark mode."      pending=True
         "The user works in Lagos."         pending=True
```

## Memory is portable, the app is disposable

This is the claim the product rests on, and the deployment migration tested it
by accident: the real users were on the local instance, and Render started with
an empty database.

    local index on the deployed instance      0 memories
    recall against Walrus Memory              5 memories

Five real memories came back with an empty local index, because recall runs
against the relayer scoped by owner and namespace, not against application
state. The consolidation state then restored on top of that:

```
POST /memories/{user}/rebuild-index
{"snapshots_scanned":5,"newest_sequence":11,"records_recovered":6,
 "records_already_present":0,"namespace":"ranti.user.telegram-5527434923.idx"}
```

Six records, the four active memories plus the two superseded ones, recovered
from an index snapshot stored as a memory in the companion namespace. So the
hygiene layer travels with the memories rather than being trapped in a database
that dies with the instance.

## Real usage on the relayer

Owner-scoped namespaces, listed straight from Walrus Memory. Synthetic probes
used for testing are marked, and must not be counted as users.

| Namespace | Memories | Note |
| --- | --- | --- |
| ranti.user.telegram-5527434923 | 6 | real, actively conversing |
| ranti.user.telegram-5527434923.idx | 5 | index snapshots for the above |
| ranti.user.telegram-6959647089 | 1 | real |
| ranti.user.telegram-6190892934 | 1 | real |
| ranti.user.telegram-9001 | 2 | SYNTHETIC PROBE, exclude |
| ranti.user.web-deploy-probe | 2 | SYNTHETIC PROBE, exclude |
| ranti.user.cli-live-probe | 2 | SYNTHETIC PROBE, exclude |
| ranti.user.integration-test-user | 3 | live integration test, exclude |

## Honest position on the usage requirement

The brief asks for at least three users storing at least ten memories each. As of
this recording there are three real Telegram identities, holding 6, 1 and 1
memories. The user count is met; the per-user memory depth is not. Only the
account owner can confirm whether the second and third identities are distinct
people or their own testing, so they should be verified before the submission
claims them as three separate users.
