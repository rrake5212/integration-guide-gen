# Golden set

Two real, complete specs captured 2026-10-27:

- `github/` — OAuth app, 75/78 facts verified, 7 steps, 36 scopes.
- `google/` — OAuth + OIDC discovery, 12/14 facts verified, 11 steps, 3 scopes.

Each directory has `spec.json`, `guide.md`, `agent.json`, `agent.md`. `expected.json` holds the
assertions the verifier tests use. The specs are snapshots; provider docs drift, so treat a failure
as “re-check the docs”, not “the code broke”.

Run:

```bash
python3 -m guidegen.verify --golden data/golden
python3 -m unittest tests.test_verifier -v
```
