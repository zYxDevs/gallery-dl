# -*- coding: utf-8 -*-

# Copyright 2021-2022 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for lolisafe/chibisafe instances"""

from .common import BaseExtractor, Message
from .. import text


class LolisafeExtractor(BaseExtractor):
    """Base class for lolisafe extractors"""
    basecategory = "lolisafe"
    directory_fmt = ("{category}", "{album_name} ({album_id})")
    archive_fmt = "{album_id}_{id}"


BASE_PATTERN = LolisafeExtractor.update({
    "xbunkr": {
        "root": "https://xbunkr.com",
        "pattern": r"xbunkr\.com",
    },
})


class LolisafeAlbumExtractor(LolisafeExtractor):
    subcategory = "album"
    pattern = BASE_PATTERN + "/a/([^/?#]+)"
    test = (
        ("https://xbunkr.com/a/TA0bu3F4", {
            "pattern": r"https://media\.xbunkr\.com/[^.]+\.\w+",
            "count": 861,
            "keyword": {
                "album_id": "TA0bu3F4",
                "album_name": "Hannahowo Onlyfans Photos",
            }
        }),
        ("https://xbunkr.com/a/GNQc2I5d"),
    )

    def __init__(self, match):
        LolisafeExtractor.__init__(self, match)
        self.album_id = match.group(match.lastindex)

    def _init(self):
        domain = self.config("domain")
        if domain == "auto":
            self.root = text.root_from_url(self.url)
        elif domain:
            self.root = text.ensure_http_scheme(domain)

    def items(self):
        files, data = self.fetch_album(self.album_id)

        yield Message.Directory, data
        for data["num"], file in enumerate(files, 1):
            url = file["file"]
            file.update(data)
            text.nameext_from_url(url, file)
            file["name"], sep, file["id"] = file["filename"].rpartition("-")
            yield Message.Url, url, file

    def fetch_album(self, album_id):
        url = f"{self.root}/api/album/get/{album_id}"
        data = self.request(url).json()

        return data["files"], {
            "album_id"  : self.album_id,
            "album_name": text.unescape(data["title"]),
            "count"     : data["count"],
        }
