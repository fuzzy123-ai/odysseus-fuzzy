from services.hwfit.fit import rank_models
from services.hwfit.models import get_models, is_prequantized


def _model(name):
    catalog = {m["name"]: m for m in get_models()}
    return catalog[name]


def test_gemma4_12b_it_catalog_entry_has_gguf_source():
    model = _model("google/gemma-4-12B-it")

    assert model["parameters_raw"] == 12_000_000_000
    assert model["quantization"] == "Q4_K_M"
    assert model["gguf_sources"] == [
        {"repo": "unsloth/gemma-4-12B-it-GGUF", "provider": "unsloth"}
    ]


def test_gemma4_12b_it_can_rank_on_8gb_cuda():
    system = {
        "has_gpu": True,
        "backend": "cuda",
        "gpu_name": "NVIDIA RTX 3050",
        "gpu_vram_gb": 8.0,
        "gpu_count": 1,
        "available_ram_gb": 32.0,
        "total_ram_gb": 64.0,
    }

    names = {r["name"] for r in rank_models(system, limit=900)}

    assert "google/gemma-4-12B-it" in names


def test_gemma4_qat_int_entries_are_prequantized_without_gguf_sources():
    int4 = _model("google/gemma-4-12B-it-qat-int4")
    int8 = _model("google/gemma-4-12B-it-qat-int8")

    assert int4["quantization"] == "QAT-INT4"
    assert int8["quantization"] == "QAT-INT8"
    assert int4["gguf_sources"] == []
    assert int8["gguf_sources"] == []
    assert is_prequantized(int4)
    assert is_prequantized(int8)


def test_official_gemma4_qat_gguf_entries_have_sources():
    q4_12b = _model("google/gemma-4-12B-it-qat-q4_0-gguf")
    q4_26b = _model("google/gemma-4-26B-A4B-it-qat-q4_0-gguf")

    assert q4_12b["gguf_sources"][0]["repo"] == "google/gemma-4-12B-it-qat-q4_0-gguf"
    assert q4_26b["gguf_sources"][0]["repo"] == "google/gemma-4-26B-A4B-it-qat-q4_0-gguf"
    assert "audio" in q4_12b["capabilities"]
