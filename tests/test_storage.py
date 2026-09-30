import json
import os
import pytest
from app.auth.storage import Credentials, CredentialStorage

def test_load_all_from_json_array(tmp_path):
    cred_file = tmp_path / "credentials.json"
    data = [
        {
            "email": "user1@example.com",
            "access_token": "acc1",
            "refresh_token": "ref1",
            "project_id": "proj1",
            "expires_at": 1000,
        },
        {
            "email": "user2@example.com",
            "access_token": "acc2",
            "refresh_token": "ref2",
            "project_id": "proj2",
            "expires_at": 2000,
        },
    ]
    cred_file.write_text(json.dumps(data), encoding="utf-8")

    storage = CredentialStorage(str(cred_file))
    accounts = storage.load_all()

    assert len(accounts) == 2
    assert accounts[0].email == "user1@example.com"
    assert accounts[0].access_token == "acc1"
    assert accounts[1].email == "user2@example.com"
    assert accounts[1].access_token == "acc2"
    # Backward compatibility for load()
    first = storage.load()
    assert first is not None
    assert first.email == "user1@example.com"

def test_load_all_from_single_object(tmp_path):
    cred_file = tmp_path / "credentials.json"
    data = {
        "access_token": "acc_single",
        "refresh_token": "ref_single",
        "project_id": "proj_single",
        "expires_at": 3000,
        "email": "single@example.com",
    }
    cred_file.write_text(json.dumps(data), encoding="utf-8")

    storage = CredentialStorage(str(cred_file))
    accounts = storage.load_all()

    assert len(accounts) == 1
    assert accounts[0].email == "single@example.com"
    assert accounts[0].access_token == "acc_single"
    assert storage.load().email == "single@example.com"

def test_load_all_env_fallback(tmp_path, monkeypatch):
    cred_file = tmp_path / "nonexistent.json"
    storage = CredentialStorage(str(cred_file))

    monkeypatch.delenv("ANTIGRAVITY_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("ANTIGRAVITY_PROJECT_ID", raising=False)
    assert storage.load_all() == []
    assert storage.load() is None

    monkeypatch.setenv("ANTIGRAVITY_REFRESH_TOKEN", "env_ref")
    monkeypatch.setenv("ANTIGRAVITY_PROJECT_ID", "env_proj")
    monkeypatch.setenv("ANTIGRAVITY_ACCESS_TOKEN", "env_acc")

    accounts = storage.load_all()
    assert len(accounts) == 1
    assert accounts[0].access_token == "env_acc"
    assert accounts[0].refresh_token == "env_ref"
    assert accounts[0].project_id == "env_proj"

def test_save_all_and_save_append_update(tmp_path):
    cred_file = tmp_path / "credentials.json"
    storage = CredentialStorage(str(cred_file))

    acc1 = Credentials("acc1", "ref1", "proj1", 1000, email="user1@example.com")
    acc2 = Credentials("acc2", "ref2", "proj2", 2000, email="user2@example.com")

    storage.save_all([acc1, acc2])

    loaded_raw = json.loads(cred_file.read_text(encoding="utf-8"))
    assert isinstance(loaded_raw, list)
    assert len(loaded_raw) == 2
    assert loaded_raw[0]["email"] == "user1@example.com"

    # Now test save() updating an existing account by email
    acc1_updated = Credentials("acc1_new", "ref1_new", "proj1", 1500, email="user1@example.com")
    storage.save(acc1_updated)

    accounts = storage.load_all()
    assert len(accounts) == 2
    assert accounts[0].access_token == "acc1_new"
    assert accounts[1].access_token == "acc2"

    # Now test save() appending a new account
    acc3 = Credentials("acc3", "ref3", "proj3", 3000, email="user3@example.com")
    storage.save(acc3)

    accounts = storage.load_all()
    assert len(accounts) == 3
    assert accounts[2].email == "user3@example.com"

    # Now test save() updating an account with matching project_id if email is None
    acc4 = Credentials("acc4", "ref4", "proj4", 4000, email=None)
    storage.save(acc4)
    accounts = storage.load_all()
    assert len(accounts) == 4

    acc4_updated = Credentials("acc4_new", "ref4", "proj4", 4500, email=None)
    storage.save(acc4_updated)
    accounts = storage.load_all()
    assert len(accounts) == 4
    assert accounts[3].access_token == "acc4_new"
