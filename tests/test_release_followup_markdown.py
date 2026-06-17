from src.release_followup_markdown import render_release_followup_markdown
from src.release_slice_router import ReleaseFollowupSlice


def test_render_release_followup_markdown_table():
    markdown = render_release_followup_markdown(
        (
            _slice(
                "REL-provider-proof-evidence",
                "Bob",
                False,
                ("docs/plans/1.0-manual-release-evidence-log.md", "plugins/obsidian/backend/model_router.py"),
            ),
            _slice(
                "REL-test-vault-rebuild-evidence",
                "Alice",
                True,
                ("docs/plans/1.0-manual-release-evidence-runbook.md",),
            ),
        )
    )

    assert markdown == "\n".join(
        [
            "# Release Followups",
            "",
            "| Slice | Owner | Parallel | Scope | Exit |",
            "| --- | --- | --- | --- | --- |",
            "| `REL-provider-proof-evidence` | Bob | no | `docs/plans/1.0-manual-release-evidence-log.md`<br>`plugins/obsidian/backend/model_router.py` | exit REL-provider-proof-evidence |",
            "| `REL-test-vault-rebuild-evidence` | Alice | yes | `docs/plans/1.0-manual-release-evidence-runbook.md` | exit REL-test-vault-rebuild-evidence |",
        ]
    )


def test_render_release_followup_markdown_empty_state():
    markdown = render_release_followup_markdown(())

    assert markdown == "# Release Followups\n\nNo follow-up slices are currently required."


def _slice(
    slice_id: str,
    owner: str,
    parallel_safe: bool,
    scope: tuple[str, ...],
) -> ReleaseFollowupSlice:
    return ReleaseFollowupSlice(
        slice_id=slice_id,
        owner=owner,
        title=slice_id,
        scope=scope,
        exit_criteria=f"exit {slice_id}",
        parallel_safe=parallel_safe,
    )
