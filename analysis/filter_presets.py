"""
Layer: Analysis — Filter Presets (Playbook Part VIII Step 12: Auto-Watch
& Filter Presets; landed as custom-roadmap Step 13).

Pure, provider-free data + predicate logic — a `FilterProfile` in, a
`ScoredResult` in, a bool out. No engine calls, no I/O, no FSM access
(Part V.2's separation of concerns, same purity contract every
rendering/scoring module in this codebase already holds itself to).
`AutoWatchManager` (handlers/auto_watch.py) is the only real caller.

PRESET THRESHOLDS ARE CLAUDE'S DOCUMENTED ASSUMPTION, not the original
Blueprint's actual §7.4 figures — this playbook explicitly supersedes
and fully absorbs that source document ("neither should be consulted
independently going forward," per this playbook's own opening page), and
it was not provided for this step. Part VI's "on ambiguity" rule applies:
reasonable values consistent with Part I-IV, stated here rather than
guessed at silently. Three deliberately different profiles, graduated by
how much real risk each tolerates in exchange for catching newer/smaller
tokens sooner — see each preset's own inline comment for its reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from handlers.scan_orchestration import ScoredResult

# Part III.3's own >30% HCI "flag" threshold, reused here rather than a
# second, uncoordinated number — same constant `holder_concentration_penalty`
# (scoring/formulas.py) already keys off, so a token that scoring flags as
# concentrated and a Conservative watch that rejects concentration always
# agree on where that line sits.
_HIGH_CONCENTRATION_HCI_THRESHOLD = 30.0

# Part III.2's own honeypot definition (module docstring in
# security_engine.py): "≥99%-sell-tax thresholds" as the critical
# honeypot signal. Reused directly rather than re-deriving.
_HONEYPOT_SELL_TAX_THRESHOLD_PCT = 99.0


@dataclass
class FilterProfile:
    """Part VIII Step 12's exact Public Interface. Toggles "mirror Part
    II's Advanced Rules screen: honeypot/freeze/social/whale checks" per
    that step's own Scope note — four boolean gates below, each backed by
    a specific, named field already on `ScoredResult` (see `matches()`'s
    own per-field docstrings for exactly which field each toggle reads).
    `require_social_presence` was a documented no-op through Step 12's
    original pass and this pass's own revalidation (no social data
    reached `ScoredResult` yet at that point); Step 14 wired Social
    Engine into `ScoredResult` and closed the gap — see `matches()`'s own
    comment on that check for what it does now."""

    name: Literal["conservative", "balanced", "aggressive", "custom"]
    min_liquidity_usd: float
    min_pool_age_hours: float
    mkt_cap_range: tuple[float, float]
    max_tax_pct: float
    reject_active_freeze_authority: bool = True
    reject_honeypot_signals: bool = True
    reject_high_concentration: bool = False
    require_social_presence: bool = False


# Conservative: strict on every axis - real liquidity, a few days of
# trading history, a wide but sane market-cap band, low tax tolerance,
# every safety gate on. Optimized for fewer, higher-confidence matches.
CONSERVATIVE = FilterProfile(
    name="conservative",
    min_liquidity_usd=50_000.0,
    min_pool_age_hours=72.0,
    mkt_cap_range=(100_000.0, 50_000_000.0),
    max_tax_pct=5.0,
    reject_active_freeze_authority=True,
    reject_honeypot_signals=True,
    reject_high_concentration=True,
    require_social_presence=True,
)

# Balanced: meaningfully looser on liquidity/age/tax (catches tokens
# within their first day, not just their first week), keeps the two
# hard contract-safety gates (freeze authority, honeypot) on, drops the
# two softer/more-judgment-call gates (concentration, social) to
# report-only rather than filtering them out entirely.
BALANCED = FilterProfile(
    name="balanced",
    min_liquidity_usd=15_000.0,
    min_pool_age_hours=24.0,
    mkt_cap_range=(20_000.0, 200_000_000.0),
    max_tax_pct=10.0,
    reject_active_freeze_authority=True,
    reject_honeypot_signals=True,
    reject_high_concentration=False,
    require_social_presence=False,
)

# Aggressive: as loose as this project's own values allow it to get.
# Honeypot rejection stays on even here deliberately - Part I.2's "trust
# is the product" principle treats a confirmed honeypot as a floor this
# preset doesn't get to trade away for speed, not a risk dial like the
# other three gates are.
AGGRESSIVE = FilterProfile(
    name="aggressive",
    min_liquidity_usd=3_000.0,
    min_pool_age_hours=1.0,
    mkt_cap_range=(1_000.0, 1_000_000_000.0),
    max_tax_pct=15.0,
    reject_active_freeze_authority=False,
    reject_honeypot_signals=True,
    reject_high_concentration=False,
    require_social_presence=False,
)

NAMED_PRESETS: dict[str, FilterProfile] = {
    "conservative": CONSERVATIVE,
    "balanced": BALANCED,
    "aggressive": AGGRESSIVE,
}

# ---------------------------------------------------------------------------
# Custom profile support (Step 12 revalidation fix).
#
# Prior state: selecting "Custom" stored the bare string "custom" in FSM
# payload, but NAMED_PRESETS has no "custom" key, so `_handle_watch_start`'s
# `NAMED_PRESETS.get(preset_key, NAMED_PRESETS["balanced"])` silently fell
# back to Balanced — Custom never actually customized anything. The
# dataclass always supported an arbitrary `FilterProfile(name="custom",
# ...)` (see the pre-existing `test_custom_preset_supports_the_same_
# toggle_set`); what was missing was any real path that *builds* one from
# user taps and *uses* it. This section is that path's analysis-layer half;
# handlers/watch_handler.py's `rule_tgl`/`rule_num`/`rule_advanced`/
# `rule_save` handlers are the flow-layer half.
#
# Design choice: stepped ladders + toggles, not free-text numeric entry.
# Matches Part II.7's callback-driven architecture (no text-input FSM state
# exists for this), and Part I.2's own "toggles before numeric config"
# progressive-disclosure principle — a discrete tap-to-cycle control is the
# same interaction shape as a toggle, just with more than two positions.
# ---------------------------------------------------------------------------

# Each ladder is ordered loosest -> strictest. Cycling always moves one
# step toward stricter and wraps back to loosest after the last step, per
# `cycle_numeric_field`'s own docstring.
LIQUIDITY_LADDER_USD: tuple[float, ...] = (3_000.0, 15_000.0, 50_000.0, 150_000.0)
POOL_AGE_LADDER_HOURS: tuple[float, ...] = (1.0, 24.0, 72.0, 168.0)
MAX_TAX_LADDER_PCT: tuple[float, ...] = (25.0, 15.0, 10.0, 5.0)
MKT_CAP_BAND_LADDER: tuple[tuple[float, float], ...] = (
    (1_000.0, 1_000_000_000.0),
    (20_000.0, 200_000_000.0),
    (100_000.0, 50_000_000.0),
    (500_000.0, 20_000_000.0),
)

# field_key (used in rule_num:<field_key>) -> (FilterProfile attr, ladder).
# A fifth custom-configurable numeric field is one new entry here (Part
# V.2 Open/Closed), never a new branch in cycle_numeric_field itself.
_SCALAR_NUMERIC_FIELDS: dict[str, tuple[str, tuple[float, ...]]] = {
    "liquidity": ("min_liquidity_usd", LIQUIDITY_LADDER_USD),
    "age": ("min_pool_age_hours", POOL_AGE_LADDER_HOURS),
    "tax": ("max_tax_pct", MAX_TAX_LADDER_PCT),
}
NUMERIC_FIELD_KEYS: tuple[str, ...] = ("liquidity", "age", "tax", "mktcap")

# field_key (used in rule_tgl:<field_key>:<on|off>) -> FilterProfile attr.
# `reject_honeypot_signals` is deliberately absent: Part I.2 treats it as
# a floor every profile keeps (see AGGRESSIVE's own comment above), never
# a dial — Custom doesn't get an exception the named presets don't have.
_TOGGLE_FIELDS: dict[str, str] = {
    "freeze": "reject_active_freeze_authority",
    "concentration": "reject_high_concentration",
    "social": "require_social_presence",
}
TOGGLE_FIELD_KEYS: tuple[str, ...] = ("freeze", "concentration", "social")


def default_custom_profile() -> FilterProfile:
    """Seed value for a brand-new Custom draft. Starts from Balanced's own
    numbers rather than a zeroed/empty profile, so a draft is a sane,
    immediately-usable filter at every point while it's being tuned, not
    just once every field has been touched."""
    return replace(BALANCED, name="custom")


def get_toggle_value(profile: FilterProfile, field_key: str) -> bool:
    """Reads one of `TOGGLE_FIELD_KEYS` off `profile` — keeps
    `_TOGGLE_FIELDS`'s field_key -> attr mapping private to this module."""
    return bool(getattr(profile, _TOGGLE_FIELDS[field_key]))


def get_numeric_value(profile: FilterProfile, field_key: str) -> float | tuple[float, float]:
    """Reads one of `NUMERIC_FIELD_KEYS` off `profile` — same purpose as
    `get_toggle_value`, kept separate from it because the return type
    genuinely differs (`mktcap` is a paired tuple, the other three are
    plain floats) rather than because the two need different logic."""
    if field_key == "mktcap":
        return profile.mkt_cap_range
    attr, _ladder = _SCALAR_NUMERIC_FIELDS[field_key]
    return float(getattr(profile, attr))


def set_bool_field(profile: FilterProfile, field_key: str, value: bool) -> FilterProfile:
    """Sets one of `TOGGLE_FIELD_KEYS` on `profile` to an explicit `value`,
    returning a new FilterProfile. Deliberately takes the target value
    rather than blind-flipping the current one: a rendered toggle button
    encodes the state it will move *to* (e.g. `rule_tgl:freeze:on`), and a
    stale/duplicate tap on that same button should be a no-op if the field
    already reached that value in between — a blind flip would instead
    toggle it back, silently disagreeing with what the button still says
    on a client that hasn't redrawn yet. Same stale-callback caution Part
    II.5 already applies to whole screens, just at single-field grain.
    Raises KeyError for an unknown field_key — deliberately not defaulted,
    since a bad key here means a callback_data string doesn't match this
    module's own taxonomy, which should surface loudly, not filter-match
    against nothing."""
    attr = _TOGGLE_FIELDS[field_key]
    return replace(profile, name="custom", **{attr: value})


def cycle_numeric_field(profile: FilterProfile, field_key: str) -> FilterProfile:
    """Advances one of `NUMERIC_FIELD_KEYS` to its next (stricter) ladder
    step, wrapping to the loosest step after the last one. `mktcap` cycles
    the paired (min, max) tuple as a single unit — every preset above
    defines that pair together, and independently stepping min or max
    could silently produce an inverted or empty band. If the draft's
    current value isn't one of the ladder's own steps (e.g. it still holds
    a value this ladder doesn't contain), lands on the first ladder step
    rather than raising, since that's a recoverable situation a user
    action shouldn't crash on."""
    if field_key == "mktcap":
        try:
            next_index = (MKT_CAP_BAND_LADDER.index(profile.mkt_cap_range) + 1) % len(
                MKT_CAP_BAND_LADDER
            )
        except ValueError:
            next_index = 0
        return replace(profile, name="custom", mkt_cap_range=MKT_CAP_BAND_LADDER[next_index])

    attr, ladder = _SCALAR_NUMERIC_FIELDS[field_key]
    current_value = getattr(profile, attr)
    try:
        next_index = (ladder.index(current_value) + 1) % len(ladder)
    except ValueError:
        next_index = 0
    return replace(profile, name="custom", **{attr: ladder[next_index]})


def matches(profile: FilterProfile, result: "ScoredResult") -> bool:
    """
    True only if every gate `profile` actually enables passes. A gate
    whose underlying data is degraded counts as a FAILED check, not a
    skipped one — Part I.2's "under-promise" principle applied here:
    better to miss a real match than to alert on a token this project
    couldn't actually verify was safe. `mkt_cap_range`/`max_tax_pct`/
    `min_liquidity_usd`/`min_pool_age_hours` are always checked
    (they're not optional toggles, unlike the four boolean gates).
    """
    core, security, holder, social = result.core, result.security, result.holder, result.social

    if core.degraded:
        return False
    if core.liquidity_usd < profile.min_liquidity_usd:
        return False
    if core.pool_age_days is None or (core.pool_age_days * 24.0) < profile.min_pool_age_hours:
        return False
    if not (profile.mkt_cap_range[0] <= core.market_cap <= profile.mkt_cap_range[1]):
        return False

    if security.degraded:
        # Every remaining check below reads SecurityResult - none of
        # them can be verified without it, so a degraded security scan
        # fails the whole profile rather than silently skipping just the
        # checks that needed it.
        return False
    if max(security.buy_tax_pct, security.sell_tax_pct) > profile.max_tax_pct:
        return False
    if profile.reject_active_freeze_authority and security.freeze_authority_active:
        return False
    if profile.reject_honeypot_signals and security.sell_tax_pct >= _HONEYPOT_SELL_TAX_THRESHOLD_PCT:
        return False

    if profile.reject_high_concentration:
        if holder.degraded:
            return False
        if holder.hci_pct > _HIGH_CONCENTRATION_HCI_THRESHOLD:
            return False

    if profile.require_social_presence:
        # Step 14 fix: Social Engine is now wired into ScoredResult
        # (scan_orchestration.run_scan), closing the gap this branch
        # used to document as a no-op. `social.degraded` covers BOTH
        # "lookup failed" and "account doesn't exist at all" (Step 13's
        # own SocialEngine.analyze contract) - either way, there's no
        # verifiable social presence to require, so this fails the gate
        # exactly like the degraded-security/degraded-holder checks
        # above it, per this function's own docstring: a gate whose
        # underlying data is degraded counts as a FAILED check, not a
        # skipped one.
        if social.degraded:
            return False

    return True
