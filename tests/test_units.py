#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel2drama 纯函数单元测试。

运行（项目根目录）：
    python3 -m unittest discover -s tests -v
    # 或
    python3 tests/test_units.py
"""

import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import novel2drama as n2d
import storyboard as sb
import render as rd


class TestExtractJson(unittest.TestCase):
    """extract_json：从模型输出稳健提取 JSON。"""

    def test_plain(self):
        self.assertEqual(n2d.extract_json('{"a": 1}'), {"a": 1})

    def test_code_block(self):
        self.assertEqual(n2d.extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_prefix_suffix(self):
        text = '好的，以下是剧本：{"a": {"b": "x{y\\"z"}} 完毕'
        self.assertEqual(n2d.extract_json(text), {"a": {"b": 'x{y"z'}})

    def test_no_json(self):
        with self.assertRaises(ValueError):
            n2d.extract_json("没有任何花括号")

    def test_incomplete(self):
        with self.assertRaises(ValueError):
            n2d.extract_json('{"a": [1, 2')


class TestChunkText(unittest.TestCase):
    """chunk_text：长文本分块与超长段落硬切。"""

    def test_short_text_single_chunk(self):
        self.assertEqual(n2d.chunk_text("短文本"), ["短文本"])

    def test_paragraph_split(self):
        text = ("一" * 80) + "\n\n" + ("二" * 80)
        chunks = n2d.chunk_text(text, max_chars=100)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertIn("一", chunks[0])
        self.assertIn("二", chunks[-1])

    def test_long_paragraph_hard_split(self):
        # 单段 15000 字、无空行：必须硬切到每块 <= 6000
        text = "这是测试句子。" * 3000
        chunks = n2d.chunk_text(text, max_chars=6000)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 6000)
        self.assertEqual(sum(c.count("这是测试句子。") for c in chunks), 3000)

    def test_long_single_sentence(self):
        # 单句都超长：按字符数硬切，内容不丢
        text = "啊" * 13000
        chunks = n2d.chunk_text(text, max_chars=6000)
        self.assertEqual("".join(chunks), text)


class TestValidateEpisode(unittest.TestCase):
    def test_missing_character_warns(self):
        episode = {
            "characters": [{"name": "甲"}],
            "scenes": [{"scene_id": 1, "beats": [
                {"type": "dialogue", "character": "乙", "text": "台词"}]}],
        }
        n2d.validate_episode(episode)  # 不抛异常，只打印警告

    def test_lacks_scenes_raises(self):
        with self.assertRaises(ValueError):
            n2d.validate_episode({"title": "x"})


class TestValidateShots(unittest.TestCase):
    CHARACTERS = {"林晚": "中国女性，三十岁，短发", "陈默": "中国男性，四十岁，眼镜"}

    def test_duration_out_of_range_fixed(self):
        shots = [{"shot_id": "S1-01", "duration_sec": 99, "video_prompt": "x"}]
        out, problems = sb.validate_shots(shots, 1, self.CHARACTERS, "", "")
        self.assertEqual(out[0]["duration_sec"], 5)
        self.assertTrue(any("时长异常" in p for p in problems))

    def test_missing_prompt_reported(self):
        shots = [{"shot_id": "S1-01", "duration_sec": 5}]
        out, problems = sb.validate_shots(shots, 1, self.CHARACTERS, "", "")
        self.assertTrue(any("缺少 video_prompt" in p for p in problems))

    def test_style_auto_inject(self):
        shots = [{"shot_id": "S1-01", "duration_sec": 5, "video_prompt": "雨夜书店"}]
        out, _ = sb.validate_shots(shots, 1, self.CHARACTERS, "", "电影感写实风格，35mm胶片")
        self.assertTrue(out[0]["video_prompt"].startswith("电影感写实风格"))

    def test_environment_auto_inject(self):
        shots = [{"shot_id": "S1-01", "duration_sec": 5, "video_prompt": "雨夜书店"}]
        env = "旧书店内景，暖黄台灯，木质书架，落灰"
        out, _ = sb.validate_shots(shots, 1, self.CHARACTERS, env, "")
        self.assertIn("场景环境：", out[0]["video_prompt"])
        self.assertIn(env[:12], out[0]["video_prompt"])

    def test_appearance_auto_inject(self):
        prompt = "近景，林晚在书店说话"
        shots = [{"shot_id": "S1-01", "duration_sec": 5, "video_prompt": prompt,
                  "dialogue": [{"character": "林晚", "text": "台词"}]}]
        out, _ = sb.validate_shots(shots, 1, self.CHARACTERS, "", "")
        self.assertIn(self.CHARACTERS["林晚"], out[0]["video_prompt"])

    def test_duplicate_ids(self):
        shots = [{"shot_id": "S1-01", "video_prompt": "a"}, {"shot_id": "S1-01", "video_prompt": "b"}]
        _, problems = sb.validate_shots(shots, 1, self.CHARACTERS, "", "")
        self.assertTrue(any("重复" in p for p in problems))


class TestDrawtextEscape(unittest.TestCase):
    def test_special_chars(self):
        text = "a:b'c,d%e[f\\g"
    def test_special_chars(self):
        text = "a:b'c,d%e[f\\g"
        out = rd.drawtext_escape(text)
        self.assertEqual(out, "a\\:b\\'c\\,d\\%e\\[f\\\\g")

    def test_no_bare_specials(self):
        text = "台词：有'引号'，逗号%百分[括号]"
        out = rd.drawtext_escape(text)
        for ch in (":", "'", ",", "%", "[", "]"):
            self.assertNotIn(ch, out.replace("\\" + ch, ""))


    def test_plain_unchanged(self):
        self.assertEqual(rd.drawtext_escape("普通台词。"), "普通台词。")
class TestBuildUserPrompt(unittest.TestCase):
    def test_first_chunk_no_continuation(self):
        prompt = n2d.build_user_prompt("文本", 1)
        self.assertNotIn("续写说明", prompt)

    def test_continuation_with_synopsis(self):
        chars = [{"name": "林晚", "desc": "侦探"}]
        prompt = n2d.build_user_prompt("后续文本", 2, chars, synopsis="场景1：雨夜书店")
        self.assertIn("续写说明", prompt)
        self.assertIn("场景1：雨夜书店", prompt)
        self.assertIn("林晚", prompt)


@unittest.skipUnless(shutil.which("ffmpeg"), "需要 ffmpeg，未安装时跳过")
class TestPlaceholderClip(unittest.TestCase):
    """demo 占位视频：不调用 API。"""
    def test_generates_file(self):
        import tempfile
        out = os.path.join(tempfile.mkdtemp(), "t.mp4")
        shot = {"shot_id": "T-01", "duration_sec": 2}
        try:
            rd.generate_placeholder_clip(shot, out)
            self.assertTrue(os.path.isfile(out))
            self.assertGreater(os.path.getsize(out), 1000)
        finally:
            if os.path.isfile(out):
                os.remove(out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
