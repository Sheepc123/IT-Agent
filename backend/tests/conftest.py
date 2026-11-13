import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from it_procurement_api.app import app
from it_procurement_api.db import get_engine
from it_procurement_api.identity import get_session


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


@pytest.fixture
def client(session):
    """API client whose requests use the rolled-back test session."""
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
