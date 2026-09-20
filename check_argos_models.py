"""Offline build/runtime asset check. Does not connect to Discord or download."""
import logging
from pathlib import Path

from dotenv import load_dotenv
from argos_config import active_languages, configure_argos, required_pairs

log = logging.getLogger(__name__)


class MissingArgosModel(RuntimeError):
    pass


class MissingSentenceModel(RuntimeError):
    pass


def sentence_model_path(detector):
    from minisbd import models
    filename = models.MODELS.get(detector.lang)
    path = Path(models.cache_dir) / filename if filename else Path(detector.lang)
    if not path.is_file():
        raise MissingSentenceModel(
            f"Missing sentence model at {path}; run install_argos_models.py during build")
    return path.resolve()


def validate_models():
    configure_argos()
    from argostranslate import package, settings
    from argostranslate.sbd import MiniSBDSentencizer
    # Argos sets its own logger to INFO at import, even with ARGOS_DEBUG=0.
    logging.getLogger("argostranslate.utils").setLevel(logging.WARNING)
    log.info("Argos asset paths: packages=%s data=%s", settings.package_data_dir, settings.data_dir)
    pairs = required_pairs(active_languages())
    installed = {(p.from_code, p.to_code): p for p in package.get_installed_packages()}
    missing = set(pairs) - installed.keys()
    if missing:
        names = ", ".join(f"{source}->{target}" for source, target in sorted(missing))
        raise MissingArgosModel(
            f"Missing translation models: {names}; searched {settings.package_data_dir}. "
            "Run install_argos_models.py during build and keep build/runtime asset paths identical.")
    for pair in pairs:
        pkg = installed[pair]
        if not (pkg.package_path / "model" / "model.bin").is_file():
            raise MissingArgosModel(f"Incomplete model {pair[0]}->{pair[1]} at {pkg.package_path}")
        sentence_model_path(MiniSBDSentencizer(pkg))
    log.info("Argos assets verified: %d directional models and sentence splitters", len(pairs))
    return len(pairs)


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    validate_models()
