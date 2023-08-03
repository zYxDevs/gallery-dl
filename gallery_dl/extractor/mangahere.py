# -*- coding: utf-8 -*-

# Copyright 2015-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://www.mangahere.cc/"""

from .common import ChapterExtractor, MangaExtractor
from .. import text
import re


class MangahereBase():
    """Base class for mangahere extractors"""
    category = "mangahere"
    root = "https://www.mangahere.cc"
    root_mobile = "https://m.mangahere.cc"
    url_fmt = root_mobile + "/manga/{}/{}.html"


class MangahereChapterExtractor(MangahereBase, ChapterExtractor):
    """Extractor for manga-chapters from mangahere.cc"""
    pattern = (r"(?:https?://)?(?:www\.|m\.)?mangahere\.c[co]/manga/"
               r"([^/]+(?:/v0*(\d+))?/c([^/?#]+))")
    test = (
        ("https://www.mangahere.cc/manga/dongguo_xiaojie/c004.2/", {
            "keyword": "7c98d7b50a47e6757b089aa875a53aa970cac66f",
            "content": "708d475f06893b88549cbd30df1e3f9428f2c884",
        }),
        # URLs without HTTP scheme (#1070)
        ("https://www.mangahere.cc/manga/beastars/c196/1.html", {
            "pattern": "https://zjcdn.mangahere.org/.*",
        }),
        ("http://www.mangahere.co/manga/dongguo_xiaojie/c003.2/"),
        ("http://m.mangahere.co/manga/dongguo_xiaojie/c003.2/"),
    )

    def __init__(self, match):
        self.part, self.volume, self.chapter = match.groups()
        url = self.url_fmt.format(self.part, 1)
        ChapterExtractor.__init__(self, match, url)

    def _init(self):
        self.session.headers["Referer"] = f"{self.root_mobile}/"

    def metadata(self, page):
        pos = page.index("</select>")
        count     , pos = text.extract(page, ">", "<", pos - 20)
        manga_id  , pos = text.extract(page, "series_id = ", ";", pos)
        chapter_id, pos = text.extract(page, "chapter_id = ", ";", pos)
        manga     , pos = text.extract(page, '"name":"', '"', pos)
        chapter, dot, minor = self.chapter.partition(".")

        return {
            "manga": text.unescape(manga),
            "manga_id": text.parse_int(manga_id),
            "title": self._get_title(),
            "volume": text.parse_int(self.volume),
            "chapter": text.parse_int(chapter),
            "chapter_minor": dot + minor,
            "chapter_id": text.parse_int(chapter_id),
            "count": text.parse_int(count),
            "lang": "en",
            "language": "English",
        }

    def images(self, page):
        pnum = 1

        while True:
            url, pos = text.extract(page, '<img src="', '"')
            yield text.ensure_http_scheme(url), None
            url, pos = text.extract(page, ' src="', '"', pos)
            yield text.ensure_http_scheme(url), None
            pnum += 2
            page = self.request(self.url_fmt.format(self.part, pnum)).text

    def _get_title(self):
        url = f"{self.root}/manga/{self.part}/"
        page = self.request(url).text

        try:
            pos = page.index(self.part) + len(self.part)
            pos = page.index(self.part, pos) + len(self.part)
            return text.extract(page, ' title="', '"', pos)[0]
        except ValueError:
            return ""


class MangahereMangaExtractor(MangahereBase, MangaExtractor):
    """Extractor for manga from mangahere.cc"""
    chapterclass = MangahereChapterExtractor
    pattern = (r"(?:https?://)?(?:www\.|m\.)?mangahere\.c[co]"
               r"(/manga/[^/?#]+/?)(?:#.*)?$")
    test = (
        ("https://www.mangahere.cc/manga/aria/", {
            "url": "9c2e54ec42e9a87ad53096c328b33c90750af3e4",
            "keyword": "71503c682c5d0c277a50409a8c5fd78e871e3d69",
            "count": 71,
        }),
        ("https://www.mangahere.cc/manga/hiyokoi/#50", {
            "url": "654850570aa03825cd57e2ae2904af489602c523",
            "keyword": "c8084d89a9ea6cf40353093669f9601a39bf5ca2",
        }),
        # adult filter (#556)
        ("http://www.mangahere.cc/manga/gunnm_mars_chronicle/", {
            "pattern": MangahereChapterExtractor.pattern,
            "count": ">= 50",
        }),
        ("https://www.mangahere.co/manga/aria/"),
        ("https://m.mangahere.co/manga/aria/"),
    )

    def _init(self):
        self.cookies.set("isAdult", "1", domain="www.mangahere.cc")

    def chapters(self, page):
        results = []
        manga, pos = text.extract(page, '<meta name="og:title" content="', '"')
        manga = text.unescape(manga)

        page = text.extract(
            page, 'id="chapterlist"', 'class="detail-main-list-more"', pos)[0]
        pos = 0
        while True:
            url, pos = text.extract(page, ' href="', '"', pos)
            if not url:
                return results
            info, pos = text.extract(page, 'class="title3">', '<', pos)
            date, pos = text.extract(page, 'class="title2">', '<', pos)

            if match := re.match(
                r"(?:Vol\.0*(\d+) )?Ch\.0*(\d+)(\S*)(?: - (.*))?", info
            ):
                volume, chapter, minor, title = match.groups()
            else:
                chapter, _, minor = url[:-1].rpartition("/c")[2].partition(".")
                minor = f".{minor}"
                volume = 0
                title = ""

            results.append((text.urljoin(self.root, url), {
                "manga": manga,
                "title": text.unescape(title) if title else "",
                "volume": text.parse_int(volume),
                "chapter": text.parse_int(chapter),
                "chapter_minor": minor,
                "date": date,
                "lang": "en",
                "language": "English",
            }))
