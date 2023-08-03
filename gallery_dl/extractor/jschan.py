# -*- coding: utf-8 -*-

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for jschan Imageboards"""

from .common import BaseExtractor, Message
from .. import text
import itertools


class JschanExtractor(BaseExtractor):
    basecategory = "jschan"


BASE_PATTERN = JschanExtractor.update({
    "94chan": {
        "root": "https://94chan.org",
        "pattern": r"94chan\.org"
    }
})


class JschanThreadExtractor(JschanExtractor):
    """Extractor for jschan threads"""
    subcategory = "thread"
    directory_fmt = ("{category}", "{board}",
                     "{threadId} {subject|nomarkup[:50]}")
    filename_fmt = "{postId}{num:?-//} {filename}.{extension}"
    archive_fmt = "{board}_{postId}_{num}"
    pattern = BASE_PATTERN + r"/([^/?#]+)/thread/(\d+)\.html"
    test = (
        ("https://94chan.org/art/thread/25.html", {
            "pattern": r"https://94chan.org/file/[0-9a-f]{64}(\.\w+)?",
            "count": ">= 15"
        })
    )

    def __init__(self, match):
        JschanExtractor.__init__(self, match)
        index = match.lastindex
        self.board = match.group(index-1)
        self.thread = match.group(index)

    def items(self):
        url = f"{self.root}/{self.board}/thread/{self.thread}.json"
        thread = self.request(url).json()
        thread["threadId"] = thread["postId"]
        posts = thread.pop("replies", ())

        yield Message.Directory, thread
        for post in itertools.chain((thread,), posts):
            if files := post.pop("files", ()):
                thread.update(post)
                thread["count"] = len(files)
                for num, file in enumerate(files):
                    url = f"{self.root}/file/" + file["filename"]
                    file.update(thread)
                    file["num"] = num
                    file["siteFilename"] = file["filename"]
                    text.nameext_from_url(file["originalFilename"], file)
                    yield Message.Url, url, file


class JschanBoardExtractor(JschanExtractor):
    """Extractor for jschan boards"""
    subcategory = "board"
    pattern = (BASE_PATTERN + r"/([^/?#]+)"
               r"(?:/index\.html|/catalog\.html|/\d+\.html|/?$)")
    test = (
        ("https://94chan.org/art/", {
            "pattern": JschanThreadExtractor.pattern,
            "count": ">= 30"
        }),
        ("https://94chan.org/art/2.html"),
        ("https://94chan.org/art/catalog.html"),
        ("https://94chan.org/art/index.html"),
    )

    def __init__(self, match):
        JschanExtractor.__init__(self, match)
        self.board = match.group(match.lastindex)

    def items(self):
        url = f"{self.root}/{self.board}/catalog.json"
        for thread in self.request(url).json():
            url = f'{self.root}/{self.board}/thread/{thread["postId"]}.html'
            thread["_extractor"] = JschanThreadExtractor
            yield Message.Queue, url, thread
