# -*- coding: utf-8 -*-

# Copyright 2016-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://www.imagefap.com/"""

from .common import Extractor, Message
from .. import text, util, exception

BASE_PATTERN = r"(?:https?://)?(?:www\.|beta\.)?imagefap\.com"


class ImagefapExtractor(Extractor):
    """Base class for imagefap extractors"""
    category = "imagefap"
    root = "https://www.imagefap.com"
    directory_fmt = ("{category}", "{gallery_id} {title}")
    filename_fmt = "{category}_{gallery_id}_{filename}.{extension}"
    archive_fmt = "{gallery_id}_{image_id}"
    request_interval = (2.0, 4.0)

    def _init(self):
        self.session.headers["Referer"] = f"{self.root}/"

    def request(self, url, **kwargs):
        response = Extractor.request(self, url, **kwargs)

        if response.history and response.url.endswith("/human-verification"):
            if msg := text.extr(response.text, '<div class="mt-4', '<'):
                msg = " ".join(msg.partition(">")[2].split())
                raise exception.StopExtraction("'%s'", msg)
            self.log.warning("HTTP redirect to %s", response.url)

        return response


class ImagefapGalleryExtractor(ImagefapExtractor):
    """Extractor for image galleries from imagefap.com"""
    subcategory = "gallery"
    pattern = BASE_PATTERN + r"/(?:gallery\.php\?gid=|gallery/|pictures/)(\d+)"

    test = (
        ("https://www.imagefap.com/gallery/7102714", {
            "pattern": r"https://cdnh?\.imagefap\.com"
                       r"/images/full/\d+/\d+/\d+\.jpg",
            "keyword": "bdcb75b1e4b9dddc718f3d66e1a58afa9d81a38b",
            "content": "694a0a57385980a6f90fbc296cadcd6c11ba2dab",
        }),
        ("https://www.imagefap.com/gallery/7876223", {
            "pattern": r"https://cdnh?\.imagefap\.com"
                       r"/images/full/\d+/\d+/\d+\.jpg",
            "keyword": {
                "categories": ["Asses", "Softcore", "Pornstars"],
                "count": 44,
                "description": "",
                "gallery_id": 7876223,
                "image_id": int,
                "num": int,
                "tags": ["big ass", "panties", "horny",
                         "pussy", "exposed", "outdoor"],
                "title": "Kelsi Monroe in lingerie",
                "uploader": "BdRachel",
            },
            "count": 44,
        }),
        # description (#3905)
        ("https://www.imagefap.com/gallery/6180555", {
            "range": "1",
            "keyword": {
                "categories": ["Amateur", "Softcore", "Homemade"],
                "count": 36,
                "description": "Nude and dressed sluts showing off the goods",
                "gallery_id": 6180555,
                "image_id": int,
                "num": int,
                "tags": []  ,
                "title": "Dressed or Undressed MG*",
                "uploader": "splitopen",
            },
        }),
        ("https://www.imagefap.com/pictures/7102714"),
        ("https://www.imagefap.com/gallery.php?gid=7102714"),
        ("https://beta.imagefap.com/gallery.php?gid=7102714"),
    )

    def __init__(self, match):
        ImagefapExtractor.__init__(self, match)
        self.gid = match.group(1)
        self.image_id = ""

    def items(self):
        url = f"{self.root}/gallery/{self.gid}"
        page = self.request(url).text
        data = self.get_job_metadata(page)
        yield Message.Directory, data
        for url, image in self.get_images():
            data.update(image)
            yield Message.Url, url, data

    def get_job_metadata(self, page):
        """Collect metadata for extractor-job"""
        extr = text.extract_from(page)

        data = {
            "gallery_id": text.parse_int(self.gid),
            "uploader": extr("porn picture gallery by ", " to see hottest"),
            "title": text.unescape(extr("<title>", "<")),
            "description": text.unescape(extr(
                'id="gdesc_text"', '<').partition(">")[2]),
            "categories": text.split_html(extr(
                'id="cnt_cats"', '</div>'))[1::2],
            "tags": text.split_html(extr(
                'id="cnt_tags"', '</div>'))[1::2],
            "count": text.parse_int(extr(' 1 of ', ' pics"')),
        }

        self.image_id = extr('id="img_ed_', '"')
        self._count = data["count"]

        return data

    def get_images(self):
        """Collect image-urls and -metadata"""
        url = f"{self.root}/photo/{self.image_id}/"
        params = {"gid": self.gid, "idx": 0, "partial": "true"}
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{url}?pgid=&gid={self.image_id}&page=0",
        }

        num = 0
        total = self._count
        while True:
            page = self.request(url, params=params, headers=headers).text

            cnt = 0
            for image_url in text.extract_iter(page, '<a href="', '"'):
                num += 1
                cnt += 1
                data = text.nameext_from_url(image_url)
                data["num"] = num
                data["image_id"] = text.parse_int(data["filename"])
                yield image_url, data

            if not cnt or cnt < 24 and num >= total:
                return
            params["idx"] += cnt


class ImagefapImageExtractor(ImagefapExtractor):
    """Extractor for single images from imagefap.com"""
    subcategory = "image"
    pattern = BASE_PATTERN + r"/photo/(\d+)"
    test = (
        ("https://www.imagefap.com/photo/1962981893", {
            "pattern": r"https://cdnh?\.imagefap\.com"
                       r"/images/full/65/196/1962981893\.jpg",
            "keyword": {
                "date": "21/08/2014",
                "gallery_id": 7876223,
                "height": 1600,
                "image_id": 1962981893,
                "title": "Kelsi Monroe in lingerie",
                "uploader": "BdRachel",
                "width": 1066,
            },
        }),
        ("https://beta.imagefap.com/photo/1962981893"),
    )

    def __init__(self, match):
        ImagefapExtractor.__init__(self, match)
        self.image_id = match.group(1)

    def items(self):
        url, data = self.get_image()
        yield Message.Directory, data
        yield Message.Url, url, data

    def get_image(self):
        url = f"{self.root}/photo/{self.image_id}/"
        page = self.request(url).text

        info, pos = text.extract(
            page, '<script type="application/ld+json">', '</script>')
        image_id, pos = text.extract(
            page, 'id="imageid_input" value="', '"', pos)
        gallery_id, pos = text.extract(
            page, 'id="galleryid_input" value="', '"', pos)
        info = util.json_loads(info)
        url = info["contentUrl"]

        return url, text.nameext_from_url(url, {
            "title": text.unescape(info["name"]),
            "uploader": info["author"],
            "date": info["datePublished"],
            "width": text.parse_int(info["width"]),
            "height": text.parse_int(info["height"]),
            "gallery_id": text.parse_int(gallery_id),
            "image_id": text.parse_int(image_id),
        })


class ImagefapFolderExtractor(ImagefapExtractor):
    """Extractor for imagefap user folders"""
    subcategory = "folder"
    pattern = (BASE_PATTERN + r"/(?:organizer/|"
               r"(?:usergallery\.php\?user(id)?=([^&#]+)&"
               r"|profile/([^/?#]+)/galleries\?)folderid=)(\d+|-1)")
    test = (
        ("https://www.imagefap.com/organizer/409758", {
            "pattern": r"https://www\.imagefap\.com/gallery/7876223",
            "url": "37822523e6e4a56feb9dea35653760c86b44ff89",
            "count": 1,
        }),
        (("https://www.imagefap.com/usergallery.php"
          "?userid=1981976&folderid=409758"), {
            "url": "37822523e6e4a56feb9dea35653760c86b44ff89",
        }),
        (("https://www.imagefap.com/usergallery.php"
          "?user=BdRachel&folderid=409758"), {
            "url": "37822523e6e4a56feb9dea35653760c86b44ff89",
        }),
        ("https://www.imagefap.com/profile/BdRachel/galleries?folderid=-1", {
            "pattern": ImagefapGalleryExtractor.pattern,
            "range": "1-40",
        }),
        (("https://www.imagefap.com/usergallery.php"
          "?userid=1981976&folderid=-1"), {
            "pattern": ImagefapGalleryExtractor.pattern,
            "range": "1-40",
        }),
        (("https://www.imagefap.com/usergallery.php"
          "?user=BdRachel&folderid=-1"), {
            "pattern": ImagefapGalleryExtractor.pattern,
            "range": "1-40",
        }),
    )

    def __init__(self, match):
        ImagefapExtractor.__init__(self, match)
        self._id, user, profile, self.folder_id = match.groups()
        self.user = user or profile

    def items(self):
        for gallery_id, name in self.galleries(self.folder_id):
            url = f"{self.root}/gallery/{gallery_id}"
            data = {
                "gallery_id": gallery_id,
                "title"     : text.unescape(name),
                "_extractor": ImagefapGalleryExtractor,
            }
            yield Message.Queue, url, data

    def galleries(self, folder_id):
        """Yield gallery IDs and titles of a folder"""
        if folder_id == "-1":
            if self._id:
                url = f"{self.root}/usergallery.php?userid={self.user}&folderid=-1"
            else:
                url = f"{self.root}/profile/{self.user}/galleries?folderid=-1"
        else:
            url = f"{self.root}/organizer/{folder_id}/"

        params = {"page": 0}
        while True:
            extr = text.extract_from(self.request(url, params=params).text)
            cnt = 0

            while True:
                gid = extr('<a  href="/gallery/', '"')
                if not gid:
                    break
                yield gid, extr("<b>", "<")
                cnt += 1

            if cnt < 20:
                break
            params["page"] += 1


class ImagefapUserExtractor(ImagefapExtractor):
    """Extractor for an imagefap user profile"""
    subcategory = "user"
    pattern = (BASE_PATTERN +
               r"/(?:profile(?:\.php\?user=|/)([^/?#]+)(?:/galleries)?"
               r"|usergallery\.php\?userid=(\d+))(?:$|#)")
    test = (
        ("https://www.imagefap.com/profile/BdRachel", {
            "pattern": ImagefapFolderExtractor.pattern,
            "count": ">= 18",
        }),
        ("https://www.imagefap.com/usergallery.php?userid=1862791", {
            "pattern": r"https://www\.imagefap\.com"
                       r"/profile/LucyRae/galleries\?folderid=-1",
            "count": 1,
        }),
        ("https://www.imagefap.com/profile/BdRachel/galleries"),
        ("https://www.imagefap.com/profile.php?user=BdRachel"),
        ("https://beta.imagefap.com/profile.php?user=BdRachel"),
    )

    def __init__(self, match):
        ImagefapExtractor.__init__(self, match)
        self.user, self.user_id = match.groups()

    def items(self):
        data = {"_extractor": ImagefapFolderExtractor}

        for folder_id in self.folders():
            if folder_id == "-1":
                url = f"{self.root}/profile/{self.user}/galleries?folderid=-1"
            else:
                url = f"{self.root}/organizer/{folder_id}/"
            yield Message.Queue, url, data

    def folders(self):
        """Return a list of folder IDs of a user"""
        if self.user:
            url = f"{self.root}/profile/{self.user}/galleries"
        else:
            url = f"{self.root}/usergallery.php?userid={self.user_id}"

        response = self.request(url)
        self.user = response.url.split("/")[-2]
        folders = text.extr(response.text, ' id="tgl_all" value="', '"')
        return folders.rstrip("|").split("|")
