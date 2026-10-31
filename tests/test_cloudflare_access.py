"""Signing in through Cloudflare Access: tokens Cloudflare would send, signed
with a test key standing in for the team's."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from tcgnecro import auth, users

TEAM, AUD = "myteam", "aud-tag-123"
ISSUER = "https://myteam.cloudflareaccess.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(email="brian@example.com", aud=AUD, iss=ISSUER, expires_in=600, key=KEY):
    now = int(time.time())
    return jwt.encode({"email": email, "aud": [aud], "iss": iss, "iat": now, "exp": now + expires_in,
                       "sub": "abc"}, key, algorithm="RS256", headers={"kid": "test"})


def cf(email="brian@example.com", **kw):
    return {"Cf-Access-Jwt-Assertion": token(email, **kw)}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    monkeypatch.setenv("TCGNECRO_CF_ACCESS_TEAM", TEAM)
    monkeypatch.setenv("TCGNECRO_CF_ACCESS_AUD", AUD)
    monkeypatch.setattr(auth.CloudflareAccess, "signing_key", lambda self, t: KEY.public_key())
    auth._cloudflare.cache_clear()
    users._cache = None
    from tcgnecro.web import app
    yield TestClient(app)
    users._cache = None
    auth._cloudflare.cache_clear()


def test_requests_without_a_valid_token_are_refused(client):
    assert client.get("/").status_code == 403
    assert client.get("/login").status_code == 403
    assert client.get("/", headers=cf(aud="another-app")).status_code == 403
    assert client.get("/", headers=cf(iss="https://evil.cloudflareaccess.com")).status_code == 403
    assert client.get("/", headers=cf(expires_in=-3600)).status_code == 403
    assert client.get("/", headers=cf(key=OTHER_KEY)).status_code == 403
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/healthz").text == "ok"                        # the container health check


def test_first_person_through_sets_up_the_admin_with_their_email(client):
    page = client.get("/login", headers=cf())
    assert page.status_code == 200 and "brian@example.com" in page.text and "Welcome" in page.text
    client.post("/login/setup", data={"name": "Brian"}, headers=cf())
    admin = users.by_name("Brian")
    assert admin.is_admin and admin.email == "brian@example.com"
    r = client.get("/", headers=cf("Brian@Example.com"))              # case doesn't matter
    assert r.status_code == 200 and "Brian" in r.text


def test_people_are_signed_in_as_the_profile_with_their_email(client):
    users.create("Brian", email="brian@example.com")
    friend = users.create("Friend")
    r = client.get("/", headers=cf("friend@example.com"), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    page = client.get("/login", headers=cf("friend@example.com")).text
    assert "No profile for friend@example.com" in page and "Who's using" not in page

    client.post(f"/profile/users/{friend.id}/email", data={"email": "Friend@Example.com"}, headers=cf())
    assert users.get(friend.id).email == "friend@example.com"
    r = client.get("/profile", headers=cf("friend@example.com"))
    assert r.status_code == 200 and 'value="Friend"' in r.text

    # The CF_Authorization cookie works too, and the picker is off.
    client.cookies.set("CF_Authorization", token("friend@example.com"))
    assert client.get("/").status_code == 200
    assert client.post("/login", data={"user_id": friend.id}).status_code == 403
    r = client.post("/logout", follow_redirects=False)
    assert r.headers["location"] == "/cdn-cgi/access/logout"


def test_emails_are_unique_and_admins_manage_them(client):
    users.create("Brian", email="brian@example.com")
    friend = users.create("Friend", email="friend@example.com")
    r = client.post(f"/profile/users/{friend.id}/email", data={"email": "BRIAN@example.com"}, headers=cf(),
                    follow_redirects=False)
    assert "already+uses" in r.headers["location"] and users.get(friend.id).email == "friend@example.com"
    r = client.post(f"/profile/users/{users.by_name('Brian').id}/email", data={"email": "nope@example.com"},
                    headers=cf(), follow_redirects=False)
    assert "sign+you+out" in r.headers["location"]                      # can't lock yourself out
    assert client.post(f"/profile/users/{friend.id}/email", data={"email": "x@example.com"},
                       headers=cf("friend@example.com")).status_code == 403   # members can't
    client.post("/profile", data={"name": "Brian", "email": "other@example.com"}, headers=cf())
    assert users.by_name("Brian").email == "brian@example.com"          # own email is fixed here


def test_without_settings_the_profile_picker_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    monkeypatch.delenv("TCGNECRO_CF_ACCESS_TEAM", raising=False)
    users._cache = None
    from tcgnecro.web import app
    c = TestClient(app)
    c.post("/login/setup", data={"name": "Brian"})
    assert c.get("/").status_code == 200
    assert auth.cloudflare() is None
    assert auth.CloudflareAccess("myteam.cloudflareaccess.com", AUD).issuer == ISSUER
    users._cache = None
