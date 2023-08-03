# -*- coding: utf-8 -*-

# Copyright 2016-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://seiga.nicovideo.jp/"""

from .common import Extractor, Message
from .. import text, util, exception


class SeigaExtractor(Extractor):
    """Base class for seiga extractors"""
    category = "seiga"
    archive_fmt = "{image_id}"
    cookies_domain = ".nicovideo.jp"
    root = "https://seiga.nicovideo.jp"

    def __init__(self, match):
        Extractor.__init__(self, match)
        self.start_image = 0

    def items(self):
        if not self.cookies_check(("user_session",)):
            raise exception.StopExtraction("'user_session' cookie required")

        images = iter(self.get_images())
        data = next(images)

        yield Message.Directory, data
        for image in util.advance(images, self.start_image):
            data.update(image)
            data["extension"] = None
            yield Message.Url, self.get_image_url(data["image_id"]), data

    def get_images(self):
        """Return iterable containing metadata and images"""

    def get_image_url(self, image_id):
        """Get url for an image with id 'image_id'"""
        url = f"{self.root}/image/source/{image_id}"
        response = self.request(
            url, method="HEAD", allow_redirects=False, notfound="image")
        location = response.headers["location"]
        if "nicovideo.jp/login" in location:
            raise exception.StopExtraction(
                "HTTP redirect to login page (%s)", location.partition("?")[0])
        return location.replace("/o/", "/priv/", 1)


class SeigaUserExtractor(SeigaExtractor):
    """Extractor for images of a user from seiga.nicovideo.jp"""
    subcategory = "user"
    directory_fmt = ("{category}", "{user[id]}")
    filename_fmt = "{category}_{user[id]}_{image_id}.{extension}"
    pattern = (r"(?:https?://)?(?:www\.|(?:sp\.)?seiga\.)?nicovideo\.jp/"
               r"user/illust/(\d+)(?:\?(?:[^&]+&)*sort=([^&#]+))?")
    test = (
        ("https://seiga.nicovideo.jp/user/illust/39537793", {
            "pattern": r"https://lohas\.nicoseiga\.jp/priv/[0-9a-f]+/\d+/\d+",
            "count": ">= 4",
            "keyword": {
                "user": {
                    "id": 39537793,
                    "message": str,
                    "name": str,
                },
                "clips": int,
                "comments": int,
                "count": int,
                "extension": None,
                "image_id": int,
                "title": str,
                "views": int,
            },
        }),
        ("https://seiga.nicovideo.jp/user/illust/79433", {
            "exception": exception.NotFoundError,
        }),
        ("https://seiga.nicovideo.jp/user/illust/39537793"
         "?sort=image_view&target=illust_all"),
        ("https://sp.seiga.nicovideo.jp/user/illust/39537793"),
    )

    def __init__(self, match):
        SeigaExtractor.__init__(self, match)
        self.user_id, self.order = match.groups()
        self.start_page = 1

    def skip(self, num):
        pages, images = divmod(num, 40)
        self.start_page += pages
        self.start_image += images
        return num

    def get_metadata(self, page):
        """Collect metadata from 'page'"""
        data = text.extract_all(page, (
            ("name" , '<img alt="', '"'),
            ("msg"  , '<li class="user_message">', '</li>'),
            (None   , '<span class="target_name">すべて</span>', ''),
            ("count", '<span class="count ">', '</span>'),
        ))[0]

        if not data["name"] and "ユーザー情報が取得出来ませんでした" in page:
            raise exception.NotFoundError("user")

        return {
            "user": {
                "id": text.parse_int(self.user_id),
                "name": data["name"],
                "message": (data["msg"] or "").strip(),
            },
            "count": text.parse_int(data["count"]),
        }

    def get_images(self):
        url = f"{self.root}/user/illust/{self.user_id}"
        params = {"sort": self.order, "page": self.start_page,
                  "target": "illust_all"}

        while True:
            cnt = 0
            page = self.request(url, params=params).text

            if params["page"] == self.start_page:
                yield self.get_metadata(page)

            for info in text.extract_iter(
                    page, '<li class="list_item', '</a></li> '):
                data = text.extract_all(info, (
                    ("image_id", '/seiga/im', '"'),
                    ("title"   , '<li class="title">', '</li>'),
                    ("views"   , '</span>', '</li>'),
                    ("comments", '</span>', '</li>'),
                    ("clips"   , '</span>', '</li>'),
                ))[0]
                for key in ("image_id", "views", "comments", "clips"):
                    data[key] = text.parse_int(data[key])
                yield data
                cnt += 1

            if cnt < 40:
                return
            params["page"] += 1


class SeigaImageExtractor(SeigaExtractor):
    """Extractor for single images from seiga.nicovideo.jp"""
    subcategory = "image"
    filename_fmt = "{category}_{image_id}.{extension}"
    pattern = (r"(?:https?://)?(?:"
               r"(?:seiga\.|www\.)?nicovideo\.jp/(?:seiga/im|image/source/)"
               r"|sp\.seiga\.nicovideo\.jp/seiga/#!/im"
               r"|lohas\.nicoseiga\.jp/(?:thumb|(?:priv|o)/[^/]+/\d+)/)(\d+)")
    test = (
        ("https://seiga.nicovideo.jp/seiga/im5977527", {
            "keyword": "c8339781da260f7fc44894ad9ada016f53e3b12a",
            "content": "d9202292012178374d57fb0126f6124387265297",
        }),
        ("https://seiga.nicovideo.jp/seiga/im123", {
            "exception": exception.NotFoundError,
        }),
        ("https://seiga.nicovideo.jp/seiga/im10877923", {
            "pattern": r"https://lohas\.nicoseiga\.jp/priv/5936a2a6c860a600e46"
                       r"5e0411c0822e0b510e286/1688757110/10877923",
        }),
        ("https://seiga.nicovideo.jp/image/source/5977527"),
        ("https://sp.seiga.nicovideo.jp/seiga/#!/im5977527"),
        ("https://lohas.nicoseiga.jp/thumb/5977527i"),
        ("https://lohas.nicoseiga.jp/priv"
         "/759a4ef1c639106ba4d665ee6333832e647d0e4e/1549727594/5977527"),
        ("https://lohas.nicoseiga.jp/o"
         "/759a4ef1c639106ba4d665ee6333832e647d0e4e/1549727594/5977527"),
    )

    def __init__(self, match):
        SeigaExtractor.__init__(self, match)
        self.image_id = match.group(1)

    def skip(self, num):
        self.start_image += num
        return num

    def get_images(self):
        self.cookies.set(
            "skip_fetish_warning", "1", domain="seiga.nicovideo.jp")

        url = f"{self.root}/seiga/im{self.image_id}"
        page = self.request(url, notfound="image").text

        data = text.extract_all(page, (
            ("date"        , '<li class="date"><span class="created">', '<'),
            ("title"       , '<h1 class="title">', '</h1>'),
            ("description" , '<p class="discription">', '</p>'),
        ))[0]

        data["user"] = text.extract_all(page, (
            ("id"  , '<a href="/user/illust/' , '"'),
            ("name", '<span itemprop="title">', '<'),
        ))[0]

        data["description"] = text.remove_html(data["description"])
        data["image_id"] = text.parse_int(self.image_id)
        data["date"] = text.parse_datetime(
            data["date"] + ":00+0900", "%Y年%m月%d日 %H:%M:%S%z")

        return (data, data)
