# -*- coding: utf-8 -*-

# Copyright 2014-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://e621.net/ and other e621 instances"""

from .common import Message
from . import danbooru
from .. import text, util


class E621Extractor(danbooru.DanbooruExtractor):
    """Base class for e621 extractors"""
    basecategory = "E621"
    page_limit = 750
    page_start = None
    per_page = 320
    request_interval_min = 1.0

    def items(self):
        self.session.headers["User-Agent"] = f"{util.USERAGENT} (by mikf)"

        includes = self.config("metadata") or ()
        if includes:
            if isinstance(includes, str):
                includes = includes.split(",")
            elif not isinstance(includes, (list, tuple)):
                includes = ("notes", "pools")

        notes = ("notes" in includes)
        pools = ("pools" in includes)

        data = self.metadata()
        for post in self.posts():
            file = post["file"]

            if not file["url"]:
                md5 = file["md5"]
                file[
                    "url"
                ] = f'https://static1.{self.root[8:]}/data/{md5[:2]}/{md5[2:4]}/{md5}.{file["ext"]}'

            if notes and post.get("has_notes"):
                url = f'{self.root}/notes.json?search[post_id]={post["id"]}'
                post["notes"] = self.request(url).json()

            if pools and post["pools"]:
                url = f'{self.root}/pools.json?search[id]={",".join(map(str, post["pools"]))}'
                post["pools"] = _pools = self.request(url).json()
                for pool in _pools:
                    pool["name"] = pool["name"].replace("_", " ")

            post["filename"] = file["md5"]
            post["extension"] = file["ext"]
            post["date"] = text.parse_datetime(
                post["created_at"], "%Y-%m-%dT%H:%M:%S.%f%z")

            post.update(data)
            yield Message.Directory, post
            yield Message.Url, file["url"], post


BASE_PATTERN = E621Extractor.update({
    "e621": {
        "root": "https://e621.net",
        "pattern": r"e621\.net",
    },
    "e926": {
        "root": "https://e926.net",
        "pattern": r"e926\.net",
    },
    "e6ai": {
        "root": "https://e6ai.net",
        "pattern": r"e6ai\.net",
    },
})


class E621TagExtractor(E621Extractor, danbooru.DanbooruTagExtractor):
    """Extractor for e621 posts from tag searches"""
    pattern = BASE_PATTERN + r"/posts?(?:\?.*?tags=|/index/\d+/)([^&#]+)"
    test = (
        ("https://e621.net/posts?tags=anry", {
            "url": "8021e5ea28d47c474c1ffc9bd44863c4d45700ba",
            "content": "501d1e5d922da20ee8ff9806f5ed3ce3a684fd58",
        }),
        ("https://e621.net/post/index/1/anry"),
        ("https://e621.net/post?tags=anry"),

        ("https://e926.net/posts?tags=anry", {
            "url": "12198b275c62ffe2de67cca676c8e64de80c425d",
            "content": "501d1e5d922da20ee8ff9806f5ed3ce3a684fd58",
        }),
        ("https://e926.net/post/index/1/anry"),
        ("https://e926.net/post?tags=anry"),

        ("https://e6ai.net/posts?tags=anry"),
        ("https://e6ai.net/post/index/1/anry"),
        ("https://e6ai.net/post?tags=anry"),
    )


class E621PoolExtractor(E621Extractor, danbooru.DanbooruPoolExtractor):
    """Extractor for e621 pools"""
    pattern = BASE_PATTERN + r"/pool(?:s|/show)/(\d+)"
    test = (
        ("https://e621.net/pools/73", {
            "url": "1bd09a72715286a79eea3b7f09f51b3493eb579a",
            "content": "91abe5d5334425d9787811d7f06d34c77974cd22",
        }),
        ("https://e621.net/pool/show/73"),

        ("https://e926.net/pools/73", {
            "url": "6936f1b6a18c5c25bee7cad700088dbc2503481b",
            "content": "91abe5d5334425d9787811d7f06d34c77974cd22",
        }),
        ("https://e926.net/pool/show/73"),

        ("https://e6ai.net/pools/3", {
            "url": "a6d1ad67a3fa9b9f73731d34d5f6f26f7e85855f",
        }),
        ("https://e6ai.net/pool/show/3"),
    )

    def posts(self):
        self.log.info("Fetching posts of pool %s", self.pool_id)

        id_to_post = {
            post["id"]: post
            for post in self._pagination(
                "/posts.json", {"tags": f"pool:{self.pool_id}"}
            )
        }

        posts = []
        append = posts.append
        for num, pid in enumerate(self.post_ids, 1):
            if pid in id_to_post:
                post = id_to_post[pid]
                post["num"] = num
                append(post)
            else:
                self.log.warning("Post %s is unavailable", pid)
        return posts


class E621PostExtractor(E621Extractor, danbooru.DanbooruPostExtractor):
    """Extractor for single e621 posts"""
    pattern = BASE_PATTERN + r"/post(?:s|/show)/(\d+)"
    test = (
        ("https://e621.net/posts/535", {
            "url": "f7f78b44c9b88f8f09caac080adc8d6d9fdaa529",
            "content": "66f46e96a893fba8e694c4e049b23c2acc9af462",
            "keyword": {"date": "dt:2007-02-17 19:02:32"},
        }),
        ("https://e621.net/posts/3181052", {
            "options": (("metadata", "notes,pools"),),
            "pattern": r"https://static\d\.e621\.net/data/c6/8c"
                       r"/c68cca0643890b615f75fb2719589bff\.png",
            "keyword": {
                "notes": [
                    {
                        "body": "Little Legends 2",
                        "created_at": "2022-05-16T13:58:38.877-04:00",
                        "creator_id": 517450,
                        "creator_name": "EeveeCuddler69",
                        "height": 475,
                        "id": 321296,
                        "is_active": True,
                        "post_id": 3181052,
                        "updated_at": "2022-05-16T13:59:02.050-04:00",
                        "version": 3,
                        "width": 809,
                        "x": 83,
                        "y": 117,
                    },
                ],
                "pools": [
                    {
                        "category": "series",
                        "created_at": "2022-02-17T00:29:22.669-05:00",
                        "creator_id": 1077440,
                        "creator_name": "Yeetus90",
                        "description": "* \"Little Legends\":/pools/27971\r\n"
                                       "* Little Legends 2\r\n"
                                       "* \"Little Legends 3\":/pools/27481",
                        "id": 27492,
                        "is_active": False,
                        "name": "Little Legends 2",
                        "post_count": 39,
                        "post_ids": list,
                        "updated_at": "2022-03-27T06:30:03.382-04:00"
                    },
                ],
            },
        }),
        ("https://e621.net/post/show/535"),

        ("https://e926.net/posts/535", {
            "url": "17aec8ebd8fab098d321adcb62a2db59dab1f4bf",
            "content": "66f46e96a893fba8e694c4e049b23c2acc9af462",
        }),
        ("https://e926.net/post/show/535"),

        ("https://e6ai.net/posts/23", {
            "url": "3c85a806b3d9eec861948af421fe0e8ad6b8f881",
            "content": "a05a484e4eb64637d56d751c02e659b4bc8ea5d5",
        }),
        ("https://e6ai.net/post/show/23"),
    )

    def posts(self):
        url = f"{self.root}/posts/{self.post_id}.json"
        return (self.request(url).json()["post"],)


class E621PopularExtractor(E621Extractor, danbooru.DanbooruPopularExtractor):
    """Extractor for popular images from e621"""
    pattern = BASE_PATTERN + r"/explore/posts/popular(?:\?([^#]*))?"
    test = (
        ("https://e621.net/explore/posts/popular"),
        (("https://e621.net/explore/posts/popular"
          "?date=2019-06-01&scale=month"), {
            "pattern": r"https://static\d.e621.net/data/../../[0-9a-f]+",
            "count": ">= 70",
        }),

        ("https://e926.net/explore/posts/popular"),
        (("https://e926.net/explore/posts/popular"
          "?date=2019-06-01&scale=month"), {
            "pattern": r"https://static\d.e926.net/data/../../[0-9a-f]+",
            "count": ">= 70",
        }),

        ("https://e6ai.net/explore/posts/popular"),
    )

    def posts(self):
        return self._pagination("/popular.json", self.params)


class E621FavoriteExtractor(E621Extractor):
    """Extractor for e621 favorites"""
    subcategory = "favorite"
    directory_fmt = ("{category}", "Favorites", "{user_id}")
    archive_fmt = "f_{user_id}_{id}"
    pattern = BASE_PATTERN + r"/favorites(?:\?([^#]*))?"
    test = (
        ("https://e621.net/favorites"),
        ("https://e621.net/favorites?page=2&user_id=53275", {
            "pattern": r"https://static\d.e621.net/data/../../[0-9a-f]+",
            "count": "> 260",
        }),

        ("https://e926.net/favorites"),
        ("https://e926.net/favorites?page=2&user_id=53275", {
            "pattern": r"https://static\d.e926.net/data/../../[0-9a-f]+",
            "count": "> 260",
        }),

        ("https://e6ai.net/favorites"),
    )

    def __init__(self, match):
        E621Extractor.__init__(self, match)
        self.query = text.parse_query(match.group(match.lastindex))

    def metadata(self):
        return {"user_id": self.query.get("user_id", "")}

    def posts(self):
        return self._pagination("/favorites.json", self.query)
