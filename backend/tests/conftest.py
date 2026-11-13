import pytest
from sqlalchemy.orm import Session

from it_procurement_api.db import get_engine


@pytest.fixture
def session():
    """A session inside a transaction that is always rolled back."""
    connection = get_engine().connect()
    outer = connection.begin()
    s = Session(connection, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        outer.rollback()
        connection.close()
