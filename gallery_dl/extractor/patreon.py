# -*- coding: utf-8 -*-

# Copyright 2019-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://www.patreon.com/"""

from .common import Extractor, Message
from .. import text, util, exception
from ..cache import memcache
import collections
import itertools


class PatreonExtractor(Extractor):
    """Base class for patreon extractors"""
    category = "patreon"
    root = "https://www.patreon.com"
    cookies_domain = ".patreon.com"
    directory_fmt = ("{category}", "{creator[full_name]}")
    filename_fmt = "{id}_{title}_{num:>02}.{extension}"
    archive_fmt = "{id}_{num}"
    browser = "firefox"
    tls12 = False
    _warning = True

    def items(self):
        if self._warning:
            if not self.cookies_check(("session_id",)):
                self.log.warning("no 'session_id' cookie set")
            PatreonExtractor._warning = False

        generators = self._build_file_generators(self.config("files"))

        for post in self.posts():

            if not post.get("current_user_can_view", True):
                self.log.warning("Not allowed to view post %s", post["id"])
                continue
            yield Message.Directory, post

            post["num"] = 0
            hashes = set()
            for kind, url, name in itertools.chain.from_iterable(
                    g(post) for g in generators):
                fhash = self._filehash(url)
                if fhash not in hashes or not fhash:
                    hashes.add(fhash)
                    post["hash"] = fhash
                    post["type"] = kind
                    post["num"] += 1
                    yield Message.Url, url, text.nameext_from_url(name, post)
                else:
                    self.log.debug("skipping %s (%s %s)", url, fhash, kind)

    @staticmethod
    def _postfile(post):
        if postfile := post.get("post_file"):
            return (("postfile", postfile["url"], postfile["name"]),)
        return ()

    def _images(self, post):
        for image in post["images"]:
            if url := image.get("download_url"):
                name = image.get("file_name") or self._filename(url) or url
                yield "image", url, name

    def _image_large(self, post):
        if image := post.get("image"):
            if url := image.get("large_url"):
                name = image.get("file_name") or self._filename(url) or url
                return (("image_large", url, name),)
        return ()

    def _attachments(self, post):
        for attachment in post["attachments"]:
            if url := self.request(
                attachment["url"],
                method="HEAD",
                allow_redirects=False,
                fatal=False,
            ).headers.get("Location"):
                yield "attachment", url, attachment["name"]

    def _content(self, post):
        if content := post.get("content"):
            for img in text.extract_iter(
                    content, '<img data-media-id="', '>'):
                if url := text.extr(img, 'src="', '"'):
                    yield "content", url, self._filename(url) or url

    def posts(self):
        """Return all relevant post objects"""

    def _pagination(self, url):
        headers = {
            "Referer": f"{self.root}/",
            "Content-Type": "application/vnd.api+json",
        }

        while url:
            url = text.ensure_http_scheme(url)
            posts = self.request(url, headers=headers).json()

            if "included" in posts:
                included = self._transform(posts["included"])
                for post in posts["data"]:
                    yield self._process(post, included)

            if "links" not in posts:
                return
            url = posts["links"].get("next")

    def _process(self, post, included):
        """Process and extend a 'post' object"""
        attr = post["attributes"]
        attr["id"] = text.parse_int(post["id"])

        if attr.get("current_user_can_view", True):

            relationships = post["relationships"]
            attr["images"] = self._files(post, included, "images")
            attr["attachments"] = self._files(post, included, "attachments")
            attr["date"] = text.parse_datetime(
                attr["published_at"], "%Y-%m-%dT%H:%M:%S.%f%z")

            tags = relationships.get("user_defined_tags")
            attr["tags"] = [
                tag["id"].replace("user_defined;", "")
                for tag in tags["data"]
                if tag["type"] == "post_tag"
            ] if tags else []

            user = relationships["user"]
            attr["creator"] = (
                self._user(user["links"]["related"]) or
                included["user"][user["data"]["id"]])

        return attr

    @staticmethod
    def _transform(included):
        """Transform 'included' into an easier to handle format"""
        result = collections.defaultdict(dict)
        for inc in included:
            result[inc["type"]][inc["id"]] = inc["attributes"]
        return result

    @staticmethod
    def _files(post, included, key):
        """Build a list of files"""
        files = post["relationships"].get(key)
        if files and files.get("data"):
            return [
                included[file["type"]][file["id"]]
                for file in files["data"]
            ]
        return []

    @memcache(keyarg=1)
    def _user(self, url):
        """Fetch user information"""
        response = self.request(url, fatal=False)
        if response.status_code >= 400:
            return None
        user = response.json()["data"]
        attr = user["attributes"]
        attr["id"] = user["id"]
        attr["date"] = text.parse_datetime(
            attr["created"], "%Y-%m-%dT%H:%M:%S.%f%z")
        return attr

    def _filename(self, url):
        """Fetch filename from an URL's Content-Disposition header"""
        response = self.request(url, method="HEAD", fatal=False)
        cd = response.headers.get("Content-Disposition")
        return text.extr(cd, 'filename="', '"')

    @staticmethod
    def _filehash(url):
        """Extract MD5 hash from a download URL"""
        parts = url.partition("?")[0].split("/")
        parts.reverse()

        return next((part for part in parts if len(part) == 32), "")

    @staticmethod
    def _build_url(endpoint, query):
        return (
            (
                f"https://www.patreon.com/api/{endpoint}"
                + "?include=campaign,access_rules,attachments,audio,images,media,"
                "native_video_insights,poll.choices,"
                "poll.current_user_responses.user,"
                "poll.current_user_responses.choice,"
                "poll.current_user_responses.poll,"
                "user,user_defined_tags,ti_checks"
                "&fields[campaign]=currency,show_audio_post_download_links,"
                "avatar_photo_url,avatar_photo_image_urls,earnings_visibility,"
                "is_nsfw,is_monthly,name,url"
                "&fields[post]=change_visibility_at,comment_count,commenter_count,"
                "content,current_user_can_comment,current_user_can_delete,"
                "current_user_can_view,current_user_has_liked,embed,image,"
                "insights_last_updated_at,is_paid,like_count,meta_image_url,"
                "min_cents_pledged_to_view,post_file,post_metadata,published_at,"
                "patreon_url,post_type,pledge_url,preview_asset_type,thumbnail,"
                "thumbnail_url,teaser_text,title,upgrade_url,url,"
                "was_posted_by_campaign_owner,has_ti_violation,moderation_status,"
                "post_level_suspension_removal_date,pls_one_liners_by_category,"
                "video_preview,view_count"
                "&fields[post_tag]=tag_type,value"
                "&fields[user]=image_url,full_name,url"
                "&fields[access_rule]=access_rule_type,amount_cents"
                "&fields[media]=id,image_urls,download_url,metadata,file_name"
                "&fields[native_video_insights]=average_view_duration,"
                "average_view_pct,has_preview,id,last_updated_at,num_views,"
                "preview_views,video_duration"
            )
            + query
        ) + "&json-api-version=1.0"

    def _build_file_generators(self, filetypes):
        if filetypes is None:
            return (self._images, self._image_large,
                    self._attachments, self._postfile, self._content)
        genmap = {
            "images"     : self._images,
            "image_large": self._image_large,
            "attachments": self._attachments,
            "postfile"   : self._postfile,
            "content"    : self._content,
        }
        if isinstance(filetypes, str):
            filetypes = filetypes.split(",")
        return [genmap[ft] for ft in filetypes]

    def _extract_bootstrap(self, page):
        return util.json_loads(text.extr(
            page, "window.patreon.bootstrap,", "\n});") + "}")


class PatreonCreatorExtractor(PatreonExtractor):
    """Extractor for a creator's works"""
    subcategory = "creator"
    pattern = (r"(?:https?://)?(?:www\.)?patreon\.com"
               r"/(?!(?:home|join|posts|login|signup)(?:$|[/?#]))"
               r"([^/?#]+)(?:/posts)?/?(?:\?([^#]+))?")
    test = (
        ("https://www.patreon.com/koveliana", {
            "range": "1-25",
            "count": ">= 25",
            "keyword": {
                "attachments"  : list,
                "comment_count": int,
                "content"      : str,
                "creator"      : dict,
                "date"         : "type:datetime",
                "id"           : int,
                "images"       : list,
                "like_count"   : int,
                "post_type"    : str,
                "published_at" : str,
                "title"        : str,
            },
        }),
        ("https://www.patreon.com/koveliana/posts?filters[month]=2020-3", {
            "count": 1,
            "keyword": {"date": "dt:2020-03-30 21:21:44"},
        }),
        ("https://www.patreon.com/kovelianot", {
            "exception": exception.NotFoundError,
        }),
        ("https://www.patreon.com/user?u=2931440"),
        ("https://www.patreon.com/user/posts/?u=2931440"),
    )

    def __init__(self, match):
        PatreonExtractor.__init__(self, match)
        self.creator, self.query = match.groups()

    def posts(self):
        query = text.parse_query(self.query)

        if creator_id := query.get("u"):
            url = f"{self.root}/user/posts?u={creator_id}"
        else:
            url = f"{self.root}/{self.creator}/posts"
        page = self.request(url, notfound="creator").text

        try:
            data = self._extract_bootstrap(page)
            campaign_id = data["creator"]["data"]["id"]
        except (KeyError, ValueError):
            raise exception.NotFoundError("creator")

        filters = "".join(
            f"&filter[{key[8:]}={text.escape(value)}"
            for key, value in query.items()
            if key.startswith("filters[")
        )

        url = self._build_url(
            "posts",
            f"&filter[campaign_id]={campaign_id}&filter[contains_exclusive_posts]=true&filter[is_draft]=false{filters}&sort="
            + query.get("sort", "-published_at"),
        )
        return self._pagination(url)


class PatreonUserExtractor(PatreonExtractor):
    """Extractor for media from creators supported by you"""
    subcategory = "user"
    pattern = r"(?:https?://)?(?:www\.)?patreon\.com/home$"
    test = ("https://www.patreon.com/home",)

    def posts(self):
        url = self._build_url("stream", (
            "&page[cursor]=null"
            "&filter[is_following]=true"
            "&json-api-use-default-includes=false"
        ))
        return self._pagination(url)


class PatreonPostExtractor(PatreonExtractor):
    """Extractor for media from a single post"""
    subcategory = "post"
    pattern = r"(?:https?://)?(?:www\.)?patreon\.com/posts/([^/?#]+)"
    test = (
        # postfile + attachments
        ("https://www.patreon.com/posts/precious-metal-23563293", {
            "count": 4,
        }),
        # postfile + content
        ("https://www.patreon.com/posts/56127163", {
            "count": 3,
            "keyword": {"filename": r"re:^(?!1).+$"},
        }),
        # tags (#1539)
        ("https://www.patreon.com/posts/free-post-12497641", {
            "keyword": {"tags": ["AWMedia"]},
        }),
        ("https://www.patreon.com/posts/not-found-123", {
            "exception": exception.NotFoundError,
        }),
    )

    def __init__(self, match):
        PatreonExtractor.__init__(self, match)
        self.slug = match.group(1)

    def posts(self):
        url = f"{self.root}/posts/{self.slug}"
        page = self.request(url, notfound="post").text
        post = self._extract_bootstrap(page)["post"]

        included = self._transform(post["included"])
        return (self._process(post["data"], included),)
