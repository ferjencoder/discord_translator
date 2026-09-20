import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from argos_config import active_languages, required_pairs
from install_argos_models import install_models


class ModelInstallerTests(unittest.TestCase):
    def test_default_is_exactly_fourteen_directional_models(self):
        with patch.dict(os.environ, {}, clear=True):
            pairs = required_pairs(active_languages())
        self.assertEqual(len(pairs), 14)
        self.assertIn(("en", "it"), pairs)
        self.assertIn(("it", "en"), pairs)
        self.assertIn(("en", "nb"), pairs)
        self.assertIn(("nb", "en"), pairs)
        self.assertFalse(any(code in {"ceb", "sv", "ru", "no"} for pair in pairs for code in pair))

    def test_already_installed_is_offline_and_idempotent(self):
        package = Mock()
        package.get_installed_packages.return_value = [SimpleNamespace(from_code="en", to_code="es")]
        self.assertEqual(len(install_models(package, (("en", "es"),))), 1)
        package.update_package_index.assert_not_called()
        package.install_from_path.assert_not_called()

    def test_unavailable_pair_fails_before_partial_install(self):
        package = Mock()
        package.get_installed_packages.return_value = []
        package.get_available_packages.return_value = [SimpleNamespace(from_code="en", to_code="es")]
        with self.assertRaises(RuntimeError):
            install_models(package, (("en", "es"), ("es", "en")))
        package.install_from_path.assert_not_called()

    def test_only_missing_required_pair_is_downloaded(self):
        existing = SimpleNamespace(from_code="en", to_code="es")
        required = Mock(from_code="es", to_code="en")
        unrelated = Mock(from_code="en", to_code="ru")
        package = Mock()
        package.get_installed_packages.side_effect = [[existing], [existing, required]]
        package.get_available_packages.return_value = [required, unrelated]
        install_models(package, (("en", "es"), ("es", "en")))
        required.download.assert_called_once()
        unrelated.download.assert_not_called()
        package.install_from_path.assert_called_once_with(required.download.return_value)
