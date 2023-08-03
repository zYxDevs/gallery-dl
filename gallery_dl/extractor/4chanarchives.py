# -*- coding: utf-8 -*-

# Copyright 2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://4chanarchives.com/"""

from .common import Extractor, Message
from .. import text


class _4chanarchivesThreadExtractor(Extractor):
    """Extractor for threads on 4chanarchives.com"""
    category = "4chanarchives"
    subcategory = "thread"
    root = "https://4chanarchives.com"
    directory_fmt = ("{category}", "{board}", "{thread} - {title}")
    filename_fmt = "{no}-{filename}.{extension}"
    archive_fmt = "{board}_{thread}_{no}"
    pattern = r"(?:https?://)?4chanarchives\.com/board/([^/?#]+)/thread/(\d+)"
    test = (
        ("https://4chanarchives.com/board/c/thread/2707110", {
            "pattern": r"https://i\.imgur\.com/(0wLGseE|qbByWDc)\.jpg",
            "count": 2,
            "keyword": {
                "board": "c",
                "com": str,
                "name": "Anonymous",
                "no": int,
                "thread": "2707110",
                "time": r"re:2016-07-1\d \d\d:\d\d:\d\d",
                "title": "Ren Kagami from 'Oyako Neburi'",
            },
        }),
    )

    def __init__(self, match):
        Extractor.__init__(self, match)
        self.board, self.thread = match.groups()

    def items(self):
        url = f"{self.root}/board/{self.board}/thread/{self.thread}"
        page = self.request(url).text
        data = self.metadata(page)
        posts = self.posts(page)

        if not data["title"]:
            data["title"] = text.unescape(text.remove_html(
                posts[0]["com"]))[:50]

        for post in posts:
            post.update(data)
            yield Message.Directory, post
            if "url" in post:
                yield Message.Url, post["url"], post

    def metadata(self, page):
        return {
            "board"     : self.board,
            "thread"    : self.thread,
            "title"     : text.unescape(text.extr(
                page, 'property="og:title" content="', '"')),
        }

    def posts(self, page):
        """Build a list of all post objects"""
        return [self.parse(html) for html in text.extract_iter(
            page, 'id="pc', '</blockquote>')]

    def parse(self, html):
        """Build post object by extracting data from an HTML post"""
        post = self._extract_post(html)
        if ">File: <" in html:
            self._extract_file(html, post)
            post["extension"] = post["url"].rpartition(".")[2]
        return post

    @staticmethod
    def _extract_post(html):
        extr = text.extract_from(html)
        return {
            "no"  : text.parse_int(extr('', '"')),
            "name": extr('class="name">', '<'),
            "time": extr('class="dateTime postNum" >', '<').rstrip(),
            "com" : text.unescape(
                html[html.find('<blockquote'):].partition(">")[2]),
        }

    @staticmethod
    def _extract_file(html, post):
        extr = text.extract_from(html, html.index(">File: <"))
        post["url"] = extr('href="', '"')
        post["filename"] = text.unquote(extr(">", "<").rpartition(".")[0])
        post["fsize"] = extr("(", ", ")
        post["w"] = text.parse_int(extr("", "x"))
        post["h"] = text.parse_int(extr("", ")"))


class _4chanarchivesBoardExtractor(Extractor):
    """Extractor for boards on 4chanarchives.com"""
    category = "4chanarchives"
    subcategory = "board"
    root = "https://4chanarchives.com"
    pattern = r"(?:https?://)?4chanarchives\.com/board/([^/?#]+)(?:/(\d+))?/?$"
    test = (
        ("https://4chanarchives.com/board/c/", {
            "pattern": _4chanarchivesThreadExtractor.pattern,
            "range": "1-40",
            "count": 40,
        }),
        ("https://4chanarchives.com/board/c"),
        ("https://4chanarchives.com/board/c/10"),
    )

    def __init__(self, match):
        Extractor.__init__(self, match)
        self.board, self.page = match.groups()

    def items(self):
        data = {"_extractor": _4chanarchivesThreadExtractor}
        pnum = text.parse_int(self.page, 1)
        needle = '''<span class="postNum desktop">
                        <span><a href="'''

        while True:
            url = f"{self.root}/board/{self.board}/{pnum}"
            page = self.request(url).text

            thread = None
            for thread in text.extract_iter(page, needle, '"'):
                yield Message.Queue, thread, data

            if thread is None:
                return
            pnum += 1
