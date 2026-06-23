from src import llm_core


def test_ollama_payload_converts_openai_image_blocks_to_native_images_array():
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "What is in this picture?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBBB"}},
        ],
    }
    payload = llm_core._build_ollama_payload("gemma4:e4b", [msg], temperature=0.0, max_tokens=0)
    out = payload["messages"][0]
    assert out["content"] == "What is in this picture?"
    assert out["images"] == ["AAAA", "BBBB"]


def test_ollama_payload_preserves_native_images_array():
    msg = {"role": "user", "content": "Describe", "images": ["XXXX"]}
    payload = llm_core._build_ollama_payload("gemma4:e4b", [msg], temperature=0.0, max_tokens=0)
    assert payload["messages"][0]["images"] == ["XXXX"]


def test_ollama_payload_merges_native_and_openai_images():
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "Hi"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,OPENAI"}},
        ],
        "images": ["NATIVE"],
    }
    payload = llm_core._build_ollama_payload("gemma4:e4b", [msg], temperature=0.0, max_tokens=0)
    assert payload["messages"][0]["images"] == ["NATIVE", "OPENAI"]


def test_ollama_payload_skips_http_image_url():
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "Look"},
            {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
        ],
    }
    payload = llm_core._build_ollama_payload("gemma4:e4b", [msg], temperature=0.0, max_tokens=0)
    assert payload["messages"][0] == {"role": "user", "content": "Look"}
