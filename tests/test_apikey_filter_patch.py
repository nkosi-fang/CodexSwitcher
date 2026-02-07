import unittest
from types import SimpleNamespace

import pyside_switcher


class ApplyApiKeyFilterPatchTests(unittest.TestCase):
    def _build_subject(self):
        obj = SimpleNamespace()
        cls = pyside_switcher.VSCodePluginPage
        for name in (
            "_apply_chatgpt_auth_only_models_patch",
            "_apply_chatgpt_auth_guard_patch",
            "_apply_apikey_filter_patch",
        ):
            setattr(obj, name, getattr(cls, name).__get__(obj, SimpleNamespace))
        return obj

    def test_keeps_patching_auth_only_rules_when_apikey_ternary_already_present(self):
        subject = self._build_subject()
        models = ["gpt-5.3-codex", "gpt-5.2-codex"]
        content = (
            'gate=i==="chatgpt"||i==="apikey"?!0:(i==="copilot"?kUe:SUe).has(v.model);'
            'if(flag&&!!mt&&CHAT_GPT_AUTH_ONLY_MODELS.has(normalizeModel(mt))){return;}'
            'CHAT_GPT_AUTH_ONLY_MODELS = new Set(["gpt-5.3-codex","gpt-5.2-codex","gpt-4.1"]);'
        )

        patched, ok = subject._apply_apikey_filter_patch(content, models)

        self.assertTrue(ok)
        self.assertIn('!=="apikey"', patched)
        self.assertIn('CHAT_GPT_AUTH_ONLY_MODELS = new Set(["gpt-4.1"])', patched)


if __name__ == "__main__":
    unittest.main()
