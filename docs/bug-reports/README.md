# Walrus Memory bug reports (Session 8 Bug Bounty)

One line per report; every claim is grounded in the file and line cited inside the report. No non-ASCII characters; verify with `grep -rnP '[^\x00-\x7F]' docs/bug-reports/`.

## Index

| # | Report | Status | Surface | Reproduced |
| - | ------ | ------ | ------- | ---------- |
| 01 | [Python SDK has no forget or delete method while the relayer exposes POST /api/forget](01-python-sdk-no-forget.md) | Valid, duplicate risk | Python SDK | Yes |
| 02 | [Python recall() cannot pass scoring_weights](02-scoring-weights-unreachable.md) | Valid, strongest | Python SDK | Yes (wire capture) |
| 03 | [Python recall() drops created_at and score](03-recall-returns-no-timestamps.md) | Valid, duplicate risk | Python SDK | Partially |
| 04 | [MemWalMock.analyze stores raw input as one fact](04-mock-analyze-does-not-extract.md) | WITHDRAWN (documented by design) | Python SDK tests | Yes |
| 05 | [Python default server_url is localhost, TypeScript defaults to hosted](05-python-default-server-url-mismatch.md) | Valid | Python SDK / docs | Yes |
| 06 | [No extract-only endpoint](06-no-extract-only-endpoint.md) | Enhancement, not a defect | Relayer / SDK | Partly (source-verified) |

## Filing checklist

1. Search the tracker before filing: `.github/ISSUE_TEMPLATE/config.yml` and `.github/ISSUE_TEMPLATE/feature.yml` state that namespace listing, forget/delete, timestamps, and recall pagination already have threads. Reports 01 and 03 carry explicit duplicate-risk notes; prefer commenting on the existing thread unless the concrete mismatch in the report adds new information.
2. Do not file 04. It is real and reproducible but explicitly documented as intentional at `packages/python-sdk-memwal/README.md:107`. Filing it would be inaccurate.
3. Use the repo's issue forms: `bug` for 01, 02, 03, 05; `enhancement` for 06. Each form requires the surface dropdown; the surface is stated in each report's Environment section.
4. Keep every step copy-pasteable: the report scripts use only the public package, generate a throwaway key with `os.urandom(32).hex()`, and never touch a live relayer for writes.
5. Never paste delegate private keys, account mnemonics, or other secrets. All examples here use disposable values.
6. Paste the environment block verbatim, including `python3 -V`, `memwal==0.1.11`, and the `GET /health` JSON.
7. Quote the failure output verbatim; do not paraphrase exceptions or truncate JSON that is used as evidence.
8. Link the exact source lines in the report so a maintainer can confirm the claim without re-deriving it. Line numbers refer to the installed 0.1.11 wheel and to the `MystenLabs/MemWal` tree used for review; note the wheel and `dev` tree are both at 0.1.11 for the Python SDK.
9. Mark any report that stops reproducing as WITHDRAWN at the top rather than editing the claim; report 04 is the worked example.
10. Verify ASCII before filing: `cd /home/ksschkw/kss/IDK && grep -rnP '[^\x00-\x7F]' docs/bug-reports/` must print nothing.

## Reproduction environment

- OS: Linux Mint 22.3 (Zena), kernel `Linux 7.0.0-29-generic x86_64`
- Python: `Python 3.12.3`
- SDK: `memwal==0.1.11`
- Interpreter used: `/home/ksschkw/kss/IDK/.research/venv/bin/python`
- Relayer checked: `https://relayer.memory.walrus.xyz/health` (HTTP 200, `apiVersion` 1.0.0, `extract.v6`)
- Sources: installed wheel under `.research/venv/.../memwal/`, the checked-out repo at `.research/MemWal`, and `.research` findings recorded in `PLAN.md` section 8.
