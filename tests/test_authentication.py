from tests.form_helpers import post_current
from tests.conftest import TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD


def test_login_page_requires_authentication_and_shows_access_notice(client):
    client.cookies.clear()

    redirect = client.get("/", follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"].startswith("/login?")

    page = client.get("/login")
    assert page.status_code == 200
    assert "Пожалуйста, запросите доступ у администратора системы" in page.text
    assert "pv@pharmrussia.com" in page.text


def test_login_rejects_wrong_password_and_accepts_valid_password(client):
    client.cookies.clear()

    invalid = post_current(client,
        "/login",
        data={"login": TEST_ADMIN_LOGIN, "password": "wrong-password", "next_url": "/"},
    )
    assert invalid.status_code == 401
    assert "Неверный логин или пароль." in invalid.text

    valid = post_current(client,
        "/login",
        data={
            "login": TEST_ADMIN_LOGIN,
            "password": TEST_ADMIN_PASSWORD,
            "next_url": "/users-roles",
        },
        follow_redirects=False,
    )
    assert valid.status_code == 303
    assert valid.headers["location"] == "/users-roles"
    assert "pv_session=" in valid.headers["set-cookie"]
    assert "HttpOnly" in valid.headers["set-cookie"]


def test_admin_can_register_user_with_existing_role(client):
    from app.database import SessionLocal
    from app.models import Role, User

    with SessionLocal() as db:
        role = db.query(Role).filter(Role.role_code == "qa_reviewer").one()
        role_id = role.id

    response = post_current(client,
        "/users-roles/users",
        data={
            "login": "new-reviewer",
            "password": "Reviewer-2026!",
            "full_name": "Новый рецензент",
            "role_ids": role_id,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        user = db.query(User).filter(User.login == "new-reviewer").one()
        assert user.password_hash
        assert {
            link.role.role_code for link in user.user_roles if not link.is_deleted
        } == {"qa_reviewer"}


def test_logout_revokes_session(client):
    response = post_current(client, "/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    protected = client.get("/", follow_redirects=False)
    assert protected.status_code == 303
