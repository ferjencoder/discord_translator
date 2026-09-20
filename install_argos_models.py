"""Build-time downloads only; never requires Discord credentials."""
import gc
import os
from dotenv import load_dotenv
from argos_config import active_languages, configure_argos, required_pairs


def install_models(package, pairs):
    installed = {(p.from_code, p.to_code): p for p in package.get_installed_packages()}
    missing = set(pairs) - installed.keys()
    if missing:
        package.update_package_index()
        available = {(p.from_code, p.to_code): p for p in package.get_available_packages()}
        unavailable = missing - available.keys()
        if unavailable:
            raise RuntimeError(f"Required Argos models unavailable: {sorted(unavailable)}")
        for pair in pairs:
            if pair in missing:
                print(f"Downloading/installing: {pair[0]} -> {pair[1]}", flush=True)
                path = available[pair].download()
                package.install_from_path(path)
                path.unlink(missing_ok=True)
    installed = {(p.from_code, p.to_code): p for p in package.get_installed_packages()}
    if set(pairs) - installed.keys():
        raise RuntimeError("Argos installation incomplete")
    return [installed[pair] for pair in pairs]


def main():
    load_dotenv()
    configure_argos()
    from argostranslate import package
    from argostranslate.sbd import MiniSBDSentencizer
    from minisbd import SBDetect
    if os.environ["ARGOS_CHUNK_TYPE"] != "MINISBD":
        raise ValueError("This build installer requires ARGOS_CHUNK_TYPE=MINISBD")
    pairs = required_pairs(active_languages())
    packages = install_models(package, pairs)
    for pkg in packages:
        detector = MiniSBDSentencizer(pkg)
        detector.detector = SBDetect(detector.lang, use_gpu=False, max_threads=1)
        detector.split_sentences("Build check.")
        del detector
        gc.collect()
    print(f"Ready: {len(pairs)} translation models and their sentence splitters.", flush=True)


if __name__ == "__main__":
    main()
