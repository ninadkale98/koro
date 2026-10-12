"""Configuration: ~/.config/koro/config.json, overridable by environment variables.

KORO_SUPABASE_URL / SUPABASE_URL      project URL, e.g. https://abc.supabase.co
KORO_SUPABASE_KEY / SUPABASE_SECRET_KEY  secret (service-role) key
KORO_LEARNER_ID                       learner uuid
KORO_CONFIG                           alternative config file path
"""
from __future__ import annotations

import json
import os
import socket
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


def config_path() -> Path:
    override = os.environ.get("KORO_CONFIG")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "koro" / "config.json"


@dataclass
class Config:
    supabase_url: str = ""
    supabase_key: str = ""
    learner_id: str = ""
    learner_name: str = ""
    device_id: str = field(default_factory=lambda: socket.gethostname() or "device")
    client: str = "cli"
    # entity id -> absolute path of the local source file, used by ingest helpers
    sources: dict = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)


def load() -> Config:
    path = config_path()
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    cfg = Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})
    env = os.environ
    cfg.supabase_url = env.get("KORO_SUPABASE_URL") or env.get("SUPABASE_URL") or cfg.supabase_url
    cfg.supabase_key = (env.get("KORO_SUPABASE_KEY") or env.get("SUPABASE_SECRET_KEY")
                        or env.get("SUPABASE_SERVICE_ROLE_KEY") or cfg.supabase_key)
    cfg.learner_id = env.get("KORO_LEARNER_ID") or cfg.learner_id
    cfg.client = env.get("KORO_CLIENT") or cfg.client
    cfg.supabase_url = cfg.supabase_url.rstrip("/")
    return cfg


def save(cfg: Config) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # the file holds a secret key
    tmp.replace(path)
    return path


def source_for(cfg: Config, entity_id: str) -> Optional[str]:
    p = cfg.sources.get(entity_id)
    return p if p and os.path.exists(p) else None
