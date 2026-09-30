class EVCalculator:

    @staticmethod
    def implied_probability(odds: float) -> float:
        """
        Calculate the implied probability from decimal odds.

        :param odds: Decimal odds
        :return: Implied probability as a float
        """
        if odds <= 1.0:
            raise ValueError("La couta debe ser mayor a 1.0.")
        return 1.0 / odds

    @staticmethod
    def calculate_ev(p_model: float, odds: float, stake: float = 10.0) -> float:
        profit = (odds - 1) * stake
        loss = stake
        return round((p_model * profit) - ((1 - p_model) * loss), 2)

