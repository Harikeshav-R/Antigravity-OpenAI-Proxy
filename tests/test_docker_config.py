# tests/test_docker_config.py
import os
import yaml

def test_litellm_config_structure():
    with open("litellm-config.yaml", "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert "model_list" in data
    models = [m["model_name"] for m in data["model_list"]]
    assert "claude-3.7-sonnet" in models
    assert "claude-sonnet-4.6" in models
    assert "claude-sonnet-4-6" in models
    assert "claude-opus-4.6" in models
    assert "claude-opus-4-6" in models
    assert "gemini-3.8-flash" in models
    assert "gemini-3.1-pro" in models
    assert "gemini-3-pro" in models
    assert "gemini-3-flash" in models
    assert "gemini-2.5-pro" in models
    assert "gemini-2.5-flash" in models
    assert "gemini-2.0-flash" in models
    assert "gemini-1.5-pro" in models
    assert "gemini-1.5-flash" in models

    # Verify no openrouter fallback entries exist
    for entry in data["model_list"]:
        target_model = entry.get("litellm_params", {}).get("model", "")
        assert "openrouter" not in target_model, f"Found openrouter target: {target_model}"

def test_dockerfile_and_compose_exist():
    assert os.path.exists("Dockerfile")
    assert os.path.exists("docker-compose.yml")
