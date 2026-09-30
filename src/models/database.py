# src/models/database.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://openpg:openpgpwd@localhost:5432/betting_db",
)

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"client_encoding": "utf8"},
    pool_size=10,
    max_overflow=20,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db_session():
  db = SessionLocal()
  try:
    yield db
  finally:
    db.close()