from fastapi.testclient import TestClient

from app.main import app


def test_closeout_ui_assets_are_served() -> None:
    client = TestClient(app)

    home = client.get("/")
    assert home.status_code == 200
    assert '/ui/closeout.css' in home.text
    assert '/ui/closeout.js' in home.text
    assert '/ui/auth-register.js' in home.text
    assert '/ui/product-memory.js' in home.text

    script = client.get("/ui/closeout.js")
    assert script.status_code == 200
    assert "techpilot_demo_session" in script.text
    assert "techpilot_locale" in script.text
    assert "zh-CN" in script.text

    register_script = client.get("/ui/auth-register.js")
    assert register_script.status_code == 200
    assert 'fetch("/auth/register"' in register_script.text
    assert 'fetch("/auth/me"' in register_script.text
    assert 'tpRegisterForm' in register_script.text
    assert 'tpRegisterConfirm' in register_script.text

    memory_script = client.get("/ui/product-memory.js")
    assert memory_script.status_code == 200
    assert 'endsWith(".docx")' in memory_script.text
    assert "application/vnd.openxmlformats-officedocument.wordprocessingml.document" in memory_script.text
    assert 'fetch("/documents/upload"' in memory_script.text
    assert "PDF · MD · DOCX" in memory_script.text

    styles = client.get("/ui/closeout.css")
    assert styles.status_code == 200
    assert ".tp-login-overlay" in styles.text
    assert ".tp-auth-switch" in styles.text
