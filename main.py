from src.controllers import SimulationController

def main():
    controller = SimulationController(min_edge=0.05)
    controller.run_simulation(market_name="Total tiros de esquina > 9.5", p_model=0.60, odds=1.80)

    if __name__ == "__main__":
        main()