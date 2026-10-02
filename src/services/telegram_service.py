# src/services/telegram_service.py
from datetime import datetime, timedelta
import os
from typing import Any, Dict, Optional
from dotenv import load_dotenv
import requests

load_dotenv()


class TelegramAlertService:
    """
    Servicio de alertas inteligentes para Telegram:
    - Filtros cuantitativos estrictos (Edge >= 4%, cuotas viables 1.60 - 3.50, liquidez).
    - Memoria de deduplicación con ventana de enfriamiento (cooldown).
    - Notificaciones de oportunidades (+EV), cierre de cuotas (CLV) y liquidación de apuestas.
    """

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        # Memoria interna: {alert_key: (last_alert_time, last_odds, last_edge)}
        self._alert_history: Dict[str, Tuple[datetime, float, float]] = {}

    def is_configured(self) -> bool:
        return bool(
            self.bot_token
            and self.chat_id
            and "tu_" not in str(self.bot_token).lower()
            and "your_" not in str(self.bot_token).lower()
        )

    def should_alert(
        self,
        opp: Dict[str, Any],
        min_edge: float = 0.04,
        min_odds: float = 1.60,
        max_odds: float = 3.50,
        min_p_model: float = 0.15,
        cooldown_hours: float = 4.0,
    ) -> bool:
        """
        Filtro cuantitativo inteligente:
        1. Edge >= 4.0%
        2. Cuota en rango viable (1.60 <= odds <= 3.50) para evitar varianza extrema
        3. Probabilidad mínima estimada (p_model >= 0.15) asegurando liquidez
        4. No repetido en el periodo de enfriamiento a menos que el edge mejore >= 1.5%
        """
        odds = float(opp.get("odds", 0.0))
        edge = float(opp.get("edge", 0.0))
        p_model = float(opp.get("p_model", 0.0))

        if edge < min_edge:
            return False
        if not (min_odds <= odds <= max_odds):
            return False
        if p_model < min_p_model:
            return False

        key = f"{opp.get('match')}_{opp.get('market')}"
        now = datetime.utcnow()

        if key in self._alert_history:
            last_time, last_odds, last_edge = self._alert_history[key]
            if now - last_time < timedelta(hours=cooldown_hours):
                # Solo alertar si el edge mejoró sustancialmente (+1.5%)
                if edge - last_edge < 0.015:
                    return False

        return True

    def send_opportunity_alert(
        self,
        opp: Dict[str, Any],
        min_edge: float = 0.04,
        min_odds: float = 1.60,
        max_odds: float = 3.50,
        check_filters: bool = True,
    ) -> bool:
        """Envía alerta de oportunidad con formato cuantitativo."""
        if check_filters and not self.should_alert(
            opp, min_edge=min_edge, min_odds=min_odds, max_odds=max_odds
        ):
            return False

        key = f"{opp.get('match')}_{opp.get('market')}"
        self._alert_history[key] = (datetime.utcnow(), float(opp["odds"]), float(opp["edge"]))

        if not self.is_configured():
            return False

        message = (
            f"🚨 *NUEVA OPORTUNIDAD (+EV) DETECTADA*\n\n"
            f"⚽ *Partido:* {opp['match']}\n"
            f"🏆 *Torneo:* {opp['league']}\n"
            f"🎯 *Mercado:* `{opp['market']}`\n"
            f"📈 *Cuota MiCasino:* `{opp['odds']:.2f}`\n\n"
            f"📊 *Prob. Modelo (Dixon-Coles):* {opp['p_model'] * 100:.1f}%\n"
            f"📉 *Prob. Implícita Casa:* {opp['p_implied'] * 100:.1f}%\n"
            f"🔥 *Ventaja Cuantitativa (Edge):* `+{opp['edge'] * 100:.2f}%`\n"
            f"💵 *EV ($10):* `${opp['ev']:+.2f}`\n"
            f"💰 *Stake Recomendado (1/4 Kelly):* `${opp['kelly_stake']:.2f}`\n\n"
            f"⏱️ _Generado automáticamente por el Background Scanner_"
        )

        return self._send_raw_message(message)

    def send_closing_odds_alert(
        self, match_name: str, market: str, taken_odds: float, closing_odds: float, clv_pct: float
    ) -> bool:
        """Notificación automática de captura de cuota de cierre y auditoría de CLV."""
        if not self.is_configured():
            return False

        clv_icon = "🟢" if clv_pct > 0 else "🔴"
        message = (
            f"🔒 *AUDITORÍA DE CUOTA DE CIERRE (CLV)*\n\n"
            f"⚽ *Partido:* {match_name}\n"
            f"🎯 *Mercado:* `{market}`\n"
            f"🎫 *Cuota Tomada:* `{taken_odds:.2f}`\n"
            f"🏁 *Cuota de Cierre:* `{closing_odds:.2f}`\n"
            f"{clv_icon} *Closing Line Value (CLV):* `{clv_pct:+.2f}%`\n\n"
            f"{'✅ Has batido a la casa antes del pitido inicial.' if clv_pct > 0 else '⚠️ La cuota subió antes del pitido inicial.'}"
        )
        return self._send_raw_message(message)

    def send_settlement_alert(
        self, match_name: str, market: str, outcome: str, profit_loss: float, stake: float
    ) -> bool:
        """Notificación de liquidación automática de apuesta."""
        if not self.is_configured():
            return False

        icon = "🎉" if outcome == "WON" else ("❌" if outcome == "LOST" else "⚪")
        sign = "+" if profit_loss > 0 else ""
        message = (
            f"{icon} *APUESTA LIQUIDADA DESATENDIDA*\n\n"
            f"⚽ *Partido:* {match_name}\n"
            f"🎯 *Mercado:* `{market}`\n"
            f"📋 *Resultado:* *{outcome}*\n"
            f"💵 *Stake:* `${stake:.2f}`\n"
            f"💰 *P&L:* *{sign}${profit_loss:.2f}*\n"
        )
        return self._send_raw_message(message)

    def _send_raw_message(self, text: str) -> bool:
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}
        try:
            res = requests.post(self.base_url, json=payload, timeout=10)
            return res.status_code == 200
        except Exception as e:
            print(f"Error enviando mensaje a Telegram: {e}")
            return False