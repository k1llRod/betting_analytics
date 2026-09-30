# src/services/telegram_service.py
import os
import requests
from dotenv import load_dotenv

load_dotenv()

class TelegramAlertService:
    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id and "tu_" not in self.bot_token)

    def send_opportunity_alert(self, opp: dict) -> bool:
        if not self.is_configured():
            return False

        message = (
            f"🚨 *NUEVA OPORTUNIDAD (+EV) DETECTADA*\n\n"
            f"⚽ *Partido:* {opp['match']}\n"
            f"🏆 *Torneo:* {opp['league']}\n"
            f"🎯 *Mercado:* `{opp['market']}`\n"
            f"📈 *Cuota MiCasino:* `{opp['odds']:.2f}`\n\n"
            f"📊 *Prob. Modelo (Dixon-Coles):* {opp['p_model']*100:.1f}%\n"
            f"📉 *Prob. Implícita Casa:* {opp['p_implied']*100:.1f}%\n"
            f"🔥 *Ventaja (Edge):* `+{opp['edge']*100:.2f}%`\n"
            f"💵 *EV ($10):* `${opp['ev']:+.2f}`\n"
            f"💰 *Stake Recomendado (1/4 Kelly):* `${opp['kelly_stake']:.2f}`\n"
        )

        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }

        try:
            res = requests.post(self.base_url, json=payload, timeout=10)
            return res.status_code == 200
        except Exception as e:
            print(f"Error enviando alerta a Telegram: {e}")
            return False