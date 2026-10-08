"""Regression for literal quotation marks observed in real grow-light OCR."""
import unittest

from vision import parse_tsv


class OCRTests(unittest.TestCase):
    def test_literal_inch_quote_does_not_swallow_following_rows(self):
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        rows = [
            '5\t1\t1\t1\t1\t1\t10\t20\t5\t7\t80\t"\n',
            '2\t1\t2\t0\t0\t0\t10\t40\t50\t20\t-1\t\n',
            '5\t1\t2\t1\t1\t1\t10\t40\t20\t7\t95\t240W\n',
        ]
        words = parse_tsv(header + "".join(rows))
        self.assertEqual([word["text"] for word in words], ['"', '240W'])
        self.assertEqual(words[1]["bbox"], [10, 40, 20, 7])
        self.assertEqual(words[1]["confidence"], 95)


if __name__ == "__main__":
    unittest.main()
