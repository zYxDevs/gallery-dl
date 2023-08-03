# -*- coding: utf-8 -*-

# Copyright 2019-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for Mastodon instances"""

from .common import BaseExtractor, Message
from .. import text, exception
from ..cache import cache


class MastodonExtractor(BaseExtractor):
    """Base class for mastodon extractors"""
    basecategory = "mastodon"
    directory_fmt = ("mastodon", "{instance}", "{account[username]}")
    filename_fmt = "{category}_{id}_{media[id]}.{extension}"
    archive_fmt = "{media[id]}"
    cookies_domain = None

    def __init__(self, match):
        BaseExtractor.__init__(self, match)
        self.item = match.group(match.lastindex)

    def _init(self):
        self.instance = self.root.partition("://")[2]
        self.reblogs = self.config("reblogs", False)
        self.replies = self.config("replies", True)

    def items(self):
        for status in self.statuses():

            if self._check_moved:
                self._check_moved(status["account"])
            if not self.reblogs and status["reblog"]:
                self.log.debug("Skipping %s (reblog)", status["id"])
                continue
            if not self.replies and status["in_reply_to_id"]:
                self.log.debug("Skipping %s (reply)", status["id"])
                continue

            attachments = status["media_attachments"]
            del status["media_attachments"]

            status["instance"] = self.instance
            acct = status["account"]["acct"]
            status["instance_remote"] = \
                acct.rpartition("@")[2] if "@" in acct else None

            status["count"] = len(attachments)
            status["tags"] = [tag["name"] for tag in status["tags"]]
            status["date"] = text.parse_datetime(
                status["created_at"][:19], "%Y-%m-%dT%H:%M:%S")

            yield Message.Directory, status
            for status["num"], media in enumerate(attachments, 1):
                status["media"] = media
                url = media["url"]
                yield Message.Url, url, text.nameext_from_url(url, status)

    def statuses(self):
        """Return an iterable containing all relevant Status objects"""
        return ()

    def _check_moved(self, account):
        self._check_moved = None
        if "moved" in account:
            self.log.warning("Account '%s' moved to '%s'",
                             account["acct"], account["moved"]["acct"])


INSTANCES = {
    "mastodon.social": {
        "root"         : "https://mastodon.social",
        "pattern"      : r"mastodon\.social",
        "access-token" : "Y06R36SMvuXXN5_wiPKFAEFiQaMSQg0o_hGgc86Jj48",
        "client-id"    : "dBSHdpsnOUZgxOnjKSQrWEPakO3ctM7HmsyoOd4FcRo",
        "client-secret": "DdrODTHs_XoeOsNVXnILTMabtdpWrWOAtrmw91wU1zI",
    },
    "pawoo": {
        "root"         : "https://pawoo.net",
        "pattern"      : r"pawoo\.net",
        "access-token" : "c12c9d275050bce0dc92169a28db09d7"
                         "0d62d0a75a8525953098c167eacd3668",
        "client-id"    : "978a25f843ec01e53d09be2c290cd75c"
                         "782bc3b7fdbd7ea4164b9f3c3780c8ff",
        "client-secret": "9208e3d4a7997032cf4f1b0e12e5df38"
                         "8428ef1fadb446dcfeb4f5ed6872d97b",
    },
    "baraag": {
        "root"         : "https://baraag.net",
        "pattern"      : r"baraag\.net",
        "access-token" : "53P1Mdigf4EJMH-RmeFOOSM9gdSDztmrAYFgabOKKE0",
        "client-id"    : "czxx2qilLElYHQ_sm-lO8yXuGwOHxLX9RYYaD0-nq1o",
        "client-secret": "haMaFdMBgK_-BIxufakmI2gFgkYjqmgXGEO2tB-R2xY",
    }
}

BASE_PATTERN = f"{MastodonExtractor.update(INSTANCES)}(?:/web)?"


class MastodonUserExtractor(MastodonExtractor):
    """Extractor for all images of an account/user"""
    subcategory = "user"
    pattern = BASE_PATTERN + r"/(?:@|users/)([^/?#]+)(?:/media)?/?$"
    test = (
        ("https://mastodon.social/@jk", {
            "pattern": r"https://files.mastodon.social/media_attachments"
                       r"/files/(\d+/){3,}original/\w+",
            "range": "1-60",
            "count": 60,
        }),
        ("https://pawoo.net/@yoru_nine/", {
            "range": "1-60",
            "count": 60,
        }),
        ("https://baraag.net/@pumpkinnsfw"),
        ("https://mastodon.social/@yoru_nine@pawoo.net", {
            "pattern": r"https://mastodon\.social/media_proxy/\d+/original",
            "range": "1-10",
            "count": 10,
        }),
        ("https://mastodon.social/@id:10843"),
        ("https://mastodon.social/users/id:10843"),
        ("https://mastodon.social/users/jk"),
        ("https://mastodon.social/users/yoru_nine@pawoo.net"),
        ("https://mastodon.social/web/@jk"),
    )

    def statuses(self):
        api = MastodonAPI(self)

        return api.account_statuses(
            api.account_id_by_username(self.item),
            only_media=not self.config("text-posts", False),
            exclude_replies=not self.replies,
        )


class MastodonBookmarkExtractor(MastodonExtractor):
    """Extractor for mastodon bookmarks"""
    subcategory = "bookmark"
    pattern = BASE_PATTERN + r"/bookmarks"
    test = (
        ("https://mastodon.social/bookmarks"),
        ("https://pawoo.net/bookmarks"),
        ("https://baraag.net/bookmarks"),
    )

    def statuses(self):
        return MastodonAPI(self).account_bookmarks()


class MastodonFollowingExtractor(MastodonExtractor):
    """Extractor for followed mastodon users"""
    subcategory = "following"
    pattern = BASE_PATTERN + r"/users/([^/?#]+)/following"
    test = (
        ("https://mastodon.social/users/0x4f/following", {
            "extractor": False,
            "count": ">= 20",
        }),
        ("https://mastodon.social/users/id:10843/following"),
        ("https://pawoo.net/users/yoru_nine/following"),
        ("https://baraag.net/users/pumpkinnsfw/following"),
    )

    def items(self):
        api = MastodonAPI(self)
        account_id = api.account_id_by_username(self.item)

        for account in api.account_following(account_id):
            account["_extractor"] = MastodonUserExtractor
            yield Message.Queue, account["url"], account


class MastodonStatusExtractor(MastodonExtractor):
    """Extractor for images from a status"""
    subcategory = "status"
    pattern = BASE_PATTERN + r"/@[^/?#]+/(\d+)"
    test = (
        ("https://mastodon.social/@jk/103794036899778366", {
            "count": 4,
            "keyword": {
                "count": 4,
                "num": int,
            },
        }),
        ("https://pawoo.net/@yoru_nine/105038878897832922", {
            "content": "b52e807f8ab548d6f896b09218ece01eba83987a",
        }),
        ("https://baraag.net/@pumpkinnsfw/104364170556898443", {
            "content": "67748c1b828c58ad60d0fe5729b59fb29c872244",
        }),
    )

    def statuses(self):
        return (MastodonAPI(self).status(self.item),)


class MastodonAPI():
    """Minimal interface for the Mastodon API

    https://docs.joinmastodon.org/
    https://github.com/tootsuite/mastodon
    """

    def __init__(self, extractor):
        self.root = extractor.root
        self.extractor = extractor

        access_token = extractor.config("access-token")
        if access_token is None or access_token == "cache":
            access_token = _access_token_cache(extractor.instance)
        if not access_token:
            try:
                access_token = INSTANCES[extractor.category]["access-token"]
            except (KeyError, TypeError):
                pass
        if access_token:
            self.headers = {"Authorization": f"Bearer {access_token}"}
        else:
            self.headers = None

    def account_id_by_username(self, username):
        if username.startswith("id:"):
            return username[3:]

        try:
            return self.account_lookup(username)["id"]
        except Exception:
            # fall back to account search
            pass

        if "@" in username:
            handle = f"@{username}"
        else:
            handle = f"@{username}@{self.extractor.instance}"

        for account in self.account_search(handle, 1):
            if account["acct"] == username:
                self.extractor._check_moved(account)
                return account["id"]
        raise exception.NotFoundError("account")

    def account_bookmarks(self):
        endpoint = "/v1/bookmarks"
        return self._pagination(endpoint, None)

    def account_following(self, account_id):
        endpoint = f"/v1/accounts/{account_id}/following"
        return self._pagination(endpoint, None)

    def account_lookup(self, username):
        endpoint = "/v1/accounts/lookup"
        params = {"acct": username}
        return self._call(endpoint, params).json()

    def account_search(self, query, limit=40):
        """Search for accounts"""
        endpoint = "/v1/accounts/search"
        params = {"q": query, "limit": limit}
        return self._call(endpoint, params).json()

    def account_statuses(self, account_id, only_media=True,
                         exclude_replies=False):
        """Fetch an account's statuses"""
        endpoint = f"/v1/accounts/{account_id}/statuses"
        params = {"only_media"     : "1" if only_media else "0",
                  "exclude_replies": "1" if exclude_replies else "0"}
        return self._pagination(endpoint, params)

    def status(self, status_id):
        """Fetch a status"""
        endpoint = f"/v1/statuses/{status_id}"
        return self._call(endpoint).json()

    def _call(self, endpoint, params=None):
        url = endpoint if endpoint.startswith("http") else f"{self.root}/api{endpoint}"
        while True:
            response = self.extractor.request(
                url, params=params, headers=self.headers, fatal=None)
            code = response.status_code

            if code < 400:
                return response
            if code == 401:
                raise exception.StopExtraction(
                    "Invalid or missing access token.\n"
                    "Run 'gallery-dl oauth:mastodon:%s' to obtain one.",
                    self.extractor.instance)
            if code == 404:
                raise exception.NotFoundError()
            if code == 429:
                self.extractor.wait(until=text.parse_datetime(
                    response.headers["x-ratelimit-reset"],
                    "%Y-%m-%dT%H:%M:%S.%fZ",
                ))
                continue
            raise exception.StopExtraction(response.json().get("error"))

    def _pagination(self, endpoint, params):
        url = endpoint
        while url:
            response = self._call(url, params)
            yield from response.json()

            url = response.links.get("next")
            if not url:
                return
            url = url["url"]
            params = None


@cache(maxage=100*365*24*3600, keyarg=0)
def _access_token_cache(instance):
    return None
