# -*- coding: utf-8 -*-

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Generic information extractor"""

from .common import Extractor, Message
from .. import config, text
import os.path
import re


class GenericExtractor(Extractor):
    """Extractor for images in a generic web page."""
    category = "generic"
    directory_fmt = ("{category}", "{pageurl}")
    archive_fmt = "{imageurl}"

    # By default, the generic extractor is disabled
    # and the "g(eneric):" prefix in url is required.
    # If the extractor is enabled, make the prefix optional
    pattern = r"(?i)(?P<generic>g(?:eneric)?:)"
    if config.get(("extractor", "generic"), "enabled"):
        pattern += r"?"

    # The generic extractor pattern should match (almost) any valid url
    # Based on: https://tools.ietf.org/html/rfc3986#appendix-B
    pattern += (
        r"(?P<scheme>https?://)?"          # optional http(s) scheme
        r"(?P<domain>[-\w\.]+)"            # required domain
        r"(?P<path>/[^?#]*)?"              # optional path
        r"(?:\?(?P<query>[^#]*))?"         # optional query
        r"(?:\#(?P<fragment>.*))?"         # optional fragment
    )

    test = (
        ("generic:https://www.nongnu.org/lzip/", {
            "count": 1,
            "content": "40be5c77773d3e91db6e1c5df720ee30afb62368",
            "keyword": {
                "description": "Lossless data compressor",
                "imageurl": "https://www.nongnu.org/lzip/lzip.png",
                "keywords": "lzip, clzip, plzip, lzlib, LZMA, bzip2, "
                            "gzip, data compression, GNU, free software",
                "pageurl": "https://www.nongnu.org/lzip/",
            },
        }),
        # internationalized domain name
        ("generic:https://räksmörgås.josefsson.org/", {
            "count": 2,
            "pattern": "^https://räksmörgås.josefsson.org/",
        }),
        ("g:https://en.wikipedia.org/Main_Page"),
        ("g:https://example.org/path/to/file?que=1?&ry=2/#fragment"),
        ("g:https://example.org/%27%3C%23/%23%3E%27.htm?key=%3C%26%3E"),
        ("generic:https://en.wikipedia.org/Main_Page"),
        ("generic:https://example.org/path/to/file?que=1?&ry=2/#fragment"),
        ("generic:https://example.org/%27%3C%23/%23%3E%27.htm?key=%3C%26%3E"),
    )

    def __init__(self, match):
        Extractor.__init__(self, match)

        # Strip the "g(eneric):" prefix
        # and inform about "forced" or "fallback" mode
        if match.group('generic'):
            self.url = match.group(0).partition(":")[2]
        else:
            self.log.info("Falling back on generic information extractor.")
            self.url = match.group(0)

        # Make sure we have a scheme, or use https
        if match.group('scheme'):
            self.scheme = match.group('scheme')
        else:
            self.scheme = 'https://'
            self.url = self.scheme + self.url

        # Used to resolve relative image urls
        self.root = self.scheme + match.group('domain')

    def items(self):
        """Get page, extract metadata & images, yield them in suitable messages

        Adapted from common.GalleryExtractor.items()

        """
        page = self.request(self.url).text
        data = self.metadata(page)
        imgs = self.images(page)

        try:
            data["count"] = len(imgs)
        except TypeError:
            pass
        images = enumerate(imgs, 1)

        yield Message.Directory, data

        for data["num"], (url, imgdata) in images:
            if imgdata:
                data.update(imgdata)
                if "extension" not in imgdata:
                    text.nameext_from_url(url, data)
            else:
                text.nameext_from_url(url, data)
            yield Message.Url, url, data

    def metadata(self, page):
        """Extract generic webpage metadata, return them in a dict."""
        data = {'pageurl': self.url, 'title': text.extr(page, '<title>', "</title>")}
        data['description'] = text.extr(
            page, '<meta name="description" content="', '"')
        data['keywords'] = text.extr(
            page, '<meta name="keywords" content="', '"')
        data['language'] = text.extr(
            page, '<meta name="language" content="', '"')
        data['name'] = text.extr(
            page, '<meta itemprop="name" content="', '"')
        data['copyright'] = text.extr(
            page, '<meta name="copyright" content="', '"')
        data['og_site'] = text.extr(
            page, '<meta property="og:site" content="', '"')
        data['og_site_name'] = text.extr(
            page, '<meta property="og:site_name" content="', '"')
        data['og_title'] = text.extr(
            page, '<meta property="og:title" content="', '"')
        data['og_description'] = text.extr(
            page, '<meta property="og:description" content="', '"')

        data = {k: text.unescape(data[k]) for k in data if data[k] != ""}

        return data

    def images(self, page):
        """Extract image urls, return a list of (image url, metadata) tuples.

        The extractor aims at finding as many _likely_ image urls as possible,
        using two strategies (see below); since these often overlap, any
        duplicate urls will be removed at the end of the process.

        Note: since we are using re.findall() (see below), it's essential that
        the following patterns contain 0 or at most 1 capturing group, so that
        re.findall() return a list of urls (instead of a list of tuples of
        matching groups). All other groups used in the pattern should be
        non-capturing (?:...).

        1: Look in src/srcset attributes of img/video/source elements

        See:
        https://www.w3schools.com/tags/att_src.asp
        https://www.w3schools.com/tags/att_source_srcset.asp

        We allow both absolute and relative urls here.

        Note that srcset attributes often contain multiple space separated
        image urls; this pattern matches only the first url; remaining urls
        will be matched by the "imageurl_pattern_ext" pattern below.
        """

        imageurl_pattern_src = (
            r"(?i)"
            r"<(?:img|video|source)\s[^>]*"    # <img>, <video> or <source>
            r"src(?:set)?=[\"']?"              # src or srcset attributes
            r"(?P<URL>[^\"'\s>]+)"             # url
        )

        """
        2: Look anywhere for urls containing common image/video extensions

        The list of allowed extensions is borrowed from the directlink.py
        extractor; other could be added, see
        https://en.wikipedia.org/wiki/List_of_file_formats

        Compared to the "pattern" class variable, here we must exclude also
        other special characters (space, ", ', <, >), since we are looking for
        urls in html tags.
        """

        imageurl_pattern_ext = (
            r"(?i)"
            r"(?:[^?&#\"'>\s]+)"           # anything until dot+extension
                                           # dot + image/video extensions
            r"\.(?:jpe?g|jpe|png|gif|web[mp]|mp4|mkv|og[gmv]|opus)"
            r"(?:[^\"'<>\s]*)?"            # optional query and fragment
        )

        imageurls_src = re.findall(imageurl_pattern_src, page)
        imageurls_ext = re.findall(imageurl_pattern_ext, page)
        imageurls = imageurls_src + imageurls_ext

        if basematch := re.search(
            r"(?i)(?:<base\s.*?href=[\"']?)(?P<url>[^\"' >]+)", page
        ):
            self.baseurl = basematch['url'].rstrip('/')
        elif self.url.endswith("/"):
            self.baseurl = self.url.rstrip('/')
        else:
            self.baseurl = os.path.dirname(self.url)

        # Build the list of absolute image urls
        absimageurls = []
        for u in imageurls:
            # Absolute urls are taken as-is
            if u.startswith('http'):
                absimageurls.append(u)
            elif u.startswith('//'):
                absimageurls.append(self.scheme + u.lstrip('/'))
            elif u.startswith('/'):
                absimageurls.append(self.root + u)
            else:
                absimageurls.append(f'{self.baseurl}/{u}')

        # Remove duplicates
        absimageurls = dict.fromkeys(absimageurls)

        return [(u, {'imageurl': u}) for u in absimageurls]
