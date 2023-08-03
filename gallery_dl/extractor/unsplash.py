# -*- coding: utf-8 -*-

# Copyright 2021-2022 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://unsplash.com/"""

from .common import Extractor, Message
from .. import text, util

BASE_PATTERN = r"(?:https?://)?unsplash\.com"


class UnsplashExtractor(Extractor):
    """Base class for unsplash extractors"""
    category = "unsplash"
    directory_fmt = ("{category}", "{user[username]}")
    filename_fmt = "{id}.{extension}"
    archive_fmt = "{id}"
    root = "https://unsplash.com"
    page_start = 1
    per_page = 20

    def __init__(self, match):
        Extractor.__init__(self, match)
        self.item = match.group(1)

    def items(self):
        fmt = self.config("format") or "raw"
        metadata = self.metadata()

        for photo in self.photos():
            util.delete_items(
                photo, ("current_user_collections", "related_collections"))
            url = photo["urls"][fmt]
            text.nameext_from_url(url, photo)

            if metadata:
                photo.update(metadata)
            photo["extension"] = "jpg"
            photo["date"] = text.parse_datetime(photo["created_at"])
            if "tags" in photo:
                photo["tags"] = [t["title"] for t in photo["tags"]]

            yield Message.Directory, photo
            yield Message.Url, url, photo

    @staticmethod
    def metadata():
        return None

    def skip(self, num):
        pages = num // self.per_page
        self.page_start += pages
        return pages * self.per_page

    def _pagination(self, url, params, results=False):
        params["per_page"] = self.per_page
        params["page"] = self.page_start

        while True:
            photos = self.request(url, params=params).json()
            if results:
                photos = photos["results"]
            yield from photos

            if len(photos) < self.per_page:
                return
            params["page"] += 1


class UnsplashImageExtractor(UnsplashExtractor):
    """Extractor for a single unsplash photo"""
    subcategory = "image"
    pattern = BASE_PATTERN + r"/photos/([^/?#]+)"
    test = ("https://unsplash.com/photos/lsoogGC_5dg", {
        "pattern": r"https://images\.unsplash\.com/photo-1586348943529-"
                   r"beaae6c28db9\?ixid=\w+&ixlib=rb-4.0.3",
        "keyword": {
            "alt_description": "re:silhouette of trees near body of water ",
            "blur_hash": "LZP4uQS4jboe%#o0WCa}2doJNaaz",
            "?  categories": list,
            "color": "#f3c08c",
            "created_at": "2020-04-08T12:29:42Z",
            "date": "dt:2020-04-08 12:29:42",
            "description": "The Island",
            "downloads": int,
            "exif": {
                "aperture": "11",
                "exposure_time": "30",
                "focal_length": "70.0",
                "iso": 200,
                "make": "Canon",
                "model": "Canon EOS 5D Mark IV"
            },
            "extension": "jpg",
            "filename": "photo-1586348943529-beaae6c28db9",
            "height": 6272,
            "id": "lsoogGC_5dg",
            "liked_by_user": False,
            "likes": int,
            "location": {
                "city": "Beaver Dam",
                "country": "United States",
                "name": "Beaver Dam, WI 53916, USA",
                "position": {
                    "latitude": 43.457769,
                    "longitude": -88.837329,
                },
            },
            "promoted_at": "2020-04-08T15:12:03Z",
            "sponsorship": None,
            "tags": list,
            "updated_at": str,
            "user": {
                "accepted_tos": True,
                "bio": str,
                "first_name": "Dave",
                "id": "uMJXuywXLiU",
                "instagram_username": "just_midwest_rock",
                "last_name": "Hoefler",
                "location": None,
                "name": "Dave Hoefler",
                "portfolio_url": None,
                "total_collections": int,
                "total_likes": int,
                "total_photos": int,
                "twitter_username": None,
                "updated_at": str,
                "username": "davehoefler",
            },
            "views": int,
            "width": 4480,
        },
    })

    def photos(self):
        url = f"{self.root}/napi/photos/{self.item}"
        return (self.request(url).json(),)


class UnsplashUserExtractor(UnsplashExtractor):
    """Extractor for all photos of an unsplash user"""
    subcategory = "user"
    pattern = BASE_PATTERN + r"/@(\w+)/?$"
    test = ("https://unsplash.com/@davehoefler", {
        "pattern": r"https://images\.unsplash\.com/(photo-\d+-\w+"
                   r"|reserve/[^/?#]+)\?ixid=\w+&ixlib=rb-4\.0\.3$",
        "range": "1-30",
        "count": 30,
    })

    def photos(self):
        url = f"{self.root}/napi/users/{self.item}/photos"
        params = {"order_by": "latest"}
        return self._pagination(url, params)


class UnsplashFavoriteExtractor(UnsplashExtractor):
    """Extractor for all likes of an unsplash user"""
    subcategory = "favorite"
    pattern = BASE_PATTERN + r"/@(\w+)/likes"
    test = ("https://unsplash.com/@davehoefler/likes", {
        "pattern": r"https://images\.unsplash\.com/(photo-\d+-\w+"
                   r"|reserve/[^/?#]+)\?ixid=\w+&ixlib=rb-4\.0\.3$",
        "range": "1-30",
        "count": 30,
    })

    def photos(self):
        url = f"{self.root}/napi/users/{self.item}/likes"
        params = {"order_by": "latest"}
        return self._pagination(url, params)


class UnsplashCollectionExtractor(UnsplashExtractor):
    """Extractor for an unsplash collection"""
    subcategory = "collection"
    pattern = BASE_PATTERN + r"/collections/([^/?#]+)(?:/([^/?#]+))?"
    test = (
        ("https://unsplash.com/collections/3178572/winter", {
            "pattern": r"https://images\.unsplash\.com/(photo-\d+-\w+"
                       r"|reserve/[^/?#]+)\?ixid=\w+&ixlib=rb-4\.0\.3$",
            "keyword": {"collection_id": "3178572",
                        "collection_title": "winter"},
            "range": "1-30",
            "count": 30,
        }),
        ("https://unsplash.com/collections/3178572/"),
        ("https://unsplash.com/collections/_8qJQ2bCMWE/2021.05"),
    )

    def __init__(self, match):
        UnsplashExtractor.__init__(self, match)
        self.title = match.group(2) or ""

    def metadata(self):
        return {"collection_id": self.item, "collection_title": self.title}

    def photos(self):
        url = f"{self.root}/napi/collections/{self.item}/photos"
        params = {"order_by": "latest"}
        return self._pagination(url, params)


class UnsplashSearchExtractor(UnsplashExtractor):
    """Extractor for unsplash search results"""
    subcategory = "search"
    pattern = BASE_PATTERN + r"/s/photos/([^/?#]+)(?:\?([^#]+))?"
    test = ("https://unsplash.com/s/photos/hair-style", {
        "pattern": r"https://(images|plus)\.unsplash\.com"
                   r"/((flagged/|premium_)?photo-\d+-\w+"
                   r"|reserve/[^/?#]+)\?ixid=\w+&ixlib=rb-4\.0\.3$",
        "range": "1-30",
        "count": 30,
    })

    def __init__(self, match):
        UnsplashExtractor.__init__(self, match)
        self.query = match.group(2)

    def photos(self):
        url = f"{self.root}/napi/search/photos"
        params = {"query": text.unquote(self.item.replace('-', ' '))}
        if self.query:
            params |= text.parse_query(self.query)
        return self._pagination(url, params, True)
