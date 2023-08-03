# -*- coding: utf-8 -*-

# Copyright 2022-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://bunkrr.su/"""

from .lolisafe import LolisafeAlbumExtractor
from .. import text


class BunkrAlbumExtractor(LolisafeAlbumExtractor):
    """Extractor for bunkrr.su albums"""
    category = "bunkr"
    root = "https://bunkrr.su"
    pattern = r"(?:https?://)?(?:app\.)?bunkr+\.(?:la|[sr]u|is|to)/a/([^/?#]+)"
    test = (
        ("https://bunkrr.su/a/Lktg9Keq", {
            "pattern": r"https://cdn\.bunkr\.ru/test-テスト-\"&>-QjgneIQv\.png",
            "content": "0c8768055e4e20e7c7259608b67799171b691140",
            "keyword": {
                "album_id": "Lktg9Keq",
                "album_name": 'test テスト "&>',
                "count": 1,
                "filename": 'test-テスト-"&>-QjgneIQv',
                "id": "QjgneIQv",
                "name": 'test-テスト-"&>',
                "num": int,
            },
        }),
        # mp4 (#2239)
        ("https://app.bunkr.ru/a/ptRHaCn2", {
            "pattern": r"https://media-files\.bunkr\.ru/_-RnHoW69L\.mp4",
            "content": "80e61d1dbc5896ae7ef9a28734c747b28b320471",
        }),
        # cdn4
        ("https://bunkr.is/a/iXTTc1o2", {
            "pattern": r"https://(cdn|media-files)4\.bunkr\.ru/",
            "content": "da29aae371b7adc8c5ef8e6991b66b69823791e8",
            "keyword": {
                "album_id": "iXTTc1o2",
                "album_name": "test2",
                "album_size": "691.1 KB",
                "count": 2,
                "description": "072022",
                "filename": "re:video-wFO9FtxG|image-sZrQUeOx",
                "id": "re:wFO9FtxG|sZrQUeOx",
                "name": "re:video|image",
                "num": int,
            },
        }),
        # cdn12 .ru TLD (#4147)
        ("https://bunkrr.su/a/j1G29CnD", {
            "pattern": r"https://(cdn12.bunkr.ru|media-files12.bunkr.la)/\w+",
            "count": 8,
        }),
        ("https://bunkrr.su/a/Lktg9Keq"),
        ("https://bunkr.la/a/Lktg9Keq"),
        ("https://bunkr.su/a/Lktg9Keq"),
        ("https://bunkr.ru/a/Lktg9Keq"),
        ("https://bunkr.is/a/Lktg9Keq"),
        ("https://bunkr.to/a/Lktg9Keq"),
    )

    def fetch_album(self, album_id):
        # album metadata
        page = self.request(f"{self.root}/a/{self.album_id}").text
        info = text.split_html(text.extr(
            page, "<h1", "</div>").partition(">")[2])
        count, _, size = info[1].split(None, 2)

        # files
        cdn = None
        files = []
        append = files.append
        headers = {"Referer": f"{self.root}/"}

        pos = page.index('class="grid-images')
        for url in text.extract_iter(page, '<a href="', '"', pos):
            if url.startswith("/"):
                if not cdn:
                    # fetch cdn root from download page
                    durl = f"{self.root}/d/{url[3:]}"
                    cdn = text.extr(self.request(
                        durl).text, 'link.href = "', '"')
                    cdn = cdn[:cdn.index("/", 8)]
                url = cdn + url[2:]

            url = text.unescape(url)
            if url.endswith((".mp4", ".m4v", ".mov", ".webm", ".mkv", ".ts",
                             ".zip", ".rar", ".7z")):
                if url.startswith("https://cdn12."):
                    url = ("https://media-files12.bunkr.la" +
                           url[url.find("/", 14):])
                else:
                    url = url.replace("://cdn", "://media-files", 1)
            append({"file": url, "_http_headers": headers})

        return files, {
            "album_id"   : self.album_id,
            "album_name" : text.unescape(info[0]),
            "album_size" : size[1:-1],
            "description": text.unescape(info[2]) if len(info) > 2 else "",
            "count"      : len(files),
        }
