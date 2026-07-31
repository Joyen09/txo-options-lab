"""Pre-registered 回測通過標準 — ⚠️ 本檔受 CLAUDE.md 鐵律 2 保護：一經寫定不可修改。

定案：2026-07-30，由 repo 擁有者拍板「標準組」。
回測不過 = 策略不行，不是標準太嚴。禁止看到結果後回頭調整本檔任何數字；
想改標準 = 承認在 curve-fitting，該策略的結論照實記錄為 FAIL。

標準（全部同時成立才 PASS，逐策略適用）：
  1. 平倉樣本 ≥ 30 筆
  2. 扣除全部成本（期交稅 + 手續費 + 滑價）後，每筆平均淨損益 > 0
  3. 最大回撤 (MDD) ≤ 初始資金的 20%
  4. 保證金佔用峰值 (margin_used / equity) ≤ 30%
  5. 滑價 ×2 敏感度測試：總淨損益仍 ≥ 0
初始資金：NT$1,000,000。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

INITIAL_EQUITY = Decimal(1_000_000)
MIN_CLOSED_TRADES = 30
MAX_DRAWDOWN = 0.20          # 佔初始資金比例
MAX_MARGIN_UTILIZATION = 0.30
SLIPPAGE_SENSITIVITY_MULT = 2  # 滑價敏感度測試的倍數


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class CriteriaReport:
    strategy: str
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def evaluate(strategy: str, n_closed: int, avg_net_twd: Decimal,
             max_drawdown: float, peak_utilization: float,
             total_net_2x_slippage: Decimal) -> CriteriaReport:
    """對單一策略的回測結果做 PASS/FAIL 判定。輸入由 engine 產出，本函式只查表。"""
    checks = (
        Check("樣本數 ≥ 30 筆平倉",
              n_closed >= MIN_CLOSED_TRADES,
              f"{n_closed} 筆"),
        Check("扣全部成本後每筆期望值 > 0",
              avg_net_twd > 0,
              f"NT${avg_net_twd:,.0f}/筆"),
        Check("MDD ≤ 20%",
              max_drawdown <= MAX_DRAWDOWN,
              f"{max_drawdown * 100:.1f}%"),
        Check("保證金佔用峰值 ≤ 30%",
              peak_utilization <= MAX_MARGIN_UTILIZATION,
              f"{peak_utilization * 100:.1f}%"),
        Check("滑價 ×2 總淨損益 ≥ 0",
              total_net_2x_slippage >= 0,
              f"NT${total_net_2x_slippage:,.0f}"),
    )
    return CriteriaReport(strategy=strategy, checks=checks)
