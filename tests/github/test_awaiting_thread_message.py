"""The blocking-threads block must say what is blocking and how to clear it."""

from datetime import datetime, timezone

from baloo.github.models import DiscussionComment, DiscussionThread
from baloo.review.orchestrator import _format_awaiting_threads


def _thread(thread_id: int, path: str, line: int) -> DiscussionThread:
    now = datetime.now(timezone.utc)
    return DiscussionThread(
        id=thread_id,
        path=path,
        line=line,
        comments=[
            DiscussionComment(
                id=thread_id,
                author="baloo-code-reviewer[bot]",
                body="finding",
                created_at=now,
                updated_at=now,
                source="review_comment",
                is_baloo=True,
            )
        ],
        is_baloo_thread=True,
        awaiting_response=True,
        last_activity=now,
        root_comment_id=thread_id,
    )


def test_block_names_each_thread_and_how_to_clear_it():
    text = _format_awaiting_threads(
        [_thread(11, "baloo/a.py", 4), _thread(22, "baloo/b.py", 9)],
        "org/repo",
        7,
    )

    assert "2 earlier finding(s) have had no response" in text
    assert "`baloo/a.py:4` — https://github.com/org/repo/pull/7#discussion_r11" in text
    assert "`baloo/b.py:9` — https://github.com/org/repo/pull/7#discussion_r22" in text
    assert "Resolve conversation" in text
    assert "won't fix" in text


def test_long_lists_are_truncated():
    text = _format_awaiting_threads(
        [_thread(i, f"baloo/f{i}.py", i) for i in range(1, 9)],
        "org/repo",
        7,
    )

    assert text.count("#discussion_r") == 5
    assert "…and 3 more" in text
