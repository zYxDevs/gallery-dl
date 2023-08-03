# -*- coding: utf-8 -*-

# Copyright 2021-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://www.erome.com/"""

from .common import Extractor, Message
from .. import text, util, exception
from ..cache import cache
import itertools

BASE_PATTERN = r"(?:https?://)?(?:www\.)?erome\.com"


class EromeExtractor(Extractor):
    category = "erome"
    directory_fmt = ("{category}", "{user}")
    filename_fmt = "{album_id} {title} {num:>02}.{extension}"
    archive_fmt = "{album_id}_{num}"
    root = "https://www.erome.com"

    def __init__(self, match):
        Extractor.__init__(self, match)
        self.item = match.group(1)
        self.__cookies = True

    def items(self):
        for album_id in self.albums():
            url = f"{self.root}/a/{album_id}"

            try:
                page = self.request(url).text
            except exception.HttpError as exc:
                self.log.warning(
                    "Unable to fetch album '%s' (%s)", album_id, exc)
                continue

            title, pos = text.extract(
                page, 'property="og:title" content="', '"')
            pos = page.index('<div class="user-profile', pos)
            user, pos = text.extract(
                page, 'href="https://www.erome.com/', '"', pos)
            data = {
                "album_id"     : album_id,
                "title"        : text.unescape(title),
                "user"         : text.unquote(user),
                "_http_headers": {"Referer": url},
            }

            yield Message.Directory, data
            groups = page.split('<div class="media-group"')
            for data["num"], group in enumerate(util.advance(groups, 1), 1):
                if url := (
                    text.extr(group, '<source src="', '"')
                    or text.extr(group, 'data-src="', '"')
                ):
                    yield Message.Url, url, text.nameext_from_url(url, data)

    def albums(self):
        return ()

    def request(self, url, **kwargs):
        if self.__cookies:
            self.__cookies = False
            self.cookies.update(_cookie_cache())

        for _ in range(5):
            response = Extractor.request(self, url, **kwargs)
            if response.cookies:
                _cookie_cache.update("", response.cookies)
            if response.content.find(
                    b"<title>Please wait a few moments</title>", 0, 600) < 0:
                return response
            self.sleep(5.0, "check")

    def _pagination(self, url, params):
        for params["page"] in itertools.count(1):
            page = self.request(url, params=params).text

            album_ids = EromeAlbumExtractor.pattern.findall(page)[::2]
            yield from album_ids

            if len(album_ids) < 36:
                return


class EromeAlbumExtractor(EromeExtractor):
    """Extractor for albums on erome.com"""
    subcategory = "album"
    pattern = BASE_PATTERN + r"/a/(\w+)"
    test = (
        ("https://www.erome.com/a/NQgdlWvk", {
            "pattern": r"https://v\d+\.erome\.com/\d+"
                       r"/NQgdlWvk/j7jlzmYB_480p\.mp4",
            "count": 1,
            "keyword": {
                "album_id": "NQgdlWvk",
                "num": 1,
                "title": "porn",
                "user": "yYgWBZw8o8qsMzM",
            },
        }),
        ("https://www.erome.com/a/TdbZ4ogi", {
            "pattern": r"https://s\d+\.erome\.com/\d+/TdbZ4ogi/\w+",
            "count": 6,
            "keyword": {
                "album_id": "TdbZ4ogi",
                "num": int,
                "title": "82e78cfbb461ad87198f927fcb1fda9a1efac9ff.",
                "user": "yYgWBZw8o8qsMzM",
            },
        }),
    )

    def albums(self):
        return (self.item,)


class EromeUserExtractor(EromeExtractor):
    subcategory = "user"
    pattern = BASE_PATTERN + r"/(?!a/|search\?)([^/?#]+)"
    test = ("https://www.erome.com/yYgWBZw8o8qsMzM", {
        "range": "1-25",
        "count": 25,
    })

    def albums(self):
        url = f"{self.root}/{self.item}"
        return self._pagination(url, {})


class EromeSearchExtractor(EromeExtractor):
    subcategory = "search"
    pattern = BASE_PATTERN + r"/search\?q=([^&#]+)"
    test = ("https://www.erome.com/search?q=cute", {
        "range": "1-25",
        "count": 25,
    })

    def albums(self):
        url = f"{self.root}/search"
        params = {"q": text.unquote(self.item)}
        return self._pagination(url, params)


@cache()
def _cookie_cache():
    return ()
