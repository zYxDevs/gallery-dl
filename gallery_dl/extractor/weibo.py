# -*- coding: utf-8 -*-

# Copyright 2019-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://www.weibo.com/"""


from .common import Extractor, Message
from .. import text, util, exception
from ..cache import cache
import random

BASE_PATTERN = r"(?:https?://)?(?:www\.|m\.)?weibo\.c(?:om|n)"
USER_PATTERN = f"{BASE_PATTERN}/(?:(u|n|p(?:rofile)?)/)?([^/?#]+)(?:/home)?"


class WeiboExtractor(Extractor):
    category = "weibo"
    directory_fmt = ("{category}", "{user[screen_name]}")
    filename_fmt = "{status[id]}_{num:>02}.{extension}"
    archive_fmt = "{status[id]}_{num}"
    root = "https://weibo.com"
    request_interval = (1.0, 2.0)

    def __init__(self, match):
        Extractor.__init__(self, match)
        self._prefix, self.user = match.groups()

    def _init(self):
        self.retweets = self.config("retweets", True)
        self.videos = self.config("videos", True)
        self.livephoto = self.config("livephoto", True)

        cookies = _cookie_cache()
        if cookies is not None:
            self.cookies.update(cookies)
        self.session.headers["Referer"] = f"{self.root}/"

    def request(self, url, **kwargs):
        response = Extractor.request(self, url, **kwargs)

        if response.history and "passport.weibo.com" in response.url:
            self._sina_visitor_system(response)
            response = Extractor.request(self, url, **kwargs)

        return response

    def items(self):
        original_retweets = (self.retweets == "original")

        for status in self.statuses():

            files = []
            if self.retweets and "retweeted_status" in status:
                if original_retweets:
                    status = status["retweeted_status"]
                    self._extract_status(status, files)
                else:
                    self._extract_status(status, files)
                    self._extract_status(status["retweeted_status"], files)
            else:
                self._extract_status(status, files)

            status["date"] = text.parse_datetime(
                status["created_at"], "%a %b %d %H:%M:%S %z %Y")
            status["count"] = len(files)
            yield Message.Directory, status

            for num, file in enumerate(files, 1):
                if file["url"].startswith("http:"):
                    file["url"] = "https:" + file["url"][5:]
                if "filename" not in file:
                    text.nameext_from_url(file["url"], file)
                    if file["extension"] == "json":
                        file["extension"] = "mp4"
                file["status"] = status
                file["num"] = num
                yield Message.Url, file["url"], file

    def _extract_status(self, status, files):
        append = files.append

        if "mix_media_info" in status:
            for item in status["mix_media_info"]["items"]:
                type = item.get("type")
                if type == "video":
                    if self.videos:
                        append(self._extract_video(item["data"]["media_info"]))
                elif type == "pic":
                    append(item["data"]["largest"].copy())
                else:
                    self.log.warning("Unknown media type '%s'", type)
            return

        if pic_ids := status.get("pic_ids"):
            pics = status["pic_infos"]
            for pic_id in pic_ids:
                pic = pics[pic_id]
                pic_type = pic.get("type")

                if pic_type == "gif" and self.videos:
                    append({"url": pic["video"]})

                elif pic_type == "livephoto" and self.livephoto:
                    append(pic["largest"].copy())

                    file = {"url": pic["video"]}
                    file["filehame"], _, file["extension"] = \
                            pic["video"].rpartition("%2F")[2].rpartition(".")
                    append(file)

                else:
                    append(pic["largest"].copy())

        if "page_info" in status:
            info = status["page_info"]
            if "media_info" in info and self.videos:
                append(self._extract_video(info["media_info"]))

    def _extract_video(self, info):
        try:
            media = max(info["playback_list"],
                        key=lambda m: m["meta"]["quality_index"])
        except Exception:
            return {"url": (info.get("stream_url_hd") or
                            info.get("stream_url") or "")}
        else:
            return media["play_info"].copy()

    def _status_by_id(self, status_id):
        url = f"{self.root}/ajax/statuses/show?id={status_id}"
        return self.request(url).json()

    def _user_id(self):
        if len(self.user) >= 10 and self.user.isdecimal():
            return self.user[-10:]
        url = f'{self.root}/ajax/profile/info?{"screen_name" if self._prefix == "n" else "custom"}={self.user}'
        return self.request(url).json()["data"]["user"]["idstr"]

    def _pagination(self, endpoint, params):
        url = f"{self.root}/ajax{endpoint}"
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "X-XSRF-TOKEN": None,
            "Referer": f'{self.root}/u/{params["uid"]}',
        }

        while True:
            response = self.request(url, params=params, headers=headers)
            headers["Accept"] = "application/json, text/plain, */*"
            headers["X-XSRF-TOKEN"] = response.cookies.get("XSRF-TOKEN")

            data = response.json()
            if not data.get("ok"):
                self.log.debug(response.content)
                if "since_id" not in params:  # first iteration
                    raise exception.StopExtraction(
                        '"%s"', data.get("msg") or "unknown error")

            data = data["data"]
            statuses = data["list"]
            if not statuses:
                return
            yield from statuses

            if "next_cursor" in data:  # videos, newvideo
                if data["next_cursor"] == -1:
                    return
                params["cursor"] = data["next_cursor"]
            elif "page" in params:     # home, article
                params["page"] += 1
            elif data["since_id"]:     # album
                params["sinceid"] = data["since_id"]
            else:                      # feed, last album page
                try:
                    params["since_id"] = statuses[-1]["id"] - 1
                except KeyError:
                    return

    def _sina_visitor_system(self, response):
        self.log.info("Sina Visitor System")

        passport_url = "https://passport.weibo.com/visitor/genvisitor"
        headers = {"Referer": response.url}
        data = {
            "cb": "gen_callback",
            "fp": '{"os":"1","browser":"Gecko91,0,0,0","fonts":"undefined",'
                  '"screenInfo":"1920*1080*24","plugins":""}',
        }

        page = Extractor.request(
            self, passport_url, method="POST", headers=headers, data=data).text
        data = util.json_loads(text.extr(page, "(", ");"))["data"]

        passport_url = "https://passport.weibo.com/visitor/visitor"
        params = {
            "a"    : "incarnate",
            "t"    : data["tid"],
            "w"    : "2",
            "c"    : "{:>03}".format(data["confidence"]),
            "gc"   : "",
            "cb"   : "cross_domain",
            "from" : "weibo",
            "_rand": random.random(),
        }
        response = Extractor.request(self, passport_url, params=params)
        _cookie_cache.update("", response.cookies)


class WeiboUserExtractor(WeiboExtractor):
    """Extractor for weibo user profiles"""
    subcategory = "user"
    pattern = USER_PATTERN + r"(?:$|#)"
    test = (
        ("https://weibo.com/1758989602", {
            "pattern": r"^https://weibo\.com/u/1758989602\?tabtype=feed$",
        }),
        ("https://weibo.com/u/1758989602"),
        ("https://weibo.com/p/1758989602"),
        ("https://m.weibo.cn/profile/2314621010"),
        ("https://m.weibo.cn/p/2304132314621010_-_WEIBO_SECOND_PROFILE_WEIBO"),
        ("https://www.weibo.com/p/1003062314621010/home"),
    )

    def initialize(self):
        pass

    def items(self):
        base = f"{self.root}/u/{self._user_id()}?tabtype="
        return self._dispatch_extractors(
            (
                (WeiboHomeExtractor, f"{base}home"),
                (WeiboFeedExtractor, f"{base}feed"),
                (WeiboVideosExtractor, f"{base}video"),
                (WeiboNewvideoExtractor, f"{base}newVideo"),
                (WeiboAlbumExtractor, f"{base}album"),
            ),
            ("feed",),
        )


class WeiboHomeExtractor(WeiboExtractor):
    """Extractor for weibo 'home' listings"""
    subcategory = "home"
    pattern = USER_PATTERN + r"\?tabtype=home"
    test = ("https://weibo.com/1758989602?tabtype=home", {
        "range": "1-30",
        "count": 30,
    })

    def statuses(self):
        endpoint = "/profile/myhot"
        params = {"uid": self._user_id(), "page": 1, "feature": "2"}
        return self._pagination(endpoint, params)


class WeiboFeedExtractor(WeiboExtractor):
    """Extractor for weibo user feeds"""
    subcategory = "feed"
    pattern = USER_PATTERN + r"\?tabtype=feed"
    test = (
        ("https://weibo.com/1758989602?tabtype=feed", {
            "range": "1-30",
            "count": 30,
        }),
        ("https://weibo.com/zhouyuxi77?tabtype=feed", {
            "keyword": {"status": {"user": {"id": 7488709788}}},
            "range": "1",
        }),
        ("https://www.weibo.com/n/周于希Sally?tabtype=feed", {
            "keyword": {"status": {"user": {"id": 7488709788}}},
            "range": "1",
        }),
        # deleted (#2521)
        ("https://weibo.com/u/7500315942?tabtype=feed", {
            "count": 0,
        }),
    )

    def statuses(self):
        endpoint = "/statuses/mymblog"
        params = {"uid": self._user_id(), "feature": "0"}
        return self._pagination(endpoint, params)


class WeiboVideosExtractor(WeiboExtractor):
    """Extractor for weibo 'video' listings"""
    subcategory = "videos"
    pattern = USER_PATTERN + r"\?tabtype=video"
    test = ("https://weibo.com/1758989602?tabtype=video", {
        "pattern": r"https://f\.(video\.weibocdn\.com|us\.sinaimg\.cn)"
                   r"/(../)?\w+\.mp4\?label=mp",
        "range": "1-30",
        "count": 30,
    })

    def statuses(self):
        endpoint = "/profile/getprofilevideolist"
        params = {"uid": self._user_id()}

        for status in self._pagination(endpoint, params):
            yield status["video_detail_vo"]


class WeiboNewvideoExtractor(WeiboExtractor):
    """Extractor for weibo 'newVideo' listings"""
    subcategory = "newvideo"
    pattern = USER_PATTERN + r"\?tabtype=newVideo"
    test = ("https://weibo.com/1758989602?tabtype=newVideo", {
        "pattern": r"https://f\.video\.weibocdn\.com/(../)?\w+\.mp4\?label=mp",
        "range": "1-30",
        "count": 30,
    })

    def statuses(self):
        endpoint = "/profile/getWaterFallContent"
        params = {"uid": self._user_id()}
        return self._pagination(endpoint, params)


class WeiboArticleExtractor(WeiboExtractor):
    """Extractor for weibo 'article' listings"""
    subcategory = "article"
    pattern = USER_PATTERN + r"\?tabtype=article"
    test = ("https://weibo.com/1758989602?tabtype=article", {
        "count": 0,
    })

    def statuses(self):
        endpoint = "/statuses/mymblog"
        params = {"uid": self._user_id(), "page": 1, "feature": "10"}
        return self._pagination(endpoint, params)


class WeiboAlbumExtractor(WeiboExtractor):
    """Extractor for weibo 'album' listings"""
    subcategory = "album"
    pattern = USER_PATTERN + r"\?tabtype=album"
    test = ("https://weibo.com/1758989602?tabtype=album", {
        "pattern": r"https://(wx\d+\.sinaimg\.cn/large/\w{32}\.(jpg|png|gif)"
                   r"|g\.us\.sinaimg\.cn/../\w+\.mp4)",
        "range": "1-3",
        "count": 3,
    })

    def statuses(self):
        endpoint = "/profile/getImageWall"
        params = {"uid": self._user_id()}

        seen = set()
        for image in self._pagination(endpoint, params):
            mid = image["mid"]
            if mid not in seen:
                seen.add(mid)
                status = self._status_by_id(mid)
                if status.get("ok") != 1:
                    self.log.debug("Skipping status %s (%s)", mid, status)
                else:
                    yield status


class WeiboStatusExtractor(WeiboExtractor):
    """Extractor for images from a status on weibo.cn"""
    subcategory = "status"
    pattern = BASE_PATTERN + r"/(detail|status|\d+)/(\w+)"
    test = (
        ("https://m.weibo.cn/detail/4323047042991618", {
            "pattern": r"https?://wx\d+.sinaimg.cn/large/\w+.jpg",
            "keyword": {"status": {
                "count": 1,
                "date": "dt:2018-12-30 13:56:36",
            }},
        }),
        ("https://m.weibo.cn/detail/4339748116375525", {
            "pattern": r"https?://f.us.sinaimg.cn/\w+\.mp4\?label=mp4_1080p",
        }),
        # unavailable video (#427)
        ("https://m.weibo.cn/status/4268682979207023", {
            "exception": exception.NotFoundError,
        }),
        # non-numeric status ID (#664)
        ("https://weibo.com/3314883543/Iy7fj4qVg"),
        # original retweets (#1542)
        ("https://m.weibo.cn/detail/4600272267522211", {
            "options": (("retweets", "original"),),
            "keyword": {"status": {"id": 4600167083287033}},
        }),
        # type == livephoto (#2146)
        ("https://weibo.com/5643044717/KkuDZ4jAA", {
            "range": "2,4,6",
            "pattern": r"https://video\.weibo\.com/media/play\?livephoto="
                       r"https%3A%2F%2Fus.sinaimg.cn%2F\w+\.mov",
        }),
        # type == gif
        ("https://weibo.com/1758989602/LvBhm5DiP", {
            "pattern": r"https://g\.us\.sinaimg.cn/o0/qNZcaAAglx07Wuf921CM0104"
                       r"120005tc0E010\.mp4\?label=gif_mp4",
        }),
        # missing 'playback_list' (#2792)
        ("https://weibo.com/2909128931/4409545658754086", {
            "count": 10,
        }),
        # empty 'playback_list' (#3301)
        ("https://weibo.com/1501933722/4142890299009993", {
            "pattern": r"https://f\.us\.sinaimg\.cn/004zstGKlx07dAHg4ZVu010f01"
                       r"000OOl0k01\.mp4\?label=mp4_hd&template=template_7&ori"
                       r"=0&ps=1CwnkDw1GXwCQx.+&KID=unistore,video",
            "count": 1,
        }),
        # mix_media_info (#3793)
        ("https://weibo.com/2427303621/MxojLlLgQ", {
            "count": 9,
        }),
        ("https://m.weibo.cn/status/4339748116375525"),
        ("https://m.weibo.cn/5746766133/4339748116375525"),
    )

    def statuses(self):
        status = self._status_by_id(self.user)
        if status.get("ok") != 1:
            self.log.debug(status)
            raise exception.NotFoundError("status")
        return (status,)


@cache(maxage=365*86400)
def _cookie_cache():
    return None
