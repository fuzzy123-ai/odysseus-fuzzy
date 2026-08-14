from src.coding_code_intelligence_contracts import CodeIntelligenceKind, CodeIntelligenceStatus
from src.coding_code_intelligence_plane import reduce_code_intelligence
from tests.test_coding_code_intelligence_plane import _envelope, _request


def test_cao_context_envelope_to_intelligence_plane_preserves_exact_read_boundary():
    envelope = _envelope()
    result = reduce_code_intelligence(envelope, request=_request(envelope, kind=CodeIntelligenceKind.IMPACT))

    assert result.status is CodeIntelligenceStatus.ACCEPTED
    assert result.exact_read_required == ("code-ref-1",)
    payload = result.to_dict()
    assert payload["execution_allowed"] is False
    assert payload["write_allowed"] is False
    assert payload["dispatch_allowed"] is False
    assert payload["live_effect_allowed"] is False
