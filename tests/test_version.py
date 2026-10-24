def test_version_check(client, monkeypatch):
    from app import version

    monkeypatch.setenv("APP_VERSION", "aaaaaaa1111")

    def fake_get(url):
        if url.endswith("/commits/main"):
            return {"sha": "bbbbbbb2222", "commit": {"committer": {"date": "2026-10-24T10:00:00Z"}}}
        return {"ahead_by": 2, "commits": [{"commit": {"message": "feat: bir\n\nayrıntı"}}, {"commit": {"message": "fix: iki"}}]}

    monkeypatch.setattr(version, "_get", fake_get)
    v = client.get("/api/version").json()
    assert v["current"] == "aaaaaaa1111" and v["behind"] == 2 and v["changes"] == ["fix: iki", "feat: bir"]
    monkeypatch.setenv("APP_VERSION", "bbbbbbb2222")
    assert client.get("/api/version").json()["behind"] == 0  # sürüm değişince önbellek yenilenir
    monkeypatch.delenv("APP_VERSION")
    assert version.platform() == "docker"
