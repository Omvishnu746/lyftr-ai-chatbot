import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

logger = logging.getLogger(__name__)

Base = declarative_base()

def get_engine():
    db_url = settings.DATABASE_URL
    if db_url.startswith("sqlite"):
        # Resolve SQLite file directory path
        db_path = db_url.replace("sqlite:///", "")
        if db_path:
            # Handle Linux/Docker /data/app.db vs Windows path
            dir_name = os.path.dirname(db_path)
            if dir_name:
                try:
                    os.makedirs(dir_name, exist_ok=True)
                except Exception as e:
                    logger.warning(f"Could not create database directory {dir_name}: {e}. Falling back to ./data/app.db")
                    # Fallback for Windows local development without root /data write access
                    fallback_dir = os.path.join(os.getcwd(), "data")
                    os.makedirs(fallback_dir, exist_ok=True)
                    db_url = f"sqlite:///{os.path.join(fallback_dir, 'app.db').replace('\\', '/')}"
                    settings.DATABASE_URL = db_url

        return create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            echo=False
        )
    return create_engine(db_url, echo=False)

engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Create all database tables on application startup."""
    # Import models here to register metadata
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized successfully")

def check_db_health() -> bool:
    """Verify that the database connection is healthy."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False
