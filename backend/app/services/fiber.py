"""Fiber plant loss budget + GPON checks (NG-FI-02/03).

Pure, deterministic functions over a stored :class:`FiberPath` — the optical
analogue of ``services/physical.py``. A GPON path is an ordered OLT→ONU chain
of passive elements; total loss is the sum of their insertion losses, scored
against the class power budget.
"""
from __future__ import annotations

from app.models import (
    FiberCheck,
    FiberElement,
    FiberKind,
    FiberPath,
    GponClass,
    LossBudget,
)

# Standard passive component losses (dB). Splitter losses are the common
# 1:N insertion-loss figures; connector/splice are typical field values.
SPLITTER_LOSS_DB: dict[int, float] = {
    2: 3.6, 4: 7.3, 8: 10.5, 16: 13.7, 32: 17.1, 64: 20.5, 128: 23.6,
}
_CONNECTOR_DB = 0.3
_SPLICE_DB = 0.1

# GPON usable power budget by class (dB).
GPON_BUDGET_DB: dict[str, float] = {
    GponClass.b_plus.value: 28.0,
    GponClass.c_plus.value: 32.0,
    GponClass.c2.value: 32.0,
}

_MAX_REACH_M = 20_000.0   # standard GPON differential reach
_MAX_PHYSICAL_SPLIT = 64  # past this is logical-only (needs reach/budget care)
_MAX_LOGICAL_SPLIT = 128


def element_loss_db(el: FiberElement) -> float:
    """Insertion loss of one element; ``loss_db`` overrides the derived value."""
    if el.loss_db is not None:
        return el.loss_db
    if el.kind == FiberKind.fiber:
        return el.length_m / 1000.0 * el.atten_db_km
    if el.kind == FiberKind.splitter:
        # ponytail: table lookup; unknown ratios fall back to a log2 estimate
        # (~3.4 dB/stage + 1 dB excess) — override with loss_db for odd taps.
        return SPLITTER_LOSS_DB.get(el.split_ratio) or (
            3.4 * (el.split_ratio.bit_length() - 1) + 1.0
        )
    if el.kind == FiberKind.connector:
        return _CONNECTOR_DB
    if el.kind == FiberKind.splice:
        return _SPLICE_DB
    return 0.0


def loss_budget(path: FiberPath) -> LossBudget:
    """Total path loss + margin vs the GPON class budget, with sanity checks."""
    total_loss = round(sum(element_loss_db(e) for e in path.elements), 3)
    total_length = sum(e.length_m for e in path.elements if e.kind == FiberKind.fiber)
    total_split = 1
    for e in path.elements:
        if e.kind == FiberKind.splitter:
            total_split *= e.split_ratio

    budget = GPON_BUDGET_DB.get(str(path.gpon_class), 28.0)
    margin = round(budget - total_loss, 3)

    checks: list[FiberCheck] = []
    checks.append(
        FiberCheck(
            ok=margin >= 0,
            reason=(
                f"Loss {total_loss} dB vs {budget} dB budget → margin {margin} dB"
                if margin >= 0
                else f"Over budget by {-margin} dB (loss {total_loss} > {budget})"
            ),
        )
    )
    checks.append(
        FiberCheck(
            ok=total_length <= _MAX_REACH_M,
            reason=(
                f"Reach {total_length / 1000:.2f} km within {_MAX_REACH_M / 1000:.0f} km"
                if total_length <= _MAX_REACH_M
                else f"Reach {total_length / 1000:.2f} km exceeds {_MAX_REACH_M / 1000:.0f} km max"
            ),
        )
    )
    if total_split > _MAX_LOGICAL_SPLIT:
        checks.append(FiberCheck(ok=False, reason=f"Split 1:{total_split} exceeds 1:{_MAX_LOGICAL_SPLIT} logical max"))
    elif total_split > _MAX_PHYSICAL_SPLIT:
        checks.append(FiberCheck(ok=True, reason=f"Split 1:{total_split} is logical-only (>1:{_MAX_PHYSICAL_SPLIT}); verify reach/budget"))
    else:
        checks.append(FiberCheck(ok=True, reason=f"Split 1:{total_split} within 1:{_MAX_PHYSICAL_SPLIT} physical"))

    return LossBudget(
        total_loss_db=total_loss,
        budget_db=budget,
        margin_db=margin,
        passed=all(c.ok for c in checks),
        total_length_m=total_length,
        total_split=total_split,
        checks=checks,
    )
