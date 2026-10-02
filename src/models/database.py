# src/models/database.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://openpg:openpgpwd@localhost:5432/betting_db",
)

# Robust multi-driver resolution: psycopg2 -> psycopg (v3) -> pg8000
driver_found = None
for drv in ["psycopg2", "psycopg", "pg8000"]:
    try:
        __import__(drv)
        driver_found = drv
        break
    except ImportError:
        continue

# Normalize URL to available driver
if driver_found == "pg8000":
    if "postgresql+psycopg2://" in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql+pg8000://")
    elif "postgresql+psycopg://" in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+pg8000://")
    elif DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+pg8000://", 1)
    connect_args = {}
elif driver_found == "psycopg":
    if "postgresql+psycopg2://" in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql+psycopg://")
    elif DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    connect_args = {}
else:
    connect_args = {"client_encoding": "utf8"}

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args=connect_args,
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