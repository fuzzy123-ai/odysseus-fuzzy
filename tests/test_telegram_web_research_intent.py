from src.telegram_web_research_intent import TelegramWebResearchIntent


def test_telegram_web_research_intent_uses_trusted_metadata_for_routing():
    intent = TelegramWebResearchIntent.create(
        trusted_channel="telegram",
        operator_id_hash="sha256:operator",
        target_url="https://www.asv-bw.de/hilfe",
        live_web_go=True,
        dsgvo_mode=True,
    )

    payload = intent.to_dict()

    assert payload["runnable"] is True
    assert payload["target_domain"] == "asv-bw.de"
    assert payload["routing_uses_untrusted_page_text"] is False
