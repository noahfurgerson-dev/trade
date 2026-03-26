"""
Base strategy class. All strategies inherit from this.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from core.robinhood import RobinhoodClient


class BaseStrategy(ABC):
    """
    Abstract base for all trading strategies.
    Each strategy defines its own signal generation and execution logic.
    """

    def __init__(self, client: RobinhoodClient, max_position_pct: float = 0.10):
        self.client = client
        self.max_position_pct = max_position_pct  # max % of portfolio per position
        self.name = self.__class__.__name__
        self.active = False
        self.log: list[dict] = []

    def _log(self, msg: str, level: str = "INFO"):
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "strategy": self.name,
            "message": msg,
        }
        self.log.append(entry)
        print(f"[{entry['time']}] [{level}] {self.name}: {msg}")

    def _max_shares(self, price: float) -> float:
        """Max shares to buy based on portfolio allocation limit."""
        equity = self.client.get_portfolio_value()
        cash = self.client.get_cash()
        max_value = min(equity * self.max_position_pct, cash)
        return max(0, int(max_value / price)) if price > 0 else 0

    @abstractmethod
    def run(self) -> list[dict]:
        """
        Execute one cycle of the strategy.
        Returns list of actions taken: [{ticker, action, qty, price, reason}]
        """
        pass

    @abstractmethod
    def describe(self) -> str:
        """One-line description of the strategy."""
        pass
