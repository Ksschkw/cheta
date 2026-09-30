---
name: project-init
description: Scaffold a new project or add an entity using a strict layered folder structure with one file per entity per layer, loose coupling enforced by an architecture check, circuit breakers at outbound boundaries, a no-emoji rule, and a ban on useless or always-passing tests. Use when the user asks to initialize, bootstrap, scaffold, or restructure a codebase, or to add a new entity following the house architecture.
---

# Project Init

## 1. Folder structure

One file per entity per layer. Dependencies point inward only.

```
<root>/
  src/
    models/entities/        <entity>_model.<ext>      source of truth
    schemas/                <entity>_schema.<ext>     transport DTOs
    crud/                   <entity>_crud.<ext>       persistence, one entity each
    services/               <entity>_service.<ext>    business logic, one entity each
    routers/                <entity>_router.<ext>     transport, one entity each
    core/
      config.<ext>
      database.<ext>
      errors.<ext>
      resilience.<ext>                                breaker, timeout, retry, bulkhead
      container.<ext>                                 composition root
  tests/                    mirrors src/ exactly
  arch-check.<ext>
```

If the project already uses different top-level names, keep theirs and map these
five layers onto it. Never rename an existing directory without being asked.

### Layer responsibilities

| Layer | Does | Never does |
|---|---|---|
| routers | Parse request, call one service, shape response | Any `if` that is not error mapping |
| services | Business logic, orchestration, cross-entity rules | Build raw queries, import a DB driver |
| crud | Persistence for one entity | Join across entities, import a service |
| schemas | Wire-format validation | Import repositories or services |
| entities | Domain types and invariants | Import schemas, repositories, services, frameworks |

### Hard rules

1. A router contains parse, call, respond. Nothing else.
2. A crud file touches one entity's storage. No cross-entity joins.
3. A service never imports a DB driver.
4. An entity imports nothing from your own project.
5. No module-level mutable singletons. Dependencies are injected from the
   composition root in `core/container`.
6. Cross-entity coordination lives in exactly one service, named after the use
   case, not the entity it starts from.
7. Two services never import each other. Extract the shared rule into a third.

### Naming

`user_model`, `user_schema`, `user_crud`, `user_service`, `user_router`. Class
names `UserModel`, `UserSchema`, `UserCrud`, `UserService`, `UserRouter`. The
layer suffix is redundant with the folder. Keep it anyway, it makes diffs and
grep honest.

Functions in `crud/`: `get_by_id`, `list`, `create`, `update`, `delete`. Return
entities, not raw rows. Functions in `services/`: named after the use case,
`register_user`, `deactivate_account`.

## 2. Loose coupling

The import graph is the coupling. Enforce it with a machine, not with prose.

- Generate an architecture check in the same run. Python: `import-linter`.
  TypeScript: `dependency-cruiser`. Go: `go-arch-lint` or `depguard`. Java and
  Kotlin: ArchUnit. C#: NetArchTest.
- The check fails the build if any layer imports outward, if two services import
  each other, or if a crud file imports another entity's crud file.
- Wire the check into the default test command, not CI only. A developer must be
  able to reproduce a red build locally with one command.
- Talk to other modules through an interface or a typed boundary, never through a
  shared mutable object. If two modules need the same data, one passes it to the
  other as an argument.

## 3. Circuit breakers

Apply at outbound boundaries only: third-party HTTP APIs, LLM providers, payment
gateways, email and SMS, brokers, caches, remote databases.

For every outbound call:

1. Explicit non-infinite timeout.
2. Circuit breaker, one per dependency, not per call site. States closed, open,
   half-open. Configurable threshold, reset window, probe count.
3. Bulkhead: dedicated pool or concurrency limit per dependency.
4. Retry with jitter, idempotent operations only, bounded attempts.
5. Explicit typed fallback. Never a silent null.
6. Breaker state changes and fallback activations emit a metric and a structured
   log.

Forbidden: circuit breakers on in-process calls between your own layers. A
service calling its own repository is not a failure domain. A breaker there adds
latency and hides bugs behind "breaker open."

All primitives live in `core/resilience` and are injected. Never constructed ad
hoc inside a service.

## 4. No emojis

No emojis anywhere in the codebase. Source files, comments, docstrings, log
messages, error strings, test names, commit messages, filenames, config, CLI
output, README diagrams, generated scaffolding.

- Status output uses `[OK]`, `[FAIL]`, `[WARN]`, `[SKIP]`. Not checkmarks or
  colored symbols.
- Log level, not decoration, carries tone.
- No box-drawing, no ASCII art, no banner art.

The rule applies to the codebase, not to runtime user data. If the product is a
chat app, users may send emojis and the system must handle them. The prohibition
is on the engineering artifacts.

Add a lint rule or pre-commit hook that fails on emoji or non-ASCII characters in
source paths. Scope it away from test fixtures containing intentional unicode.

## 5. Tests

A test exists to catch a regression. If it cannot fail, it is not a test. Delete
it.

### Required

- One test file per layer, per entity, mirroring `src/`.
- Test the layer's own responsibility. Mock only the layer directly beneath it.
- At least one test per use case on the happy path.
- At least one test per use case on the failure path: what happens when the
  repository returns not-found, when the outbound call times out, when the
  breaker is open.
- A test asserting the external error response leaks nothing internal.

### Banned

- Tests that assert nothing, or assert `is not None`, `!= null`, or truthiness
  where the value does not matter.
- Tests that only assert the code ran without raising. If the assertion is
  "it did not crash," the test has no value.
- Tests that mock the thing under test.
- Tests that mock every dependency so heavily that they assert the mocks were
  called, not that behavior is correct.
- Tests that will pass no matter what the code does. If you deleted the body of
  the function, the test must go red. Verify this. If it still passes, the test
  is decoration.
- Tests asserting on log strings, on exact error message wording, or on
  implementation detail that is not part of the contract.
- Snapshot tests of anything that changes on every run.
- One test per trivial getter or setter.
- Generated test stubs. Never leave `test_something` with a `pass` body.

### Before writing a test, ask

1. What regression does this catch?
2. If I break the code, does this go red? If unsure, break the code and check.

If there is no good answer to question 1, do not write the test.

## 6. Initialization procedure

1. Resolve language, framework, package manager, test runner, and existing
   layout. If the repo is non-empty, read it first.
2. Confirm which entities to scaffold. If the user says "just do it," scaffold
   one entity named `User` and say so.
3. If any target file already exists, stop and report. Never overwrite.
4. Create the directory skeleton per §1.
5. Write one complete vertical slice for the sample entity: entity, schema, crud,
   service, router. Real and runnable, no `# TODO` stubs.
6. Write `core/`: config, database lifecycle, errors, resilience, container.
7. Wire the slice in the composition root and register the route.
8. Write the architecture check per §2.
9. Write tests per §5, mirroring the slice.
10. Run the test command and the architecture check. Report actual output. If
    either fails, fix it before reporting. Do not report success on a red build.
11. Report: files created, the profile assumed, the commands to run, and any
    assumption the user should confirm.

## 7. Adding an entity later

1. Read one existing vertical slice and copy its exact shape and idioms.
2. Create the five files.
3. Wire into the composition root.
4. Add tests per §5.
5. Run the architecture check. Report.

Do not invent a new pattern. Do not refactor unrelated files. Do not add a
dependency without asking.

## 8. Reject on sight

- Logic in a router beyond parse, call, respond.
- A crud file importing another crud file or a service.
- An entity importing a framework or transport type.
- Raw SQL outside `crud/`.
- A service returning an ORM object across a layer boundary.
- A global mutable singleton.
- Two services importing each other.
- A file named `utils`, `helpers`, or `common`.
- A circuit breaker around an in-process call.
- A fallback returning null or None instead of a typed result.
- An outbound call with no timeout.
- Any emoji or decorative character in code, logs, comments, or output.
- A test that cannot fail.
- A test asserting the code did not raise.
- A test with a `pass` body.
- A commented-out test left in place.
