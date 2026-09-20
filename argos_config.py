"""Shared model selection and paths; call before importing Argos."""
import os
from pathlib import Path

SUPPORTED_LANGUAGES = ("en", "es", "ar", "de", "fr", "no", "pt")


def active_languages():
    values = tuple(dict.fromkeys(p.strip().lower() for p in os.getenv(
        "ACTIVE_TRANSLATION_LANGS", ",".join(SUPPORTED_LANGUAGES)
    ).split(",") if p.strip()))
    if not values or "en" not in values or set(values) - set(SUPPORTED_LANGUAGES):
        raise ValueError("ACTIVE_TRANSLATION_LANGS must include en and use only en,es,ar,de,fr,no,pt")
    return values


def model_code(code):
    return "nb" if code == "no" else code


def required_pairs(languages):
    return tuple(pair for lang in languages if lang != "en"
                 for pair in (("en", model_code(lang)), (model_code(lang), "en")))


def configure_argos():
    root = Path(__file__).resolve().parent / "data" / "argos"
    defaults = {
        "ARGOS_PACKAGES_DIR": str(root / "packages"),
        "XDG_DATA_HOME": str(root / "share"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "ARGOS_DEVICE_TYPE": "cpu", "ARGOS_COMPUTE_TYPE": "int8",
        "ARGOS_INTER_THREADS": "1", "ARGOS_INTRA_THREADS": "1",
        "ARGOS_BATCH_SIZE": "1", "ARGOS_BEAM_SIZE": "1",
        "ARGOS_CHUNK_TYPE": "MINISBD", "ARGOS_LOW_MEMORY": "true",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    os.environ["ARGOS_MODEL_PROVIDER"] = "OPENNMT"
    os.environ["ARGOS_DEBUG"] = "0"
    if os.environ["ARGOS_CHUNK_TYPE"] != "MINISBD":
        raise ValueError("This deployment requires ARGOS_CHUNK_TYPE=MINISBD")
