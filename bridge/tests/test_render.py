"""렌더링 테스트. Pillow와 폰트가 있어야 실행된다."""

from __future__ import annotations

import io
import unittest

try:
    from PIL import Image

    from moni_bridge.render import FONT_PATH, Renderer
    AVAILABLE = FONT_PATH.exists()
except ImportError:
    AVAILABLE = False


def ink(bitmap: bytes, x0: int, y0: int, x1: int, y1: int) -> int:
    count = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            count += (bitmap[y * 30 + (x >> 3)] >> (7 - (x & 7))) & 1
    return count


@unittest.skipUnless(AVAILABLE, "Pillow 또는 폰트 없음")
class RendererTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = Renderer()

    def test_bubble_size_and_polarity(self):
        pages = self.renderer.bubble("안녕!")
        self.assertEqual([len(p) for p in pages], [3120])
        # 1=글자: 글자는 왼쪽에 있고 오른쪽 안쪽 여백은 비어 있어야 한다.
        self.assertGreater(ink(pages[0], 10, 30, 120, 75), 100)
        self.assertEqual(ink(pages[0], 150, 30, 230, 75), 0)

    def test_long_text_uses_two_pages(self):
        self.assertEqual(len(self.renderer.bubble("가나다라 " * 30)), 2)

    def test_forty_chars_fit_one_page(self):
        self.assertEqual(len(self.renderer.bubble("우산 챙겨! " * 6 + "오후")), 1)

    def test_missing_glyphs_are_replaced(self):
        self.assertEqual(self.renderer._clean("14°…갂🙂"), "14도...??")

    def test_common_korean_is_kept(self):
        text = "오늘 날씨 어때? 우산 챙겨! 타이머 끝났어."
        self.assertEqual(self.renderer._clean(text), text)

    def test_panels_are_small_baseline_jpeg(self):
        panels = (
            {"type": "weather", "data": {"temp": -3, "icon": "snow", "pop": 80}},
            {"type": "timer", "data": {"remaining": 5999}},
            {"type": "time", "data": {"text": "09:05"}},
        )
        for panel in panels:
            blob = self.renderer.panel(panel)
            self.assertLessEqual(len(blob), 12 * 1024)
            image = Image.open(io.BytesIO(blob))
            self.assertEqual(image.size, (108, 120))
            self.assertNotIn("progressive", image.info)


if __name__ == "__main__":
    unittest.main()
