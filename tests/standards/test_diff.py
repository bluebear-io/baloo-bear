import pytest

from baloo.standards.diff import PatchParseError, changed_ranges


def test_added_and_modified_lines_become_new_side_ranges():
    patch = "\n".join(
        [
            "@@ -1,4 +1,5 @@",
            " keep",
            "-old",
            "+new",
            "+added",
            " keep",
            " keep",
            "@@ -20,3 +21,3 @@ def f():",
            " ctx",
            "-a",
            "+b",
            " ctx",
        ]
    )
    assert changed_ranges(patch) == [(2, 3), (22, 22)]


def test_pure_deletions_add_no_range():
    assert changed_ranges("@@ -1,3 +1,2 @@\n keep\n-gone\n keep") == []


def test_a_new_file_is_one_range():
    patch = "@@ -0,0 +1,3 @@\n+a\n+b\n+c\n\\ No newline at end of file"
    assert changed_ranges(patch) == [(1, 3)]


def test_interleaved_removals_do_not_split_a_run_of_additions():
    assert changed_ranges("@@ -1,2 +1,2 @@\n+a\n-x\n+b\n-y") == [(1, 2)]


def test_content_before_a_hunk_is_rejected():
    with pytest.raises(PatchParseError):
        changed_ranges("+a\n@@ -1 +1 @@\n+a")
