"""Tests for the agent filesystem sandbox command builder."""

from baloo.agent import sandbox


def test_off_mode_returns_empty_prefix():
    assert sandbox.build_sandbox_prefix("off", "/work/tree") == []


def test_bwrap_prefix_binds_only_the_worktree_and_chdirs(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    prefix = sandbox.build_sandbox_prefix("bwrap", str(wt))

    assert prefix[0] == "bwrap"
    assert prefix[-1] == "--"  # command follows the prefix
    # worktree is bound read-only at its own path and is the working dir
    assert "--ro-bind" in prefix
    assert str(wt.resolve()) in prefix
    chdir_idx = prefix.index("--chdir")
    assert prefix[chdir_idx + 1] == str(wt.resolve())
    # /tmp is a fresh tmpfs so the cache root (also under /tmp) is hidden
    assert "--tmpfs" in prefix and "/tmp" in prefix


def test_bwrap_prefix_shares_network(tmp_path):
    # The agent must reach the model API; network namespace must NOT be unshared.
    prefix = sandbox.build_sandbox_prefix("bwrap", str(tmp_path))
    assert "--unshare-net" not in prefix


def test_probe_cmd_mirrors_real_prefix_privileged_ops():
    # A weak probe (e.g. `bwrap --ro-bind / / -- true`) passes on hardened
    # platforms where the real prefix's `--proc`/`--unshare-pid` then fail —
    # a false positive that crashes reviews instead of degrading. Lock the
    # probe to the operations that actually fail.
    assert "--proc" in sandbox._PROBE_CMD
    assert "--unshare-pid" in sandbox._PROBE_CMD
    # dynamic loader must be available or exec fails misleadingly
    assert "/lib" in sandbox._PROBE_CMD


def test_sandbox_available_off_is_false():
    assert sandbox.sandbox_available("off") is False


def test_sandbox_available_false_when_binary_missing(monkeypatch):
    monkeypatch.setattr(sandbox, "_bwrap_works", None)
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    assert sandbox.sandbox_available("bwrap") is False


def test_sandbox_available_probes_runtime_when_binary_present(monkeypatch):
    # Binary present but the runtime probe decides whether it actually works.
    monkeypatch.setattr(sandbox, "_bwrap_works", None)
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")

    ok = type("P", (), {"returncode": 0})()
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: ok)
    assert sandbox.sandbox_available("bwrap") is True

    # Binary present but userns blocked → probe returns non-zero → unavailable.
    monkeypatch.setattr(sandbox, "_bwrap_works", None)
    bad = type("P", (), {"returncode": 1})()
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: bad)
    assert sandbox.sandbox_available("bwrap") is False


def test_sandbox_available_probe_result_is_cached(monkeypatch):
    # After the first probe, the result is reused without re-running bwrap.
    monkeypatch.setattr(sandbox, "_bwrap_works", None)
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    calls = {"n": 0}

    def fake_run(*a, **k):
        calls["n"] += 1
        return type("P", (), {"returncode": 0})()

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    assert sandbox.sandbox_available("bwrap") is True
    assert sandbox.sandbox_available("bwrap") is True
    assert calls["n"] == 1


def test_build_subprocess_env_drops_secrets_keeps_runtime(monkeypatch):
    base = {
        "PATH": "/usr/bin",
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "ANTHROPIC_API_KEY": "sk-keep",
        "GEMINI_API_KEY": "g-keep",
        "OPENAI_API_KEY": "oa-keep",
        "AWS_ACCESS_KEY_ID": "AKIAKEEP",
        "AWS_SECRET_ACCESS_KEY": "secret-keep",
        "AWS_SESSION_TOKEN": "session-keep",
        "AWS_REGION": "us-west-2",
        "AWS_BEARER_TOKEN_BEDROCK": "bearer-keep",
        "AWS_WEB_IDENTITY_TOKEN_FILE": "/var/run/secrets/eks.amazonaws.com/serviceaccount/token",
        "AWS_ROLE_ARN": "arn:aws:iam::123:role/baloo",
        "HTTPS_PROXY": "http://proxy.corp:8080",
        "https_proxy": "http://proxy.corp:8080",
        "NO_PROXY": "localhost",
        "GITHUB_PRIVATE_KEY": "SECRET",
        "GITHUB_WEBHOOK_SECRET": "SECRET",
        "DATABASE_URL": "postgres://SECRET",
        "POSTGRES_PASSWORD": "SECRET",
        "DASHBOARD_PASSWORD": "SECRET",
    }
    env = sandbox.build_subprocess_env(base)

    # Model + runtime + proxy vars survive (proxy required in enterprise networks).
    assert env["ANTHROPIC_API_KEY"] == "sk-keep"
    assert env["GEMINI_API_KEY"] == "g-keep"
    assert env["OPENAI_API_KEY"] == "oa-keep"
    assert env["AWS_ACCESS_KEY_ID"] == "AKIAKEEP"
    assert env["AWS_SECRET_ACCESS_KEY"] == "secret-keep"
    assert env["AWS_SESSION_TOKEN"] == "session-keep"
    assert env["AWS_REGION"] == "us-west-2"
    assert env["AWS_BEARER_TOKEN_BEDROCK"] == "bearer-keep"
    assert env["AWS_WEB_IDENTITY_TOKEN_FILE"].endswith("/token")
    assert env["AWS_ROLE_ARN"].endswith("/baloo")
    assert env["HTTPS_PROXY"] == "http://proxy.corp:8080"
    assert env["https_proxy"] == "http://proxy.corp:8080"
    assert env["NO_PROXY"] == "localhost"
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/root"
    # No baloo secret leaks through.
    for leaked in (
        "GITHUB_PRIVATE_KEY",
        "GITHUB_WEBHOOK_SECRET",
        "DATABASE_URL",
        "POSTGRES_PASSWORD",
        "DASHBOARD_PASSWORD",
    ):
        assert leaked not in env


def test_aws_ro_bind_args_includes_irsa_and_credential_files(tmp_path):
    token = tmp_path / "token"
    creds = tmp_path / "credentials"
    token.write_text("jwt")
    creds.write_text("[default]\n")
    args = sandbox._aws_ro_bind_args(
        {
            "AWS_WEB_IDENTITY_TOKEN_FILE": str(token),
            "AWS_SHARED_CREDENTIALS_FILE": str(creds),
            "AWS_CONFIG_FILE": "",  # ignored
        }
    )
    assert args.count("--ro-bind-try") == 2
    assert str(token.resolve()) in args
    assert str(creds.resolve()) in args


def test_aws_ro_bind_args_includes_default_home_credentials(tmp_path):
    """AWS_PROFILE relies on ~/.aws, which is invisible unless bind-mounted."""
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    (home / ".aws" / "credentials").write_text("[bedrock]\n")
    (home / ".aws" / "config").write_text("[profile bedrock]\n")

    args = sandbox._aws_ro_bind_args({"HOME": str(home), "AWS_PROFILE": "bedrock"})

    assert str((home / ".aws" / "credentials").resolve()) in args
    assert str((home / ".aws" / "config").resolve()) in args


def test_aws_ro_bind_args_does_not_duplicate_explicit_paths(tmp_path):
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    creds = home / ".aws" / "credentials"
    creds.write_text("[default]\n")

    args = sandbox._aws_ro_bind_args({"HOME": str(home), "AWS_SHARED_CREDENTIALS_FILE": str(creds)})

    assert args.count(str(creds.resolve())) == 2  # one --ro-bind-try src/dest pair


def test_bwrap_prefix_binds_aws_credential_files(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    token = tmp_path / "eks-token"
    token.write_text("jwt")
    monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", str(token))
    monkeypatch.delenv("AWS_SHARED_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("AWS_CONFIG_FILE", raising=False)
    monkeypatch.delenv("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE", raising=False)

    prefix = sandbox.build_sandbox_prefix("bwrap", str(wt))
    token_path = str(token.resolve())
    assert "--ro-bind-try" in prefix
    idx = prefix.index(token_path)
    assert prefix[idx - 1] == "--ro-bind-try"
    assert prefix[idx + 1] == token_path


def test_databricks_ro_bind_args_binds_the_generated_agent_dir():
    args = sandbox._databricks_ro_bind_args({"HOME": "/home/baloo"})
    path = "/home/baloo/.baloo/pi-databricks"
    # --ro-bind-try, so this is a harmless no-op for every other provider.
    assert args == ["--ro-bind-try", path, path]


def test_databricks_ro_bind_args_falls_back_to_path_home(monkeypatch):
    # ensure_agent_dir() uses Path.home(), which falls back to the passwd entry
    # when HOME is unset. Bailing out here instead would write the config but
    # never bind it, and PI would fail with Unknown provider "databricks".
    from pathlib import Path

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/frompasswd")))
    args = sandbox._databricks_ro_bind_args({})
    path = "/home/frompasswd/.baloo/pi-databricks"
    assert args == ["--ro-bind-try", path, path]


def test_bwrap_prefix_binds_the_databricks_agent_dir(tmp_path, monkeypatch):
    # PI_CODING_AGENT_DIR survives the env scrub, but the directory it names
    # lives on the host and is invisible inside bwrap unless bound.
    wt = tmp_path / "wt"
    wt.mkdir()
    monkeypatch.setenv("HOME", "/home/baloo")

    prefix = sandbox.build_sandbox_prefix("bwrap", str(wt))
    path = "/home/baloo/.baloo/pi-databricks"
    idx = prefix.index(path)
    assert prefix[idx - 1] == "--ro-bind-try"
    assert prefix[idx + 1] == path


def test_build_subprocess_env_keeps_databricks_credentials():
    base = {
        "PATH": "/usr/bin",
        "DATABRICKS_TOKEN": "dapi-keep",
        "PI_CODING_AGENT_DIR": "/home/baloo/.baloo/pi-databricks",
        "GITHUB_PRIVATE_KEY": "SECRET",
    }
    env = sandbox.build_subprocess_env(base)

    assert env["DATABRICKS_TOKEN"] == "dapi-keep"
    assert env["PI_CODING_AGENT_DIR"] == "/home/baloo/.baloo/pi-databricks"
    assert "GITHUB_PRIVATE_KEY" not in env
