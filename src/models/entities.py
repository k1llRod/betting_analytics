# src/models/entities.py
from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from src.models.database import Base


class Match(Base):
  __tablename__ = "matches"

  id = Column(Integer, primary_key=True, autoincrement=True)
  match_date = Column(DateTime(timezone=True), nullable=False)
  league = Column(String(100), nullable=False)
  home_team = Column(String(100), nullable=False)
  away_team = Column(String(100), nullable=False)
  home_score = Column(Integer, nullable=True)
  away_score = Column(Integer, nullable=True)
  status = Column(String(20), default="SCHEDULED")

  stats = relationship("MatchStats", back_populates="match", uselist=False)
  odds = relationship("MarketOdds", back_populates="match")


class MatchStats(Base):
  __tablename__ = "match_stats"

  match_id = Column(Integer, ForeignKey("matches.id"), primary_key=True)
  home_xg = Column(Float, nullable=True)
  away_xg = Column(Float, nullable=True)
  home_shots_target = Column(Integer)
  away_shots_target = Column(Integer)
  home_shots_blocked = Column(Integer)
  away_shots_blocked = Column(Integer)
  home_corners = Column(Integer)
  away_corners = Column(Integer)
  home_possession = Column(Integer)

  match = relationship("Match", back_populates="stats")


class MarketOdds(Base):
  __tablename__ = "market_odds"

  id = Column(Integer, primary_key=True, autoincrement=True)
  match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
  market_type = Column(String(50), nullable=False)
  selection = Column(String(50), nullable=False)
  odds = Column(Float, nullable=False)
  bookmaker = Column(String(50), nullable=False)
  recorded_at = Column(DateTime(timezone=True), default=datetime.utcnow)

  match = relationship("Match", back_populates="odds")

class BetLog(Base):
  __tablename__ = "bet_logs"

  id = Column(Integer, primary_key=True, index=True)
  placed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
  league = Column(String(50), nullable=False)
  match_name = Column(String(150), nullable=False)
  market = Column(String(100), nullable=False)
  odds = Column(Float, nullable=False)
  p_model = Column(Float, nullable=False)
  edge = Column(Float, nullable=False)
  stake = Column(Float, nullable=False)
  status = Column(String(20), default="PENDING", nullable=False)  # PENDING, WON, LOST, VOID
  profit_loss = Column(Float, default=0.0, nullable=False)
  resolved_at = Column(DateTime, nullable=True)
  closing_odds = Column(Float, nullable=True)