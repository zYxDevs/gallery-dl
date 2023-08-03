# -*- coding: utf-8 -*-

# Copyright 2018-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://rule34.paheal.net/"""

from .common import Extractor, Message
from .. import text


class PahealExtractor(Extractor):
    """Base class for paheal extractors"""
    basecategory = "shimmie2"
    category = "paheal"
    filename_fmt = "{category}_{id}_{md5}.{extension}"
    archive_fmt = "{id}"
    root = "https://rule34.paheal.net"

    def items(self):
        self.cookies.set(
            "ui-tnc-agreed", "true", domain="rule34.paheal.net")
        data = self.get_metadata()

        for post in self.get_posts():
            url = post["file_url"]
            for key in ("id", "width", "height"):
                post[key] = text.parse_int(post[key])
            post["tags"] = text.unquote(post["tags"])
            post.update(data)
            yield Message.Directory, post
            yield Message.Url, url, text.nameext_from_url(url, post)

    def get_metadata(self):
        """Return general metadata"""
        return {}

    def get_posts(self):
        """Return an iterable containing data of all relevant posts"""

    def _extract_post(self, post_id):
        url = f"{self.root}/post/view/{post_id}"
        extr = text.extract_from(self.request(url).text)

        post = {
            "id"      : post_id,
            "tags"    : extr(": ", "<"),
            "md5"     : extr("/_thumbs/", "/"),
            "file_url": (extr("id='main_image' src='", "'") or
                         extr("<source src='", "'")),
            "uploader": text.unquote(extr(
                "class='username' href='/user/", "'")),
            "date"    : text.parse_datetime(
                extr("datetime='", "'"), "%Y-%m-%dT%H:%M:%S%z"),
            "source"  : text.unescape(text.extr(
                extr(">Source&nbsp;Link<", "</td>"), "href='", "'")),
        }

        dimensions, size, ext = extr("Info</th><td>", ">").split(" // ")
        post["width"], _, height = dimensions.partition("x")
        post["size"] = text.parse_bytes(size[:-1])
        post["height"], _, duration = height.partition(", ")
        post["duration"] = text.parse_float(duration[:-1])

        return post


class PahealTagExtractor(PahealExtractor):
    """Extractor for images from rule34.paheal.net by search-tags"""
    subcategory = "tag"
    directory_fmt = ("{category}", "{search_tags}")
    pattern = (r"(?:https?://)?(?:rule34|rule63|cosplay)\.paheal\.net"
               r"/post/list/([^/?#]+)")
    test = (
        ("https://rule34.paheal.net/post/list/Ayane_Suzuki/1", {
            "pattern": r"https://[^.]+\.paheal\.net/_images/\w+/\d+%20-%20",
            "count": ">= 15"
        }),
        ("https://rule34.paheal.net/post/list/Ayane_Suzuki/1", {
            "range": "1",
            "options": (("metadata", True),),
            "keyword": {
                "date": "dt:2018-01-07 07:04:05",
                "duration": 0.0,
                "extension": "jpg",
                "filename": "2446128 - Ayane_Suzuki Idolmaster "
                            "idolmaster_dearly_stars Zanzi",
                "height": 768,
                "id": 2446128,
                "md5": "b0ceda9d860df1d15b60293a7eb465c1",
                "search_tags": "Ayane_Suzuki",
                "size": 205312,
                "source": "https://www.pixiv.net/member_illust.php"
                          "?mode=medium&illust_id=19957280",
                "tags": "Ayane_Suzuki Idolmaster "
                        "idolmaster_dearly_stars Zanzi",
                "uploader": "XXXname",
                "width": 1024,
            },
        }),
    )
    per_page = 70

    def __init__(self, match):
        PahealExtractor.__init__(self, match)
        self.tags = text.unquote(match.group(1))

    def _init(self):
        if self.config("metadata"):
            self._extract_data = self._extract_data_ex

    def get_metadata(self):
        return {"search_tags": self.tags}

    def get_posts(self):
        pnum = 1
        while True:
            url = f"{self.root}/post/list/{self.tags}/{pnum}"
            page = self.request(url).text

            pos = page.find("id='image-list'")
            for post in text.extract_iter(
                    page, "<img id='thumb_", "Only</a>", pos):
                yield self._extract_data(post)

            if ">Next<" not in page:
                return
            pnum += 1

    @staticmethod
    def _extract_data(post):
        pid , pos = text.extract(post, "", "'")
        data, pos = text.extract(post, "title='", "'", pos)
        md5 , pos = text.extract(post, "/_thumbs/", "/", pos)
        url , pos = text.extract(post, "<a href='", "'", pos)

        tags, data, date = data.split("\n")
        dimensions, size, ext = data.split(" // ")
        width, _, height = dimensions.partition("x")
        height, _, duration = height.partition(", ")

        return {
            "id": pid, "md5": md5, "file_url": url,
            "width": width, "height": height,
            "duration": text.parse_float(duration[:-1]),
            "tags": text.unescape(tags),
            "size": text.parse_bytes(size[:-1]),
            "date": text.parse_datetime(date, "%B %d, %Y; %H:%M"),
        }

    def _extract_data_ex(self, post):
        pid = post[:post.index("'")]
        return self._extract_post(pid)


class PahealPostExtractor(PahealExtractor):
    """Extractor for single images from rule34.paheal.net"""
    subcategory = "post"
    pattern = (r"(?:https?://)?(?:rule34|rule63|cosplay)\.paheal\.net"
               r"/post/view/(\d+)")
    test = (
        ("https://rule34.paheal.net/post/view/481609", {
            "pattern": r"https://tulip\.paheal\.net/_images"
                       r"/bbdc1c33410c2cdce7556c7990be26b7/481609%20-%20"
                       r"Azumanga_Daioh%20inanimate%20Osaka%20Vuvuzela\.jpg",
            "content": "7b924bcf150b352ac75c9d281d061e174c851a11",
            "keyword": {
                "date": "dt:2010-06-17 15:40:23",
                "extension": "jpg",
                "file_url": "re:https://tulip.paheal.net/_images/bbdc1c33410c",
                "filename": "481609 - Azumanga_Daioh inanimate Osaka Vuvuzela",
                "height": 660,
                "id": 481609,
                "md5": "bbdc1c33410c2cdce7556c7990be26b7",
                "size": 157389,
                "source": "",
                "tags": "Azumanga_Daioh inanimate Osaka Vuvuzela",
                "uploader": "CaptainButtface",
                "width": 614,
            },
        }),
        ("https://rule34.paheal.net/post/view/488534", {
            "keyword": {
                "date": "dt:2010-06-25 13:51:17",
                "height": 800,
                "md5": "b39edfe455a0381110c710d6ed2ef57d",
                "size": 758989,
                "source": "http://www.furaffinity.net/view/4057821/",
                "tags": "inanimate thelost-dragon Vuvuzela",
                "uploader": "leacheate_soup",
                "width": 1200,
            },
        }),
        # video
        ("https://rule34.paheal.net/post/view/3864982", {
            "pattern": r"https://[\w]+\.paheal\.net/_images/7629fc0ff77e32637d"
                       r"de5bf4f992b2cb/3864982%20-%20animated%20Metal_Gear%20"
                       r"Metal_Gear_Solid_V%20Quiet%20Vg_erotica%20webm\.webm",
            "keyword": {
                "date": "dt:2020-09-06 01:59:03",
                "duration": 30.0,
                "extension": "webm",
                "height": 2500,
                "id": 3864982,
                "md5": "7629fc0ff77e32637dde5bf4f992b2cb",
                "size": 18454938,
                "source": "https://twitter.com/VG_Worklog"
                          "/status/1302407696294055936",
                "tags": "animated Metal_Gear Metal_Gear_Solid_V "
                        "Quiet Vg_erotica webm",
                "uploader": "justausername",
                "width": 1768,
            },
        }),
    )

    def __init__(self, match):
        PahealExtractor.__init__(self, match)
        self.post_id = match.group(1)

    def get_posts(self):
        return (self._extract_post(self.post_id),)
