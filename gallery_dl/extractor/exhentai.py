# -*- coding: utf-8 -*-

# Copyright 2014-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://e-hentai.org/ and https://exhentai.org/"""

from .common import Extractor, Message
from .. import text, util, exception
from ..cache import cache
import itertools
import math

BASE_PATTERN = r"(?:https?://)?(e[x-]|g\.e-)hentai\.org"


class ExhentaiExtractor(Extractor):
    """Base class for exhentai extractors"""
    category = "exhentai"
    directory_fmt = ("{category}", "{gid} {title[:247]}")
    filename_fmt = "{gid}_{num:>04}_{image_token}_{filename}.{extension}"
    archive_fmt = "{gid}_{num}"
    cookies_domain = ".exhentai.org"
    cookies_names = ("ipb_member_id", "ipb_pass_hash")
    root = "https://exhentai.org"
    request_interval = 5.0

    LIMIT = False

    def __init__(self, match):
        Extractor.__init__(self, match)
        self.version = match.group(1)

    def initialize(self):
        domain = self.config("domain", "auto")
        if domain == "auto":
            domain = ("ex" if self.version == "ex" else "e-") + "hentai.org"
        self.root = f"https://{domain}"
        self.cookies_domain = f".{domain}"

        Extractor.initialize(self)

        if self.version != "ex":
            self.cookies.set("nw", "1", domain=self.cookies_domain)
        self.session.headers["Referer"] = f"{self.root}/"
        self.original = self.config("original", True)

        limits = self.config("limits", False)
        if limits and limits.__class__ is int:
            self.limits = limits
            self._remaining = 0
        else:
            self.limits = False

    def request(self, url, **kwargs):
        response = Extractor.request(self, url, **kwargs)
        if response.history and response.headers.get("Content-Length") == "0":
            self.log.info("blank page")
            raise exception.AuthorizationError()
        return response

    def login(self):
        """Login and set necessary cookies"""
        if self.LIMIT:
            raise exception.StopExtraction("Image limit reached!")

        if self.cookies_check(self.cookies_names):
            return

        username, password = self._get_auth_info()
        if username:
            return self.cookies_update(self._login_impl(username, password))

        self.log.info("no username given; using e-hentai.org")
        self.root = "https://e-hentai.org"
        self.cookies_domain = ".e-hentai.org"
        self.cookies.set("nw", "1", domain=self.cookies_domain)
        self.original = False
        self.limits = False

    @cache(maxage=90*24*3600, keyarg=1)
    def _login_impl(self, username, password):
        self.log.info("Logging in as %s", username)
        url = "https://forums.e-hentai.org/index.php?act=Login&CODE=01"
        headers = {
            "Referer": "https://e-hentai.org/bounce_login.php?b=d&bt=1-1",
        }
        data = {
            "CookieDate": "1",
            "b": "d",
            "bt": "1-1",
            "UserName": username,
            "PassWord": password,
            "ipb_login_submit": "Login!",
        }

        response = self.request(url, method="POST", headers=headers, data=data)
        if b"You are now logged in as:" not in response.content:
            raise exception.AuthenticationError()
        return {c: response.cookies[c] for c in self.cookies_names}


class ExhentaiGalleryExtractor(ExhentaiExtractor):
    """Extractor for image galleries from exhentai.org"""
    subcategory = "gallery"
    pattern = (BASE_PATTERN +
               r"(?:/g/(\d+)/([\da-f]{10})"
               r"|/s/([\da-f]{10})/(\d+)-(\d+))")
    test = (
        ("https://exhentai.org/g/1200119/d55c44d3d0/", {
            "options": (("original", False),),
            "keyword": {
                "cost": int,
                "date": "dt:2018-03-18 20:14:00",
                "eh_category": "Non-H",
                "expunged": False,
                "favorites": r"re:^[12]\d$",
                "filecount": "4",
                "filesize": 1488978,
                "gid": 1200119,
                "height": int,
                "image_token": "re:[0-9a-f]{10}",
                "lang": "ja",
                "language": "Japanese",
                "parent": "",
                "rating": r"re:\d\.\d+",
                "size": int,
                "tags": [
                    "parody:komi-san wa komyushou desu.",
                    "character:shouko komi",
                    "group:seventh lowlife",
                    "other:sample",
                ],
                "thumb": "https://exhentai.org/t/ce/0a/ce0a5bcb583229a9b07c0f8"
                         "3bcb1630ab1350640-624622-736-1036-jpg_250.jpg",
                "title": "C93 [Seventh_Lowlife] Komi-san ha Tokidoki Daitan de"
                         "su (Komi-san wa Komyushou desu) [Sample]",
                "title_jpn": "(C93) [Comiketjack (わ！)] 古見さんは、時々大胆"
                             "です。 (古見さんは、コミュ症です。) [見本]",
                "token": "d55c44d3d0",
                "torrentcount": "0",
                "uploader": "klorpa",
                "width": int,
            },
            "content": ("2c68cff8a7ca540a78c36fdbf5fbae0260484f87",
                        "e9891a4c017ed0bb734cd1efba5cd03f594d31ff"),
        }),
        ("https://exhentai.org/g/960461/4f0e369d82/", {
            "exception": exception.NotFoundError,
        }),
        ("http://exhentai.org/g/962698/7f02358e00/", {
            "exception": exception.AuthorizationError,
        }),
        ("https://exhentai.org/s/f68367b4c8/1200119-3", {
            "options": (("original", False),),
            "count": 2,
        }),
        ("https://e-hentai.org/s/f68367b4c8/1200119-3", {
            "options": (("original", False),),
            "count": 2,
        }),
        ("https://g.e-hentai.org/g/1200119/d55c44d3d0/"),
    )

    def __init__(self, match):
        ExhentaiExtractor.__init__(self, match)
        self.key = {}
        self.count = 0
        self.gallery_id = text.parse_int(match.group(2) or match.group(5))
        self.gallery_token = match.group(3)
        self.image_token = match.group(4)
        self.image_num = text.parse_int(match.group(6), 1)

    def _init(self):
        source = self.config("source")
        if source == "hitomi":
            self.items = self._items_hitomi

    def items(self):
        self.login()

        if self.gallery_token:
            gpage = self._gallery_page()
            self.image_token = text.extr(gpage, 'hentai.org/s/', '"')
            if not self.image_token:
                self.log.error("Failed to extract initial image token")
                self.log.debug("Page content:\n%s", gpage)
                return
            ipage = self._image_page()
        else:
            ipage = self._image_page()
            part = text.extr(ipage, 'hentai.org/g/', '"')
            if not part:
                self.log.error("Failed to extract gallery token")
                self.log.debug("Page content:\n%s", ipage)
                return
            self.gallery_token = part.split("/")[1]
            gpage = self._gallery_page()

        data = self.get_metadata(gpage)
        self.count = text.parse_int(data["filecount"])
        yield Message.Directory, data

        def _validate_response(response):
            # declared inside 'items()' to be able to access 'data'
            if not response.history and response.headers.get(
                    "content-type", "").startswith("text/html"):
                self._report_limits(data)
            return True

        images = itertools.chain(
            (self.image_from_page(ipage),), self.images_from_api())
        for url, image in images:
            data.update(image)
            if self.limits:
                self._check_limits(data)
            data["_http_validate"] = _validate_response if "/fullimg.php" in url else None
            yield Message.Url, url, data

    def _items_hitomi(self):
        if self.config("metadata", False):
            data = self.metadata_from_api()
            data["date"] = text.parse_timestamp(data["posted"])
        else:
            data = {}

        from .hitomi import HitomiGalleryExtractor
        url = f"https://hitomi.la/galleries/{self.gallery_id}.html"
        data["_extractor"] = HitomiGalleryExtractor
        yield Message.Queue, url, data

    def get_metadata(self, page):
        """Extract gallery metadata"""
        data = self.metadata_from_page(page)
        if self.config("metadata", False):
            data.update(self.metadata_from_api())
            data["date"] = text.parse_timestamp(data["posted"])
        return data

    def metadata_from_page(self, page):
        extr = text.extract_from(page)
        data = {
            "gid": self.gallery_id,
            "token": self.gallery_token,
            "thumb": extr("background:transparent url(", ")"),
            "title": text.unescape(extr('<h1 id="gn">', '</h1>')),
            "title_jpn": text.unescape(extr('<h1 id="gj">', '</h1>')),
            "_": extr('<div id="gdc"><div class="cs ct', '"'),
            "eh_category": extr('>', '<'),
            "uploader": extr('<div id="gdn">', '</div>'),
            "date": text.parse_datetime(
                extr('>Posted:</td><td class="gdt2">', '</td>'), "%Y-%m-%d %H:%M"
            ),
            "parent": extr('>Parent:</td><td class="gdt2"><a href="', '"'),
            "expunged": extr('>Visible:</td><td class="gdt2">', '<') != "Yes",
            "language": extr('>Language:</td><td class="gdt2">', ' '),
            "filesize": text.parse_bytes(
                extr('>File Size:</td><td class="gdt2">', '<').rstrip("Bb")
            ),
            "filecount": extr('>Length:</td><td class="gdt2">', ' '),
            "favorites": extr('id="favcount">', ' '),
            "rating": extr(">Average: ", "<"),
            "torrentcount": extr('>Torrent Download (', ')'),
        }

        if data["uploader"].startswith("<"):
            data["uploader"] = text.unescape(text.extr(
                data["uploader"], ">", "<"))

        f = data["favorites"][0]
        if f == "N":
            data["favorites"] = "0"
        elif f == "O":
            data["favorites"] = "1"

        data["lang"] = util.language_to_code(data["language"])
        data["tags"] = [
            text.unquote(tag.replace("+", " "))
            for tag in text.extract_iter(page, 'hentai.org/tag/', '"')
        ]

        return data

    def metadata_from_api(self):
        url = f"{self.root}/api.php"
        data = {
            "method": "gdata",
            "gidlist": ((self.gallery_id, self.gallery_token),),
            "namespace": 1,
        }

        data = self.request(url, method="POST", json=data).json()
        if "error" in data:
            raise exception.StopExtraction(data["error"])

        return data["gmetadata"][0]

    def image_from_page(self, page):
        """Get image url and data from webpage"""
        pos = page.index('<div id="i3"><a onclick="return load_image(') + 26
        extr = text.extract_from(page, pos)

        self.key["next"] = extr("'", "'")
        iurl = extr('<img id="img" src="', '"')
        orig = extr('hentai.org/fullimg.php', '"')

        try:
            if self.original and orig:
                url = f"{self.root}/fullimg.php{text.unescape(orig)}"
                data = self._parse_original_info(extr('ownload original', '<'))
            else:
                url = iurl
                data = self._parse_image_info(url)
        except IndexError:
            self.log.debug("Page content:\n%s", page)
            raise exception.StopExtraction(
                "Unable to parse image info for '%s'", url)

        data["num"] = self.image_num
        data["image_token"] = self.key["start"] = extr('var startkey="', '";')
        self.key["show"] = extr('var showkey="', '";')

        self._check_509(iurl, data)
        return url, text.nameext_from_url(iurl, data)

    def images_from_api(self):
        """Get image url and data from api calls"""
        api_url = f"{self.root}/api.php"
        nextkey = self.key["next"]
        request = {
            "method" : "showpage",
            "gid"    : self.gallery_id,
            "imgkey" : nextkey,
            "showkey": self.key["show"],
        }
        for request["page"] in range(self.image_num + 1, self.count + 1):
            page = self.request(api_url, method="POST", json=request).json()
            imgkey = nextkey
            nextkey, pos = text.extract(page["i3"], "'", "'")
            imgurl , pos = text.extract(page["i3"], 'id="img" src="', '"', pos)
            origurl, pos = text.extract(page["i7"], '<a href="', '"')

            try:
                if self.original and origurl:
                    url = text.unescape(origurl)
                    data = self._parse_original_info(text.extract(
                        page["i7"], "ownload original", "<", pos)[0])
                else:
                    url = imgurl
                    data = self._parse_image_info(url)
            except IndexError:
                self.log.debug("Page content:\n%s", page)
                raise exception.StopExtraction(
                    "Unable to parse image info for '%s'", url)

            data["num"] = request["page"]
            data["image_token"] = imgkey

            self._check_509(imgurl, data)
            yield url, text.nameext_from_url(imgurl, data)

            request["imgkey"] = nextkey

    def _report_limits(self, data):
        ExhentaiExtractor.LIMIT = True
        raise exception.StopExtraction(
            "Image limit reached! "
            "Continue with '%s/s/%s/%s-%s' as URL after resetting it.",
            self.root, data["image_token"], self.gallery_id, data["num"])

    def _check_limits(self, data):
        if not self._remaining or data["num"] % 25 == 0:
            self._update_limits()
        self._remaining -= data["cost"]
        if self._remaining <= 0:
            self._report_limits(data)

    def _check_509(self, url, data):
        # full 509.gif URLs
        # - https://exhentai.org/img/509.gif
        # - https://ehgt.org/g/509.gif
        if url.endswith(("hentai.org/img/509.gif",
                         "ehgt.org/g/509.gif")):
            self.log.debug(url)
            self._report_limits(data)

    def _update_limits(self):
        url = "https://e-hentai.org/home.php"
        cookies = {
            cookie.name: cookie.value
            for cookie in self.cookies
            if cookie.domain == self.cookies_domain and
            cookie.name != "igneous"
        }

        page = self.request(url, cookies=cookies).text
        current = text.extr(page, "<strong>", "</strong>")
        self.log.debug("Image Limits: %s/%s", current, self.limits)
        self._remaining = self.limits - text.parse_int(current)

    def _gallery_page(self):
        url = f"{self.root}/g/{self.gallery_id}/{self.gallery_token}/"
        response = self.request(url, fatal=False)
        page = response.text

        if response.status_code == 404 and "Gallery Not Available" in page:
            raise exception.AuthorizationError()
        if page.startswith(("Key missing", "Gallery not found")):
            raise exception.NotFoundError("gallery")
        if "hentai.org/mpv/" in page:
            self.log.warning("Enabled Multi-Page Viewer is not supported")
        return page

    def _image_page(self):
        url = f"{self.root}/s/{self.image_token}/{self.gallery_id}-{self.image_num}"
        page = self.request(url, fatal=False).text

        if page.startswith(("Invalid page", "Keep trying")):
            raise exception.NotFoundError("image page")
        return page

    @staticmethod
    def _parse_image_info(url):
        for part in url.split("/")[4:]:
            try:
                _, size, width, height, _ = part.split("-")
                break
            except ValueError:
                pass
        else:
            size = width = height = 0

        return {
            "cost"  : 1,
            "size"  : text.parse_int(size),
            "width" : text.parse_int(width),
            "height": text.parse_int(height),
        }

    @staticmethod
    def _parse_original_info(info):
        parts = info.lstrip().split(" ")
        size = text.parse_bytes(parts[3] + parts[4][0])

        return {
            # 1 initial point + 1 per 0.1 MB
            "cost"  : 1 + math.ceil(size / 100000),
            "size"  : size,
            "width" : text.parse_int(parts[0]),
            "height": text.parse_int(parts[2]),
        }


class ExhentaiSearchExtractor(ExhentaiExtractor):
    """Extractor for exhentai search results"""
    subcategory = "search"
    pattern = BASE_PATTERN + r"/(?:\?([^#]*)|tag/([^/?#]+))"
    test = (
        ("https://e-hentai.org/?f_search=touhou"),
        ("https://exhentai.org/?f_cats=767&f_search=touhou"),
        ("https://exhentai.org/tag/parody:touhou+project"),
        (("https://exhentai.org/?f_doujinshi=0&f_manga=0&f_artistcg=0"
          "&f_gamecg=0&f_western=0&f_non-h=1&f_imageset=0&f_cosplay=0"
          "&f_asianporn=0&f_misc=0&f_search=touhou&f_apply=Apply+Filter"), {
            "pattern": ExhentaiGalleryExtractor.pattern,
            "range": "1-30",
            "count": 30,
            "keyword": {
                "gallery_id": int,
                "gallery_token": r"re:^[0-9a-f]{10}$"
            },
        }),
    )

    def __init__(self, match):
        ExhentaiExtractor.__init__(self, match)
        self.search_url = self.root

        _, query, tag = match.groups()
        if tag:
            if "+" in tag:
                ns, _, tag = tag.rpartition(":")
                tag = f'{ns}:"{tag.replace("+", " ")}$"'
            else:
                tag += "$"
            self.params = {"f_search": tag, "page": 0}
        else:
            self.params = text.parse_query(query)
            if "next" not in self.params:
                self.params["page"] = text.parse_int(self.params.get("page"))

    def items(self):
        self.login()
        data = {"_extractor": ExhentaiGalleryExtractor}
        search_url = self.search_url
        params = self.params

        while True:
            last = None
            page = self.request(search_url, params=params).text

            for gallery in ExhentaiGalleryExtractor.pattern.finditer(page):
                url = gallery.group(0)
                if url == last:
                    continue
                last = url
                data["gallery_id"] = text.parse_int(gallery.group(2))
                data["gallery_token"] = gallery.group(3)
                yield (Message.Queue, f"{url}/", data)

            next_url = text.extr(page, 'nexturl="', '"', None)
            if next_url is not None:
                if not next_url:
                    return
                search_url = next_url
                params = None

            elif 'class="ptdd">&gt;<' in page or ">No hits found</p>" in page:
                return
            else:
                params["page"] += 1


class ExhentaiFavoriteExtractor(ExhentaiSearchExtractor):
    """Extractor for favorited exhentai galleries"""
    subcategory = "favorite"
    pattern = BASE_PATTERN + r"/favorites\.php(?:\?([^#]*)())?"
    test = (
        ("https://e-hentai.org/favorites.php", {
            "count": 1,
            "pattern": r"https?://e-hentai\.org/g/1200119/d55c44d3d0"
        }),
        ("https://exhentai.org/favorites.php?favcat=1&f_search=touhou"
         "&f_apply=Search+Favorites"),
    )

    def __init__(self, match):
        ExhentaiSearchExtractor.__init__(self, match)
        self.search_url = f"{self.root}/favorites.php"
