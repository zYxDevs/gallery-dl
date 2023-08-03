# -*- coding: utf-8 -*-

# Copyright 2021-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://kemono.party/"""


from .common import Extractor, Message
from .. import text, exception
from ..cache import cache
import itertools
import re

BASE_PATTERN = r"(?:https?://)?(?:www\.|beta\.)?(kemono|coomer)\.(party|su)"
USER_PATTERN = f"{BASE_PATTERN}/([^/?#]+)/user/([^/?#]+)"
HASH_PATTERN = r"/[0-9a-f]{2}/[0-9a-f]{2}/([0-9a-f]{64})"


class KemonopartyExtractor(Extractor):
    """Base class for kemonoparty extractors"""
    category = "kemonoparty"
    root = "https://kemono.party"
    directory_fmt = ("{category}", "{service}", "{user}")
    filename_fmt = "{id}_{title}_{num:>02}_{filename[:180]}.{extension}"
    archive_fmt = "{service}_{user}_{id}_{num}"
    cookies_domain = ".kemono.party"

    def __init__(self, match):
        domain = match.group(1)
        tld = match.group(2)
        self.category = f"{domain}party"
        self.root = text.root_from_url(match.group(0))
        self.cookies_domain = f".{domain}.{tld}"
        Extractor.__init__(self, match)

    def _init(self):
        self.session.headers["Referer"] = f"{self.root}/"
        self._prepare_ddosguard_cookies()
        self._find_inline = re.compile(
            r'src="(?:https?://(?:kemono|coomer)\.(?:party|su))?(/inline/[^"]+'
            r'|/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}\.[^"]+)').findall

    def items(self):
        find_hash = re.compile(HASH_PATTERN).match
        generators = self._build_file_generators(self.config("files"))
        duplicates = self.config("duplicates")
        comments = self.config("comments")
        username = dms = None

        # prevent files from being sent with gzip compression
        headers = {"Accept-Encoding": "identity"}

        if self.config("metadata"):
            username = text.unescape(text.extract(
                self.request(self.user_url).text,
                '<meta name="artist_name" content="', '"')[0])
        if self.config("dms"):
            dms = True

        posts = self.posts()
        if max_posts := self.config("max-posts"):
            posts = itertools.islice(posts, max_posts)

        for post in posts:

            headers[
                "Referer"
            ] = f'{self.root}/{post["service"]}/user/{post["user"]}/post/{post["id"]}'
            post["_http_headers"] = headers
            post["date"] = text.parse_datetime(
                post["published"] or post["added"],
                "%a, %d %b %Y %H:%M:%S %Z")
            if username:
                post["username"] = username
            if comments:
                post["comments"] = self._extract_comments(post)
            if dms is not None:
                if dms is True:
                    dms = self._extract_dms(post)
                post["dms"] = dms

            files = []
            hashes = set()

            for file in itertools.chain.from_iterable(
                    g(post) for g in generators):
                url = file["path"]

                if match := find_hash(url):
                    file["hash"] = hash = match.group(1)
                    if not duplicates:
                        if hash in hashes:
                            self.log.debug("Skipping %s (duplicate)", url)
                            continue
                        hashes.add(hash)
                else:
                    file["hash"] = ""

                files.append(file)

            post["count"] = len(files)
            yield Message.Directory, post

            for post["num"], file in enumerate(files, 1):
                post["_http_validate"] = None
                post["hash"] = file["hash"]
                post["type"] = file["type"]
                url = file["path"]

                text.nameext_from_url(file.get("name", url), post)
                ext = text.ext_from_url(url)
                if not post["extension"]:
                    post["extension"] = ext
                elif ext == "txt" and post["extension"] != "txt":
                    post["_http_validate"] = _validate

                if url[0] == "/":
                    url = f"{self.root}/data{url}"
                elif url.startswith(self.root):
                    url = f"{self.root}/data{url[20:]}"
                yield Message.Url, url, post

    def login(self):
        username, password = self._get_auth_info()
        if username:
            self.cookies_update(self._login_impl(
                (username, self.cookies_domain), password))

    @cache(maxage=28*24*3600, keyarg=1)
    def _login_impl(self, username, password):
        username = username[0]
        self.log.info("Logging in as %s", username)

        url = f"{self.root}/account/login"
        data = {"username": username, "password": password}

        response = self.request(url, method="POST", data=data)
        if response.url.endswith("/account/login") and \
                    "Username or password is incorrect" in response.text:
            raise exception.AuthenticationError()

        return {c.name: c.value for c in response.history[0].cookies}

    def _file(self, post):
        file = post["file"]
        if not file:
            return ()
        file["type"] = "file"
        return (file,)

    def _attachments(self, post):
        for attachment in post["attachments"]:
            attachment["type"] = "attachment"
        return post["attachments"]

    def _inline(self, post):
        for path in self._find_inline(post["content"] or ""):
            yield {"path": path, "name": path, "type": "inline"}

    def _build_file_generators(self, filetypes):
        if filetypes is None:
            return (self._attachments, self._file, self._inline)
        genmap = {
            "file"       : self._file,
            "attachments": self._attachments,
            "inline"     : self._inline,
        }
        if isinstance(filetypes, str):
            filetypes = filetypes.split(",")
        return [genmap[ft] for ft in filetypes]

    def _extract_comments(self, post):
        url = f'{self.root}/{post["service"]}/user/{post["user"]}/post/{post["id"]}'
        page = self.request(url).text

        comments = []
        for comment in text.extract_iter(page, "<article", "</article>"):
            extr = text.extract_from(comment)
            cid = extr('id="', '"')
            comments.append(
                {
                    "id": cid,
                    "user": extr(f'href="#{cid}"', '</').strip(" \n\r>"),
                    "body": extr(
                        '<section class="comment__body">', '</section>'
                    ).strip(),
                    "date": extr('datetime="', '"'),
                }
            )
        return comments

    def _extract_dms(self, post):
        url = f'{self.root}/{post["service"]}/user/{post["user"]}/dms'
        page = self.request(url).text

        return [
            {
                "body": text.unescape(
                    text.extract(
                        dm,
                        "<pre>",
                        "</pre></",
                    )[0].strip()
                ),
                "date": text.extr(dm, 'datetime="', '"'),
            }
            for dm in text.extract_iter(page, "<article", "</article>")
        ]


def _validate(response):
    return (response.headers["content-length"] != "9" or
            response.content != b"not found")


class KemonopartyUserExtractor(KemonopartyExtractor):
    """Extractor for all posts from a kemono.party user listing"""
    subcategory = "user"
    pattern = USER_PATTERN + r"/?(?:\?o=(\d+))?(?:$|[?#])"
    test = (
        ("https://kemono.party/fanbox/user/6993449", {
            "range": "1-25",
            "count": 25,
        }),
        # 'max-posts' option, 'o' query parameter (#1674)
        ("https://kemono.party/patreon/user/881792?o=150", {
            "options": (("max-posts", 25),),
            "count": "< 100",
        }),
        ("https://kemono.su/subscribestar/user/alcorart"),
        ("https://kemono.party/subscribestar/user/alcorart"),
    )

    def __init__(self, match):
        _, _, service, user_id, offset = match.groups()
        self.subcategory = service
        KemonopartyExtractor.__init__(self, match)
        self.api_url = f"{self.root}/api/{service}/user/{user_id}"
        self.user_url = f"{self.root}/{service}/user/{user_id}"
        self.offset = text.parse_int(offset)

    def posts(self):
        url = self.api_url
        params = {"o": self.offset}

        while True:
            posts = self.request(url, params=params).json()
            yield from posts

            cnt = len(posts)
            if cnt < 25:
                return
            params["o"] += cnt


class KemonopartyPostExtractor(KemonopartyExtractor):
    """Extractor for a single kemono.party post"""
    subcategory = "post"
    pattern = USER_PATTERN + r"/post/([^/?#]+)"
    test = (
        ("https://kemono.party/fanbox/user/6993449/post/506575", {
            "pattern": r"https://kemono.party/data/21/0f"
                       r"/210f35388e28bbcf756db18dd516e2d82ce75[0-9a-f]+\.jpg",
            "content": "900949cefc97ab8dc1979cc3664785aac5ba70dd",
            "keyword": {
                "added": "Wed, 06 May 2020 20:28:02 GMT",
                "content": str,
                "count": 1,
                "date": "dt:2019-08-11 02:09:04",
                "edited": None,
                "embed": dict,
                "extension": "jpeg",
                "filename": "P058kDFYus7DbqAkGlfWTlOr",
                "hash": "210f35388e28bbcf756db18dd516e2d8"
                        "2ce758e0d32881eeee76d43e1716d382",
                "id": "506575",
                "num": 1,
                "published": "Sun, 11 Aug 2019 02:09:04 GMT",
                "service": "fanbox",
                "shared_file": False,
                "subcategory": "fanbox",
                "title": "c96取り置き",
                "type": "file",
                "user": "6993449",
            },
        }),
        # inline image (#1286)
        ("https://kemono.party/fanbox/user/7356311/post/802343", {
            "pattern": r"https://kemono\.party/data/47/b5/47b5c014ecdcfabdf2c8"
                       r"5eec53f1133a76336997ae8596f332e97d956a460ad2\.jpg",
            "keyword": {"hash": "47b5c014ecdcfabdf2c85eec53f1133a"
                                "76336997ae8596f332e97d956a460ad2"},
        }),
        # kemono.party -> data.kemono.party
        ("https://kemono.party/gumroad/user/trylsc/post/IURjT", {
            "pattern": r"https://kemono\.party/data/("
                       r"a4/7b/a47bfe938d8c1682eef06e885927484cd8df1b.+\.jpg|"
                       r"c6/04/c6048f5067fd9dbfa7a8be565ac194efdfb6e4.+\.zip)",
        }),
        # username (#1548, #1652)
        ("https://kemono.party/gumroad/user/3252870377455/post/aJnAH", {
            "options": (("metadata", True),),
            "keyword": {"username": "Kudalyn's Creations"},
        }),
        # skip patreon duplicates
        ("https://kemono.party/patreon/user/4158582/post/32099982", {
            "count": 2,
        }),
        # allow duplicates (#2440)
        ("https://kemono.party/patreon/user/4158582/post/32099982", {
            "options": (("duplicates", True),),
            "count": 3,
        }),
        # DMs (#2008)
        ("https://kemono.party/patreon/user/34134344/post/38129255", {
            "options": (("dms", True),),
            "keyword": {"dms": [{
                "body": r"re:Hi! Thank you very much for supporting the work I"
                        r" did in May. Here's your reward pack! I hope you fin"
                        r"d something you enjoy in it. :\)\n\nhttps://www.medi"
                        r"afire.com/file/\w+/Set13_tier_2.zip/file",
                "date": "2021-07-31 02:47:51.327865",
            }]},
        }),
        # coomer.party (#2100)
        ("https://coomer.party/onlyfans/user/alinity/post/125962203", {
            "pattern": r"https://coomer\.party/data/7d/3f/7d3fd9804583dc224968"
                       r"c0591163ec91794552b04f00a6c2f42a15b68231d5a8\.jpg",
        }),
        # invalid file (#3510)
        ("https://kemono.party/patreon/user/19623797/post/29035449", {
            "pattern": r"907ba78b4545338d3539683e63ecb51c"
                       r"f51c10adc9dabd86e92bd52339f298b9\.txt",
            "content": "da39a3ee5e6b4b0d3255bfef95601890afd80709",  # empty
        }),
        ("https://kemono.su/subscribestar/user/alcorart/post/184330"),
        ("https://kemono.party/subscribestar/user/alcorart/post/184330"),
        ("https://www.kemono.party/subscribestar/user/alcorart/post/184330"),
        ("https://beta.kemono.party/subscribestar/user/alcorart/post/184330"),
    )

    def __init__(self, match):
        _, _, service, user_id, post_id = match.groups()
        self.subcategory = service
        KemonopartyExtractor.__init__(self, match)
        self.api_url = f"{self.root}/api/{service}/user/{user_id}/post/{post_id}"
        self.user_url = f"{self.root}/{service}/user/{user_id}"

    def posts(self):
        posts = self.request(self.api_url).json()
        return (posts[0],) if len(posts) > 1 else posts


class KemonopartyDiscordExtractor(KemonopartyExtractor):
    """Extractor for kemono.party discord servers"""
    subcategory = "discord"
    directory_fmt = ("{category}", "discord", "{server}",
                     "{channel_name|channel}")
    filename_fmt = "{id}_{num:>02}_{filename}.{extension}"
    archive_fmt = "discord_{server}_{id}_{num}"
    pattern = BASE_PATTERN + r"/discord/server/(\d+)(?:/channel/(\d+))?#(.*)"
    test = (
        (("https://kemono.party/discord"
          "/server/488668827274444803#finish-work"), {
            "count": 4,
            "keyword": {"channel_name": "finish-work"},
        }),
        (("https://kemono.su/discord"
          "/server/256559665620451329/channel/462437519519383555#"), {
            "pattern": r"https://kemono\.su/data/("
                       r"e3/77/e377e3525164559484ace2e64425b0cec1db08.*\.png|"
                       r"51/45/51453640a5e0a4d23fbf57fb85390f9c5ec154.*\.gif)",
            "keyword": {"hash": "re:e377e3525164559484ace2e64425b0cec1db08"
                                "|51453640a5e0a4d23fbf57fb85390f9c5ec154"},
            "count": ">= 2",
        }),
        # 'inline' files
        (("https://kemono.party/discord"
          "/server/315262215055736843/channel/315262215055736843#general"), {
            "pattern": r"https://cdn\.discordapp\.com/attachments/\d+/\d+/.+$",
            "options": (("image-filter", "type == 'inline'"),),
            "keyword": {"hash": ""},
            "range": "1-5",
        }),
    )

    def __init__(self, match):
        KemonopartyExtractor.__init__(self, match)
        _, _, self.server, self.channel, self.channel_name = match.groups()

    def items(self):
        self._prepare_ddosguard_cookies()

        find_inline = re.compile(
            r"https?://(?:cdn\.discordapp.com|media\.discordapp\.net)"
            r"(/[A-Za-z0-9-._~:/?#\[\]@!$&'()*+,;%=]+)").findall
        find_hash = re.compile(HASH_PATTERN).match

        posts = self.posts()
        if max_posts := self.config("max-posts"):
            posts = itertools.islice(posts, max_posts)

        for post in posts:
            files = []
            append = files.append
            for attachment in post["attachments"]:
                match = find_hash(attachment["path"])
                attachment["hash"] = match.group(1) if match else ""
                attachment["type"] = "attachment"
                append(attachment)
            for path in find_inline(post["content"] or ""):
                append(
                    {
                        "path": f"https://cdn.discordapp.com{path}",
                        "name": path,
                        "type": "inline",
                        "hash": "",
                    }
                )

            post["channel_name"] = self.channel_name
            post["date"] = text.parse_datetime(
                post["published"], "%a, %d %b %Y %H:%M:%S %Z")
            post["count"] = len(files)
            yield Message.Directory, post

            for post["num"], file in enumerate(files, 1):
                post["hash"] = file["hash"]
                post["type"] = file["type"]
                url = file["path"]

                text.nameext_from_url(file.get("name", url), post)
                if not post["extension"]:
                    post["extension"] = text.ext_from_url(url)

                if url[0] == "/":
                    url = f"{self.root}/data{url}"
                elif url.startswith(self.root):
                    url = f"{self.root}/data{url[20:]}"
                yield Message.Url, url, post

    def posts(self):
        if self.channel is None:
            url = f"{self.root}/api/discord/channels/lookup?q={self.server}"
            for channel in self.request(url).json():
                if channel["name"] == self.channel_name:
                    self.channel = channel["id"]
                    break
            else:
                raise exception.NotFoundError("channel")

        url = f"{self.root}/api/discord/channel/{self.channel}"
        params = {"skip": 0}

        while True:
            posts = self.request(url, params=params).json()
            yield from posts

            cnt = len(posts)
            if cnt < 25:
                break
            params["skip"] += cnt


class KemonopartyDiscordServerExtractor(KemonopartyExtractor):
    subcategory = "discord-server"
    pattern = BASE_PATTERN + r"/discord/server/(\d+)$"
    test = (
        ("https://kemono.party/discord/server/488668827274444803", {
            "pattern": KemonopartyDiscordExtractor.pattern,
            "count": 13,
        }),
        ("https://kemono.su/discord/server/488668827274444803", {
            "pattern": KemonopartyDiscordExtractor.pattern,
            "count": 13,
        }),
    )

    def __init__(self, match):
        KemonopartyExtractor.__init__(self, match)
        self.server = match.group(3)

    def items(self):
        url = f"{self.root}/api/discord/channels/lookup?q={self.server}"
        channels = self.request(url).json()

        for channel in channels:
            url = f'{self.root}/discord/server/{self.server}/channel/{channel["id"]}#{channel["name"]}'
            channel["_extractor"] = KemonopartyDiscordExtractor
            yield Message.Queue, url, channel


class KemonopartyFavoriteExtractor(KemonopartyExtractor):
    """Extractor for kemono.party favorites"""
    subcategory = "favorite"
    pattern = BASE_PATTERN + r"/favorites(?:/?\?([^#]+))?"
    test = (
        ("https://kemono.party/favorites", {
            "pattern": KemonopartyUserExtractor.pattern,
            "url": "f4b5b796979bcba824af84206578c79101c7f0e1",
            "count": 3,
        }),
        ("https://kemono.party/favorites?type=post", {
            "pattern": KemonopartyPostExtractor.pattern,
            "url": "ecfccf5f0d50b8d14caa7bbdcf071de5c1e5b90f",
            "count": 3,
        }),
        ("https://kemono.su/favorites?type=post", {
            "pattern": KemonopartyPostExtractor.pattern,
            "url": "4be8e84cb384a907a8e7997baaf6287b451783b5",
            "count": 3,
        }),
    )

    def __init__(self, match):
        KemonopartyExtractor.__init__(self, match)
        self.favorites = (text.parse_query(match.group(3)).get("type") or
                          self.config("favorites") or
                          "artist")

    def items(self):
        self._prepare_ddosguard_cookies()
        self.login()

        if self.favorites == "artist":
            users = self.request(f"{self.root}/api/favorites?type=artist").json()
            for user in users:
                user["_extractor"] = KemonopartyUserExtractor
                url = f'{self.root}/{user["service"]}/user/{user["id"]}'
                yield Message.Queue, url, user

        elif self.favorites == "post":
            posts = self.request(f"{self.root}/api/favorites?type=post").json()
            for post in posts:
                post["_extractor"] = KemonopartyPostExtractor
                url = f'{self.root}/{post["service"]}/user/{post["user"]}/post/{post["id"]}'
                yield Message.Queue, url, post
