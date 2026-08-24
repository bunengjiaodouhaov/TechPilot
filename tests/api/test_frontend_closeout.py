from fastapi.testclient import TestClient

from app.main import app


def test_closeout_ui_assets_are_served() -> None:
    client = TestClient(app)

    home = client.get("/")
    assert home.status_code == 200
    assert '/ui/closeout.css' in home.text
    assert '/ui/closeout.js' in home.text

    script = client.get("/ui/closeout.js")
    assert script.status_code == 200
    assert "techpilot_demo_session" in script.text
    assert "techpilot_locale" in script.text
    assert "zh-CN" in script.text

    styles = client.get("/ui/closeout.css")
    assert styles.status_code == 200
    assert ".tp-login-overlay" in styles.text
