# -*- coding: utf-8 -*-

# Copyright 2016-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Collection of extractors for various imagehosts"""

from .common import Extractor, Message
from .. import text, exception
from ..cache import memcache
from os.path import splitext


class ImagehostImageExtractor(Extractor):
    """Base class for single-image extractors for various imagehosts"""
    basecategory = "imagehost"
    subcategory = "image"
    archive_fmt = "{token}"
    _https = True
    _params = None
    _cookies = None
    _encoding = None

    def __init__(self, match):
        Extractor.__init__(self, match)
        self.page_url = f'http{"s" if self._https else ""}://{match.group(1)}'
        self.token = match.group(2)

        if self._params == "simple":
            self._params = {
                "imgContinue": "Continue+to+image+...+",
            }
        elif self._params == "complex":
            self._params = {
                "op": "view",
                "id": self.token,
                "pre": "1",
                "adb": "1",
                "next": "Continue+to+image+...+",
            }

    def items(self):
        page = self.request(
            self.page_url,
            method=("POST" if self._params else "GET"),
            data=self._params,
            cookies=self._cookies,
            encoding=self._encoding,
        ).text

        url, filename = self.get_info(page)
        data = text.nameext_from_url(filename, {"token": self.token})
        data.update(self.metadata(page))
        if self._https and url.startswith("http:"):
            url = f"https:{url[5:]}"

        yield Message.Directory, data
        yield Message.Url, url, data

    def get_info(self, page):
        """Find image-url and string to get filename from"""

    def metadata(self, page):
        """Return additional metadata"""
        return ()


class ImxtoImageExtractor(ImagehostImageExtractor):
    """Extractor for single images from imx.to"""
    category = "imxto"
    pattern = (r"(?:https?://)?(?:www\.)?((?:imx\.to|img\.yt)"
               r"/(?:i/|img-)(\w+)(\.html)?)")
    test = (
        ("https://imx.to/i/1qdeva", {  # new-style URL
            "url": "ab2173088a6cdef631d7a47dec4a5da1c6a00130",
            "content": "0c8768055e4e20e7c7259608b67799171b691140",
            "keyword": {
                "size"  : 18,
                "width" : 64,
                "height": 32,
                "hash"  : "94d56c599223c59f3feb71ea603484d1",
            },
        }),
        ("https://imx.to/img-57a2050547b97.html", {  # old-style URL
            "url": "a83fe6ef1909a318c4d49fcf2caf62f36c3f9204",
            "content": "54592f2635674c25677c6872db3709d343cdf92f",
            "keyword": {
                "size"  : 5284,
                "width" : 320,
                "height": 160,
                "hash"  : "40da6aaa7b8c42b18ef74309bbc713fc",
            },
        }),
        ("https://img.yt/img-57a2050547b97.html", {  # img.yt domain
            "url": "a83fe6ef1909a318c4d49fcf2caf62f36c3f9204",
        }),
        ("https://imx.to/img-57a2050547b98.html", {
            "exception": exception.NotFoundError,
        }),
    )
    _params = "simple"
    _encoding = "utf-8"

    def __init__(self, match):
        ImagehostImageExtractor.__init__(self, match)
        if "/img-" in self.page_url:
            self.page_url = self.page_url.replace("img.yt", "imx.to")
            self.url_ext = True
        else:
            self.url_ext = False

    def get_info(self, page):
        url, pos = text.extract(
            page, '<div style="text-align:center;"><a href="', '"')
        if not url:
            raise exception.NotFoundError("image")
        filename, pos = text.extract(page, ' title="', '"', pos)
        if self.url_ext and filename:
            filename += splitext(url)[1]
        return url, filename or url

    def metadata(self, page):
        extr = text.extract_from(page, page.index("[ FILESIZE <"))
        size = extr(">", "</span>").replace(" ", "")[:-1]
        width, _, height = extr(">", " px</span>").partition("x")
        return {
            "size"  : text.parse_bytes(size),
            "width" : text.parse_int(width),
            "height": text.parse_int(height),
            "hash"  : extr(">", "</span>"),
        }


class ImxtoGalleryExtractor(ImagehostImageExtractor):
    """Extractor for image galleries from imx.to"""
    category = "imxto"
    subcategory = "gallery"
    pattern = r"(?:https?://)?(?:www\.)?(imx\.to/g/([^/?#]+))"
    test = ("https://imx.to/g/ozdy", {
        "pattern": ImxtoImageExtractor.pattern,
        "keyword": {"title": "untitled gallery"},
        "count": 40,
    })

    def items(self):
        page = self.request(self.page_url).text
        title, pos = text.extract(page, '<div class="title', '<')
        data = {
            "_extractor": ImxtoImageExtractor,
            "title": text.unescape(title.partition(">")[2]).strip(),
        }

        for url in text.extract_iter(page, "<a href=", " ", pos):
            yield Message.Queue, url.strip("\"'"), data


class AcidimgImageExtractor(ImagehostImageExtractor):
    """Extractor for single images from acidimg.cc"""
    category = "acidimg"
    pattern = r"(?:https?://)?((?:www\.)?acidimg\.cc/img-([a-z0-9]+)\.html)"
    test = ("https://acidimg.cc/img-5acb6b9de4640.html", {
        "url": "f132a630006e8d84f52d59555191ed82b3b64c04",
        "keyword": "135347ab4345002fc013863c0d9419ba32d98f78",
        "content": "0c8768055e4e20e7c7259608b67799171b691140",
    })
    _params = "simple"
    _encoding = "utf-8"

    def get_info(self, page):
        url, pos = text.extract(page, "<img class='centred' src='", "'")
        if not url:
            url, pos = text.extract(page, '<img class="centred" src="', '"')
        if not url:
            raise exception.NotFoundError("image")

        filename, pos = text.extract(page, "alt='", "'", pos)
        if not filename:
            filename, pos = text.extract(page, 'alt="', '"', pos)

        return url, (filename + splitext(url)[1]) if filename else url


class ImagevenueImageExtractor(ImagehostImageExtractor):
    """Extractor for single images from imagevenue.com"""
    category = "imagevenue"
    pattern = (r"(?:https?://)?((?:www|img\d+)\.imagevenue\.com"
               r"/([A-Z0-9]{8,10}|view/.*|img\.php\?.*))")
    test = (
        ("https://www.imagevenue.com/ME13LS07", {
            "pattern": r"https://cdn-images\.imagevenue\.com"
                       r"/10/ac/05/ME13LS07_o\.png",
            "keyword": "ae15d6e3b2095f019eee84cd896700cd34b09c36",
            "content": "cfaa8def53ed1a575e0c665c9d6d8cf2aac7a0ee",
        }),
        (("https://www.imagevenue.com/view/o?i=92518_13732377"
          "annakarina424200712535AM_122_486lo.jpg&h=img150&l=loc486"), {
            "url": "8bf0254e29250d8f5026c0105bbdda3ee3d84980",
        }),
        (("http://img28116.imagevenue.com/img.php"
          "?image=th_52709_test_122_64lo.jpg"), {
            "url": "f98e3091df7f48a05fb60fbd86f789fc5ec56331",
        }),
    )

    def get_info(self, page):
        pos = page.index('class="card-body')
        url, pos = text.extract(page, '<img src="', '"', pos)
        filename, pos = text.extract(page, 'alt="', '"', pos)
        return url, text.unescape(filename)


class ImagetwistImageExtractor(ImagehostImageExtractor):
    """Extractor for single images from imagetwist.com"""
    category = "imagetwist"
    pattern = (r"(?:https?://)?((?:www\.|phun\.)?"
               r"image(?:twist|haha)\.com/([a-z0-9]{12}))")
    test = (
        ("https://imagetwist.com/f1i2s4vhvbrq/test.png", {
            "url": "8d5e168c0bee30211f821c6f3b2116e419d42671",
            "keyword": "d1060a4c2e3b73b83044e20681712c0ffdd6cfef",
            "content": "0c8768055e4e20e7c7259608b67799171b691140",
        }),
        ("https://www.imagetwist.com/f1i2s4vhvbrq/test.png"),
        ("https://phun.imagetwist.com/f1i2s4vhvbrq/test.png"),
        ("https://imagehaha.com/f1i2s4vhvbrq/test.png"),
        ("https://www.imagehaha.com/f1i2s4vhvbrq/test.png"),
    )

    @property
    @memcache(maxage=3*3600)
    def _cookies(self):
        return self.request(self.page_url).cookies

    def get_info(self, page):
        url     , pos = text.extract(page, '<img src="', '"')
        filename, pos = text.extract(page, ' alt="', '"', pos)
        return url, filename


class ImgspiceImageExtractor(ImagehostImageExtractor):
    """Extractor for single images from imgspice.com"""
    category = "imgspice"
    pattern = r"(?:https?://)?((?:www\.)?imgspice\.com/([^/?#]+))"
    test = ("https://imgspice.com/nwfwtpyog50y/test.png.html", {
        "url": "b8c30a8f51ee1012959a4cfd46197fabf14de984",
        "keyword": "100e310a19a2fa22d87e1bbc427ecb9f6501e0c0",
        "content": "0c8768055e4e20e7c7259608b67799171b691140",
    })

    def get_info(self, page):
        pos = page.find('id="imgpreview"')
        if pos < 0:
            raise exception.NotFoundError("image")
        url , pos = text.extract(page, 'src="', '"', pos)
        name, pos = text.extract(page, 'alt="', '"', pos)
        return url, text.unescape(name)


class PixhostImageExtractor(ImagehostImageExtractor):
    """Extractor for single images from pixhost.to"""
    category = "pixhost"
    pattern = (r"(?:https?://)?((?:www\.)?pixhost\.(?:to|org)"
               r"/show/\d+/(\d+)_[^/?#]+)")
    test = ("http://pixhost.to/show/190/130327671_test-.png", {
        "url": "4e5470dcf6513944773044d40d883221bbc46cff",
        "keyword": "3bad6d59db42a5ebbd7842c2307e1c3ebd35e6b0",
        "content": "0c8768055e4e20e7c7259608b67799171b691140",
    })
    _cookies = {"pixhostads": "1", "pixhosttest": "1"}

    def get_info(self, page):
        url     , pos = text.extract(page, "class=\"image-img\" src=\"", "\"")
        filename, pos = text.extract(page, "alt=\"", "\"", pos)
        return url, filename


class PixhostGalleryExtractor(ImagehostImageExtractor):
    """Extractor for image galleries from pixhost.to"""
    category = "pixhost"
    subcategory = "gallery"
    pattern = (r"(?:https?://)?((?:www\.)?pixhost\.(?:to|org)"
               r"/gallery/([^/?#]+))")
    test = ("https://pixhost.to/gallery/jSMFq", {
        "pattern": PixhostImageExtractor.pattern,
        "count": 3,
    })

    def items(self):
        page = text.extr(self.request(
            self.page_url).text, 'class="images"', "</div>")
        data = {"_extractor": PixhostImageExtractor}
        for url in text.extract_iter(page, '<a href="', '"'):
            yield Message.Queue, url, data


class PostimgImageExtractor(ImagehostImageExtractor):
    """Extractor for single images from postimages.org"""
    category = "postimg"
    pattern = (r"(?:https?://)?((?:www\.)?(?:postimg|pixxxels)\.(?:cc|org)"
               r"/(?!gallery/)(?:image/)?([^/?#]+)/?)")
    test = ("https://postimg.cc/Wtn2b3hC", {
        "url": "72f3c8b1d6c6601a20ad58f35635494b4891a99e",
        "keyword": "2d05808d04e4e83e33200db83521af06e3147a84",
        "content": "cfaa8def53ed1a575e0c665c9d6d8cf2aac7a0ee",
    })

    def get_info(self, page):
        pos = page.index(' id="download"')
        url     , pos = text.rextract(page, ' href="', '"', pos)
        filename, pos = text.extract(page, 'class="imagename">', '<', pos)
        return url, text.unescape(filename)


class PostimgGalleryExtractor(ImagehostImageExtractor):
    """Extractor for images galleries from postimages.org"""
    category = "postimg"
    subcategory = "gallery"
    pattern = (r"(?:https?://)?((?:www\.)?(?:postimg|pixxxels)\.(?:cc|org)"
               r"/(?:gallery/)([^/?#]+)/?)")
    test = ("https://postimg.cc/gallery/wxpDLgX", {
        "pattern": PostimgImageExtractor.pattern,
        "count": 22,
    })

    def items(self):
        page = self.request(self.page_url).text
        data = {"_extractor": PostimgImageExtractor}
        for url in text.extract_iter(page, ' class="thumb"><a href="', '"'):
            yield Message.Queue, url, data


class TurboimagehostImageExtractor(ImagehostImageExtractor):
    """Extractor for single images from www.turboimagehost.com"""
    category = "turboimagehost"
    pattern = (r"(?:https?://)?((?:www\.)?turboimagehost\.com"
               r"/p/(\d+)/[^/?#]+\.html)")
    test = ("https://www.turboimagehost.com/p/39078423/test--.png.html", {
        "url": "b94de43612318771ced924cb5085976f13b3b90e",
        "keyword": "704757ca8825f51cec516ec44c1e627c1f2058ca",
        "content": "f38b54b17cd7462e687b58d83f00fca88b1b105a",
    })

    def get_info(self, page):
        url = text.extract(page, 'src="', '"', page.index("<img "))[0]
        return url, url


class ViprImageExtractor(ImagehostImageExtractor):
    """Extractor for single images from vipr.im"""
    category = "vipr"
    pattern = r"(?:https?://)?(vipr\.im/(\w+))"
    test = ("https://vipr.im/kcd5jcuhgs3v.html", {
        "url": "88f6a3ecbf3356a11ae0868b518c60800e070202",
        "keyword": "c432e8a1836b0d97045195b745731c2b1bb0e771",
    })

    def get_info(self, page):
        url = text.extr(page, '<img src="', '"')
        return url, url


class ImgclickImageExtractor(ImagehostImageExtractor):
    """Extractor for single images from imgclick.net"""
    category = "imgclick"
    pattern = r"(?:https?://)?((?:www\.)?imgclick\.net/([^/?#]+))"
    test = ("http://imgclick.net/4tbrre1oxew9/test-_-_.png.html", {
        "url": "140dcb250a325f2d26b2d918c18b8ac6a2a0f6ab",
        "keyword": "6895256143eab955622fc149aa367777a8815ba3",
        "content": "0c8768055e4e20e7c7259608b67799171b691140",
    })
    _https = False
    _params = "complex"

    def get_info(self, page):
        url     , pos = text.extract(page, '<br><img src="', '"')
        filename, pos = text.extract(page, 'alt="', '"', pos)
        return url, filename


class FappicImageExtractor(ImagehostImageExtractor):
    """Extractor for single images from fappic.com"""
    category = "fappic"
    pattern = r"(?:https?://)?((?:www\.)?fappic\.com/(\w+)/[^/?#]+)"
    test = ("https://www.fappic.com/98wxqcklyh8k/test.png", {
        "pattern": r"https://img\d+\.fappic\.com/img/\w+/test\.png",
        "keyword": "433b1d310b0ff12ad8a71ac7b9d8ba3f8cd1e898",
        "content": "0c8768055e4e20e7c7259608b67799171b691140",
    })

    def get_info(self, page):
        url     , pos = text.extract(page, '<a href="#"><img src="', '"')
        filename, pos = text.extract(page, 'alt="', '"', pos)

        if filename.startswith("Porn-Picture-"):
            filename = filename[13:]

        return url, filename
