import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional
from pathlib import Path


def _ext_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _config_dir() -> Path:
    return _ext_root() / "config"


@dataclass(frozen=True)
class DemucsModelEntry:
    name: str
    repo: Optional[str]
    local_path: Optional[str]
    default_template: str
    allow_download_default: bool
    description: str


@dataclass(frozen=True)
class DemucsModelsConfig:
    default_model: str
    model_cache_dir: Optional[str]
    models: Dict[str, DemucsModelEntry]


@dataclass(frozen=True)
class DemucsTemplatesConfig:
    default_template: str
    templates: Dict[str, Dict[str, Any]]


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _require(d: Dict[str, Any], k: str, ctx: str):
    if k not in d:
        raise ValueError(f"Missing required key '{k}' in {ctx}")
    return d[k]


def load_demucs_models() -> DemucsModelsConfig:
    p = _config_dir() / "demucs_models.json"
    data = _load_json(p)

    default_model = str(_require(data, "default_model", str(p)))
    model_cache_dir = data.get("model_cache_dir", None)
    if model_cache_dir is not None:
        model_cache_dir = str(model_cache_dir)

    models_raw = _require(data, "models", str(p))
    if not isinstance(models_raw, dict):
        raise ValueError(f"'models' must be an object map in {p}")

    models: Dict[str, DemucsModelEntry] = {}
    for name, m in models_raw.items():
        if not isinstance(m, dict):
            raise ValueError(f"Model entry '{name}' must be object in {p}")
        repo = m.get("repo", None)
        local_path = m.get("local_path", None)
        default_template = str(m.get("default_template", "balanced"))
        allow_download_default = bool(m.get("allow_download_default", False))
        description = str(m.get("description", name))
        models[str(name)] = DemucsModelEntry(
            name=str(name),
            repo=str(repo) if repo is not None else None,
            local_path=str(local_path) if local_path is not None else None,
            default_template=default_template,
            allow_download_default=allow_download_default,
            description=description,
        )

    if default_model not in models:
        raise ValueError(f"default_model '{default_model}' not found in models list ({p})")

    return DemucsModelsConfig(
        default_model=default_model,
        model_cache_dir=model_cache_dir,
        models=models,
    )


def load_demucs_templates() -> DemucsTemplatesConfig:
    p = _config_dir() / "demucs_templates.json"
    data = _load_json(p)

    default_template = str(_require(data, "default_template", str(p)))
    templates_raw = _require(data, "templates", str(p))
    if not isinstance(templates_raw, dict):
        raise ValueError(f"'templates' must be an object map in {p}")

    templates: Dict[str, Dict[str, Any]] = {}
    for name, t in templates_raw.items():
        if not isinstance(t, dict):
            raise ValueError(f"Template '{name}' must be object in {p}")
        templates[str(name)] = dict(t)

    if default_template not in templates:
        raise ValueError(f"default_template '{default_template}' not found in templates ({p})")

    return DemucsTemplatesConfig(default_template=default_template, templates=templates)


# Cached singletons for node + routes
_DEMUCS_MODELS: Optional[DemucsModelsConfig] = None
_DEMUCS_TEMPLATES: Optional[DemucsTemplatesConfig] = None


def get_demucs_models() -> DemucsModelsConfig:
    global _DEMUCS_MODELS
    if _DEMUCS_MODELS is None:
        _DEMUCS_MODELS = load_demucs_models()
    return _DEMUCS_MODELS


def get_demucs_templates() -> DemucsTemplatesConfig:
    global _DEMUCS_TEMPLATES
    if _DEMUCS_TEMPLATES is None:
        _DEMUCS_TEMPLATES = load_demucs_templates()
    return _DEMUCS_TEMPLATES