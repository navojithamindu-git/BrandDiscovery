from cryptography.fernet import Fernet
import pytest
from app.config import Settings
from app.services.oauth import _business_portfolio_pages, authorization_url, create_state, verify_state
from app.services.token_vault import TokenVault


def test_token_vault_encrypts_and_round_trips(tmp_path):
    path = tmp_path / "tokens.enc"
    vault = TokenVault(Fernet.generate_key().decode(), str(path))
    vault.set("connection-1", "secret-token")
    assert vault.get("connection-1") == "secret-token"
    assert b"secret-token" not in path.read_bytes()


def test_oauth_state_is_signed_single_use():
    settings = Settings(meta_app_id="app", meta_app_secret="secret", meta_oauth_state_secret="state-secret")
    state = create_state(7, settings)
    assert verify_state(state, settings) == 7
    with pytest.raises(ValueError, match="invalid OAuth state"):
        verify_state(state, settings)


def test_business_login_config_uses_config_id():
    settings = Settings(meta_app_id="app", meta_login_config_id="config", meta_oauth_state_secret="secret")
    url = authorization_url(settings, 3)
    assert "config_id=config" in url
    assert "scope=" not in url


def test_business_portfolio_page_fallback_deduplicates_owned_and_client_pages():
    class FakeClient:
        def all_pages(self, path, params):
            if path == "/me/businesses":
                return [{"id": "business-1"}]
            if path.endswith("/owned_pages"):
                return [{"id": "page-1", "name": "Craft Bay", "access_token": "page-token"}]
            if path.endswith("/client_pages"):
                return [{"id": "page-1"}, {"id": "page-2", "name": "Other"}]
            raise AssertionError(path)

    pages = _business_portfolio_pages(FakeClient())
    assert [page["id"] for page in pages] == ["page-1", "page-2"]
