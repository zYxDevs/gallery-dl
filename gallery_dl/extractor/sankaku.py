# -*- coding: utf-8 -*-

# Copyright 2014-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://sankaku.app/"""

from .booru import BooruExtractor
from .common import Message
from .. import text, util, exception
from ..cache import cache
import collections
import re

BASE_PATTERN = r"(?:https?://)?" \
    r"(?:(?:chan|beta|black|white)\.sankakucomplex\.com|sankaku\.app)" \
    r"(?:/[a-z]{2})?"


class SankakuExtractor(BooruExtractor):
    """Base class for sankaku channel extractors"""
    basecategory = "booru"
    category = "sankaku"
    filename_fmt = "{category}_{id}_{md5}.{extension}"
    cookies_domain = None
    _warning = True

    TAG_TYPES = {
        0: "general",
        1: "artist",
        2: "studio",
        3: "copyright",
        4: "character",
        5: "genre",
        6: "",
        7: "",
        8: "medium",
        9: "meta",
    }

    def skip(self, num):
        return 0

    def _file_url(self, post):
        url = post["file_url"]
        if not url:
            if post["status"] != "active":
                self.log.warning(
                    "Unable to download post %s (%s)",
                    post["id"], post["status"])
            elif self._warning:
                self.log.warning(
                    "Login required to download 'contentious_content' posts")
                SankakuExtractor._warning = False
        elif url[8] == "v":
            url = "https://s.sankakucomplex.com" + url[url.index("/", 8):]
        return url

    def _prepare(self, post):
        post["created_at"] = post["created_at"]["s"]
        post["date"] = text.parse_timestamp(post["created_at"])
        post["tags"] = [tag["name"] for tag in post["tags"] if tag["name"]]
        post["tag_string"] = " ".join(post["tags"])
        post["_http_validate"] = self._check_expired

    def _check_expired(self, response):
        return not response.history or '.com/expired.png' not in response.url

    def _tags(self, post, page):
        tags = collections.defaultdict(list)
        types = self.TAG_TYPES
        for tag in post["tags"]:
            if name := tag["name"]:
                tags[types[tag["type"]]].append(name)
        for key, value in tags.items():
            post[f"tags_{key}"] = value
            post[f"tag_string_{key}"] = " ".join(value)


class SankakuTagExtractor(SankakuExtractor):
    """Extractor for images from sankaku.app by search-tags"""
    subcategory = "tag"
    directory_fmt = ("{category}", "{search_tags}")
    archive_fmt = "t_{search_tags}_{id}"
    pattern = BASE_PATTERN + r"/?\?([^#]*)"
    test = (
        ("https://sankaku.app/?tags=bonocho", {
            "count": 5,
            "pattern": r"https://s\.sankakucomplex\.com/data/[^/]{2}/[^/]{2}"
                       r"/[0-9a-f]{32}\.\w+\?e=\d+&(expires=\d+&)?m=[^&#]+",
        }),
        ("https://beta.sankakucomplex.com/?tags=bonocho"),
        ("https://chan.sankakucomplex.com/?tags=bonocho"),
        ("https://black.sankakucomplex.com/?tags=bonocho"),
        ("https://white.sankakucomplex.com/?tags=bonocho"),
        ("https://sankaku.app/ja?tags=order%3Apopularity"),
        ("https://sankaku.app/no/?tags=order%3Apopularity"),
        # error on five or more tags
        ("https://chan.sankakucomplex.com/?tags=bonocho+a+b+c+d", {
            "options": (("username", None),),
            "exception": exception.StopExtraction,
        }),
        # match arbitrary query parameters
        ("https://chan.sankakucomplex.com"
         "/?tags=marie_rose&page=98&next=3874906&commit=Search"),
        # 'date:' tags (#1790)
        ("https://chan.sankakucomplex.com/?tags=date:2023-03-20", {
            "range": "1",
            "count": 1,
        }),
    )

    def __init__(self, match):
        SankakuExtractor.__init__(self, match)
        query = text.parse_query(match.group(1))
        self.tags = text.unquote(query.get("tags", "").replace("+", " "))

        if "date:" in self.tags:
            # rewrite 'date:' tags (#1790)
            self.tags = re.sub(
                r"date:(\d\d)[.-](\d\d)[.-](\d\d\d\d)",
                r"date:\3.\2.\1", self.tags)
            self.tags = re.sub(
                r"date:(\d\d\d\d)[.-](\d\d)[.-](\d\d)",
                r"date:\1.\2.\3", self.tags)

    def metadata(self):
        return {"search_tags": self.tags}

    def posts(self):
        params = {"tags": self.tags}
        return SankakuAPI(self).posts_keyset(params)


class SankakuPoolExtractor(SankakuExtractor):
    """Extractor for image pools or books from sankaku.app"""
    subcategory = "pool"
    directory_fmt = ("{category}", "pool", "{pool[id]} {pool[name_en]}")
    archive_fmt = "p_{pool}_{id}"
    pattern = BASE_PATTERN + r"/(?:books|pool/show)/(\d+)"
    test = (
        ("https://sankaku.app/books/90", {
            "count": 5,
        }),
        ("https://beta.sankakucomplex.com/books/90"),
        ("https://chan.sankakucomplex.com/pool/show/90"),
    )

    def __init__(self, match):
        SankakuExtractor.__init__(self, match)
        self.pool_id = match.group(1)

    def metadata(self):
        pool = SankakuAPI(self).pools(self.pool_id)
        pool["tags"] = [tag["name"] for tag in pool["tags"]]
        pool["artist_tags"] = [tag["name"] for tag in pool["artist_tags"]]

        self._posts = pool.pop("posts")
        for num, post in enumerate(self._posts, 1):
            post["num"] = num

        return {"pool": pool}

    def posts(self):
        return self._posts


class SankakuPostExtractor(SankakuExtractor):
    """Extractor for single posts from sankaku.app"""
    subcategory = "post"
    archive_fmt = "{id}"
    pattern = BASE_PATTERN + r"/post/show/([0-9a-f]+)"
    test = (
        ("https://sankaku.app/post/show/360451", {
            "content": "5e255713cbf0a8e0801dc423563c34d896bb9229",
            "options": (("tags", True),),
            "keyword": {
                "tags_artist"   : ["bonocho"],
                "tags_studio"   : ["dc_comics"],
                "tags_medium"   : list,
                "tags_copyright": list,
                "tags_character": list,
                "tags_general"  : list,
            },
        }),
        # 'contentious_content'
        ("https://sankaku.app/post/show/21418978", {
            "pattern": r"https://s\.sankakucomplex\.com"
                       r"/data/13/3c/133cda3bfde249c504284493903fb985\.jpg",
        }),
        # empty tags (#1617)
        ("https://sankaku.app/post/show/20758561", {
            "options": (("tags", True),),
            "count": 1,
            "keyword": {
                "tags": list,
                "tags_general": ["key(mangaka)", "key(mangaka)"],
            },
        }),
        # md5 hexdigest instead of ID (#3952)
        (("https://chan.sankakucomplex.com/post/show"
          "/f8ba89043078f0e4be2d9c46550b840a"), {
            "pattern": r"https://s\.sankakucomplex\.com"
                       r"/data/f8/ba/f8ba89043078f0e4be2d9c46550b840a\.jpg",
            "count": 1,
            "keyword": {
                "id": 33195194,
                "md5": "f8ba89043078f0e4be2d9c46550b840a",
            },
        }),
        ("https://chan.sankakucomplex.com/post/show/360451"),
        ("https://chan.sankakucomplex.com/ja/post/show/360451"),
        ("https://beta.sankakucomplex.com/post/show/360451"),
        ("https://white.sankakucomplex.com/post/show/360451"),
        ("https://black.sankakucomplex.com/post/show/360451"),
    )

    def __init__(self, match):
        SankakuExtractor.__init__(self, match)
        self.post_id = match.group(1)

    def posts(self):
        return SankakuAPI(self).posts(self.post_id)


class SankakuBooksExtractor(SankakuExtractor):
    """Extractor for books by tag search on sankaku.app"""
    subcategory = "books"
    pattern = BASE_PATTERN + r"/books/?\?([^#]*)"
    test = (
        ("https://sankaku.app/books?tags=aiue_oka", {
            "range": "1-20",
            "count": 20,
        }),
        ("https://beta.sankakucomplex.com/books?tags=aiue_oka"),
    )

    def __init__(self, match):
        SankakuExtractor.__init__(self, match)
        query = text.parse_query(match.group(1))
        self.tags = text.unquote(query.get("tags", "").replace("+", " "))

    def items(self):
        params = {"tags": self.tags, "pool_type": "0"}
        for pool in SankakuAPI(self).pools_keyset(params):
            pool["_extractor"] = SankakuPoolExtractor
            url = f'https://sankaku.app/books/{pool["id"]}'
            yield Message.Queue, url, pool


class SankakuAPI():
    """Interface for the sankaku.app API"""

    def __init__(self, extractor):
        self.extractor = extractor
        self.headers = {
            "Accept": "application/vnd.sankaku.api+json;v=2",
            "Origin": extractor.root,
            "Referer": f"{extractor.root}/",
        }

        self.username, self.password = self.extractor._get_auth_info()
        if not self.username:
            self.authenticate = util.noop

    def pools(self, pool_id):
        params = {"lang": "en"}
        return self._call(f"/pools/{pool_id}", params)

    def pools_keyset(self, params):
        return self._pagination("/pools/keyset", params)

    def posts(self, post_id):
        params = {
            "lang" : "en",
            "page" : "1",
            "limit": "1",
            "tags" : ("md5:" if len(post_id) == 32 else "id_range:") + post_id,
        }
        return self._call("/posts", params)

    def posts_keyset(self, params):
        return self._pagination("/posts/keyset", params)

    def authenticate(self):
        self.headers["Authorization"] = \
            _authenticate_impl(self.extractor, self.username, self.password)

    def _call(self, endpoint, params=None):
        url = f"https://capi-v2.sankakucomplex.com{endpoint}"
        for _ in range(5):
            self.authenticate()
            response = self.extractor.request(
                url, params=params, headers=self.headers, fatal=None)

            if response.status_code == 429:
                until = response.headers.get("X-RateLimit-Reset")
                if not until and b"tags-limit" in response.content:
                    raise exception.StopExtraction("Search tag limit exceeded")
                seconds = None if until else 60
                self.extractor.wait(until=until, seconds=seconds)
                continue

            data = response.json()
            try:
                success = data.get("success", True)
            except AttributeError:
                success = True
            if not success:
                code = data.get("code")
                if code and code.endswith(
                        ("unauthorized", "invalid-token", "invalid_token")):
                    _authenticate_impl.invalidate(self.username)
                    continue
                raise exception.StopExtraction(code)
            return data

    def _pagination(self, endpoint, params):
        params["lang"] = "en"
        params["limit"] = str(self.extractor.per_page)

        refresh = self.extractor.config("refresh", False)
        if refresh:
            offset = expires = 0
            from time import time

        while True:
            data = self._call(endpoint, params)

            if refresh:
                posts = data["data"]
                if offset:
                    posts = util.advance(posts, offset)

                for post in posts:
                    if not expires:
                        if url := post["file_url"]:
                            expires = text.parse_int(
                                text.extr(url, "e=", "&")) - 60

                    if 0 < expires <= time():
                        self.extractor.log.debug("Refreshing download URLs")
                        expires = None
                        break

                    offset += 1
                    yield post

                if expires is None:
                    expires = 0
                    continue
                offset = expires = 0

            else:
                yield from data["data"]

            params["next"] = data["meta"]["next"]
            if not params["next"]:
                return


@cache(maxage=365*24*3600, keyarg=1)
def _authenticate_impl(extr, username, password):
    extr.log.info("Logging in as %s", username)

    url = "https://capi-v2.sankakucomplex.com/auth/token"
    headers = {"Accept": "application/vnd.sankaku.api+json;v=2"}
    data = {"login": username, "password": password}

    response = extr.request(
        url, method="POST", headers=headers, json=data, fatal=False)
    data = response.json()

    if response.status_code >= 400 or not data.get("success"):
        raise exception.AuthenticationError(data.get("error"))
    return "Bearer " + data["access_token"]
