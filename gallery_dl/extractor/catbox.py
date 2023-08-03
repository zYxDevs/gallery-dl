# -*- coding: utf-8 -*-

# Copyright 2022-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://catbox.moe/"""

from .common import GalleryExtractor, Extractor, Message
from .. import text


class CatboxAlbumExtractor(GalleryExtractor):
    """Extractor for catbox albums"""
    category = "catbox"
    subcategory = "album"
    root = "https://catbox.moe"
    filename_fmt = "{filename}.{extension}"
    directory_fmt = ("{category}", "{album_name} ({album_id})")
    archive_fmt = "{album_id}_{filename}"
    pattern = r"(?:https?://)?(?:www\.)?catbox\.moe(/c/[^/?#]+)"
    test = (
        ("https://catbox.moe/c/1igcbe", {
            "url": "35866a88c29462814f103bc22ec031eaeb380f8a",
            "content": "70ddb9de3872e2d17cc27e48e6bf395e5c8c0b32",
            "pattern": r"https://files\.catbox\.moe/\w+\.\w{3}$",
            "count": 3,
            "keyword": {
                "album_id": "1igcbe",
                "album_name": "test",
                "date": "dt:2022-08-18 00:00:00",
                "description": "album test &>",
            },
        }),
        ("https://www.catbox.moe/c/cd90s1"),
        ("https://catbox.moe/c/w7tm47#"),
    )

    def metadata(self, page):
        extr = text.extract_from(page)
        return {
            "album_id"   : self.gallery_url.rpartition("/")[2],
            "album_name" : text.unescape(extr("<h1>", "<")),
            "date"       : text.parse_datetime(extr(
                "<p>Created ", "<"), "%B %d %Y"),
            "description": text.unescape(extr("<p>", "<")),
        }

    def images(self, page):
        return [
            (f"https://files.catbox.moe/{path}", None)
            for path in text.extract_iter(page, ">https://files.catbox.moe/", "<")
        ]


class CatboxFileExtractor(Extractor):
    """Extractor for catbox files"""
    category = "catbox"
    subcategory = "file"
    archive_fmt = "{filename}"
    pattern = r"(?:https?://)?(?:files|litter|de)\.catbox\.moe/([^/?#]+)"
    test = (
        ("https://files.catbox.moe/8ih3y7.png", {
            "pattern": r"^https://files\.catbox\.moe/8ih3y7\.png$",
            "content": "0c8768055e4e20e7c7259608b67799171b691140",
            "count": 1,
        }),
        ("https://litter.catbox.moe/t8v3n9.png"),
        ("https://de.catbox.moe/bjdmz1.jpg"),
    )

    def items(self):
        url = text.ensure_http_scheme(self.url)
        file = text.nameext_from_url(url, {"url": url})
        yield Message.Directory, file
        yield Message.Url, url, file
