#!/usr/bin/env python3
"""
Unit tests for the image tool on LiteLLM.

Image models go through litellm.aimage_generation, except OpenRouter's, which
go through chat completions (see TestOpenRouter). Agent configs saved
before that (bare OpenAI names, "nano-banana", `true`) must keep working, and the
keys MATE already uses must reach the provider.
"""

import asyncio
import base64
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.tools import image_tools as it


def setUpModule():
    # No dashboard setting unless a test sets one; keeps these tests off the database
    global _setting_patch
    _setting_patch = patch("shared.utils.system_settings.get_setting", return_value=None)
    _setting_patch.start()


def tearDownModule():
    _setting_patch.stop()

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _response(b64=None, url=None):
    return SimpleNamespace(data=[SimpleNamespace(b64_json=b64, url=url)])


def _chat_response(images=None, content=""):
    """A chat completion as LiteLLM returns OpenRouter's: images on the message."""
    message = SimpleNamespace(content=content, images=images)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class TestResolveImageModel(unittest.TestCase):

    @patch.dict(os.environ, {}, clear=True)
    def test_default_is_dall_e_3(self):
        self.assertEqual(it.resolve_image_model(None), ("dall-e-3", {}))

    @patch.dict(os.environ, {"IMAGE_MODEL": "black_forest_labs/flux-pro-1.1"}, clear=True)
    def test_image_model_sets_the_default(self):
        self.assertEqual(it.resolve_image_model(None)[0], "black_forest_labs/flux-pro-1.1")

    @patch.dict(os.environ, {"IMAGE_MODEL": "stability/sd3-large"}, clear=True)
    def test_the_dashboard_setting_wins_over_image_model(self):
        with patch("shared.utils.system_settings.get_setting", return_value="recraft/recraftv3"):
            self.assertEqual(it.default_image_model(), ("recraft/recraftv3", "dashboard"))
            self.assertEqual(it.resolve_image_model(None)[0], "recraft/recraftv3")
        self.assertEqual(it.default_image_model(), ("stability/sd3-large", "IMAGE_MODEL"))

    @patch.dict(os.environ, {}, clear=True)
    def test_nano_banana_means_gemini_flash_image_on_openrouter(self):
        self.assertEqual(it.resolve_image_model("nano-banana")[0],
                         "openrouter/google/gemini-2.5-flash-image")

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "g"}, clear=True)
    def test_gemini_takes_the_google_key(self):
        # LiteLLM's image call reads only GEMINI_API_KEY
        self.assertEqual(it.resolve_image_model("gemini/gemini-2.5-flash-image")[1], {"api_key": "g"})

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "or"}, clear=True)
    def test_a_bare_openai_name_falls_back_to_openrouter_as_before(self):
        self.assertEqual(it.resolve_image_model("gpt-image-1")[1],
                         {"api_key": "or", "api_base": "https://openrouter.ai/api/v1"})

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk", "OPENROUTER_API_KEY": "or"}, clear=True)
    def test_the_openai_key_wins_for_a_bare_name(self):
        self.assertEqual(it.resolve_image_model("dall-e-3")[1], {})

    @patch.dict(os.environ, {"OPENAI_API_KEY_BACKUP": "bk"}, clear=True)
    def test_the_backup_key_is_used_without_the_main_one(self):
        self.assertEqual(it.resolve_image_model("dall-e-3")[1], {"api_key": "bk"})


class TestToolNames(unittest.TestCase):

    def test_names_kept_from_before(self):
        self.assertEqual(it.image_tool_name("gpt-image-1"), "generate_image_gpt_image_1")
        self.assertEqual(it.image_tool_name("dall-e-3"), "generate_image_dall_e_3")
        self.assertEqual(it.image_tool_name("nano-banana"), "generate_image_nano_banana")
        self.assertEqual(it.image_tool_name("openrouter/google/gemini-2.5-flash-image"),
                         "generate_image_nano_banana")

    def test_any_model_gets_a_valid_name(self):
        self.assertEqual(it.image_tool_name("black_forest_labs/flux-pro-1.1"),
                         "generate_image_black_forest_labs_flux_pro_1_1")

    def _tools(self, image_tools):
        return it.create_image_tools_from_config(
            {"name": "a", "tool_config": json.dumps({"image_tools": image_tools})})

    def test_config_shapes(self):
        self.assertEqual([t.__name__ for t in self._tools(True)], ["generate_image"])
        self.assertEqual([t.__name__ for t in self._tools({"model": ""})], ["generate_image"])
        self.assertEqual([t.__name__ for t in self._tools({"model": "stability/sd3-large"})],
                         ["generate_image_stability_sd3_large"])
        self.assertEqual(self._tools(False), [])


class TestGenerate(unittest.TestCase):

    def _run(self, response, model="black_forest_labs/flux-pro-1.1", config=None, env=None):
        ctx = MagicMock()
        ctx.save_artifact = AsyncMock(return_value=3)
        call = AsyncMock(return_value=response)
        with patch.dict(os.environ, env or {}, clear=True), \
                patch("litellm.aimage_generation", call), \
                patch.object(it, "_get_context_values", return_value=("app", "u", "s")):
            result = asyncio.run(it._generate_image_internal("a cat", ctx, model, config or {}))
        return result, call, ctx

    def test_any_model_is_passed_to_litellm_with_its_parameters(self):
        result, call, _ = self._run(_response(b64=base64.b64encode(PNG).decode()),
                                    config={"aspect_ratio": "16:9"})
        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs["model"], "black_forest_labs/flux-pro-1.1")
        self.assertEqual(kwargs["aspect_ratio"], "16:9")
        self.assertTrue(kwargs["drop_params"])
        self.assertNotIn("size", kwargs)  # OpenAI defaults only for bare OpenAI names
        self.assertTrue(result["success"])
        self.assertEqual(result["artifact"]["version"], 3)

    def test_endpoint_and_key_settings_are_not_taken_from_tool_config(self):
        # An agent can write tool_config through create_agent; it must not redirect the call
        _, call, _ = self._run(_response(b64=base64.b64encode(PNG).decode()),
                               config={"api_base": "http://169.254.169.254", "api_key": "x",
                                       "extra_headers": {"a": "b"}, "quality": "hd"})
        kwargs = call.call_args.kwargs
        self.assertNotIn("api_base", kwargs)
        self.assertNotIn("extra_headers", kwargs)
        self.assertNotEqual(kwargs.get("api_key"), "x")
        self.assertEqual(kwargs["quality"], "hd")

    def test_a_bare_openai_name_keeps_its_defaults(self):
        _, call, _ = self._run(_response(b64=base64.b64encode(PNG).decode()), model="dall-e-3",
                               env={"OPENAI_API_KEY": "sk"})
        kwargs = call.call_args.kwargs
        self.assertEqual((kwargs["size"], kwargs["n"], kwargs["quality"]), ("1024x1024", 1, "standard"))

    def test_a_jpeg_is_saved_as_a_jpeg(self):
        result, _, ctx = self._run(_response(b64=base64.b64encode(JPEG).decode()))
        filename, part = ctx.save_artifact.call_args.args
        self.assertTrue(filename.endswith(".jpg"))
        self.assertEqual(part.inline_data.mime_type, "image/jpeg")
        self.assertEqual(result["artifact"]["mime_type"], "image/jpeg")

    def test_a_png_is_marked_as_ai_generated(self):
        with patch.object(it, "mark_png_as_ai_generated", wraps=it.mark_png_as_ai_generated) as mark:
            self._run(_response(b64=base64.b64encode(PNG).decode()))
        mark.assert_called_once()

    def test_a_data_url_is_decoded(self):
        data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
        result, _, ctx = self._run(_response(b64=data_url))
        self.assertTrue(result["success"])
        self.assertTrue(ctx.save_artifact.call_args.args[0].endswith(".png"))

    def test_no_image_is_an_error_not_a_success(self):
        result, _, ctx = self._run(SimpleNamespace(data=[]))
        self.assertFalse(result["success"])
        ctx.save_artifact.assert_not_called()

    def test_a_provider_error_is_reported(self):
        call = AsyncMock(side_effect=Exception("AuthenticationError: invalid api key"))
        with patch("litellm.aimage_generation", call):
            result = asyncio.run(it._generate_image_internal("a cat", None, "xai/grok-imagine-image", {}))
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "authentication_error")
        self.assertNotIn("b64", json.dumps(result))

    def test_nano_banana_wrapper_sends_google_names_to_openrouter(self):
        call = AsyncMock(return_value=_chat_response([{"image_url": {"url": "https://img.example/x.png"}}]))
        with patch("litellm.acompletion", call):
            asyncio.run(it.generate_image_nano_banana(
                "a cat", None, model_config={"model": "google/gemini-2.5-flash-image"}))
        self.assertEqual(call.call_args.kwargs["model"], "openrouter/google/gemini-2.5-flash-image")


class TestOpenRouter(unittest.TestCase):
    """OpenRouter answers with an image only when the request asks for one.

    LiteLLM's aimage_generation leaves out modalities for OpenRouter, so the
    model replied in text and every generation failed with "no image".
    """

    MODEL = "openrouter/google/gemini-2.5-flash-image"

    def _run(self, reply, config=None):
        ctx = MagicMock()
        ctx.save_artifact = AsyncMock(return_value=1)
        chat = AsyncMock(return_value=reply)
        image = AsyncMock()
        with patch("litellm.acompletion", chat), patch("litellm.aimage_generation", image):
            result = asyncio.run(it._generate_image_internal("a cat", ctx, self.MODEL, config or {}))
        return result, chat, image, ctx

    def test_asks_for_an_image_through_chat_completions(self):
        data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
        result, chat, image, ctx = self._run(_chat_response([{"image_url": {"url": data_url}}]))
        self.assertTrue(result["success"])
        image.assert_not_called()
        kwargs = chat.call_args.kwargs
        self.assertEqual(kwargs["model"], self.MODEL)
        self.assertEqual(kwargs["modalities"], ["image", "text"])
        self.assertEqual(kwargs["messages"], [{"role": "user", "content": "a cat"}])
        saved = ctx.save_artifact.call_args.args[1].inline_data
        self.assertEqual(saved.mime_type, "image/png")
        self.assertTrue(saved.data.startswith(b"\x89PNG"))

    def test_size_and_quality_become_image_config(self):
        reply = _chat_response([{"image_url": {"url": "https://img.example/x.png"}}])
        with patch.object(it, "_download_image", AsyncMock(return_value=PNG)):
            _, chat, _, _ = self._run(reply, {"size": "1536x1024", "quality": "high"})
        self.assertEqual(chat.call_args.kwargs["extra_body"],
                         {"image_config": {"aspect_ratio": "3:2", "image_size": "4K"}})
        with patch.object(it, "_download_image", AsyncMock(return_value=PNG)):
            _, chat, _, _ = self._run(reply, {"aspect_ratio": "16:9"})
        self.assertEqual(chat.call_args.kwargs["extra_body"], {"image_config": {"aspect_ratio": "16:9"}})
        with patch.object(it, "_download_image", AsyncMock(return_value=PNG)):
            _, chat, _, _ = self._run(reply)
        self.assertNotIn("extra_body", chat.call_args.kwargs)

    def test_a_url_goes_through_the_download_guard(self):
        reply = _chat_response([{"image_url": {"url": "http://169.254.169.254/x.png"}}])
        guard = AsyncMock(side_effect=ValueError("refusing an internal address"))
        with patch.object(it, "_download_image", guard):
            result, _, _, ctx = self._run(reply)
        guard.assert_called_once_with("http://169.254.169.254/x.png")
        self.assertFalse(result["success"])
        ctx.save_artifact.assert_not_called()

    def test_a_text_only_reply_says_what_the_model_said(self):
        result, _, _, ctx = self._run(_chat_response(None, "I can't draw that.\nTry another prompt."))
        self.assertFalse(result["success"])
        self.assertIn("the model said: I can't draw that. Try another prompt.", result["error"])
        self.assertNotIn("\n", result["error"])
        ctx.save_artifact.assert_not_called()

    def test_other_providers_keep_the_image_call(self):
        call = AsyncMock(return_value=_response(b64=base64.b64encode(PNG).decode()))
        chat = AsyncMock()
        with patch("litellm.aimage_generation", call), patch("litellm.acompletion", chat):
            asyncio.run(it._generate_image_internal("a cat", None, "gemini/imagen-4.0-generate-001", {}))
        call.assert_called_once()
        chat.assert_not_called()


class TestOnlyImageParametersPass(unittest.TestCase):
    """tool_config can be written by an agent steered from a chat (update_agent)."""

    def test_litellm_control_arguments_are_dropped(self):
        hostile = {
            "mock_response": "http://169.254.169.254/latest/meta-data/",
            "success_callback": ["langsmith"], "langsmith_base_url": "https://evil.example",
            "aws_bedrock_runtime_endpoint": "https://evil.example", "ssl_verify": False,
            "api_base": "http://10.0.0.1", "metadata": {"a": 1}, "caching": True,
            "size": "1024x1024", "aspect_ratio": "16:9",
        }
        config = it.get_model_config("black_forest_labs/flux-pro-1.1", hostile)
        self.assertEqual(config, {"size": "1024x1024", "aspect_ratio": "16:9"})

    def test_every_litellm_control_argument_is_outside_the_allowlist(self):
        from litellm.types.utils import all_litellm_params
        self.assertEqual(it._ALLOWED_PARAMS & set(all_litellm_params), set())


class TestDownloadImage(unittest.TestCase):

    def _resolve_to(self, ip):
        loop = MagicMock()
        loop.getaddrinfo = AsyncMock(return_value=[(None, None, None, "", (ip, 0))])
        return patch("shared.utils.tools.image_tools.asyncio.get_running_loop", return_value=loop)

    def _serve(self, handler):
        transport = httpx.MockTransport(handler)
        real = httpx.AsyncClient
        return patch("httpx.AsyncClient", side_effect=lambda **kw: real(transport=transport, **kw))

    @patch.dict(os.environ, {}, clear=True)
    def test_internal_addresses_are_refused(self):
        for ip in ("169.254.169.254", "127.0.0.1", "10.0.0.5", "::1", "::ffff:127.0.0.1"):
            with self._resolve_to(ip), self.assertRaisesRegex(ValueError, "internal address"):
                asyncio.run(it._download_image("http://img.example/x.png"))

    def test_only_http_urls(self):
        for url in ("file:///etc/passwd", "ftp://img.example/x", "gopher://x"):
            with self.assertRaises(ValueError):
                asyncio.run(it._download_image(url))

    @patch.dict(os.environ, {}, clear=True)
    def test_redirects_are_not_followed(self):
        # A public URL must not bounce the download to an internal one
        with self._resolve_to("93.184.216.34"), self._serve(
                lambda r: httpx.Response(302, headers={"Location": "http://169.254.169.254/"})):
            with self.assertRaises(httpx.HTTPStatusError):
                asyncio.run(it._download_image("http://img.example/x.png"))

    @patch.dict(os.environ, {}, clear=True)
    def test_a_public_image_is_downloaded(self):
        with self._resolve_to("93.184.216.34"), self._serve(lambda r: httpx.Response(200, content=PNG)):
            self.assertEqual(asyncio.run(it._download_image("https://img.example/x.png")), PNG)

    @patch.dict(os.environ, {}, clear=True)
    def test_an_oversized_image_is_refused(self):
        with self._resolve_to("93.184.216.34"), \
                patch.object(it, "_MAX_IMAGE_BYTES", 10), \
                self._serve(lambda r: httpx.Response(200, content=b"x" * 100)):
            with self.assertRaisesRegex(ValueError, "too large"):
                asyncio.run(it._download_image("https://img.example/x.png"))

    @patch.dict(os.environ, {"IMAGE_ALLOW_PRIVATE_NETWORK": "true"}, clear=True)
    def test_a_local_image_model_can_be_allowed(self):
        with self._resolve_to("127.0.0.1"), self._serve(lambda r: httpx.Response(200, content=PNG)):
            self.assertEqual(asyncio.run(it._download_image("http://localhost:9997/x.png")), PNG)

    @patch.dict(os.environ, {}, clear=True)
    def test_a_refused_url_fails_the_generation(self):
        # Not "success" with the internal URL handed to the agent
        ctx = MagicMock()
        ctx.save_artifact = AsyncMock(return_value=1)
        call = AsyncMock(return_value=_response(url="http://169.254.169.254/latest/meta-data/"))
        with patch("litellm.aimage_generation", call), self._resolve_to("169.254.169.254"):
            result = asyncio.run(it._generate_image_internal("a cat", ctx, "dall-e-3", {}))
        self.assertFalse(result["success"])
        self.assertNotIn("url", result)
        ctx.save_artifact.assert_not_called()

    def test_the_agent_gets_one_line_of_an_error(self):
        call = AsyncMock(side_effect=Exception("Boom: bad request\nTraceback (most recent call last):\n  File \"/srv/x.py\""))
        with patch("litellm.aimage_generation", call):
            result = asyncio.run(it._generate_image_internal("a cat", None, "dall-e-3", {}))
        self.assertEqual(result["error"], "Image generation failed: Boom: bad request")


class TestValidateSetup(unittest.TestCase):

    @patch.dict(os.environ, {"IMAGE_MODEL": "gemini/gemini-2.5-flash-image", "GOOGLE_API_KEY": "g"},
                clear=True)
    def test_the_google_key_is_enough_for_gemini(self):
        ok, error, details = it.validate_image_generation_setup()
        self.assertTrue(ok, error)
        self.assertEqual(details["provider"], "gemini")

    @patch.dict(os.environ, {"IMAGE_MODEL": "black_forest_labs/flux-pro-1.1"}, clear=True)
    def test_providers_litellm_cannot_check_are_checked_here(self):
        # validate_environment calls these configured whatever the environment holds
        ok, error, details = it.validate_image_generation_setup()
        self.assertFalse(ok)
        self.assertEqual(details["missing_keys"], ["BFL_API_KEY", "BLACK_FOREST_LABS_API_KEY"])
        with patch.dict(os.environ, {"BFL_API_KEY": "k"}):
            self.assertTrue(it.validate_image_generation_setup()[0])

    @patch.dict(os.environ, {}, clear=True)
    def test_a_missing_key_is_named(self):
        ok, error, details = it.validate_image_generation_setup()
        self.assertFalse(ok)
        self.assertIn("OPENAI_API_KEY", error)
        self.assertEqual(details["default_model"], "dall-e-3")


class TestSniffImage(unittest.TestCase):

    def test_formats(self):
        self.assertEqual(it._sniff_image(PNG), ("image/png", "png"))
        self.assertEqual(it._sniff_image(JPEG), ("image/jpeg", "jpg"))
        self.assertEqual(it._sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 "), ("image/webp", "webp"))
        self.assertEqual(it._sniff_image(b"GIF89a"), ("image/gif", "gif"))


if __name__ == "__main__":
    unittest.main()
