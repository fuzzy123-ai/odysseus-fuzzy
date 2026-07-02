from src.agent_web_crawl_policy import AgentWebCrawlPolicy


def test_crawl_policy_requires_live_network_go():
    policy = AgentWebCrawlPolicy.create(
        allowed_domains=["asv-bw.de"],
        external_network_go=False,
    )

    decision = policy.decide_url("https://asv-bw.de/hilfe", depth=0, pages_seen=0)

    assert decision.allowed is False
    assert decision.reason == "external_network_go_required"


def test_crawl_policy_allows_domain_under_limits():
    policy = AgentWebCrawlPolicy.create(
        allowed_domains=["asv-bw.de"],
        max_depth=2,
        max_pages=10,
        external_network_go=True,
    )

    decision = policy.decide_url("https://www.asv-bw.de/hilfe", depth=1, pages_seen=3)

    assert decision.allowed is True
    assert decision.reason == "allowed"


def test_crawl_policy_blocks_external_login_and_page_limit():
    policy = AgentWebCrawlPolicy.create(
        allowed_domains=["asv-bw.de"],
        max_pages=1,
        external_network_go=True,
    )

    assert policy.decide_url("https://evil.test", depth=0, pages_seen=0).reason == "domain_not_allowed"
    assert policy.decide_url("https://asv-bw.de/login", depth=0, pages_seen=0).reason == "login_page_blocked"
    assert policy.decide_url("https://asv-bw.de/hilfe", depth=0, pages_seen=1).reason == "page_limit"
