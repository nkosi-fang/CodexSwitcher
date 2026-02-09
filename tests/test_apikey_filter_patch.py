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
            "_is_apikey_dynamic_model_flow",
            "_apply_dynamic_apikey_models_patch",
            "_apply_apikey_order_inject_patch",
            "_apply_initial_data_patch",
            "_reasoning_efforts_literal",
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

    def test_does_not_report_success_when_apikey_gate_is_missing(self):
        subject = self._build_subject()
        models = ["gpt-5.3-codex"]
        content = (
            'if(flag&&!!mt&&CHAT_GPT_AUTH_ONLY_MODELS.has(normalizeModel(mt))){return;}'
            'CHAT_GPT_AUTH_ONLY_MODELS = new Set(["gpt-5.3-codex","gpt-4.1"]);'
        )

        patched, ok = subject._apply_apikey_filter_patch(content, models)

        self.assertFalse(ok)
        self.assertNotIn('i==="chatgpt"||i==="apikey"?!0:', patched)

    def test_dynamic_flow_injects_apikey_models_when_static_order_rule_is_missing(self):
        subject = self._build_subject()
        models = ["gpt-5.3-codex"]
        content = (
            'function Jv(){return {listModels:1,modelsByType:1};}'
            'u=h=>{const{data:f}=h,m={models:[]};let g=null;return '
            'f.forEach(v=>{if(i==="chatgpt"||i==="apikey"?!0:(i==="copilot"?kUe:SUe).has(v.model))'
            '{m.models.push(v),g=v.isDefault?v:g}}),{modelsByType:m,defaultModel:g}};'
        )

        patched, ok = subject._apply_apikey_order_inject_patch(content, models)

        self.assertTrue(ok)
        self.assertIn('__csDynamicModels=["gpt-5.3-codex"]', patched)
        self.assertIn('i==="apikey"&&(()=>{const __csDynamicModels=', patched)

    def test_optional_initial_data_rule_treated_as_ok_for_dynamic_flow(self):
        subject = self._build_subject()
        models = ["gpt-5.3-codex"]
        content = (
            'function Jv(){return {listModels:1,modelsByType:1};}'
            'gate=i==="chatgpt"||i==="apikey"?!0:(i==="copilot"?kUe:SUe).has(v.model);'
        )

        patched, ok = subject._apply_initial_data_patch(content, models)

        self.assertTrue(ok)
        self.assertEqual(patched, content)


    def test_dynamic_flow_patch_merges_models_when_marker_already_exists(self):
        subject = self._build_subject()
        models = ["gpt-5.3-codex", "gpt-5.2-codex"]
        content = (
            'function Jv(){return {listModels:1,modelsByType:1};}'
            'u=h=>{const{data:f}=h,m={models:[]};let g=null;return '
            'f.forEach(v=>{if(i==="chatgpt"||i==="apikey"?!0:(i==="copilot"?kUe:SUe).has(v.model))'
            '{m.models.push(v),g=v.isDefault?v:g}}),'
            'i==="apikey"&&(()=>{const __csDynamicModels=["gpt-5.2-codex"],__csDynamicEfforts=[];'
            '__csDynamicModels.forEach(__csModel=>{m.models.find(__csItem=>__csItem.model===__csModel)||'
            'm.models.unshift({model:__csModel,supportedReasoningEfforts:__csDynamicEfforts,defaultReasoningEffort:"medium",isDefault:!1})})(),{modelsByType:m,defaultModel:g}};'
        )

        patched, ok = subject._apply_apikey_order_inject_patch(content, models)

        self.assertTrue(ok)
        self.assertIn('__csDynamicModels=["gpt-5.3-codex","gpt-5.2-codex"]', patched)

    def test_optional_rules_still_fail_without_dynamic_flow_markers(self):
        subject = self._build_subject()
        models = ["gpt-5.3-codex"]
        content = 'gate=i==="chatgpt"||i==="apikey"?!0:(i==="copilot"?kUe:SUe).has(v.model);'

        _, order_ok = subject._apply_apikey_order_inject_patch(content, models)
        _, init_ok = subject._apply_initial_data_patch(content, models)

        self.assertFalse(order_ok)
        self.assertFalse(init_ok)



if __name__ == "__main__":
    unittest.main()
