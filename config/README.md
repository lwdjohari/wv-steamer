# Demucs Config

## demucs_models.json
Defines models shown in the node dropdown.
- `repo`: pretrained model id (Demucs)
- `local_path`: absolute/inside-container file path to a local model checkpoint
- `allow_download_default`: default behavior; the node still has a per-run boolean

Optional:
- `model_cache_dir`: override where local model files are expected if downloads are disabled.

## demucs_templates.json
Defines preset parameter templates for Demucs knobs.
Selecting a template in the UI resets node knobs to that template.