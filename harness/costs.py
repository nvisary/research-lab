"""Trading cost model. Tweak here, do not bake into strategies."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    taker_fee: float = 0.00055   # Bybit perp taker
    maker_fee: float = 0.0002    # Bybit perp maker (rebate ignored conservatively)
    slippage_bps: float = 1.0    # 1bp average for liquid majors at 1m

    @property
    def total_one_way(self) -> float:
        return self.taker_fee + self.slippage_bps * 1e-4


DEFAULT = CostModel()
