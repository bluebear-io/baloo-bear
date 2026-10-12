"""Generated PI agent directories for providers configured through ``models.json``.

Some providers need PI configuration Baloo cannot pass on the command line, so
Baloo writes a ``models.json`` into a dedicated directory and points the PI
subprocess at it with ``PI_CODING_AGENT_DIR``. This module owns where those
directories live and how the file is published; each provider module owns what
goes in it.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def agent_dir_path(name: str, home: str | os.PathLike[str] | None = None) -> Path:
    """Return the generated agent directory for ``name`` (e.g. ``pi-databricks``).

    Deliberately *not* under ``/tmp``: the sandbox mounts a fresh tmpfs there,
    which would hide the file from the agent. ``sandbox`` binds this same path
    read-only, so the two must agree.
    """
    base = Path(home) if home is not None else Path.home()
    return base / ".baloo" / name


def write_models_json(
    name: str, config: dict, base_dir: str | os.PathLike[str] | None = None
) -> Path:
    """Publish ``config`` as ``models.json`` in the ``name`` agent dir and return the dir."""
    agent_dir = agent_dir_path(name, base_dir)
    agent_dir.mkdir(parents=True, exist_ok=True)

    config_path = agent_dir / "models.json"
    payload = json.dumps(config, indent=2) + "\n"

    # Skip the write when the content already matches, so the steady state is a
    # pure read and concurrent reviews don't touch the file at all.
    try:
        if config_path.read_text(encoding="utf-8") == payload:
            return agent_dir
    except FileNotFoundError:
        pass

    # Write via a uniquely-named temp file in the same directory, then rename.
    # os.replace is atomic on POSIX, so a PI subprocess reading models.json
    # concurrently sees either the old file or the new one, never a truncated
    # one. The temp name must be unique: a shared name would let two concurrent
    # first-run writers interleave into the same file and rename the mess into
    # place, reintroducing the race this avoids.
    fd, tmp_name = tempfile.mkstemp(dir=agent_dir, prefix=".models-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, config_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    logger.info("wrote PI provider config to %s", config_path)

    return agent_dir
