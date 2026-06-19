from src.nextcloud_tag_governance import (
    TagCandidate,
    TagProjectionPolicy,
    govern_nextcloud_tag,
    govern_nextcloud_tags,
)


def test_user_tag_is_preserved_without_automatic_rename() -> None:
    decision = govern_nextcloud_tag(
        TagCandidate(tag="Projects", tag_class="user", confidence=0.2)
    )

    assert decision.status == "preserved"
    assert decision.reason == "manual_user_tag_preserved"
    assert decision.canonical_tag == "project"
    assert decision.nextcloud_tag == "Projects"
    assert not decision.allow_nextcloud_projection


def test_semantic_alias_projects_to_canonical_when_policy_and_confidence_allow() -> None:
    decision = govern_nextcloud_tag(
        TagCandidate(tag="todo", tag_class="semantic", confidence=0.91)
    )

    assert decision.status == "projected"
    assert decision.reason == "mapped_to_canonical"
    assert decision.canonical_tag == "task"
    assert decision.nextcloud_tag == "task"
    assert decision.allow_nextcloud_projection


def test_low_confidence_canonical_tag_is_review_only() -> None:
    decision = govern_nextcloud_tag(
        TagCandidate(tag="project", tag_class="system", confidence=0.6)
    )

    assert decision.status == "review"
    assert decision.reason == "confidence_below_minimum"
    assert decision.nextcloud_tag is None


def test_free_semantic_tag_needs_review_and_does_not_project_to_nextcloud() -> None:
    decision = govern_nextcloud_tag(
        TagCandidate(tag="customer-escalation", tag_class="semantic", confidence=0.97)
    )

    assert decision.status == "review"
    assert decision.reason == "free_tag_requires_review"
    assert decision.canonical_tag is None
    assert decision.nextcloud_tag is None


def test_graph_only_tag_is_never_projected() -> None:
    decision = govern_nextcloud_tag(
        TagCandidate(tag="latent-cluster-7", tag_class="graph_only", confidence=0.99)
    )

    assert decision.status == "blocked"
    assert decision.reason == "graph_only_never_projects"
    assert decision.nextcloud_tag is None


def test_policy_can_block_semantic_projection_even_for_canonical_tags() -> None:
    decision = govern_nextcloud_tag(
        TagCandidate(tag="project", tag_class="semantic", confidence=0.95),
        policy=TagProjectionPolicy(allow_semantic_projection=False),
    )

    assert decision.status == "blocked"
    assert decision.reason == "policy_blocks_semantic_projection"
    assert decision.nextcloud_tag is None


def test_bulk_report_preserves_existing_user_tags_and_deduplicates_projected_tags() -> None:
    report = govern_nextcloud_tags(
        [
            TagCandidate(tag="todo", tag_class="semantic", confidence=0.91),
            TagCandidate(tag="task", tag_class="system", confidence=0.93),
            TagCandidate(tag="freeform-idea", tag_class="semantic", confidence=0.9),
            TagCandidate(tag="latent-cluster-2", tag_class="graph_only", confidence=0.9),
        ],
        existing_user_tags=["Keep Me", "Keep Me", " Personal "],
    )

    assert report.preserved_user_tags == ("Keep Me", "Personal")
    assert report.projected_nextcloud_tags == ("task",)
    assert report.review_tags == ("freeform-idea",)
    assert report.blocked_tags == ("latent-cluster-2",)


def test_report_dict_is_stable_for_routing_consumers() -> None:
    payload = govern_nextcloud_tags(
        [TagCandidate(tag="decision-log", tag_class="system", confidence=0.95)],
        existing_user_tags=["Pinned"],
    ).to_dict()

    assert payload["preserved_user_tags"] == ("Pinned",)
    assert payload["projected_nextcloud_tags"] == ("decision",)
    assert payload["review_tags"] == ()
    assert payload["blocked_tags"] == ()
    assert payload["decisions"][0]["reason"] == "mapped_to_canonical"
