# -*- coding: utf-8 -*-

# Copyright 2019-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://www.pornhub.com/"""

from .common import Extractor, Message
from .. import text, exception

BASE_PATTERN = r"(?:https?://)?(?:[\w-]+\.)?pornhub\.com"


class PornhubExtractor(Extractor):
    """Base class for pornhub extractors"""
    category = "pornhub"
    root = "https://www.pornhub.com"


class PornhubGalleryExtractor(PornhubExtractor):
    """Extractor for image galleries on pornhub.com"""
    subcategory = "gallery"
    directory_fmt = ("{category}", "{user}", "{gallery[id]} {gallery[title]}")
    filename_fmt = "{num:>03}_{id}.{extension}"
    archive_fmt = "{id}"
    pattern = BASE_PATTERN + r"/album/(\d+)"
    test = (
        ("https://www.pornhub.com/album/19289801", {
            "pattern": r"https://\w+.phncdn.com/pics/albums/\d+/\d+/\d+/\d+/",
            "count": ">= 300",
            "keyword": {
                "id"     : int,
                "num"    : int,
                "score"  : int,
                "views"  : int,
                "caption": str,
                "user"   : "Danika Mori",
                "gallery": {
                    "id"   : 19289801,
                    "score": int,
                    "views": int,
                    "tags" : list,
                    "title": "Danika Mori Best Moments",
                },
            },
        }),
        ("https://www.pornhub.com/album/69040172", {
            "exception": exception.AuthorizationError,
        }),
    )

    def __init__(self, match):
        PornhubExtractor.__init__(self, match)
        self.gallery_id = match.group(1)
        self._first = None

    def items(self):
        self.cookies.set(
            "accessAgeDisclaimerPH", "1", domain=".pornhub.com")

        data = self.metadata()
        yield Message.Directory, data
        for num, image in enumerate(self.images(), 1):
            url = image["url"]
            image.update(data)
            image["num"] = num
            yield Message.Url, url, text.nameext_from_url(url, image)

    def metadata(self):
        url = f"{self.root}/album/{self.gallery_id}"
        extr = text.extract_from(self.request(url).text)

        title = extr("<title>", "</title>")
        score = extr('<div id="albumGreenBar" style="width:', '"')
        views = extr('<div id="viewsPhotAlbumCounter">', '<')
        tags = extr('<div id="photoTagsBox"', '<script')
        self._first = extr('<a href="/photo/', '"')
        title, _, user = title.rpartition(" - ")

        return {
            "user" : text.unescape(user[:-14]),
            "gallery": {
                "id"   : text.parse_int(self.gallery_id),
                "title": text.unescape(title),
                "score": text.parse_int(score.partition("%")[0]),
                "views": text.parse_int(views.partition(" ")[0]),
                "tags" : text.split_html(tags)[2:],
            },
        }

    def images(self):
        url = f"{self.root}/album/show_album_json?album={self.gallery_id}"
        response = self.request(url)

        if response.content == b"Permission denied":
            raise exception.AuthorizationError()
        images = response.json()
        key = end = self._first

        while True:
            img = images[key]
            yield {
                "url"    : img["img_large"],
                "caption": img["caption"],
                "id"     : text.parse_int(img["id"]),
                "views"  : text.parse_int(img["times_viewed"]),
                "score"  : text.parse_int(img["vote_percent"]),
            }
            key = str(img["next"])
            if key == end:
                return


class PornhubUserExtractor(PornhubExtractor):
    """Extractor for all galleries of a pornhub user"""
    subcategory = "user"
    pattern = (BASE_PATTERN + r"/(users|model|pornstar)/([^/?#]+)"
               "(?:/photos(?:/(public|private|favorites))?)?/?$")
    test = (
        ("https://www.pornhub.com/pornstar/danika-mori/photos", {
            "pattern": PornhubGalleryExtractor.pattern,
            "count": ">= 6",
        }),
        ("https://www.pornhub.com/users/flyings0l0/"),
        ("https://www.pornhub.com/users/flyings0l0/photos/public"),
        ("https://www.pornhub.com/users/flyings0l0/photos/private"),
        ("https://www.pornhub.com/users/flyings0l0/photos/favorites"),
        ("https://www.pornhub.com/model/bossgirl/photos"),
    )

    def __init__(self, match):
        PornhubExtractor.__init__(self, match)
        self.type, self.user, self.cat = match.groups()

    def items(self):
        url = f'{self.root}/{self.type}/{self.user}/photos/{self.cat or "public"}/ajax'
        params = {"page": 1}
        headers = {
            "Referer": url[:-5],
            "X-Requested-With": "XMLHttpRequest",
        }

        data = {"_extractor": PornhubGalleryExtractor}
        while True:
            response = self.request(
                url, method="POST", headers=headers, params=params,
                allow_redirects=False)

            if 300 <= response.status_code < 400:
                url = f'{self.root}{response.headers["location"]}/photos/{self.cat or "public"}/ajax'
                continue

            gid = None
            for gid in text.extract_iter(response.text, 'id="albumphoto', '"'):
                yield (Message.Queue, f"{self.root}/album/{gid}", data)
            if gid is None:
                return

            params["page"] += 1
