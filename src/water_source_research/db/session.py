from functools import lru_cache
from sqlalchemy import Engine, create_engine
from water_source_research.core.config import get_settings
from . import (  # noqa: F401 - registers tables on Base.metadata
    evidence_models, procedure_models, record_models, research_models, review_models,
)
from .models import Base

@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)

def init_db(engine: Engine) -> None:
    """Create missing tables for tests/dev. Production schema changes go through Alembic."""
    Base.metadata.create_all(engine)
