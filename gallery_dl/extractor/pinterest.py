# -*- coding: utf-8 -*-

# Copyright 2016-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://www.pinterest.com/"""

from .common import Extractor, Message
from .. import text, util, exception
from ..cache import cache
import itertools

BASE_PATTERN = r"(?:https?://)?(?:\w+\.)?pinterest\.[\w.]+"


class PinterestExtractor(Extractor):
    """Base class for pinterest extractors"""
    category = "pinterest"
    filename_fmt = "{category}_{id}{media_id:?_//}.{extension}"
    archive_fmt = "{id}{media_id}"
    root = "https://www.pinterest.com"

    def _init(self):
        domain = self.config("domain")
        if not domain or domain == "auto" :
            self.root = text.root_from_url(self.url)
        else:
            self.root = text.ensure_http_scheme(domain)

        self.api = PinterestAPI(self)

    def items(self):
        self.api.login()
        data = self.metadata()
        videos = self.config("videos", True)

        yield Message.Directory, data
        for pin in self.pins():

            if isinstance(pin, tuple):
                url, data = pin
                yield Message.Queue, url, data
                continue

            pin.update(data)

            if carousel_data := pin.get("carousel_data"):
                for num, slot in enumerate(carousel_data["carousel_slots"], 1):
                    slot["media_id"] = slot.pop("id")
                    pin.update(slot)
                    pin["num"] = num
                    size, image = next(iter(slot["images"].items()))
                    url = image["url"].replace(f"/{size}/", "/originals/")
                    yield Message.Url, url, text.nameext_from_url(url, pin)

            else:
                try:
                    media = self._media_from_pin(pin)
                except Exception:
                    self.log.debug("Unable to fetch download URL for pin %s",
                                   pin.get("id"))
                    continue

                if videos or media.get("duration") is None:
                    pin.update(media)
                    pin["num"] = 0
                    pin["media_id"] = ""

                    url = media["url"]
                    text.nameext_from_url(url, pin)

                    if pin["extension"] == "m3u8":
                        url = f"ytdl:{url}"
                        pin["extension"] = "mp4"

                    yield Message.Url, url, pin

    def metadata(self):
        """Return general metadata"""

    def pins(self):
        """Return all relevant pin objects"""

    @staticmethod
    def _media_from_pin(pin):
        if not (videos := pin.get("videos")):
            return pin["images"]["orig"]
        video_formats = videos["video_list"]

        for fmt in ("V_HLSV4", "V_HLSV3_WEB", "V_HLSV3_MOBILE"):
            if fmt in video_formats:
                media = video_formats[fmt]
                break
        else:
            media = max(video_formats.values(),
                        key=lambda x: x.get("width", 0))

        if "V_720P" in video_formats:
            media["_fallback"] = (video_formats["V_720P"]["url"],)

        return media


class PinterestPinExtractor(PinterestExtractor):
    """Extractor for images from a single pin from pinterest.com"""
    subcategory = "pin"
    pattern = BASE_PATTERN + r"/pin/([^/?#]+)(?!.*#related$)"
    test = (
        ("https://www.pinterest.com/pin/858146903966145189/", {
            "url": "afb3c26719e3a530bb0e871c480882a801a4e8a5",
            "content": ("4c435a66f6bb82bb681db2ecc888f76cf6c5f9ca",
                        "d3e24bc9f7af585e8c23b9136956bd45a4d9b947"),
        }),
        # video pin (#1189)
        ("https://www.pinterest.com/pin/422564377542934214/", {
            "pattern": r"https://v\d*\.pinimg\.com/videos/mc/hls/d7/22/ff"
                       r"/d722ff00ab2352981b89974b37909de8.m3u8",
        }),
        ("https://www.pinterest.com/pin/858146903966145188/", {
            "exception": exception.NotFoundError,
        }),
    )

    def __init__(self, match):
        PinterestExtractor.__init__(self, match)
        self.pin_id = match.group(1)
        self.pin = None

    def metadata(self):
        self.pin = self.api.pin(self.pin_id)
        return self.pin

    def pins(self):
        return (self.pin,)


class PinterestBoardExtractor(PinterestExtractor):
    """Extractor for images from a board from pinterest.com"""
    subcategory = "board"
    directory_fmt = ("{category}", "{board[owner][username]}", "{board[name]}")
    archive_fmt = "{board[id]}_{id}"
    pattern = (BASE_PATTERN + r"/(?!pin/)([^/?#]+)"
               "/(?!_saved|_created|pins/)([^/?#]+)/?$")
    test = (
        ("https://www.pinterest.com/g1952849/test-/", {
            "pattern": r"https://i\.pinimg\.com/originals/",
            "count": 2,
        }),
        # board with sections (#835)
        ("https://www.pinterest.com/g1952849/stuff/", {
            "options": (("sections", True),),
            "count": 4,
        }),
        # secret board (#1055)
        ("https://www.pinterest.de/g1952849/secret/", {
            "count": 2,
        }),
        ("https://www.pinterest.com/g1952848/test/", {
            "exception": exception.GalleryDLException,
        }),
        # .co.uk TLD (#914)
        ("https://www.pinterest.co.uk/hextra7519/based-animals/"),
    )

    def __init__(self, match):
        PinterestExtractor.__init__(self, match)
        self.user = text.unquote(match.group(1))
        self.board_name = text.unquote(match.group(2))
        self.board = None

    def metadata(self):
        self.board = self.api.board(self.user, self.board_name)
        return {"board": self.board}

    def pins(self):
        board = self.board
        pins = self.api.board_pins(board["id"])

        if board["section_count"] and self.config("sections", True):
            base = f'{self.root}/{board["owner"]["username"]}/{board["name"]}/id:'
            data = {"_extractor": PinterestSectionExtractor}
            sections = [(base + section["id"], data)
                        for section in self.api.board_sections(board["id"])]
            pins = itertools.chain(pins, sections)

        return pins


class PinterestUserExtractor(PinterestExtractor):
    """Extractor for a user's boards"""
    subcategory = "user"
    pattern = BASE_PATTERN + r"/(?!pin/)([^/?#]+)(?:/_saved)?/?$"
    test = (
        ("https://www.pinterest.com/g1952849/", {
            "pattern": PinterestBoardExtractor.pattern,
            "count": ">= 2",
        }),
        ("https://www.pinterest.com/g1952849/_saved/"),
    )

    def __init__(self, match):
        PinterestExtractor.__init__(self, match)
        self.user = text.unquote(match.group(1))

    def items(self):
        for board in self.api.boards(self.user):
            if url := board.get("url"):
                board["_extractor"] = PinterestBoardExtractor
                yield Message.Queue, self.root + url, board


class PinterestAllpinsExtractor(PinterestExtractor):
    """Extractor for a user's 'All Pins' feed"""
    subcategory = "allpins"
    directory_fmt = ("{category}", "{user}")
    pattern = BASE_PATTERN + r"/(?!pin/)([^/?#]+)/pins/?$"
    test = ("https://www.pinterest.com/g1952849/pins/", {
        "pattern": r"https://i\.pinimg\.com/originals/[0-9a-f]{2}"
                   r"/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{32}\.\w{3}",
        "count": 7,
    })

    def __init__(self, match):
        PinterestExtractor.__init__(self, match)
        self.user = text.unquote(match.group(1))

    def metadata(self):
        return {"user": self.user}

    def pins(self):
        return self.api.user_pins(self.user)


class PinterestCreatedExtractor(PinterestExtractor):
    """Extractor for a user's created pins"""
    subcategory = "created"
    directory_fmt = ("{category}", "{user}")
    pattern = BASE_PATTERN + r"/(?!pin/)([^/?#]+)/_created/?$"
    test = ("https://www.pinterest.de/digitalmomblog/_created/", {
        "pattern": r"https://i\.pinimg\.com/originals/[0-9a-f]{2}"
                   r"/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{32}\.(jpg|png)",
        "count": 10,
        "range": "1-10",
    })

    def __init__(self, match):
        PinterestExtractor.__init__(self, match)
        self.user = text.unquote(match.group(1))

    def metadata(self):
        return {"user": self.user}

    def pins(self):
        return self.api.user_activity_pins(self.user)


class PinterestSectionExtractor(PinterestExtractor):
    """Extractor for board sections on pinterest.com"""
    subcategory = "section"
    directory_fmt = ("{category}", "{board[owner][username]}",
                     "{board[name]}", "{section[title]}")
    archive_fmt = "{board[id]}_{id}"
    pattern = BASE_PATTERN + r"/(?!pin/)([^/?#]+)/([^/?#]+)/([^/?#]+)"
    test = ("https://www.pinterest.com/g1952849/stuff/section", {
        "count": 2,
    })

    def __init__(self, match):
        PinterestExtractor.__init__(self, match)
        self.user = text.unquote(match.group(1))
        self.board_slug = text.unquote(match.group(2))
        self.section_slug = text.unquote(match.group(3))
        self.section = None

    def metadata(self):
        if self.section_slug.startswith("id:"):
            section = self.section = self.api.board_section(
                self.section_slug[3:])
        else:
            section = self.section = self.api.board_section_by_name(
                self.user, self.board_slug, self.section_slug)
        section.pop("preview_pins", None)
        return {"board": section.pop("board"), "section": section}

    def pins(self):
        return self.api.board_section_pins(self.section["id"])


class PinterestSearchExtractor(PinterestExtractor):
    """Extractor for Pinterest search results"""
    subcategory = "search"
    directory_fmt = ("{category}", "Search", "{search}")
    pattern = BASE_PATTERN + r"/search/pins/?\?q=([^&#]+)"
    test = ("https://www.pinterest.com/search/pins/?q=nature", {
        "range": "1-50",
        "count": ">= 50",
    })

    def __init__(self, match):
        PinterestExtractor.__init__(self, match)
        self.search = text.unquote(match.group(1))

    def metadata(self):
        return {"search": self.search}

    def pins(self):
        return self.api.search(self.search)


class PinterestRelatedPinExtractor(PinterestPinExtractor):
    """Extractor for related pins of another pin from pinterest.com"""
    subcategory = "related-pin"
    directory_fmt = ("{category}", "related {original_pin[id]}")
    pattern = BASE_PATTERN + r"/pin/([^/?#]+).*#related$"
    test = ("https://www.pinterest.com/pin/858146903966145189/#related", {
        "range": "31-70",
        "count": 40,
        "archive": False,
    })

    def metadata(self):
        return {"original_pin": self.api.pin(self.pin_id)}

    def pins(self):
        return self.api.pin_related(self.pin_id)


class PinterestRelatedBoardExtractor(PinterestBoardExtractor):
    """Extractor for related pins of a board from pinterest.com"""
    subcategory = "related-board"
    directory_fmt = ("{category}", "{board[owner][username]}",
                     "{board[name]}", "related")
    pattern = BASE_PATTERN + r"/(?!pin/)([^/?#]+)/([^/?#]+)/?#related$"
    test = ("https://www.pinterest.com/g1952849/test-/#related", {
        "range": "31-70",
        "count": 40,
        "archive": False,
    })

    def pins(self):
        return self.api.board_content_recommendation(self.board["id"])


class PinterestPinitExtractor(PinterestExtractor):
    """Extractor for images from a pin.it URL"""
    subcategory = "pinit"
    pattern = r"(?:https?://)?pin\.it/([^/?#]+)"

    test = (
        ("https://pin.it/Hvt8hgT", {
            "url": "8daad8558382c68f0868bdbd17d05205184632fa",
        }),
        ("https://pin.it/Hvt8hgS", {
            "exception": exception.NotFoundError,
        }),
    )

    def __init__(self, match):
        PinterestExtractor.__init__(self, match)
        self.shortened_id = match.group(1)

    def items(self):
        url = f"https://api.pinterest.com/url_shortener/{self.shortened_id}/redirect/"
        response = self.request(url, method="HEAD", allow_redirects=False)
        location = response.headers.get("Location")
        if not location or not PinterestPinExtractor.pattern.match(location):
            raise exception.NotFoundError("pin")
        yield Message.Queue, location, {"_extractor": PinterestPinExtractor}


class PinterestAPI():
    """Minimal interface for the Pinterest Web API

    For a better and more complete implementation in PHP, see
    - https://github.com/seregazhuk/php-pinterest-bot
    """

    def __init__(self, extractor):
        csrf_token = util.generate_token()

        self.extractor = extractor
        self.root = extractor.root
        self.cookies = {"csrftoken": csrf_token}
        self.headers = {
            "Accept": "application/json, text/javascript, " "*/*, q=0.01",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": f"{self.root}/",
            "X-Requested-With": "XMLHttpRequest",
            "X-APP-VERSION": "0c4af40",
            "X-CSRFToken": csrf_token,
            "X-Pinterest-AppState": "active",
            "Origin": self.root,
        }

    def pin(self, pin_id):
        """Query information about a pin"""
        options = {"id": pin_id, "field_set_key": "detailed"}
        return self._call("Pin", options)["resource_response"]["data"]

    def pin_related(self, pin_id):
        """Yield related pins of another pin"""
        options = {"pin": pin_id, "add_vase": True, "pins_only": True}
        return self._pagination("RelatedPinFeed", options)

    def board(self, user, board_name):
        """Query information about a board"""
        options = {"slug": board_name, "username": user,
                   "field_set_key": "detailed"}
        return self._call("Board", options)["resource_response"]["data"]

    def boards(self, user):
        """Yield all boards from 'user'"""
        options = {
            "sort"            : "last_pinned_to",
            "field_set_key"   : "profile_grid_item",
            "filter_stories"  : False,
            "username"        : user,
            "page_size"       : 25,
            "include_archived": True,
        }
        return self._pagination("Boards", options)

    def board_pins(self, board_id):
        """Yield all pins of a specific board"""
        options = {"board_id": board_id}
        return self._pagination("BoardFeed", options)

    def board_section(self, section_id):
        """Yield a specific board section"""
        options = {"section_id": section_id}
        return self._call("BoardSection", options)["resource_response"]["data"]

    def board_section_by_name(self, user, board_slug, section_slug):
        """Yield a board section by name"""
        options = {"board_slug": board_slug, "section_slug": section_slug,
                   "username": user}
        return self._call("BoardSection", options)["resource_response"]["data"]

    def board_sections(self, board_id):
        """Yield all sections of a specific board"""
        options = {"board_id": board_id}
        return self._pagination("BoardSections", options)

    def board_section_pins(self, section_id):
        """Yield all pins from a board section"""
        options = {"section_id": section_id}
        return self._pagination("BoardSectionPins", options)

    def board_content_recommendation(self, board_id):
        """Yield related pins of a specific board"""
        options = {"id": board_id, "type": "board", "add_vase": True}
        return self._pagination("BoardContentRecommendation", options)

    def user_pins(self, user):
        """Yield all pins from 'user'"""
        options = {
            "is_own_profile_pins": False,
            "username"           : user,
            "field_set_key"      : "grid_item",
            "pin_filter"         : None,
        }
        return self._pagination("UserPins", options)

    def user_activity_pins(self, user):
        """Yield pins created by 'user'"""
        options = {
            "exclude_add_pin_rep": True,
            "field_set_key"      : "grid_item",
            "is_own_profile_pins": False,
            "username"           : user,
        }
        return self._pagination("UserActivityPins", options)

    def search(self, query):
        """Yield pins from searches"""
        options = {"query": query, "scope": "pins", "rs": "typed"}
        return self._pagination("BaseSearch", options)

    def login(self):
        """Login and obtain session cookies"""
        username, password = self.extractor._get_auth_info()
        if username:
            self.cookies.update(self._login_impl(username, password))

    @cache(maxage=180*24*3600, keyarg=1)
    def _login_impl(self, username, password):
        self.extractor.log.info("Logging in as %s", username)

        url = f"{self.root}/resource/UserSessionResource/create/"
        options = {
            "username_or_email": username,
            "password"         : password,
        }
        data = {
            "data"      : util.json_dumps({"options": options}),
            "source_url": "",
        }

        try:
            response = self.extractor.request(
                url, method="POST", headers=self.headers,
                cookies=self.cookies, data=data)
            resource = response.json()["resource_response"]
        except (exception.HttpError, ValueError, KeyError):
            raise exception.AuthenticationError()

        if resource["status"] != "success":
            raise exception.AuthenticationError()
        return {
            cookie.name: cookie.value
            for cookie in response.cookies
        }

    def _call(self, resource, options):
        url = f"{self.root}/resource/{resource}Resource/get/"
        params = {
            "data"      : util.json_dumps({"options": options}),
            "source_url": "",
        }

        response = self.extractor.request(
            url, params=params, headers=self.headers,
            cookies=self.cookies, fatal=False)

        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.history:
            self.root = text.root_from_url(response.url)
        if response.status_code < 400:
            return data
        if response.status_code == 404:
            resource = self.extractor.subcategory.rpartition("-")[2]
            raise exception.NotFoundError(resource)
        self.extractor.log.debug("Server response: %s", response.text)
        raise exception.StopExtraction("API request failed")

    def _pagination(self, resource, options):
        while True:
            data = self._call(resource, options)
            results = data["resource_response"]["data"]
            if isinstance(results, dict):
                results = results["results"]
            yield from results

            try:
                bookmarks = data["resource"]["options"]["bookmarks"]
                if (not bookmarks or bookmarks[0] == "-end-" or
                        bookmarks[0].startswith("Y2JOb25lO")):
                    return
                options["bookmarks"] = bookmarks
            except KeyError:
                return
