# DexScan AI — Engineering Handoff: Playbook Steps 12, 13, 14

**Scope of this document:** OFFICIAL PLAYBOOK STEP 12 (Auto-Watch + Filters) revalidation,
OFFICIAL PLAYBOOK STEP 13 (Social Intelligence Engine) revalidation, and OFFICIAL PLAYBOOK
STEP 14 (Scoring V3) implementation, per the Unified Developer Playbook, Part VIII.

**A note on custom-roadmap numbering:** this repo's own internal step numbers (visible in
README.md's "Continuing the build" table) diverge from the Playbook's numbers starting
around custom-roadmap Step 10. Every step number in this document is a **Playbook** number
unless explicitly marked "custom roadmap." Do not cross-reference these against the repo's
own commit history or file-level comments that say e.g. "Step 12" without checking which
numbering scheme that comment is using — several files in this repo (predating this
session) mix both.

**A note on verification standard, stated up front because it changes how every "PASS"
below should be read:** this session ran in a sandboxed environment with no network access.
`aiogram`, `pydantic`, `pytest`, and `mypy` could not be installed (confirmed by a failed
`pip install` attempt). Every finding and fix in this document is backed by direct code
reading, hand-traced execution logic, and a full-repo `ast.parse` syntax sweep (75/75 files,
real and executed) — **not** by a real `pytest` or `mypy` run. This repo's own history
(documented in README.md) shows that distinction matters: two of the three FSM bugs fixed
in prior sessions were caught only by live `pytest` execution, not by code reading. Treat
every "PASS"/"FIXED" below as "verified as far as static analysis can verify" and run the
real test suite before trusting this in production.

---

## 1. OFFICIAL PLAYBOOK STEP 12 REVALIDATION — Auto-Watch & Filter Presets

| Requirement | Status | Notes |
|---|---|---|
| Start / stop / status / emergency stop | PASS | One `asyncio.Task` per user, `stop()` awaited before `start()` re-enters (true one-task-per-user), cancellation is genuinely awaited, not fire-and-forget. |
| Discovery polling | PASS | Polls `get_new_listings()` each cycle; `get_trending()` is used by the on-demand Scan Menu button instead — a deliberate, reasonable split, not a gap. |
| Filter evaluation | PASS | `analysis.filter_presets.matches()` fails closed on any degraded upstream engine result before evaluating its own gate. |
| Duplicate prevention | PASS | Per-task `seen_addresses` set, resets on restart (in-memory, session-scoped, per Part I.3). |
| Cooldown behavior | **NOT SEPARATELY VERIFIED** | STEP11_HANDOFF's checklist lists this separately from "duplicate prevention"; no distinct cooldown-window mechanism was found beyond the dedup set. Plausibly the same thing under two names in the original ask — not conclusively resolved either way. |
| Alert generation / rendering / View Full Report / Result Detail / Trade Staging integration | PASS — traced end to end | `scan_orchestration.run_scan` caches every result identically for manual scans and Auto-Watch (`session_store.cache_put`); `render_watch_alert`'s button uses the literal same `result_view:{uuid}` callback and `ScanHandler` handler a normal Result List entry does. Confirmed by direct code tracing, not docstring claims: **no second trading/execution path exists.** |
| Conservative / Balanced / Aggressive presets | PASS | Real, distinct, graduated thresholds; honeypot rejection is a floor on all three (not a per-preset dial), matching Part I.2. |
| **Custom preset / Custom configuration UI** | **WAS FAIL → FIXED** | See below. |
| Error / degraded behavior | PASS | `matches()` fails closed (not open) on any degraded core/security/holder input. |
| Persistence / session behavior | PASS | Fully in-memory; no new persistence introduced. |
| Regression behavior | **1 pre-existing bug found and fixed** | See "FSM self-loop" below. |

### Fix 1 — Custom Filter was completely non-functional

**Before:** selecting "Custom" stored the string `"custom"` in FSM payload. `NAMED_PRESETS`
(the dict every preset lookup goes through) has no `"custom"` key, so
`NAMED_PRESETS.get("custom", NAMED_PRESETS["balanced"])` **silently substituted Balanced's
thresholds for every user who selected Custom.** The pre-existing unit test
(`test_custom_preset_supports_the_same_toggle_set`) only proved the `FilterProfile`
dataclass *could* represent a custom profile — it never exercised the real selection→watch
flow, which is exactly how this slipped through review.

**After:** built the missing feature for real, not just patched the fallback:
- `analysis/filter_presets.py` — added four numeric ladders (liquidity, pool age, max tax,
  market-cap band), a toggle map for the three genuinely-variable boolean gates
  (`reject_active_freeze_authority`, `reject_high_concentration`, `require_social_presence`
  — honeypot rejection is deliberately **not** exposed as a toggle anywhere, including here,
  since Part I.2 treats it as a floor), and `default_custom_profile` /
  `set_bool_field` / `cycle_numeric_field` / `get_toggle_value` / `get_numeric_value`.
- `rendering/menus.py` — a real `render_advanced_rules` screen.
- `handlers/watch_handler.py` — four new handlers (`rule_advanced`, `rule_tgl`, `rule_num`,
  `rule_save`), and the actual bug fix: `_handle_watch_start` now reads the user's real
  draft profile for `"custom"` instead of the dict-miss fallback.
- Toggle buttons use **explicit target values** (`rule_tgl:field:on`/`off`), not a blind
  flip — a blind flip is unsafe against Telegram's stale-button problem (a double-tap on a
  not-yet-redrawn button would silently reverse the field); an explicit target is idempotent
  against that instead.

### Fix 2 — A third instance of the "missing FSM edge" bug class, caught before shipping

While wiring the toggle/cycle buttons to stay on the Advanced Rules screen after each tap,
found that `CONFIGURING_FILTER` had no self-loop in `state/fsm.py`'s adjacency map — the
first tap would have raised `InvalidTransitionError`. This is the same bug class as two
prior, already-fixed bugs in this same file (`TRADE_STAGING`'s missing self-loop,
`AUTO_WATCH_ACTIVE`→`RESULT_DETAIL`'s missing edge) — README.md explicitly recommended
checking for exactly this. Added as documented addition #8. **Also had to fix
`tests/test_fsm.py`'s own hand-encoded expectation table**, which would otherwise have
started failing against the corrected implementation (that table is intentionally
independent of the real adjacency map, to test the map against the Playbook's spec rather
than against itself).

### Unrelated cleanup found in passing

`handlers/dispatcher.py`'s `dispatch()` had its entire fallback path (user-id check +
handler loop + log statement) duplicated verbatim immediately after itself — dead,
unreachable code, almost certainly a copy-paste artifact. Removed; no behavior change
(confirmed by inspection: the first copy always returns or the loop's own broad
`except Exception` always returns, so the second copy could never execute).

### Tests added for Step 12

- `tests/test_filter_presets.py`: 7 new unit tests for the ladder/toggle helpers
  (default-seeds-from-Balanced, explicit-set-not-flip, unknown-key raises, ladder
  advance+wraparound, market-cap paired-tuple cycling, off-ladder recovery,
  numeric/toggle getters).
- `tests/test_watch_flow_integration.py`: 2 new integration tests run through the real
  `Dispatcher` — one proves the exact original bug's symptom is gone
  (`status.profile_name == "custom"`, not `"balanced"`, after a full
  advanced→toggle→cycle→save→start sequence, with the specific configured values still
  present in FSM payload afterward), one proves a stale `rule_tgl` tap from an unrelated
  screen (`AUTO_WATCH_ACTIVE`, which genuinely cannot reach `CONFIGURING_FILTER`) degrades
  to the dispatcher's existing generic recovery message instead of crashing.

**OFFICIAL PLAYBOOK STEP 12 — VERIFIED** (to the standard stated above), with the Custom
Filter feature now real and one additional FSM gap closed pre-emptively.

---

## 2. OFFICIAL PLAYBOOK STEP 13 REVALIDATION — Social Intelligence Engine

| Requirement | Status | Notes |
|---|---|---|
| Social Engine | PASS | High quality; matches Part III.4 closely. |
| Provider abstraction | PASS | Clean `SocialDataProvider` protocol, dependency inversion. |
| TwitterAPI.io integration | PASS structurally; **1 reliability gap fixed** | See below. |
| Engagement calculations | PASS | Covered by `tweet_frequency_per_day` + `influencer_mention_count` — the actual Playbook Part III.4 text does not specify a separate "engagement" metric beyond these. |
| Audience-quality analysis | PASS | `verified_follower_ratio`. |
| Sentiment analysis | PASS | Lexicon-based, explicit zero-mention/zero-hit guards (no divide-by-zero). |
| Spam/bot detection | PASS | Same mechanism as audience-quality per Playbook's own text — not a separate feature to build. |
| `SocialResult` structure | PASS | Exact match to Part III.4 / Step 13's Public Interface. |
| Degraded behavior | PASS | Sub-feature-granular; covers both "lookup failed" and "account not found" under one `degraded=True` contract. |
| Caching behavior | **Not implemented — not actually Playbook-required** | See note below. |
| Rate-limit handling | **FIXED** | See below. |
| Tests | PASS, excellent | 16 pre-existing tests already covered all 4 of the Playbook's own Definition-of-Done fixture cases plus several extras. 3 new tests added for the retry fix. |
| Integration into the rest of the app | Was deliberately deferred (correctly) → **now wired, see Section 3** | |

### On "caching behavior" and "rate-limit handling"

STEP11_HANDOFF.md's own Step-13 checklist names both explicitly. Checked the actual
Playbook Part VIII Step 13 text directly: **neither is named in the Playbook's own
Constraints, Acceptance Criteria, or Definition of Done for this step.** This is a real
discrepancy between the two documents, surfaced rather than silently resolved one way:

- **Caching**: not built. Would be a legitimate, in-memory-only (Part I.3-compliant)
  enhancement — flagged in Section 7 as future work, not treated as a Step 13 failure since
  the Playbook doesn't ask for it.
- **Rate-limit handling**: built, because it's a reasonable, low-risk, well-precedented fix
  (RugCheck's provider already has an identical retry/backoff pattern from a prior
  reliability pass), not because the Playbook strictly requires it.

### Fix — `TwitterApiIoProvider` had no retry/backoff at all

Every other resilience-hardened provider in this codebase (RugCheck) retries a transient
failure up to 3 times with exponential backoff before raising. `twitterapi_io.py`'s shared
`_get()` helper raised on the very first non-200 or transport error — no protection against
a transient blip or a 429. Added the identical 3-attempt/exponential-backoff pattern,
mirroring RugCheck's own numbers and reasoning exactly, so the two providers' resilience
stays in step even though they were hardened in different sessions.

### Important pre-existing finding, unrelated to Social specifically

While looking for a template to test the new retry logic against, found that three
**pre-existing** provider test files — `test_rugcheck_provider.py`, `test_dexscreener_provider.py`,
`test_solana_rpc_provider.py` — import `aiohttp._ScriptedResponse` and construct
`aiohttp.ClientSession(url_behaviors=...)`. **Neither exists in real `aiohttp`, and neither
is defined anywhere in this repo** — there is no `conftest.py` at all, anywhere, in the
whole tree. These three files would fail with `ImportError` at collection time under a real
`pytest` run with real `aiohttp` installed, before a single assertion executes. This
predates this session and was not introduced by it; **not fixed here** (out of Step 13/14
scope), but it directly contradicts an assumption anyone would reasonably make from seeing
"tests exist" for those three providers. Flagged in Section 6/7 as the single highest-value
thing to fix next in the test suite. The new `tests/test_twitterapi_io_provider.py` was
deliberately written against plain `unittest.mock` instead, specifically so it doesn't
depend on this same missing scaffolding.

**OFFICIAL PLAYBOOK STEP 13 — VERIFIED** (to the standard stated above), with one
reliability fix made and one pre-existing, out-of-scope test-infrastructure defect
surfaced for visibility.

---

## 3. OFFICIAL PLAYBOOK STEP 14 IMPLEMENTATION — Scoring V3

### Architecture

Implemented exactly the flow the Playbook specifies:

```
Core → (Security ‖ Holder ‖ Social, concurrent) → Momentum → Scoring V3 → Risk/Opportunity → Rendering
```

Security, Holder, and Social all consume only `CoreResult` and never each other's output, so
all three now run concurrently via `asyncio.gather` (extended from the prior pass's
Security‖Holder pair, per this step's own Integration Requirements: "Momentum and Social can
run in parallel with each other and with Holder"). Momentum stays outside the gather,
unchanged — it's synchronous and requires `HolderResult` as an input, so it was never a
candidate for concurrency regardless.

`SocialEngine.analyze` needs a handle-or-ticker string; `PairData` exposes no dedicated
social-link field, so — per Step 13's own anticipated fallback — `run_scan` passes
`core_result.primary_pair.base_token_symbol` (the scanned token's own ticker, not the
pairing currency). Documented as a best-effort assumption in `scan_orchestration.py`'s
module docstring; real-world hit rate against actual X/Twitter accounts is unverified (see
Section 6).

### Files modified (verified against a diff of the original upload, not memory)

**Scoring core:**
- `scoring/formulas.py` — added `sentiment_score()` and `score_opportunity_v3()`, the
  final, unabridged Part III.6 formula with no redistribution logic left in it.
- `scoring/pipeline.py` — replaced the `NotImplementedError` v3 stub with a real dispatch
  branch (`_score_v3`, `_explain_v3`); narrowed `social`'s type from `Any` to
  `SocialResult | None`.

**Orchestration and composition:**
- `handlers/scan_orchestration.py` — `run_scan` gained a required `social_engine` parameter
  and `on_social_complete` hook; `ScoredResult` gained a `social` field; Social now runs in
  the concurrent gather; real `social_result` now flows into both `MomentumEngine.compute`
  and `ScoringPipeline.score`.
- `main.py` — constructs `TwitterApiIoProvider`/`SocialEngine`; wires `social_engine`
  through `AutoWatchManager`, `ScanHandler`, `WatchHandler`. Logs a warning at startup if
  `TWITTERAPI_IO_KEY` is unset (Social degrades gracefully on every call rather than
  crashing anything — same principle as an unset optional key anywhere else in this app).
- `handlers/scan_handler.py`, `handlers/auto_watch.py`, `handlers/watch_handler.py` —
  constructors and `run_scan` call sites updated for the new parameter.

**Rendering:**
- `rendering/result_renderer.py` — replaced the Step-7 Social placeholder with a real
  `_social_section`; fixed a now-stale conditional in `_momentum_section` that used to read
  `social_momentum == 0.0` as "not available yet" — now that Social always runs, a
  genuinely-neutral `0.0` reading needs to display as neutral, not as missing data.
- `rendering/menus.py` — `render_scanning_progress`'s trailing "Social analysis arrives in
  a later build step" line removed.

**Filters (closes a loop opened in Step 12):**
- `analysis/filter_presets.py` — `require_social_presence` was a documented no-op in the
  Step 12 pass (no social data existed yet to check). Now that it does, implemented for
  real: a degraded `SocialResult` (covers both "lookup failed" and "no account found") fails
  the gate, exactly like the existing degraded-security/degraded-holder checks beside it.

### Interfaces changed

| Interface | Before | After |
|---|---|---|
| `run_scan(...)` | 6 required positional engine/infra args | 7 — `social_engine` inserted after `momentum_engine` |
| `ScoredResult` | 4 engine-result fields | 5 — `social: SocialResult` added |
| `ScoringPipeline.score(..., social: Any = None)` | `social` untyped | `social: SocialResult \| None = None` |
| `ScanHandler.__init__`, `WatchHandler.__init__`, `AutoWatchManager.__init__` | no `social_engine` param | all three now take `social_engine` |

### Scoring changes — the actual formula, stage by stage

```
v1 (Step 6):  Score_Opportunity = 0.7·ln(Volume_24h)                    [Trend, Sentiment absent — reweighted]
v2 (Step 10): Score_Opportunity = 0.4·Score_Trend + 0.6·ln(Volume_24h)  [Sentiment absent — reweighted]
v3 (Step 14): Score_Opportunity = 0.4·Score_Trend + 0.3·SentimentScore + 0.3·ln(Volume_24h)   [FINAL — unabridged]

SentimentScore = clamp(0,100, (sentiment_ratio + 1.0) × 50.0)   ← this session's documented
                                                                    assumption; the Playbook
                                                                    specifies the term but not
                                                                    its exact scale mapping
Score_Risk — UNCHANGED from v2. Part III.6 gives Risk only three terms and none of them is
             Social; score_risk_v2 is reused as-is, not replaced by a score_risk_v3.
```

A degraded `SocialResult` contributes the neutral midpoint (`SentimentScore = 50.0`), not 0
— consistent with how `vulnerability_penalty`/`holder_concentration_penalty` already treat
their own degraded inputs (unmeasured ≠ confirmed-negative).

### Security / correctness verification (per this step's own explicit requirement)

Grepped the entire diff for any of: private-key handling, transaction signing, seed-phrase
access, fund custody. **None found or added.** Every file touched in this pass is scoring,
orchestration, rendering, or filter logic — `score_opportunity_v3`/`sentiment_score` are
pure functions over already-fetched data with no I/O. The Trading Integration Layer
(Step 11) itself was not modified beyond passing `social_engine` through constructor
signatures it doesn't otherwise touch. The non-custodial architecture is unchanged.

---

## 4. TESTING

### Tests added this session

- `tests/test_filter_presets.py`: +10 tests (7 for the Custom Filter fix, 3 for the
  `require_social_presence` fix).
- `tests/test_watch_flow_integration.py`: +2 integration tests.
- `tests/test_twitterapi_io_provider.py`: **new file**, 3 tests for retry/backoff
  (success-first-try, recovers-after-two-failures, exhausts-and-raises), deliberately
  independent of the missing `_ScriptedResponse` scaffolding.
- `tests/test_scoring_v3.py`: **new file**, 21 tests — `sentiment_score` (neutral midpoint,
  hand-computed linear mapping, bounds, degraded-is-neutral), `score_opportunity_v3`
  (hand-computed formula, zero-volume guard, sentiment sign comparison with isolated
  contribution check, degraded-equals-neutral, 100-cap), pipeline dispatch (v3 reached,
  v1/v2 fallback still reached, both `ValueError` guards), a 5-engine field-consistency
  test, a determinism test, and 3 explainability tests (new sentiment line present, stale
  v2 closing line absent, v2's own lines carried forward unmodified).
- Small updates to existing fixtures in `test_result_renderer.py`, `test_trading_integration.py`,
  `test_scan_orchestration.py`, `test_scan_flow_integration.py`, `test_auto_watch.py` to
  supply the new required `social`/`social_engine` values — plus one **inverted** test in
  `test_result_renderer.py` that used to assert exactly one "Not yet available" placeholder
  remained (Social's); now correctly asserts zero remain.

### Tests executed

**None via real `pytest`.** This sandbox has no network access; `pip install pytest` was
attempted and failed (no cached wheel, no network to fetch one). What was actually run and
is genuinely verified:

- `ast.parse` against all 75 `.py` files in the repository, individually, after every batch
  of edits — real, executed, currently passing. This catches syntax errors (unbalanced
  brackets, bad indentation, missing colons) but **nothing semantic** — wrong types, missing
  runtime attributes, incorrect logic all pass this check silently.
- Every formula, dispatch branch, and FSM transition touched this session was hand-traced
  against specific example inputs before being trusted (documented inline in this session's
  own reasoning, not reproduced in full here) — this is how the `_explain_v3` list-slicing
  test's own bug was caught and fixed before being left in the file, and how the third FSM
  self-loop gap was caught before shipping.

### Known infrastructure limitations

1. No network access in this environment → no real `pytest`/`mypy` run is possible here.
   **Before deploying any of this, run the real test suite** in an environment with
   `aiogram`, `pydantic`, `pytest`, and `mypy` installed.
2. Three pre-existing provider test files (`test_rugcheck_provider.py`,
   `test_dexscreener_provider.py`, `test_solana_rpc_provider.py`) reference test
   infrastructure (`aiohttp._ScriptedResponse`, `ClientSession(url_behaviors=...)`) that
   does not exist in real `aiohttp` and is not defined anywhere in this repo. They will
   fail at collection under a real test run. See Section 2.

---

## 5. SECURITY VERIFICATION

- No private key, seed phrase, transaction-signing, or fund-custody code was touched or
  added anywhere in this session's diff (checked directly, not assumed).
- `score_opportunity_v3`/`sentiment_score` are pure, side-effect-free functions over
  already-fetched engine outputs — Scoring remains analysis-only, per Part I.3/Step 14's
  own explicit constraint.
- `TWITTERAPI_IO_KEY` is read only through `Settings` (Part V.3), never logged, and an
  unset key degrades Social Engine gracefully rather than crashing the process or being
  silently treated as "working."
- The Trading Integration Layer's own logic (deep-link construction, Trade Staging
  approval gate) was not modified — only its constructors' argument lists changed, to
  accept `social_engine` alongside the other four.

---

## 6. REMAINING LIMITATIONS

- **No real test execution in this session** — the single largest caveat on everything
  above; see Section 4.
- **The pre-existing `_ScriptedResponse` test-infrastructure gap** (Section 2) — three
  provider test files are currently uncollectable under real `pytest`+`aiohttp`.
- **Auto-Watch "cooldown behavior"** — not conclusively distinguished from duplicate
  prevention; may be the same mechanism under two names in the original ask, not resolved
  either way.
- **The Custom Filter feature is entirely new code with no prior session's execution
  history behind it** — of everything in this session's diff, it's the piece with the least
  real-world mileage. Recommend it be the first thing exercised against a real `pytest` run.
- **`base_token_symbol` as a Twitter handle/ticker guess** — a documented, Playbook-sanctioned
  fallback (no better field exists in `PairData`), but its actual real-world resolution rate
  against genuine X/Twitter accounts has not been measured.
- **`SentimentScore`'s linear 0–100 mapping** is this session's own documented assumption,
  not a value specified anywhere in the Playbook — reasonable and directly sanity-checkable,
  but worth a second look if scored results start looking off in a sentiment-heavy way.

---

## 7. DEFERRED WORK

- **Playbook Step 15** (Settings/Help/Errors/Polish) — correctly not started; out of this
  session's scope per STEP11_HANDOFF's own explicit instruction.
- **Playbook Step 16** (Final Integration Pass) — correctly not started.
- **Custom-roadmap Step 15** (Database Persistence & User Preferences) — README.md's own
  "Continuing the build" section flags this as an open architectural question (it would
  contradict Part I.3's no-database constraint) left for a human or a future session to
  resolve. STEP11_HANDOFF.md's instructions redirect past this entirely, back to finishing
  the Playbook's own Steps 12–14 — this session followed that redirect and did **not**
  address the open question. It's still open.
- **Social result caching** — a legitimate, in-memory-only (Part I.3-compliant) enhancement
  that would reduce redundant twitterapi.io calls for a repeatedly-scanned token; not built,
  since the Playbook doesn't require it (see Section 2).
- **The `_ScriptedResponse` test-infrastructure gap** — needs either a real shared fixture
  module built (matching whatever those three files' authors originally intended) or those
  three files rewritten against real `aiohttp`/`unittest.mock`, the way the two new provider
  test files in this session were.

---

## 8. CURRENT VERIFIED OFFICIAL PLAYBOOK STATE

| Playbook Step | State |
|---|---|
| 0–11 | Built in prior sessions (per this repo's own README.md history); not re-verified in this session except where Step 12/13/14 work directly touched shared files (`state/fsm.py`, `handlers/dispatcher.py`). |
| **12 — Auto-Watch & Filter Presets** | **VERIFIED**, to this session's stated standard (static analysis + hand-tracing, not live pytest). Custom Filter fixed from non-functional to real; one additional FSM gap closed. |
| **13 — Social Intelligence Engine** | **VERIFIED**, to the same standard. One reliability fix made (retry/backoff); one pre-existing, out-of-scope test-infrastructure defect surfaced. |
| **14 — Scoring V3** | **IMPLEMENTED**, to the same standard. Code-complete, internally consistent by hand-tracing, syntax-verified across the full repo — **not** execution-verified. This is the one meaningful difference from the bar Steps 4–11 were apparently held to in sessions that had real network/pytest access: treat Step 14 as "ready for a real test run," not yet as "tested." |
| 15, 16 | Not started; correctly out of scope. |

**Bottom line:** this session's own verification standard is genuinely weaker than what
this repo's history shows earlier steps received, for one specific, disclosed reason (no
network access in this sandbox) — not because the work was rushed. The right next action,
before anything else, is running the real test suite in an environment that has one.
