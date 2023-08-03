# -*- coding: utf-8 -*-

# Copyright 2019-2021 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://nsfwalbum.com/"""

from .common import GalleryExtractor
from .. import text


class NsfwalbumAlbumExtractor(GalleryExtractor):
    """Extractor for image albums on nsfwalbum.com"""
    category = "nsfwalbum"
    subcategory = "album"
    root = "https://nsfwalbum.com"
    filename_fmt = "{album_id}_{num:>03}_{id}.{extension}"
    directory_fmt = ("{category}", "{album_id} {title}")
    archive_fmt = "{id}"
    pattern = r"(?:https?://)?(?:www\.)?nsfwalbum\.com(/album/(\d+))"
    test = ("https://nsfwalbum.com/album/401611", {
        "range": "1-5",
        "url": "b0481fc7fad5982da397b6359fbed8421b8ba284",
        "keyword": "e98f9b0d473c00000831618d0235863b1dd78294",
    })

    def __init__(self, match):
        self.album_id = match.group(2)
        GalleryExtractor.__init__(self, match)

    def metadata(self, page):
        extr = text.extract_from(page)
        return {
            "album_id": text.parse_int(self.album_id),
            "title"   : text.unescape(extr('<h6>', '</h6>')),
            "models"  : text.split_html(extr('"models"> Models:', '</div>')),
            "studio"  : text.remove_html(extr('"models"> Studio:', '</div>')),
        }

    def images(self, page):
        iframe = f"{self.root}/iframe_image.php?id="
        backend = f"{self.root}/backend.php"
        retries = self._retries

        for image_id in text.extract_iter(page, 'data-img-id="', '"'):
            spirit = None
            tries = 0

            while tries <= retries:
                try:
                    if not spirit:
                        spirit = self._annihilate(text.extract(
                            self.request(iframe + image_id).text,
                            'giraffe.annihilate("', '"')[0])
                        params = {"spirit": spirit, "photo": image_id}
                    data = self.request(backend, params=params).json()
                    break
                except Exception:
                    tries += 1
            else:
                self.log.warning("Unable to fetch image %s", image_id)
                continue

            yield (
                data[0],
                {
                    "id": text.parse_int(image_id),
                    "width": text.parse_int(data[1]),
                    "height": text.parse_int(data[2]),
                    "_http_validate": self._validate_response,
                    "_fallback": (
                        f"{self.root}/imageProxy.php?photoId={image_id}&spirit={spirit}",
                    ),
                },
            )

    @staticmethod
    def _validate_response(response):
        return not response.request.url.endswith(
            ("/no_image.jpg", "/placeholder.png"))

    @staticmethod
    def _annihilate(value, base=6):
        return "".join(
            chr(ord(char) ^ base)
            for char in value
        )
