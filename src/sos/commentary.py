"""Turn the facts payload into one or two sentences a human can read.

V1 is rule-based templates. No API call, no key, no network — the tool is
fully useful with zero LLM dependency, which is the point.

The seam for V1.5 is :func:`generate`. Swapping its body for an Anthropic API
call requires no change anywhere else: same signature, same input payload.
The guardrails that would go in that system prompt are the same rules encoded
below — describe movement, never assert cause, say "within normal variation"
when the move is inside the noise band, and lead with the category when the
shift is a denominator effect.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

#: Share of Search is a leading indicator. Every readout should say so once.
LEADING_INDICATOR_NOTE = (
    "Share of Search tends to lead market share rather than reflect it, "
    "so treat this as an early signal, not a scoreboard."
)


def generate(facts: Dict[str, Any]) -> List[str]:
    """Render commentary bullets from a ``month_facts`` payload.

    Returns one or two bullets for the whole brand set — not one per brand.
    A wall of per-brand sentences is noise; the reader wants to know what
    moved and whether it means anything.
    """
    if not facts or not facts.get("brands"):
        return ["No data available for this month yet."]

    own = facts.get("own_brand_facts")
    if not own:
        return [_category_sentence(facts), LEADING_INDICATOR_NOTE]

    bullets: List[str] = []

    # When the category itself moved, that has to come first. A brand's share
    # rising because a competitor collapsed reads as a win otherwise.
    if facts.get("category_driven"):
        bullets.append(_category_driven_sentence(facts, own))
    else:
        bullets.append(_brand_sentence(facts, own))

    second = _second_bullet(facts, own)
    if second:
        bullets.append(second)

    return bullets


# --------------------------------------------------------------------------
# Sentence builders
# --------------------------------------------------------------------------


def _brand_sentence(facts: Dict[str, Any], own: Dict[str, Any]) -> str:
    brand = own["brand"]
    share = own.get("sos_pct")
    delta = own.get("mom_delta_pp")
    rank = own.get("rank")
    total = facts.get("brand_count")

    parts = [f"{brand} holds {_pct(share)} of category search in {facts['month_label']}"]

    if rank and total:
        parts.append(f"ranking {_ordinal(rank)} of {total}")

    sentence = ", ".join(parts) + "."

    if delta is None:
        return sentence + " No prior month to compare against yet."

    movement = _movement_phrase(delta)
    if own.get("exceeds_noise_threshold") is True:
        sentence += (
            f" That is {movement}, a larger move than this brand's usual "
            "month-to-month variation."
        )
    elif own.get("exceeds_noise_threshold") is False:
        sentence += f" That is {movement}, within normal variation for this brand."
    else:
        sentence += (
            f" That is {movement}, though there is not yet enough history to say "
            "whether it is meaningful."
        )

    return sentence


def _category_driven_sentence(facts: Dict[str, Any], own: Dict[str, Any]) -> str:
    """Lead with the denominator when the denominator is what moved."""
    brand = own["brand"]
    category_change = facts.get("category_total_change_pct") or 0.0
    direction = "grew" if category_change > 0 else "contracted"
    own_change = facts.get("own_volume_change_pct")
    share_move = _movement_phrase(own.get("mom_delta_pp"))

    sentence = (
        f"Total category search {direction} {abs(category_change):.1f}% month on month, "
        f"so share movement in {facts['month_label']} is mostly a category effect. "
    )

    if own_change is not None:
        sentence += (
            f"{brand}'s own search volume changed {own_change:+.1f}% while its share "
            f"went {share_move} to {_pct(own.get('sos_pct'))} — the shift is largely in "
            "the denominator, not in demand for the brand."
        )
    else:
        sentence += (
            f"{brand}'s share went {share_move} to {_pct(own.get('sos_pct'))}, which "
            "reflects the category moving rather than the brand."
        )

    return sentence


def _second_bullet(facts: Dict[str, Any], own: Dict[str, Any]) -> Optional[str]:
    """Whatever is most worth knowing after the headline."""
    # Data gaps first — they make every percentage that month provisional.
    gaps = facts.get("brands_with_data_gaps") or []
    if gaps:
        names = _join(gaps)
        return (
            f"No volume was returned for {names} this month, so the category total "
            "excludes them and every share here is provisional. Treat the month as "
            "incomplete rather than as a real shift."
        )

    # A rank change in the category is usually the next most useful fact.
    movers = [
        b
        for b in facts["brands"]
        if b.get("rank_change") and b["brand"] != own["brand"] and b.get("exceeds_noise_threshold")
    ]
    if movers:
        mover = max(movers, key=lambda b: abs(b.get("rank_change") or 0))
        direction = "up" if (mover["rank_change"] or 0) > 0 else "down"
        return (
            f"{mover['brand']} moved {direction} to {_ordinal(mover['rank'])} on "
            f"{_pct(mover['sos_pct'])} ({_signed_pp(mover.get('mom_delta_pp'))} month on month). "
            + LEADING_INDICATOR_NOTE
        )

    # Year-on-year context beats another month-on-month restatement.
    yoy = own.get("yoy_delta_pp")
    if yoy is not None:
        direction = "above" if yoy >= 0 else "below"
        return (
            f"Year on year, {own['brand']} sits {abs(yoy):.1f} points {direction} where it "
            f"was in {facts['month_label'].split()[0]} last year. " + LEADING_INDICATOR_NOTE
        )

    ambiguous = facts.get("ambiguous_brands") or []
    if ambiguous:
        return (
            f"{_join(ambiguous)} {'is' if len(ambiguous) == 1 else 'are'} flagged as an "
            "ambiguous brand name, so some of that volume may be unrelated searches. "
            + LEADING_INDICATOR_NOTE
        )

    return LEADING_INDICATOR_NOTE


def _category_sentence(facts: Dict[str, Any]) -> str:
    leader = facts.get("leader")
    top = next((b for b in facts["brands"] if b["brand"] == leader), None)
    if not top:
        return f"Category data available for {facts['month_label']}."
    return (
        f"{leader} leads category search in {facts['month_label']} on "
        f"{_pct(top.get('sos_pct'))} of {facts.get('brand_count')} tracked brands."
    )


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _signed_pp(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:+.1f}pp"


def _movement_phrase(delta: Optional[float]) -> str:
    if delta is None:
        return "unchanged"
    if abs(delta) < 0.05:
        return "flat"
    direction = "up" if delta > 0 else "down"
    return f"{direction} {abs(delta):.1f} points"


def _ordinal(number: Optional[int]) -> str:
    if not number:
        return "unranked"
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _join(names: List[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f" and {names[-1]}"
