import unittest

from startup_retry import is_cloudflare_1015, retry_after_from_exception


class _Response:
    def __init__(self, headers=None):
        self.headers = headers or {}


class _Exc:
    def __init__(self, text='', headers=None):
        self.text = text
        self.response = _Response(headers)

    def __str__(self):
        return self.text


class StartupRateLimitTests(unittest.TestCase):
    def test_detects_cloudflare_1015(self):
        exc = _Exc('<title>Access denied</title><h1>Error 1015</h1> Cloudflare rate limited')
        self.assertTrue(is_cloudflare_1015(exc))

    def test_reads_retry_after_header(self):
        self.assertEqual(retry_after_from_exception(_Exc(headers={'Retry-After': '42.5'})), 42.5)

    def test_ignores_invalid_retry_after_header(self):
        self.assertIsNone(retry_after_from_exception(_Exc(headers={'Retry-After': 'nope'})))


if __name__ == '__main__':
    unittest.main()
