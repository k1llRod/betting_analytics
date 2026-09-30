class ConsoleView:

  @staticmethod
  def display_opportunity(opp: dict):
    print("\n" + "=" * 45)
    print(f" MERCADO: {opp['market']}")
    print("=" * 45)
    print(f" Probabilidad Modelo: {opp['p_model'] * 100:.1f}%")
    print(f" Cuota Disponible:    {opp['odds']:.2f}")
    print(f" Prob. Implícita:     {opp['p_implied'] * 100:.1f}%")
    print(f" Ventaja (Edge):      {opp['edge'] * 100:+.2f}%")
    print(f" Valor Esperado ($10): ${opp['ev']:+.2f}")
    print("-" * 45)
    if opp["is_value"]:
      print(" >>> [ALERTA] ¡Oportunidad con +EV detectada! <<<")
    else:
      print(" >>> [DESCARTADO] Sin valor suficiente. <<<")
    print("=" * 45 + "\n")

