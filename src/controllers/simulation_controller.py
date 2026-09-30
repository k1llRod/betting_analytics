# src/controllers/simulation_controller.py
from typing import Any, Dict, Optional
from src.models import EVCalculator


class SimulationController:

    def __init__(self, min_edge: float = 0.05):
        self.min_edge = min_edge
        self.calculator = EVCalculator()

    def evaluate_market(
            self,
            market_name: str,
            p_model: float,
            odds: float,
            stake: float = 10.0,
            custom_min_edge: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Calcula la probabilidad implícita, la ventaja (Edge) y el valor esperado (+EV)."""
        threshold = (
            custom_min_edge if custom_min_edge is not None else self.min_edge
        )
        p_implied = self.calculator.implied_probability(odds)
        edge = p_model - p_implied
        ev = self.calculator.calculate_ev(p_model, odds, stake=stake)

        return {
            "market": market_name,
            "p_model": p_model,
            "odds": odds,
            "stake": stake,
            "p_implied": p_implied,
            "edge": edge,
            "ev": ev,
            "is_value": edge >= threshold,
            "threshold": threshold,
        }

    def run_simulation(
            self, market_name: str, p_model: float, odds: float
    ) -> Dict[str, Any]:
        from src.views.console_view import ConsoleView

        result = self.evaluate_market(market_name, p_model, odds)
        ConsoleView.display_opportunity(result)
        return result
