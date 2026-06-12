"""
config.py — YAML configuration and profile loading.

Reads ~/.boxraudio.yaml (or a custom path) and exposes defaults and named profiles.
"""

import os
import yaml
from pathlib import Path


DEFAULT_CONFIG_PATH = os.path.expanduser("~/.boxraudio.yaml")
DEFAULT_CACHE_PATH  = os.path.expanduser("~/.boxraudio_cache.db")


DEFAULT_CONFIG = {
    "defaults": {
        "cache_file":  DEFAULT_CACHE_PATH,
        "workers":     4,
        "size_only":   True,
        "show_banner": True,
    },
    "profiles": {},
}


class ConfigError(Exception):
    pass


def load_config(path: str = None) -> dict:
    """Load configuration from YAML file, returning a dict."""
    config_path = os.path.expanduser(path) if path else DEFAULT_CONFIG_PATH
    if not os.path.isfile(config_path):
        return DEFAULT_CONFIG.copy()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {config_path}: {e}") from e

    merged = DEFAULT_CONFIG.copy()
    if "defaults" in data:
        merged["defaults"].update(data["defaults"])
    if "profiles" in data:
        merged["profiles"] = data["profiles"]

    return merged


def expand_paths(d: dict) -> dict:
    """Recursively expand ~ in all string values that look like paths."""
    result = {}
    for k, v in d.items():
        if isinstance(v, str) and v.startswith("~"):
            result[k] = os.path.expanduser(v)
        elif isinstance(v, list):
            result[k] = [
                os.path.expanduser(item) if isinstance(item, str) and item.startswith("~") else item
                for item in v
            ]
        elif isinstance(v, dict):
            result[k] = expand_paths(v)
        else:
            result[k] = v
    return result


def get_profile(config: dict, profile_name: str) -> dict:
    """
    Get a named profile, merged with global defaults.

    Merge semantics:
    - Scalar values from the profile override defaults
    - List values from the profile REPLACE the defaults list (do not concat)
      — this means if defaults has dedupe_search: [a] and profile has
      dedupe_search: [b, c], you get [b, c], not [a, b, c]. To extend the
      defaults list, repeat the items you want to keep.
    """
    if profile_name not in config.get("profiles", {}):
        raise ConfigError(f"Profile '{profile_name}' not found in config.")

    merged = dict(config.get("defaults", {}))
    merged.update(config["profiles"][profile_name])
    return expand_paths(merged)


def list_profiles(config: dict) -> list:
    """Return a list of available profile names."""
    return sorted(config.get("profiles", {}).keys())


def get_defaults(config: dict) -> dict:
    """Return just the global defaults, with paths expanded."""
    return expand_paths(config.get("defaults", {}))
