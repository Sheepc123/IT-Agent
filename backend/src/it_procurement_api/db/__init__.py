from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from it_procurement_api.settings import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3}
    )


def new_session() -> Session:
    return sessionmaker(get_engine(), expire_on_commit=False)()
