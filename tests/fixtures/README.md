# Test fixtures

Empty in Step 1 by design. Populated per-provider starting Step 4:

- `dexscreener/` — Step 4 (Core Engine)
- `rugcheck/` — Step 5 (Security Engine)
- `holders/` — Step 8 (Holder Engine)
- `social/` — Step 13 (Social Engine)

Playbook reference: Part V.8 — fixtures are the *only* way engine tests
touch "external" data; no test in this suite makes a live network call.
