# src/views/streamlit_view.py
from datetime import datetime
from pathlib import Path
import sys
from typing import Dict, List, Optional
import unicodedata

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
import streamlit as st
from src.controllers import SimulationController
from src.models.database import SessionLocal
from src.models.entities import BetLog, Match, TeamParametersCache
from src.services.backroll_service import BankrollService
from src.services.closing_odds_service import ClosingOddsService
from src.services.micasino_scraper import MiCasinoScraper
from src.services.poisson_model import PoissonPredictor
from src.services.result_settlement_service import ResultSettlementService

from src.utils import LEAGUE_CONFIG, calculate_quarter_kelly, clean_str, match_team_name


# --- DASHBOARD PRINCIPAL ---
def render_dashboard():
    st.set_page_config(
        page_title="Betting Analytics - Quantitative Engine",
        page_icon="📈",
        layout="wide",
    )

    st.title("🎯 Motor Cuantitativo & Radar Multiliga (+EV)")
    st.caption(
        "Dixon-Coles Bivariado • Decaimiento Temporal • Expected Goals (xG) • Mercados Avanzados • Auditoría CLV"
    )

    # --- BARRA LATERAL: PARÁMETROS DEL MODELO DIXON-COLES ---
    with st.sidebar:
        st.header("⚙️ Calibración del Modelo")
        xi_val = st.slider(
            "Factor Decaimiento Temporal (ξ)",
            min_value=0.0010,
            max_value=0.0100,
            value=0.0035,
            step=0.0005,
            format="%.4f",
            help="ξ ≈ 0.0035 otorga mayor peso a los partidos recientes (~6 meses de vida media)",
        )
        xg_weight = st.slider(
            "Ponderación Goles Esperados (xG)",
            min_value=0.0,
            max_value=1.0,
            value=0.25,
            step=0.05,
            help="Combina xG con goles observados para reducir varianza en muestras cortas",
        )
        use_cache = st.checkbox("Activar Caché Persistente en DB", value=True)

        if st.button("🗑️ Limpiar Caché de Parámetros"):
            db = SessionLocal()
            try:
                db.query(TeamParametersCache).delete()
                db.commit()
                st.success("Caché limpiado correctamente.")
            finally:
                db.close()

    tab_radar, tab_simulator, tab_tracker = st.tabs(
        ["🚨 Radar en Vivo (+EV)", "🔍 Simulador Individual", "📊 Bankroll Tracker (P&L)"]
    )

    # ==========================================
    # PESTAÑA 1: RADAR EN VIVO MULTILIGA
    # ==========================================
    with tab_radar:
        st.subheader("Radar de Oportunidades en Tiempo Real")

        r_col1, r_col2, r_col3, r_col4 = st.columns(4)
        with r_col1:
            league_choice = st.selectbox(
                "Torneo a Escanear",
                options=list(LEAGUE_CONFIG.keys()),
                index=0,
            )
            selected_config = LEAGUE_CONFIG[league_choice]
            champ_id = selected_config["champ_id"]
            db_league_key = selected_config["db_key"]

        with r_col2:
            min_edge_input = st.slider(
                "Edge Mínimo Requerido (%)", 1.0, 10.0, 4.0, 0.5
            )
        with r_col3:
            max_odds_limit = st.number_input(
                "Cuota Máxima Permitida", 1.5, 30.0, 4.0, 0.25
            )
        with r_col4:
            bankroll = st.number_input("Bankroll Total ($)", 50.0, 10000.0, 500.0, 50.0)

        # Estado en sesión para persistir resultados
        if "scan_opportunities" not in st.session_state:
            st.session_state["scan_opportunities"] = []
        if "last_scanned_league" not in st.session_state:
            st.session_state["last_scanned_league"] = ""

        if st.button(
            f"🚀 Escanear {league_choice}", use_container_width=True, type="primary"
        ):
            with st.spinner(
                f"Consultando Altenar para {league_choice} y calculando probabilidades Dixon-Coles (ξ={xi_val})..."
            ):
                db = SessionLocal()
                try:
                    db_teams = [
                        t[0]
                        for t in db.query(Match.home_team)
                        .filter(Match.league == db_league_key)
                        .distinct()
                        .all()
                    ]

                    if not db_teams:
                        st.warning(
                            f"No se encontraron partidos históricos para la liga '{db_league_key}' en PostgreSQL. Ejecuta la ingesta previa."
                        )
                        st.session_state["scan_opportunities"] = []
                    else:
                        scraper = MiCasinoScraper()
                        markets = scraper.fetch_championship_events(champ_id=champ_id)

                        if not markets:
                            st.warning(
                                f"No se obtuvieron cuotas activas para {league_choice} en MiCasino en este momento."
                            )
                            st.session_state["scan_opportunities"] = []
                        else:
                            predictor = PoissonPredictor(
                                db, xi=xi_val, xg_weight=xg_weight, use_cache=use_cache
                            )
                            controller = SimulationController(min_edge=min_edge_input / 100.0)

                            events = {}
                            for m in markets:
                                events.setdefault(m.event_name, []).append(m)

                            ops = []
                            for event_name, event_markets in events.items():
                                first = event_markets[0]
                                h_team = match_team_name(first.home_team, db_teams)
                                a_team = match_team_name(first.away_team, db_teams)

                                if not h_team or not a_team:
                                    continue

                                try:
                                    probs = predictor.predict_match(
                                        league=db_league_key, home_team=h_team, away_team=a_team
                                    )
                                except Exception:
                                    continue

                                for m in event_markets:
                                    if m.price > max_odds_limit:
                                        continue

                                    p_model = None
                                    label = ""

                                    # 1. 1X2
                                    if m.market_type == "1X2":
                                        if m.selection == "HOME":
                                            p_model = probs.home_win
                                            label = f"Gana Local ({h_team})"
                                        elif m.selection == "DRAW":
                                            p_model = probs.draw
                                            label = "Empate (X)"
                                        elif m.selection == "AWAY":
                                            p_model = probs.away_win
                                            label = f"Gana Visitante ({a_team})"

                                    # 2. Total de Goles
                                    elif m.market_type == "TOTAL_GOALS":
                                        if m.line == 2.5:
                                            p_model = probs.over_2_5_goals if m.selection == "OVER" else probs.under_2_5_goals
                                            label = f"{'Más' if m.selection == 'OVER' else 'Menos'} de 2.5 Goles"
                                        elif m.line == 1.5:
                                            p_model = probs.over_1_5_goals if m.selection == "OVER" else probs.under_1_5_goals
                                            label = f"{'Más' if m.selection == 'OVER' else 'Menos'} de 1.5 Goles"
                                        elif m.line == 3.5:
                                            p_model = probs.over_3_5_goals if m.selection == "OVER" else probs.under_3_5_goals
                                            label = f"{'Más' if m.selection == 'OVER' else 'Menos'} de 3.5 Goles"

                                    # 3. Ambos Equipos Marcan (BTTS)
                                    elif m.market_type == "BTTS":
                                        if m.selection == "YES":
                                            p_model = probs.btts_yes
                                            label = "Ambos Marcan (Sí)"
                                        elif m.selection == "NO":
                                            p_model = probs.btts_no
                                            label = "Ambos Marcan (No)"

                                    # 4. Apuesta Sin Empate (AH 0.0)
                                    elif m.market_type == "DRAW_NO_BET":
                                        if m.selection == "HOME":
                                            p_model = probs.draw_no_bet["HOME"]
                                            label = f"Sin Empate ({h_team})"
                                        elif m.selection == "AWAY":
                                            p_model = probs.draw_no_bet["AWAY"]
                                            label = f"Sin Empate ({a_team})"

                                    # 5. Doble Oportunidad (AH +0.5)
                                    elif m.market_type == "DOUBLE_CHANCE":
                                        if m.selection in probs.double_chance:
                                            p_model = probs.double_chance[m.selection]
                                            label = f"Doble Oportunidad ({m.selection})"

                                    if p_model is None:
                                        continue

                                    res = controller.evaluate_market(
                                        market_name=label,
                                        p_model=p_model,
                                        odds=m.price,
                                        stake=10.0,
                                        custom_min_edge=min_edge_input / 100.0,
                                    )

                                    if res["is_value"] and res["p_model"] >= 0.10:
                                        kelly_stake = calculate_quarter_kelly(
                                            p_model=res["p_model"],
                                            odds=res["odds"],
                                            bankroll=bankroll,
                                            fraction=0.25,
                                        )
                                        ops.append({
                                            "Partido": f"{h_team} vs {a_team}",
                                            "Mercado": res["market"],
                                            "Cuota": f"{res['odds']:.2f}",
                                            "Prob. Dixon-Coles": f"{res['p_model'] * 100:.1f}%",
                                            "Prob. Casa": f"{res['p_implied'] * 100:.1f}%",
                                            "Edge (+EV)": f"{res['edge'] * 100:+.2f}%",
                                            "EV ($10)": f"${res['ev']:+.2f}",
                                            "Stake Sugerido (1/4 Kelly)": f"${kelly_stake:.2f}",
                                            "_raw": {
                                                "odds": float(res["odds"]),
                                                "p_model": float(res["p_model"]),
                                                "edge": float(res["edge"]),
                                                "kelly_stake": float(kelly_stake),
                                                "kickoff_time": first.start_date,
                                            },
                                        })

                            st.session_state["scan_opportunities"] = ops
                            st.session_state["last_scanned_league"] = league_choice
                finally:
                    db.close()

        # Renderizado de resultados
        stored_ops = st.session_state.get("scan_opportunities", [])
        scanned_league = st.session_state.get("last_scanned_league", "")

        if stored_ops:
            display_data = [{k: v for k, v in o.items() if k != "_raw"} for o in stored_ops]
            df_ops = pd.DataFrame(display_data)
            st.success(
                f"🎯 Se detectaron **{len(stored_ops)}** apuestas con Valor Esperado Positivo (+EV) en {scanned_league}"
            )
            st.dataframe(df_ops, use_container_width=True)

            st.divider()
            st.markdown("#### 📥 Registrar Apuesta en el Tracker")

            with st.form("form_register_bet"):
                reg_col1, reg_col2 = st.columns([3, 1])

                options_labels = [
                    f"{o['Partido']} | {o['Mercado']} @ {o['Cuota']} (Stake Kelly: {o['Stake Sugerido (1/4 Kelly)']})"
                    for o in stored_ops
                ]

                with reg_col1:
                    selected_idx = st.selectbox(
                        "Selecciona la oportunidad a tomar:",
                        options=range(len(stored_ops)),
                        format_func=lambda i: options_labels[i],
                    )

                picked_op = stored_ops[selected_idx]
                raw_data = picked_op["_raw"]

                with reg_col2:
                    custom_stake = st.number_input(
                        "Monto a Apostar ($):",
                        min_value=1.0,
                        max_value=float(bankroll),
                        value=max(1.0, float(raw_data["kelly_stake"])),
                        step=1.0,
                    )

                submit_btn = st.form_submit_button(
                    "💾 Confirmar y Registrar Apuesta", type="primary", use_container_width=True
                )

                if submit_btn:
                    db = SessionLocal()
                    try:
                        bk_service = BankrollService(db)
                        new_bet = bk_service.place_bet(
                            league=scanned_league,
                            match_name=picked_op["Partido"],
                            market=picked_op["Mercado"],
                            odds=float(raw_data["odds"]),
                            p_model=float(raw_data["p_model"]),
                            edge=float(raw_data["edge"]),
                            stake=float(custom_stake),
                            kickoff_time=raw_data.get("kickoff_time"),
                        )
                        st.success(f"✅ ¡Apuesta #{new_bet.id} guardada en el Bankroll Tracker!")
                    finally:
                        db.close()

    # ==========================================
    # PESTAÑA 2: SIMULADOR INDIVIDUAL
    # ==========================================
    with tab_simulator:
        st.subheader("Análisis Particular de Encuentro & Desglose Multimercado")
        db = SessionLocal()
        try:
            leagues = [row[0] for row in db.query(Match.league).distinct().all()]
            selected_lg = st.selectbox("Liga a Simular", options=leagues)
            teams = sorted(
                list(
                    set(
                        [
                            row[0]
                            for row in db.query(Match.home_team)
                            .filter(Match.league == selected_lg)
                            .distinct()
                            .all()
                        ]
                        + [
                            row[0]
                            for row in db.query(Match.away_team)
                            .filter(Match.league == selected_lg)
                            .distinct()
                            .all()
                        ]
                    )
                )
            )
        finally:
            db.close()

        sc1, sc2 = st.columns(2)
        with sc1:
            h_sel = st.selectbox("Local", options=teams, index=0 if teams else None)
        with sc2:
            a_opts = [t for t in teams if t != h_sel]
            a_sel = st.selectbox("Visitante", options=a_opts, index=0 if a_opts else None)

        if h_sel and a_sel:
            db = SessionLocal()
            try:
                predictor = PoissonPredictor(
                    db, xi=xi_val, xg_weight=xg_weight, use_cache=use_cache
                )
                pr = predictor.predict_match(
                    league=selected_lg, home_team=h_sel, away_team=a_sel
                )
            finally:
                db.close()

            st.markdown("##### 1. Mercado 1X2 Clásico & Goles Esperados (λ, μ)")
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric(f"Victoria {h_sel}", f"{pr.home_win * 100:.1f}%")
            m2.metric("Empate (X)", f"{pr.draw * 100:.1f}%")
            m3.metric(f"Victoria {a_sel}", f"{pr.away_win * 100:.1f}%")
            m4.metric(f"xG Local ({h_sel})", f"{pr.expected_home_goals:.2f}")
            m5.metric(f"xG Visitante ({a_sel})", f"{pr.expected_away_goals:.2f}")

            st.markdown("##### 2. Totales de Goles & Ambos Equipos Marcan (BTTS)")
            g1, g2, g3, g4 = st.columns(4)
            g1.metric("Más de 1.5 Goles", f"{pr.over_1_5_goals * 100:.1f}%")
            g2.metric("Más de 2.5 Goles", f"{pr.over_2_5_goals * 100:.1f}%")
            g3.metric("Más de 3.5 Goles", f"{pr.over_3_5_goals * 100:.1f}%")
            g4.metric("Ambos Marcan (BTTS Sí)", f"{pr.btts_yes * 100:.1f}%")

            st.markdown("##### 3. Hándicaps Asiáticos & Apuesta Sin Empate")
            h1, h2, h3, h4 = st.columns(4)
            h1.metric("Sin Empate (Local)", f"{pr.draw_no_bet['HOME'] * 100:.1f}%")
            h2.metric("Sin Empate (Visitante)", f"{pr.draw_no_bet['AWAY'] * 100:.1f}%")
            h3.metric(f"AH {h_sel} (-0.5)", f"{pr.asian_handicap['-0.5']['HOME'] * 100:.1f}%")
            h4.metric(f"AH {h_sel} (+0.5)", f"{pr.asian_handicap['+0.5']['HOME'] * 100:.1f}%")

    # ==========================================
    # PESTAÑA 3: BANKROLL TRACKER (P&L) & AUDITORÍA
    # ==========================================
    with tab_tracker:
        st.subheader("Seguimiento Cuantitativo de Rendimiento & Auditoría CLV")

        db = SessionLocal()
        try:
            bk_service = BankrollService(db)
            metrics = bk_service.get_summary_metrics()

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("P&L Total", f"${metrics['total_profit_loss']:+,.2f}")
            m2.metric("Yield / ROI", f"{metrics['yield_pct']:+.2f}%")
            m3.metric("Win Rate", f"{metrics['win_rate']:.1f}%")
            m4.metric(
                "Apuestas (Cerradas / Activas)",
                f"{metrics['resolved_bets']} / {metrics['pending_bets']}",
            )

            st.divider()

            # Botones de automatización desatendida manual
            act_col1, act_col2 = st.columns(2)
            with act_col1:
                if st.button("🔒 Capturar Cuotas de Cierre Ahora (Auditar CLV)", use_container_width=True):
                    closing_svc = ClosingOddsService(db)
                    n_aud = closing_svc.audit_pending_closing_odds(window_minutes=60)
                    st.success(f"Cuotas de cierre auditadas: {n_aud}")
                    st.rerun()

            with act_col2:
                if st.button("⚡ Liquidar Resultados Automáticamente", use_container_width=True):
                    settle_svc = ResultSettlementService(db)
                    n_set = settle_svc.settle_pending_bets()
                    st.success(f"Apuestas finalizadas liquidadas: {n_set}")
                    st.rerun()

            # Formulario manual para liquidar apuestas pendientes
            pending_bets = db.query(BetLog).filter(BetLog.status == "PENDING").all()
            if pending_bets:
                st.write("#### ⏳ Liquidar Apuestas Pendientes Manualmente")
                p_col1, p_col2, p_col3, p_col4 = st.columns([3, 1.5, 1.5, 2])
                with p_col1:
                    bet_to_resolve = st.selectbox(
                        "Seleccionar Apuesta",
                        options=pending_bets,
                        format_func=lambda b: (
                            f"#{b.id} | {b.match_name} - {b.market} @ {b.odds} (${b.stake})"
                        ),
                    )
                with p_col2:
                    outcome = st.selectbox(
                        "Resultado", options=["WON", "LOST", "VOID"], index=0
                    )
                with p_col3:
                    default_closing = float(bet_to_resolve.odds) if bet_to_resolve else 2.0
                    closing_val = st.number_input(
                        "Cuota de Cierre (Opcional):",
                        min_value=1.0,
                        max_value=50.0,
                        value=default_closing,
                        step=0.01,
                    )
                with p_col4:
                    st.write("")
                    st.write("")
                    if st.button("Guardar Resultado", type="primary", use_container_width=True):
                        bk_service.resolve_bet(
                            bet_id=bet_to_resolve.id,
                            outcome=outcome,
                            closing_odds=closing_val,
                        )
                        st.success(
                            f"Apuesta #{bet_to_resolve.id} liquidada como {outcome} (Cierre: @{closing_val})"
                        )
                        st.rerun()

            # Tabla de Historial
            st.write("#### 📋 Historial de Apuestas y Rendimiento CLV")
            df_hist = bk_service.get_history_dataframe()
            if not df_hist.empty:
                st.dataframe(df_hist, use_container_width=True)

                resolved_df = (
                    df_hist[df_hist["Estado"].isin(["WON", "LOST"])]
                    .iloc[::-1]
                    .copy()
                )
                if not resolved_df.empty:
                    resolved_df["P&L Acumulado"] = resolved_df["P&L ($)"].cumsum()
                    st.line_chart(
                        resolved_df.set_index("Fecha")["P&L Acumulado"],
                        use_container_width=True,
                    )
            else:
                st.info("No hay apuestas registradas en la base de datos todavía.")
        finally:
            db.close()


if __name__ == "__main__":
    render_dashboard()