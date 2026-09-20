import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from argos_config import configure_argos
from check_argos_models import MissingArgosModel, MissingSentenceModel, validate_models


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ACTIVE_TRANSLATION_LANGS": "en,es"})
        self.env.start()
        self.addCleanup(self.env.stop)
        configure_argos()

    def test_missing_models_identify_pairs_and_do_not_download(self):
        with patch("argostranslate.package.get_installed_packages", return_value=[]), patch(
            "argostranslate.package.update_package_index") as download:
            with self.assertRaisesRegex(MissingArgosModel, "en->es, es->en"):
                validate_models()
            download.assert_not_called()

    def test_incomplete_model_is_distinct_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            packages = [SimpleNamespace(from_code=a, to_code=b, package_path=Path(directory))
                        for a, b in (("en", "es"), ("es", "en"))]
            with patch("argostranslate.package.get_installed_packages", return_value=packages):
                with self.assertRaisesRegex(MissingArgosModel, "Incomplete model en->es"):
                    validate_models()

    def test_complete_assets_and_missing_sentence_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model").mkdir()
            (root / "model" / "model.bin").touch()
            packages = [SimpleNamespace(from_code=a, to_code=b, package_path=root)
                        for a, b in (("en", "es"), ("es", "en"))]
            with patch("argostranslate.package.get_installed_packages", return_value=packages), patch(
                "argostranslate.sbd.MiniSBDSentencizer"), patch(
                "check_argos_models.sentence_model_path", return_value=root / "sentence.onnx") as sentence:
                self.assertEqual(validate_models(), 2)
                self.assertEqual(sentence.call_count, 2)
                self.assertEqual(logging.getLogger("argostranslate.utils").level, logging.WARNING)
                sentence.side_effect = MissingSentenceModel("Missing sentence model")
                with self.assertRaises(MissingSentenceModel):
                    validate_models()
