import unittest

from scripts.apply_javryo import javryo_manifest_changed


class JavryoReleaseGenerationTests(unittest.TestCase):
    def test_verified_direct_changes_invalidate_runtime_generation(self):
        before = {"A": {"standard.javryo": {"finalUrl": "https://streamtape.com/v/a"}},
                  "B": {"standard.reupload": {"finalUrl": "https://gofile.io/d/b"}}}
        after = {"B": {"standard.reupload": {"finalUrl": "https://gofile.io/d/b"}}}
        self.assertTrue(javryo_manifest_changed(before, after))
        self.assertFalse(javryo_manifest_changed(after, dict(after)))
        other_change = {"B": {"standard.reupload": {"finalUrl": "https://gofile.io/d/c"}}}
        self.assertFalse(javryo_manifest_changed(after, other_change))


if __name__ == "__main__":
    unittest.main()
