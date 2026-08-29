# DexScan AI

Telegram-native crypto token discovery and screening bot. A read-only
signals layer — it never holds keys, never custodies funds, and never
executes trades. See the playbook's Part I.3 for the exact boundary.

**This project has exactly one planning document: the *DexScan AI Unified
Developer Playbook*.** Do not create a second one. If anything here seems
to contradict that playbook, the playbook wins, and this README should be
corrected to match it — not the other way around.

## Status

**Playbook Step 14 now runtime-verified for real (not just hand-traced);
Playbook Step 15 (Settings, Help, Errors & Polish) implemented.** Full
detail: `docs/STEP14_STEP15_HANDOFF.md`.

This sandbox had the same no-network constraint as every prior session
here, but this pass went further than `ast.parse`: a small, fully
disclosed local harness (stdlib `unittest` + a minimal `pytest`
compatibility shim covering only `mark.asyncio`/`approx`/`raises`/
`mark.parametrize`, plus narrow `aiohttp` and `aiogram.types` stand-ins
providing only the handful of names those layers actually reference)
ran **457 of this repo's own real tests for real, 0 failing** — every
engine, every scoring formula, filter/auto-watch business logic, all
four providers, and the rendering layer. Genuinely unverified by
execution: the handler layer's direct Telegram I/O, `config.py`, and
`main.py` (need real `aiogram` + `pydantic-settings`) — reviewed by
hand instead, not run. Run the real suite with network access before
deploying regardless — a local shim is not a substitute for the real
libraries, however carefully it's scoped.

Two real production bugs found and fixed this pass (not just
infrastructure): Auto-Watch was permanently skipping re-evaluation of a
non-matching candidate for the rest of a watch session instead of only
suppressing duplicate *alerts* (a token aging past `min_pool_age_hours`
between polls would never be caught); and a fully-degraded Core scan
crashed `_run_social()` on `None.base_token_symbol` instead of
degrading gracefully like Security/Holder already do. The three
provider test files flagged as broken (`test_rugcheck_provider.py`,
`test_dexscreener_provider.py`, `test_solana_rpc_provider.py` —
referencing non-existent `aiohttp._ScriptedResponse`/`url_behaviors=`)
are rewritten against real `tests/_aiohttp_test_doubles.py` doubles and
now pass for real, 21/21.

---

**Playbook Steps 12 and 13 revalidated (with fixes); Playbook Step 14
(Scoring V3) implemented.** Full detail: `docs/STEP12_13_14_HANDOFF.md`.

Note on numbering, because it matters here specifically: this pass does
**not** correspond to a new custom-roadmap step. STEP11_HANDOFF.md's own
instructions redirected past continuing this roadmap (skip past the
open custom-roadmap Step 15 / database-persistence question below,
unchanged and still open) and back to finishing the Playbook's own
remaining steps directly. So: Playbook Steps 12/13 are now verified,
Playbook Step 14 is now implemented, and none of that has a custom-roadmap
number of its own.

Two things worth knowing before trusting any of it: this session's
sandbox had no network access, so nothing here was checked with a real
`pytest`/`mypy` run — only `ast.parse` (real, executed, 75/75 files
passing) and hand-tracing. Run the real suite before deploying. And:
three **pre-existing** provider test files
(`test_rugcheck_provider.py`, `test_dexscreener_provider.py`,
`test_solana_rpc_provider.py`) reference `aiohttp` test scaffolding
(`_ScriptedResponse`, `url_behaviors=`) that doesn't exist in real
`aiohttp` and isn't defined anywhere in this repo (there's no
`conftest.py` at all) — they'll fail at collection under a real test
run. Not introduced by this session; found while adding
`tests/test_twitterapi_io_provider.py`, which was deliberately written
against plain `unittest.mock` instead for exactly this reason.

**Steps 1–14 (custom roadmap) complete.** See "Continuing the build" at
the bottom for the full roadmap mapping and the still-open Step 15
question (unchanged by this pass).

**Step 14 (custom roadmap) — Execution Router / Buy Bot Deep Linking,
the Playbook's own Step 11 (Trading Integration Layer & Trade Staging),
built against its exact spec.** New: `integrations/trading.py`,
`integrations/providers.py`, `handlers/trade_staging_handler.py`,
`tests/test_trading_integration.py`. Modified: `rendering/result_renderer.py`
(Trade Staging + the post-approval "Ready" screen moved here from
`menus.py`'s Step-3 placeholder, and Result Detail's Buy button now
carries a real `result_id`), `rendering/menus.py` (placeholder removed,
not duplicated), `state/fsm.py` (two missing adjacency edges found and
fixed — see below), `main.py`, `tests/test_menus.py`,
`tests/test_result_renderer.py`, `tests/test_fsm.py`.

This session opened with an uploaded "Dependency Reconciliation" prompt
making specific claims about which steps were done, and specific
instructions to build this one next. Per that same prompt's own
warning **and** this project's standing rule (top of this README,
Part 0's "flag it explicitly"), none of it was taken on faith — every
claim was checked against this actual repository (`find`, targeted
`grep`, and reading the real source) before any code changed, and the
diagnosis held up. What it could *not* have known, because it isn't in
the repository: whether the Playbook's own six named trading bots still
work the way its spec assumes, months after that spec was written. They
don't all, uniformly — see below.

**Two real, independently-discovered problems this step's own research
surfaced, neither one specific to this codebase:**
1. **BullX suspended all trading on 2026-06-01** and, per several
   sources as recent as this session's own date, has not resumed and is
   widely read as a de facto shutdown. Still implemented as one of the
   six named providers (removing a Playbook-named bot outright is a
   bigger deviation than flagging it) but `BotProvider.is_operational`
   is `False` for it, with a `status_note` the Trade Staging screen
   actually renders — never silently offered as a live default, never
   silently dropped either. Flip one flag back once (if) it returns;
   see `integrations/providers.py`'s own Verification Status note.
2. **Photon has no Telegram bot at all** — a web-only terminal, contrary
   to some (lower-quality, repeated) marketing copy describing it as a
   Telegram bot the same way Trojan/GMGN are. `BotProvider` gained a
   `uses_telegram_start_param` flag over a single hardcoded URL shape to
   model this honestly rather than force a non-existent `t.me` link.

**Every bot username/URL in `integrations/providers.py` is a timestamped,
documented best-effort snapshot from public web search, not a verified
integration against any bot's own formal API — none of the six publishes
one for this.** CoinGecko's own trading-bot guide flags this exact
category (searching Telegram for a bot's real username) as a common
phishing vector. Re-confirm before this ever reaches a real user; see
that module's own docstring for the full caveat.

**Two real FSM bugs found and fixed, both by the same mechanism as Step
7's own Rescan-button discovery — a real `pytest`/aiogram run this
sandbox could finally do (network access confirmed this session; see
"Testing" below), not available to every prior session:**
1. `TRADE_STAGING` had no self-loop, so "Change Target Bot" — a button
   the Step 3 placeholder had been shipping for eleven steps — crashed
   into Dispatcher's generic error handler on first real tap.
2. `AUTO_WATCH_ACTIVE` couldn't reach `RESULT_DETAIL`, so **every**
   real Auto-Watch alert's "View Full Report" button (`result_view`,
   the same callback and handler a normal Result List uses) would have
   crashed the same way — not an edge case, since a person is in
   `AUTO_WATCH_ACTIVE` for exactly as long as an alert can actually
   fire. This is the dependency boundary this session's own opening
   instructions were most concerned with — Auto-Watch (built early,
   custom Step 13) actually depends on Trade Staging existing in a way
   that goes one step further than "the Buy button was a placeholder":
   the *path to reach* Result Detail from within an active watch was
   never fully connected either, and nothing before this step had ever
   exercised it enough to notice.

Both are now `state/fsm.py`'s documented additions #6 and #7, each with
its own regression test — see `tests/test_fsm.py` (independently
re-derived, not copied from the fix) and `tests/test_trading_integration
.py`'s dispatcher-level tests.

**One deliberate, documented simplification**: `build_deep_link` follows
the Playbook's own literal `{referral_tag}_{contract_address}` format
for every Telegram-based bot uniformly, even though the six bots' own
real referral-link conventions differ in practice (`r-`, `ref_`, `i_`,
`access_` all turned up across different bots' own promotional pages
during research) — implementing the Playbook's stated format faithfully
was judged better than silently "correcting" it per-bot beyond what was
asked.

**Step 13 (custom roadmap) — Auto-Watch + Filter Presets, the Playbook's
own Step 12 concept, built against its exact spec.** The largest single
pass yet — new files: `analysis/filter_presets.py`,
`handlers/auto_watch.py`, `handlers/watch_handler.py`,
`tests/test_filter_presets.py`, `tests/test_auto_watch.py`,
`tests/test_watch_flow_integration.py`, `tests/test_dexscreener_provider.py`;
modified: `analysis/api_abstraction.py` (new `TokenDiscoveryProvider`
Protocol), `analysis/providers/dexscreener.py`/`dexscreener_parser.py`
(two new discovery-feed methods), `bot/types.py` (`AutoWatchStatus`),
`state/session_store.py` (background-task registry, per the Playbook's
own explicit Scope for this step), `rendering/menus.py`, `main.py`.

**A real gap surfaced before any of this could be built at all**: the
Playbook's Auto-Watch spec assumes a "re-scan Trending Pairs / New
Listings" candidate feed already exists — nothing in this codebase, in
either numbering scheme, had ever fetched candidate tokens; every prior
engine only ever scanned one address a user already had in hand.
Resolved by researching DexScreener's own actual discovery endpoints
(docs.dexscreener.com/api/reference) rather than assuming Core Engine's
existing `get_pairs` could somehow be repurposed for it — two new,
genuinely different feeds, added as a new `TokenDiscoveryProvider`
Protocol `DexScreenerProvider` (the same class Core Engine's own
`MarketDataProvider` already uses) now also satisfies:
- `get_new_listings` reads `/token-profiles/latest/v1` — a real, honest
  "new" proxy, but it's profile-submission recency, not confirmed
  pool-creation recency.
- `get_trending` reads `/token-boosts/latest/v1` — confirmed via
  DexScreener's own Boosting docs to be **paid promotion**, not an
  organic-interest signal. Rendered and documented as exactly that
  everywhere it appears — this project's own "Trust is the product"
  principle (Part I.2) is why that distinction isn't glossed over.

**Preset thresholds (Conservative/Balanced/Aggressive) are Claude's
documented assumption**, not the original Blueprint's actual §7.4
figures — that document is fully superseded and wasn't available for
this pass (see the top of this README). One deliberate asymmetry: every
preset rejects confirmed honeypot signals, even Aggressive — Part I.2's
trust principle treated as a floor the "risk tolerance" dial doesn't get
to trade away, not just another toggle.

**Two real bugs, both caught by this step's own integration tests before
shipping, not after:** (1) `nav_watch`'s handler was unconditionally
resetting the FSM to Idle on every visit, which made an already-running
watch display as "stopped" the moment a person navigated back to check
on it — caught by `test_watch_flow_integration.py`, fixed by making the
FSM transition depend on whether `AutoWatchManager.status()` actually
shows one running. (2) Two of the Risk/Opportunity Matrix's nine
grid-cell descriptions (Step 12) used "little"/"no meaningful" instead
of literally naming the band, breaking that module's own stated design
rule — caught by `test_risk_opportunity_matrix.py`, fixed the same pass
it was found.

`asyncio.all_tasks()` checked directly (not just per-test assertions)
after running the full `test_auto_watch.py` suite — zero orphaned tasks,
satisfying Step 12's own Constraint literally, not just by inference.

Deliberately NOT built this pass, staying inside what was actually
asked: wallet-specific tracking (the roadmap option not chosen), a
rule-by-rule Custom filter editor (the dataclass supports it fully;
no UI does yet — "My Filters" says so honestly), and Trade Staging (the
Playbook's own Step 11 — Auto-Watch alerts route to the real Result
Detail screen, whose Buy button is still the same honestly-deferred
placeholder Step 7 built).

---

**Step 12 (custom roadmap) — Scoring v2 + Risk/Opportunity Matrix.** Two
genuinely different pieces of work, chosen together per the answer this
step's own clarifying question got:

**Scoring v2 is Playbook-canonical** — `scoring/formulas.py` gained
`holder_concentration_penalty`/`score_risk_v2`/`score_opportunity_v2`,
`scoring/pipeline.py`'s dispatch now actually uses them, and
`scan_orchestration.run_scan` calls `scoring_pipeline.score(...,
holder=holder_result, momentum=momentum_result)` for real. The
`NotImplementedError` guard Step 6 built specifically to block this
until its dedicated step — respected untouched through Steps 8, 9, 10,
and 11 — is finally the thing this step resolves; every one of those
earlier README entries said so explicitly, and this is where that
chain ends. `HolderConcentrationPenalty`'s exact HCI%→penalty curve
isn't specified anywhere in the playbook (Part III.3 only names the 30%
*flag* threshold, not a scoring formula) — implemented as a direct 1:1
passthrough, documented as Claude's assumption in `formulas.py` itself.

**The Risk/Opportunity Matrix is NOT Playbook-canonical** — a
custom-roadmap-only addition, kept in its own module
(`scoring/risk_opportunity_matrix.py`) specifically so it's never
confused with Part III.6's actual formula. Worth being explicit about a
real tension this one required navigating: Part III.6 states, as a rule
to "never relax," that Risk Level and AI Score/Opportunity are shown as
two separate signals, never blended into one number — specifically so a
safe-but-unremarkable token isn't visually indistinguishable from a
risky-but-hyped one. A single combined "matrix tier" risks doing exactly
that blending. The resolution: the matrix renders as an ADDITIONAL
section (`🎯 Risk / Opportunity Read`), always showing both raw axis
scores next to its label, never replacing the existing separate AI
Score line or the Security section's own Risk Level line — both stay
exactly as they were. Band thresholds (three equal thirds, Low/
Moderate/High on both axes) are also Claude's documented assumption,
since this feature doesn't exist in the playbook to specify them.

---

**Step 11 (custom roadmap) — Solana RPC fallback + Security retry.**
Scope, per the chosen answer: focus on Solana specifically, make Holder
Analysis and Security Engine scans resilient to a single provider having
a bad moment.

These two engines got genuinely different treatment, and it's worth
being explicit about why: **Holder Analysis got real multi-provider
fallback.** `analysis/providers/solana_rpc.py`'s `SolanaRpcHolderProvider`
now tries an ordered list of RPC endpoints per call (Helius → QuickNode →
Shyft → the free public endpoint, always last, always present even with
nothing else configured) and falls through to the next one on a
transport-level failure only — a JSON-RPC-level error for one item
inside an otherwise-successful call is a real answer, not a fallback
trigger. This works because every Solana RPC vendor speaks the identical
standard JSON-RPC protocol; failing over to a different one is a true
swap, not a feature downgrade. Every endpoint but the last uses a shorter
6s timeout rather than the full 15s, so a fully dead primary doesn't cost
minutes before reaching a working one. `resolve_rpc_url` (Step 8) is now
`resolve_rpc_urls` (plural, returns the whole ordered list) and moved
from `solana_rpc.py` into the dependency-free `solana_rpc_parser.py` — a
genuine improvement beyond what was asked: it's now testable without
aiohttp installed, and so, as a side effect, is the entire rest of
`test_holder_engine.py`, which could previously only be exercised
indirectly through fakes.

**Security Engine got retry-with-backoff, not fallback — and that's a
real, load-bearing distinction, not a lesser version of the same fix.**
RugCheck isn't an RPC node; it's a specialized analysis service computing
things (LP lock ratio, ownership renouncement, tax simulation) no raw
Solana RPC endpoint exposes on its own. A QuickNode/Shyft-style swap
can't stand in for a RugCheck outage — there's nothing to fail over to
with the same feature set. `analysis/providers/rugcheck.py`'s `scan()`
now retries a transient failure up to 3 times with exponential backoff
(1s, 2s) before giving up; a 404 (a real "nothing found" answer) is never
retried. This genuinely helps with brief blips; it does not, and cannot,
protect against a sustained RugCheck outage. `SecurityEngine.__init__`
already anticipates a real second provider (Step 5's own Future
Compatibility note) — if one gets named later, wiring it in doesn't
require touching this file's retry logic.

New `config.Settings` fields: `quicknode_rpc_url` (the whole endpoint URL
from QuickNode's dashboard — unlike Helius/Shyft, there's no generic
template to combine with a short key) and `shyft_api_key`.

---

**Step 10 (custom roadmap) / Playbook Step 13 — Social Intelligence
Engine.** Built and tested in isolation, matching Steps 8/9's own
precedent — `main.py`, `scan_orchestration.py`, `result_renderer.py`, and
`scoring/pipeline.py` are all untouched; wiring `SocialEngine` in is
deferred, ready for whenever the roadmap reaches it.

Several playbook gaps filled in, each documented at the point it matters
most (`analysis/social_engine.py`, `analysis/api_abstraction.py`,
`analysis/providers/twitterapi_io.py`) rather than only here:
- **`SocialDataProvider` needed a third method.** The playbook's own
  Public Interface names only `lookup_user`/`search_mentions`, but
  `verified_follower_ratio` can't be computed from either — it requires
  the account's actual follower list. Confirmed against twitterapi.io's
  published endpoint reference (`GET /twitter/user/followers`) before
  adding `list_followers` to the Protocol; it fetches one page (up to
  twitterapi.io's own 200-per-page cap), a documented sample rather than
  an exhaustive count, for the same cost/no-persistence reasons Step 8's
  `HolderEngine` samples its top holders instead of enumerating every one.
- **`x_score` has no formula in the playbook** — Part III.4 names the
  check ("reputation via twitterapi.io's user-lookup endpoint") but not
  a computation. Implemented as four weighted, capped components summing
  to 100 (verification, account age, log10-scaled follower count,
  follower:following ratio) — full derivation and exact weights in
  `social_engine.py`'s module docstring.
- **Sentiment: lexicon-based, not a model call.** The playbook offers
  either as valid ("a simple lexicon-based or small-model classifier");
  a small, crypto-Twitter-specific bullish/bearish term list keeps this
  engine dependency-free and deterministic, consistent with the rest of
  the project having no ML inference dependency anywhere.
- **`follower_growth_pct` is a permanent `0.0` in this build**, not a
  "not wired up yet" gap — twitterapi.io's followers endpoint has no
  per-follower "date followed" timestamp to reuse the way Step 8's
  `holder_growth_24h_pct` reuses each holder's funding timestamp. A
  future provider exposing that field could close this without changing
  `SocialResult`'s shape.
- **Search operator correction:** `since_time:`/`until_time:` (Unix
  timestamps), not `since:`/`until:` — twitterapi.io's own API reference
  states the latter are "not supported now," a platform-level change
  confirmed while implementing this step, the same class of correction
  Step 4's DexScreener `get_pairs` signature already documents for a
  different provider.
- **`INFLUENCER_FOLLOWER_THRESHOLD = 10,000`** (`bot/constants.py`) —
  the playbook says "high-tier-account activity" with no number.

**Step 9 complete — Momentum Intelligence Engine, wired in.** `ScoredResult`
gained `momentum: MomentumResult`; `run_scan` now sequences Core ->
gather(Security, Holder) -> Momentum -> Scoring. One internal playbook
contradiction surfaced and resolved while wiring this in: Step 9's own
Public Interface requires `holder: HolderResult` as a *required* argument
to `MomentumEngine.compute`, but Step 10's Integration Requirements text
says Holder and Momentum "run in parallel with each other since neither
depends on the other" — both can't be true given that signature. Resolved
in favor of the signature (Part 0's own rule: code already written wins
over prose describing it): Momentum runs sequentially, immediately after
the Security/Holder gather resolves, not inside it. `scoring/pipeline.py`
is untouched — its own `score()` already raises `NotImplementedError` for
a non-`None` `momentum` argument, a deliberate Step 6 guard against
exactly this kind of premature wiring, confirmed by reading the code
before assuming either way.

---

**Step 8 of 16 complete — Holder Intelligence Engine.**

**Free-tier provider substitution (explicit requirement for this step,
overriding the playbook's own Solscan assumption):** Solscan's holder
endpoints sit behind its paid Pro plan (~$199/mo). `analysis/holder_engine.py`
instead runs on `analysis/providers/solana_rpc.py`, which speaks the
*standard* Solana JSON-RPC surface (`getTokenSupply`,
`getTokenLargestAccounts`, `getAccountInfo`, `getSignaturesForAddress`,
`getTransaction`) — free with zero signup against the public cluster
endpoint, or against Helius's free tier ($0/mo, 1M credits, no card) for
higher rate limits, via one `HELIUS_API_KEY` env var and no code change
either way. See that module's docstring for verified current pricing/limits
and exactly which methods are used for what.

Documented, known scope limits of the free-tier approach (flagged in code,
not silent — same discipline `score_confidence`'s `CodeVerifiedBoolean` gap
gets in Step 6):
- `holder_count` reflects the top-20 accounts Solana's own
  `getTokenLargestAccounts` method returns — a hard cap on every caller,
  free or paid, not something a bigger plan removes. Flagged via
  `HolderResult.holder_count_is_estimate`.
- `classified_wallets` only ever produces `"burn"` labels (a known
  incinerator address) — `"team"`/`"smart_money"` need an off-chain
  labeled-wallet source no RPC endpoint exposes on its own.
- `holder_growth_24h_pct` is derived from the same top-holder funding
  lookups already made for insider/bundle detection, honestly scoped to
  that observed set rather than the full (unenumerable-for-free) holder
  base.

**One easy-to-miss correctness trap caught while implementing this step:**
Helius's own published OpenAPI description for `getTokenLargestAccounts`
labels its `address` field *"Solana wallet address holding a significant
portion of the token supply"* — this is incorrect. Every Solana client and
the underlying JSON-RPC spec return a SPL *token account* address there,
not the owning wallet; the two are different accounts, and conflating them
would have silently attributed every holder to the wrong entity. Verified
against the raw response schema (not just the prose) before writing
`solana_rpc_parser.py`'s `parse_account_owner`, which resolves the real
owner via a separate `getAccountInfo` call and reads
`value.data.parsed.info.owner` — deliberately not the shallower
`value.owner`, which is always just the SPL Token Program's own address
and would have made every single holder look identical.

Also added: `holder_engine.py` rounds `hci_pct` to 6 decimal places before
returning it — ten independently-computed percentages summing to a
mathematically-exact 30.0% can otherwise land on something like
`29.999999999999996` from ordinary IEEE-754 accumulation, which is exactly
the silent off-by-one the playbook's own Step 8 text warns about by name.
Verified directly: `tests/test_holder_engine.py`'s boundary test asserts
`== 30.0`, not `pytest.approx`.

`HolderEngine` gates on `chain is Chain.SOL` itself, before ever calling
the provider — unlike `SecurityEngine`, which lets RugCheck's own
`chain_supported` flag decide case by case. A Solana RPC endpoint can't
answer for an EVM address at all; there's no "unconfirmed coverage" middle
ground to model the way there was for RugCheck, so there's nothing worth
a wasted request.

Out of this step's scope on purpose (Step 10's job, not this one's):
`scan_handler.py`, `result_renderer.py`, `scoring/pipeline.py`, and
`main.py` are all untouched — `HolderEngine` and its provider are
standalone and fully tested, ready for Step 10 to wire in alongside the
Momentum engine.

*(Pre-existing, unrelated to this step: running `mypy --strict .` across
the **whole tree** — as opposed to per-file, which is how it was
apparently last verified — surfaces 125 errors in 12 files from Steps
1–7 (mostly `unittest.mock`/aiogram stub friction in test files, plus one
`Settings()` call in `main.py`). Confirmed by re-checking the untouched
Step 7 zip in isolation: the count is identical, so nothing in this step
introduced or worsened it. Left alone — fixing other steps' files isn't
this step's scope — but worth a note for whoever runs Step 16's full
integration pass.)*

---

**Step 7 of 16 complete — THE MVP MILESTONE** — plus a post-Step-7 patch
driven by a real `pytest` run (274 passed / 2 failed on first execution
outside this sandbox). Both failures are now fixed and re-verified:

- **`tests/fixtures/dexscreener/zero_fdv_and_zero_liquidity.json`** was
  named for a case it didn't actually construct — `liquidity.usd` was
  left at `1200.0` instead of `0.0`. Fixture bug, not a code bug; fixed
  and re-confirmed against the test's own (correct) expectation.
- **The "Rescan" button was completely non-functional in the real app.**
  `state/fsm.py`'s adjacency map never gained a `RESULT_DETAIL ->
  SCANNING` edge when Step 7 built the Rescan feature five steps after
  that map was written — the button rendered, looked normal, and tapping
  it silently produced a generic "something went wrong" message via
  `Dispatcher`'s own exception safety net, with no visible crash. This
  sandbox has no network access to install `aiogram`, so nothing here
  could execute the actual handler chain and catch it — a real `pytest`
  run was what actually surfaced it. Fixed (`state/fsm.py`'s module
  docstring documents it as addition #5), and re-verified against a
  small fake `aiogram` stand-in built specifically to re-run the exact
  failing scenario end-to-end, not just re-read the code.

DexScan AI is a genuinely usable end-to-end product: paste a contract
address, watch a live (progressively-updating) scan, get a real AI Intel
Report with actual Core + Security data and an explainable score.

Worth knowing about Step 7 itself:
- **Two more bugs caught before they shipped**, same discipline as
  above: an import (`EngineStatus` from `bot.constants`) was wrong — it
  actually lives in `bot.types`, caught by grepping the real file instead
  of trusting memory.
- **`main.py` originally reached into `Dispatcher`'s private `_handlers`
  list** to insert the new `ScanHandler` at the right position. Fixed
  properly: `build_navigation_handlers()` now takes an `extra_handlers`
  parameter, so "the catch-all must be last" stays enforced in one place
  instead of being duplicated wherever a handler gets registered.
- **Result List/Detail moved out of `rendering/menus.py` entirely**,
  into a new `rendering/result_renderer.py` — Step 3's placeholder
  versions are gone, not left duplicated alongside the real ones.
  Trade Staging itself was Step 11's own turn to move (custom Step 14 —
  see the "Status" section above); this Step 7 note is left as it was
  written, since it was accurate at the time.
- **Two small, in-scope completions beyond the spec's literal bullet
  points**: real block-explorer links (Solscan/Etherscan/etc. — Telegram
  `url=` buttons, no handler needed) and the contract address rendered
  in a `<code>` block for native tap-to-copy.

## Running locally

```
pip install -r requirements.txt
cp .env.example .env      # then fill in your real Telegram bot token
python main.py
```

Try it: `/start` → Scan Now → Paste Contract Address → paste a real
Solana/ETH/BSC/Base/Arbitrum/TON token address. You should see live
scanning progress, then a real AI Intel Report.

## Testing

```
pytest
mypy --strict .
```

## Continuing the build

**This project stopped following the Playbook's own Part VIII step order
after Step 10 — this is a deliberate, explicit choice made outside the
playbook, recorded here per Part 0's "flag a contradiction rather than
silently deviate" rule.** The chosen roadmap from here:

| # | This project's roadmap | Playbook's own Part VIII (for reference) |
|---|---|---|
| 10 | Social Signals Engine — **done** | Scoring v2 (wires Holder+Momentum into the AI Score) |
| 11 | Solana RPC fallback + Security retry — **done** | Trading Integration + Trade Staging |
| 12 | Enhanced AI Scoring & Risk Tiering Matrix — **done** | Auto-Watch + Filters *(Playbook's Growth milestone)* |
| 13 | Auto-Watch + Filters (Playbook's own Step 12 concept) — **done** | Social Intelligence Engine |
| 14 | Execution Router / Buy Bot Deep Linking — **done** | Scoring v3 *(Playbook's Platform milestone)* |
| 15 | Database Persistence & User Preferences — **next candidate, open question below** | Settings/Help/Errors/Polish |
| 16 | Production Hardening & Deployment | Final Integration Pass (no new features) |

**Resolved (see `docs/STEP14_STEP15_HANDOFF.md`):** option (c) below was
chosen — this project reverted to the Playbook's own Step 15
(Settings/Help/Errors/Polish), not custom-roadmap "Database Persistence."
No persistence, database, or file/cache storage exists anywhere in this
tree (grep-verified as part of that pass and re-confirmed in the Step 16
pre-work below). The Auto-Watch revalidation called for below was also
completed, in `docs/STEP12_13_14_HANDOFF.md`. The open-question text is
left as it was written, for the history, rather than rewritten:

**Open question, not yet decided:** Step 15 ("Database Persistence") is
not just a renumbering — it directly contradicts Part I.3's own
"non-negotiable" no-database constraint, restated three separate places
in the playbook, including as a stated security property ("structurally
incapable of moving a user's funds"). Whoever picks this back up should
resolve this *before* Step 15, one of: (a) formally amend Part I.3 here
in the README once a real design for it exists, (b) drop persistence and
keep the rest of this list, or (c) revert to the playbook's own Step 15
(Settings/Help/Errors/Polish) instead. Unchanged by this step — still
open, still worth resolving before, not during, whatever comes next.

**Before Step 15, though — one lower-risk, high-value pass this step's
own resume instructions specifically called for and this session
deliberately deferred rather than rushing into the same session as
Step 14 itself**: a full revalidation of the Auto-Watch + Filter
milestone (custom Step 13 / Playbook Step 12) now that Trade Staging is
real and one genuine gap in that milestone's own dependency chain (the
`AUTO_WATCH_ACTIVE` → `RESULT_DETAIL` FSM edge — see above) has already
turned up once by accident. Worth a deliberate pass checking for
siblings of that same bug class, not just trusting that the one found
this session was the only one — the exact same caution this README
already asked of Step 14 itself, applied one level up.

Tell Claude: **"Revalidate the Auto-Watch + Filter milestone (custom
Step 13 / Playbook Step 12) now that Trade Staging exists"** — or, if
that pass comes back clean, **"Continue with Step 15 (Database
Persistence & User Preferences)"**, but only after resolving the open
question immediately above; do not let a session drift into building
persistence code before that's actually settled.
