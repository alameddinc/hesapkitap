import importlib

from fastapi.testclient import TestClient


def test_spa_serving(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "x.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("gizli")
    monkeypatch.setenv("WEB_DIST", str(dist))
    monkeypatch.delenv("APP_PASSWORD", raising=False)

    from app import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    import app.api.main as main

    main = importlib.reload(main)
    c = TestClient(main.app)

    assert c.get("/").text == "<html>app</html>"
    assert c.get("/islemler").text == "<html>app</html>"  # SPA fallback
    assert c.get("/assets/x.js").text == "console.log(1)"
    assert c.get("/api/yok").status_code == 404           # API yolu SPA'ya düşmez
    assert "gizli" not in c.get("/..%2Fsecret.txt").text  # dizin dışına çıkılamaz
    assert c.get("/api/meta").headers["x-frame-options"] == "DENY"
