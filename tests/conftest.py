import pytest

from app import create_app
from app.config import TestConfig
from app.models import User, db


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        user = User(username="rm1", full_name="Test RM")
        user.set_password("password123")
        other = User(username="rm2", full_name="Other RM")
        other.set_password("password123")
        db.session.add_all([user, other])
        db.session.commit()
    yield app
    with app.app_context():
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in(client):
    client.post("/login", data={"username": "rm1", "password": "password123"})
    return client


def login_as(client, username):
    client.post("/logout")
    client.post("/login", data={"username": username, "password": "password123"})
