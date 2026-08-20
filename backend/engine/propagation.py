"""Propagation-model registry — closed-form path-loss models (NG-RF-01).

Pure and framework-agnostic like the rest of ``engine/``: stdlib ``math`` only,
no native deps (ARM/Windows rule), fully deterministic. Each model is a plain
function with the uniform signature

    path_loss_db(distance_m, freq_mhz, tx_height_m, rx_height_m, **params) -> float

and is registered in :data:`REGISTRY` alongside human-readable metadata (valid
frequency/distance ranges, the params it honours, area-type options). A study
picks a model id and supplies params; :func:`path_loss` dispatches.

Models shipped here are the cheap, standard, closed-form ones:

* ``fspl``          — Friis free-space path loss (reuses ``wireless.fspl_db``).
* ``okumura_hata``  — Okumura-Hata, 150-1500 MHz (urban/suburban/open).
* ``cost231_hata``  — COST-231-Hata PCS extension, 1500-2000 MHz.
* ``p1546_lite``    — a documented *approximation* of ITU-R P.1546 behaviour.

Longley-Rice / ITM is **deliberately deferred** (NG-RF-01 marks it "C/S if too
heavy"): it needs a terrain profile and a non-trivial pure-Python port. The
registry is the seam — add an ``"itm"`` entry here when that port lands, no other
call-site changes.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from engine.wireless import fspl_db as _fspl_hz


# --- Okumura-Hata mobile-antenna correction a(h_re) -------------------------
def _hata_a_hm(freq_mhz: float, rx_height_m: float, large_city: bool) -> float:
    """Mobile-antenna height-gain correction ``a(h_re)`` (dB).

    Small/medium city uses the general formula; large (metropolitan) cities use
    the frequency-split quadratic form from the Hata model.
    """
    hm = rx_height_m
    f = freq_mhz
    if large_city:
        if f >= 300.0:
            return 3.2 * (math.log10(11.75 * hm)) ** 2 - 4.97
        return 8.29 * (math.log10(1.54 * hm)) ** 2 - 1.1
    return (1.1 * math.log10(f) - 0.7) * hm - (1.56 * math.log10(f) - 0.8)


def _hata_core(freq_mhz: float, tx_height_m: float, rx_height_m: float,
               dist_km: float, large_city: bool) -> float:
    """Okumura-Hata *urban* median path loss (dB)."""
    hb = tx_height_m
    a = _hata_a_hm(freq_mhz, rx_height_m, large_city)
    return (
        69.55
        + 26.16 * math.log10(freq_mhz)
        - 13.82 * math.log10(hb)
        - a
        + (44.9 - 6.55 * math.log10(hb)) * math.log10(dist_km)
    )


# --- model callables --------------------------------------------------------
def fspl(distance_m: float, freq_mhz: float,
         tx_height_m: float = 0.0, rx_height_m: float = 0.0, **_: object) -> float:
    """Free-space path loss. Antenna heights are ignored (free space)."""
    return _fspl_hz(distance_m, freq_mhz * 1e6)


def okumura_hata(distance_m: float, freq_mhz: float,
                 tx_height_m: float = 30.0, rx_height_m: float = 1.5,
                 *, area_type: str = "urban", large_city: bool = False,
                 **_: object) -> float:
    """Okumura-Hata median path loss (dB), valid 150-1500 MHz.

    ``area_type`` ∈ {urban, suburban, open}. ``large_city`` selects the
    metropolitan ``a(h_re)`` correction (only affects the urban base term).
    """
    if area_type not in _AREA.options:
        raise ValueError(
            f"unknown area_type {area_type!r} for okumura_hata (expected one of {_AREA.options})"
        )
    d_km = max(distance_m, 1.0) / 1000.0
    urban = _hata_core(freq_mhz, tx_height_m, rx_height_m, d_km, large_city)
    if area_type == "suburban":
        return urban - 2.0 * (math.log10(freq_mhz / 28.0)) ** 2 - 5.4
    if area_type == "open":
        return (
            urban
            - 4.78 * (math.log10(freq_mhz)) ** 2
            + 18.33 * math.log10(freq_mhz)
            - 40.94
        )
    return urban


def cost231_hata(distance_m: float, freq_mhz: float,
                 tx_height_m: float = 30.0, rx_height_m: float = 1.5,
                 *, area_type: str = "urban", **_: object) -> float:
    """COST-231-Hata (PCS extension of Hata), valid 1500-2000 MHz.

    ``area_type`` ∈ {urban, suburban, open}. The metropolitan correction term
    ``C_m`` is 3 dB for ``urban`` (dense metropolitan) and 0 dB otherwise. Uses
    the small/medium-city ``a(h_re)`` form as prescribed by COST-231.
    """
    if area_type not in _AREA.options:
        raise ValueError(
            f"unknown area_type {area_type!r} for cost231_hata (expected one of {_AREA.options})"
        )
    hb = tx_height_m
    a = _hata_a_hm(freq_mhz, rx_height_m, large_city=False)
    d_km = max(distance_m, 1.0) / 1000.0
    c_m = 3.0 if area_type == "urban" else 0.0
    base = (
        46.3
        + 33.9 * math.log10(freq_mhz)
        - 13.82 * math.log10(hb)
        - a
        + (44.9 - 6.55 * math.log10(hb)) * math.log10(d_km)
        + c_m
    )
    if area_type == "suburban":
        return base - 2.0 * (math.log10(freq_mhz / 28.0)) ** 2 - 5.4
    if area_type == "open":
        return (
            base
            - 4.78 * (math.log10(freq_mhz)) ** 2
            + 18.33 * math.log10(freq_mhz)
            - 40.94
        )
    return base


def p1546_lite(distance_m: float, freq_mhz: float,
               tx_height_m: float = 30.0, rx_height_m: float = 1.5,
               **_: object) -> float:
    """A *lite approximation* of ITU-R P.1546 point-to-area behaviour.

    IMPORTANT: this is **not** the full Rec. ITU-R P.1546 method. That method
    interpolates empirical field-strength-vs-distance curves (per frequency
    band, time percentage, path type) and applies terrain/clutter/height
    corrections — it needs the published curve tables, which we do not embed.

    What we approximate instead is P.1546's *qualitative* shape with a bounded
    closed form: free-space loss plus a slowly-growing clutter/diffraction
    excess, minus transmitter/receiver height gains referenced to P.1546's
    nominal heights (h1 = 10 m transmitter, h2 = 1.5 m receiver):

        L = FSPL(d,f) + 18·log10(1 + d_km)      # excess beyond free space
                      - 20·log10(max(h_tx,1)/10) # tx height-gain (ref 10 m)
                      - 10·log10(max(h_rx,1)/1.5)# rx height-gain (ref 1.5 m)

    It is monotonic (rises with distance and frequency, falls with height) and
    stays sane over its declared 30-3000 MHz / 1 m-100 km envelope. Use it as a
    coarse sanity model, not a certification-grade prediction.
    """
    d_km = max(distance_m, 1.0) / 1000.0
    base = _fspl_hz(distance_m, freq_mhz * 1e6)
    clutter = 18.0 * math.log10(1.0 + d_km)
    g_tx = 20.0 * math.log10(max(tx_height_m, 1.0) / 10.0)
    g_rx = 10.0 * math.log10(max(rx_height_m, 1.0) / 1.5)
    return base + clutter - g_tx - g_rx


# --- registry ---------------------------------------------------------------
PathLossFn = Callable[..., float]


@dataclass(frozen=True, slots=True)
class ModelParam:
    """A tunable parameter exposed by a model."""

    name: str
    default: object
    description: str = ""
    options: list[str] = field(default_factory=list)  # closed set, if any

    def as_dict(self) -> dict:
        d = {"name": self.name, "default": self.default, "description": self.description}
        if self.options:
            d["options"] = list(self.options)
        return d


@dataclass(frozen=True, slots=True)
class PropagationModel:
    """Registry entry: a path-loss callable plus its metadata."""

    id: str
    name: str
    fn: PathLossFn
    freq_min_mhz: float
    freq_max_mhz: float
    dist_min_m: float
    dist_max_m: float
    params: list[ModelParam] = field(default_factory=list)
    note: str = ""
    # Declared antenna-height validity (None = model doesn't publish one, e.g.
    # fspl ignores heights and p1546_lite states none). Hata-family models do.
    hb_min_m: float | None = None
    hb_max_m: float | None = None
    hm_min_m: float | None = None
    hm_max_m: float | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "freq_min_mhz": self.freq_min_mhz,
            "freq_max_mhz": self.freq_max_mhz,
            "dist_min_m": self.dist_min_m,
            "dist_max_m": self.dist_max_m,
            "params": [p.as_dict() for p in self.params],
            "note": self.note,
        }


_AREA = ModelParam(
    "area_type", "urban", "Clutter/terrain class",
    options=["urban", "suburban", "open"],
)

REGISTRY: dict[str, PropagationModel] = {
    "fspl": PropagationModel(
        id="fspl", name="Free-Space Path Loss (Friis)", fn=fspl,
        freq_min_mhz=1.0, freq_max_mhz=100_000.0,
        # 0 m is valid (e.g. a coverage-raster cell colocated with the site);
        # fspl_db floors internally at 1 m only to dodge log10(0), which is a
        # numerical guard, not a modelled validity limit like Hata's 1-20 km.
        dist_min_m=0.0, dist_max_m=1_000_000.0,
        note="Exact theoretical minimum loss; ignores antenna heights.",
    ),
    "okumura_hata": PropagationModel(
        id="okumura_hata", name="Okumura-Hata", fn=okumura_hata,
        freq_min_mhz=150.0, freq_max_mhz=1500.0,
        dist_min_m=1000.0, dist_max_m=20_000.0,
        params=[
            _AREA,
            ModelParam("large_city", False, "Use metropolitan a(h_re) correction"),
        ],
        note="Empirical median for 150-1500 MHz macrocells; hb 30-200 m, hm 1-10 m.",
        hb_min_m=30.0, hb_max_m=200.0, hm_min_m=1.0, hm_max_m=10.0,
    ),
    "cost231_hata": PropagationModel(
        id="cost231_hata", name="COST-231-Hata", fn=cost231_hata,
        freq_min_mhz=1500.0, freq_max_mhz=2000.0,
        dist_min_m=1000.0, dist_max_m=20_000.0,
        params=[_AREA],
        note=(
            "PCS extension of Hata for 1500-2000 MHz; urban adds C_m = 3 dB; "
            "same antenna-height envelope as Hata: hb 30-200 m, hm 1-10 m."
        ),
        hb_min_m=30.0, hb_max_m=200.0, hm_min_m=1.0, hm_max_m=10.0,
    ),
    "p1546_lite": PropagationModel(
        id="p1546_lite", name="ITU-R P.1546-lite (approx.)", fn=p1546_lite,
        freq_min_mhz=30.0, freq_max_mhz=3000.0,
        dist_min_m=1.0, dist_max_m=100_000.0,
        note="Bounded closed-form APPROXIMATION of P.1546 shape, not the ITU curves.",
    ),
    # ponytail: Longley-Rice / ITM deferred (NG-RF-01, "C/S if too heavy") —
    # add an "itm" entry here once a pure-Python terrain port exists.
}


def list_models() -> list[dict]:
    """Registry metadata for every model, ordered by id (deterministic)."""
    return [REGISTRY[k].as_dict() for k in sorted(REGISTRY)]


def path_loss(
    model_id: str,
    distance_m: float,
    freq_mhz: float,
    tx_height_m: float = 30.0,
    rx_height_m: float = 1.5,
    **params: object,
) -> float:
    """Path loss (dB) from the chosen model.

    Raises :class:`ValueError` for an unknown ``model_id``, a frequency or
    distance outside the model's declared valid range, a non-physical
    (<= 0) antenna height, or (for Hata-family models) a height outside the
    model's declared hb/hm envelope. Callers map this to HTTP 422.
    """
    model = REGISTRY.get(model_id)
    if model is None:
        raise ValueError(f"unknown propagation model: {model_id!r}")
    if not (model.freq_min_mhz <= freq_mhz <= model.freq_max_mhz):
        raise ValueError(
            f"frequency {freq_mhz} MHz out of range for {model_id} "
            f"({model.freq_min_mhz}-{model.freq_max_mhz} MHz)"
        )
    if not (model.dist_min_m <= distance_m <= model.dist_max_m):
        raise ValueError(
            f"distance {distance_m} m out of range for {model_id} "
            f"({model.dist_min_m}-{model.dist_max_m} m)"
        )
    # Only models that actually consume antenna heights may reject them: fspl
    # ignores heights entirely and declares 0.0 as its own default, so a
    # height-blind caller must not be turned away here.
    if model.hb_min_m is not None and (tx_height_m <= 0 or rx_height_m <= 0):
        raise ValueError(
            f"antenna heights must be positive "
            f"(tx_height_m={tx_height_m}, rx_height_m={rx_height_m})"
        )
    if model.hb_min_m is not None and not (model.hb_min_m <= tx_height_m <= model.hb_max_m):
        raise ValueError(
            f"tx_height_m {tx_height_m} out of range for {model_id} "
            f"({model.hb_min_m}-{model.hb_max_m} m)"
        )
    if model.hm_min_m is not None and not (model.hm_min_m <= rx_height_m <= model.hm_max_m):
        raise ValueError(
            f"rx_height_m {rx_height_m} out of range for {model_id} "
            f"({model.hm_min_m}-{model.hm_max_m} m)"
        )
    return model.fn(distance_m, freq_mhz, tx_height_m, rx_height_m, **params)


__all__ = [
    "REGISTRY",
    "ModelParam",
    "PropagationModel",
    "cost231_hata",
    "fspl",
    "list_models",
    "okumura_hata",
    "p1546_lite",
    "path_loss",
]
