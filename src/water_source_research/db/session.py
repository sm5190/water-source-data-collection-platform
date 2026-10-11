from functools import lru_cache
from sqlalchemy import Engine, create_engine
from water_source_research.core.config import get_settings
from . import procedure_models, research_models  # noqa: F401 - registers tables on Base.metadata
from .models import Base

@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)

def init_db(engine: Engine) -> None:
    """Create missing tables for tests/dev. Production schema changes go through Alembic."""
    Base.metadata.create_all(engine)
