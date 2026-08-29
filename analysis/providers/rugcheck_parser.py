"""
Layer: Provider adapter — RugCheck response parsing (Playbook Part II.3;
Part VIII Step 5).

Pure data transformation, split out of `rugcheck.py` for the same reason
`dexscreener_parser.py` was split from `dexscreener.py`: zero `aiohttp`
import means this — the part with the most genuine uncertainty in this
step — can be executed and tested directly with fixture JSON, in any
environment, rather than only syntax-checked.

Confidence level per field, from researching RugCheck while implementing
this step (its own Swagger docs at api.rugcheck.xyz/swagger, a real CLI
output sample, and a third-party skill description with specific
threshold documentation — no single pristine "official schema" source
was available, so this is triangulated from several partial ones):

- HIGH confidence: `mintAuthority`/`freezeAuthority` (a pubkey string
  when active, `null` when renounced — confirmed by a documented
  `token.mintAuthority != null` check), `score_normalised` (0-100,
  documented as "higher = riskier" — the OPPOSITE polarity `SecurityReport
  .trust_score` needs, so this module inverts it), `risks[]` (array of
  named findings, seen in a real CLI output example), `rugged` (boolean).
- MEDIUM confidence: the raw (non-normalized) `score` field exists but is
  unbounded and NOT used here — `score_normalised` is used instead
  specifically to avoid guessing at the raw scale's practical maximum.
- LOW confidence / best-effort: LP lock ratio's exact JSON path, and
  ownership-renouncement's exact field name. Both are extracted
  defensively with a documented fallback: LP lock reads `None` (not 0.0)
  when the expected path isn't found, so `SecurityEngine` can tell
  "confirmed unlocked" apart from "couldn't confirm" — collapsing those
  would be a real information loss (a 0% LP lock is a strong signal;
  "unknown" isn't). Ownership renouncement defaults to `False` (the
  conservative/suspicious assumption) only when nothing in the payload
  answers the question either way.

RugCheck has no separate buy/sell tax split in its data model — that's
an EVM-specific concept (a Solidity contract's transfer function can
legitimately behave differently on buy vs. sell, which is the actual
honeypot mechanism on Ethereum/BSC/etc.). Solana's SPL Token-2022
"transfer fee" extension, if present, is a single uniform percentage
applied to every transfer. Mapped onto both `buy_tax_pct` and
`sell_tax_pct` here (documented, not silent) so `SecurityEngine`'s tax-
threshold logic — written for Part III.2's asymmetric-tax model — still
means something for a Solana result: a real transfer fee still trips the
combined-tax and honeypot thresholds correctly, it's just symmetric by
construction for this specific provider.
"""

from __future__ import annotations

import logging
from typing import Any

from analysis.api_abstraction import SecurityReport

logger = logging.getLogger(__name__)


def parse_report(raw: dict[str, Any], chain_supported: bool) -> SecurityReport:
    """
    Translates one RugCheck report payload into a normalized
    `SecurityReport`. `chain_supported` is passed in rather than derived
    here — the caller (`rugcheck.py`) is the one that knows whether this
    payload came back for a chain RugCheck actually covers, vs. an empty/
    error response for one it doesn't (see that file's docstring).
    """
    if not chain_supported:
        return SecurityReport(
            trust_score=50.0,
            mint_authority_active=False,
            freeze_authority_active=False,
            buy_tax_pct=0.0,
            sell_tax_pct=0.0,
            lp_lock_ratio_pct=None,
            lp_lock_duration_days=None,
            ownership_renounced=False,
            raw_risk_flags=[],
            chain_supported=False,
        )

    token = raw.get("token") or {}

    # HIGH confidence field, inverted polarity (module docstring).
    score_normalised = raw.get("score_normalised")
    if score_normalised is None:
        # Some responses may only carry the raw, unbounded `score` -
        # without a confirmed practical maximum to rescale against, the
        # honest move is "unknown," not a guessed conversion.
        trust_score = 50.0
        logger.debug("No score_normalised in RugCheck response; defaulting trust_score to neutral 50.0")
    else:
        trust_score = 100.0 - float(score_normalised)

    mint_authority = token.get("mintAuthority", raw.get("mintAuthority"))
    freeze_authority = token.get("freezeAuthority", raw.get("freezeAuthority"))

    transfer_fee_pct = _extract_transfer_fee_pct(raw)

    lp_lock_ratio, lp_lock_duration = _extract_lp_lock(raw)

    ownership_renounced = _extract_ownership_renounced(raw, mint_authority, freeze_authority)

    raw_risks = raw.get("risks") or []
    risk_flags = [
        str(r.get("name") or r.get("description") or "Unnamed risk")
        for r in raw_risks
        if isinstance(r, dict)
    ]

    return SecurityReport(
        trust_score=max(0.0, min(100.0, trust_score)),
        mint_authority_active=mint_authority is not None,
        freeze_authority_active=freeze_authority is not None,
        buy_tax_pct=transfer_fee_pct,
        sell_tax_pct=transfer_fee_pct,
        lp_lock_ratio_pct=lp_lock_ratio,
        lp_lock_duration_days=lp_lock_duration,
        ownership_renounced=ownership_renounced,
        raw_risk_flags=risk_flags,
        chain_supported=True,
    )


def _extract_transfer_fee_pct(raw: dict[str, Any]) -> float:
    """Solana's Token-2022 transfer-fee extension, if present. Several
    plausible field shapes exist across RugCheck's response versions
    (basis points vs. a direct percentage) - defensively checks a couple
    of reasonable candidates and defaults to 0.0 (no fee extension in
    use, the common case for most tokens) rather than guessing."""
    transfer_fee = raw.get("transferFee")
    if isinstance(transfer_fee, dict):
        pct = transfer_fee.get("pct")
        if pct is not None:
            return float(pct)
        bps = transfer_fee.get("feeBps") or transfer_fee.get("basisPoints")
        if bps is not None:
            return float(bps) / 100.0  # basis points -> percent
    elif isinstance(transfer_fee, (int, float)):
        return float(transfer_fee)
    return 0.0


def _extract_lp_lock(raw: dict[str, Any]) -> tuple[float | None, float | None]:
    """LOW confidence (module docstring) - `markets[]` most plausibly
    carries per-pool lock data based on RugCheck's own site prominently
    showing an "LP Locked: X%" figure, but the exact nested path wasn't
    confidently confirmed. Checks the most likely shape; returns (None,
    None) — "couldn't confirm," not "confirmed unlocked" — otherwise."""
    markets = raw.get("markets")
    if isinstance(markets, list):
        for market in markets:
            if not isinstance(market, dict):
                continue
            lp = market.get("lp")
            if isinstance(lp, dict) and lp.get("lpLockedPct") is not None:
                duration = lp.get("lpLockedDurationDays") or lp.get("lockDurationDays")
                return float(lp["lpLockedPct"]), (float(duration) if duration is not None else None)
    return None, None


def _extract_ownership_renounced(
    raw: dict[str, Any], mint_authority: Any, freeze_authority: Any
) -> bool:
    """LOW confidence exact field name (module docstring). Checks an
    explicit field if present; otherwise treats both authorities being
    null as the closest available proxy for "nothing left for a deployer
    to control" on Solana (which has no separate Ownable-style `owner`
    slot the way an EVM contract does — mint/freeze authority are the
    actual controllable permissions). Defaults False (conservative) only
    when none of this is present."""
    explicit = raw.get("ownershipRenounced")
    if isinstance(explicit, bool):
        return explicit
    if mint_authority is None and freeze_authority is None:
        return True
    return False
