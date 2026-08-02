from src.coding_edit_transaction import reduce_edit_transaction
from tests.test_coding_edit_transaction import _packet, _state


def test_cao_a_through_d_contracts_feed_a_content_free_edit_descriptor():
    state, capsule = _state()
    receipt, operation, transaction = _packet(state, capsule)
    result = reduce_edit_transaction(state, transaction=transaction, receipts=(receipt,), observed_source_digests={operation.repo_path: operation.expected_source_digest}, observed_anchor_digests={operation.repo_path: operation.expected_anchor_digest})
    assert result.transaction_id == transaction.transaction_id
    assert result.transaction_applied is result.execution_performed is result.write_allowed is False
