# -*- coding: utf-8 -*-

# Copyright 2016-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://twitter.com/"""

from .common import Extractor, Message
from .. import text, util, exception
from ..cache import cache
import itertools
import json
import re

BASE_PATTERN = r"(?:https?://)?(?:www\.|mobile\.)?(?:[fv]x)?twitter\.com"


class TwitterExtractor(Extractor):
    """Base class for twitter extractors"""
    category = "twitter"
    directory_fmt = ("{category}", "{user[name]}")
    filename_fmt = "{tweet_id}_{num}.{extension}"
    archive_fmt = "{tweet_id}_{retweet_id}_{num}"
    cookies_domain = ".twitter.com"
    cookies_names = ("auth_token",)
    root = "https://twitter.com"
    browser = "firefox"

    def __init__(self, match):
        Extractor.__init__(self, match)
        self.user = match.group(1)

    def _init(self):
        self.textonly = self.config("text-tweets", False)
        self.retweets = self.config("retweets", False)
        self.replies = self.config("replies", True)
        self.twitpic = self.config("twitpic", False)
        self.pinned = self.config("pinned", False)
        self.quoted = self.config("quoted", False)
        self.videos = self.config("videos", True)
        self.cards = self.config("cards", False)
        self.cards_blacklist = self.config("cards-blacklist")
        self.syndication = self.config("syndication")

        if not self.config("transform", True):
            self._transform_user = util.identity
            self._transform_tweet = util.identity
        self._user = None
        self._user_obj = None
        self._user_cache = {}
        self._init_sizes()

    def _init_sizes(self):
        size = self.config("size")
        if size is None:
            self._size_image = "orig"
            self._size_fallback = ("4096x4096", "large", "medium", "small")
        else:
            if isinstance(size, str):
                size = size.split(",")
            self._size_image = size[0]
            self._size_fallback = size[1:]

    def items(self):
        self.login()
        self.api = TwitterAPI(self)
        metadata = self.metadata()

        if self.config("expand"):
            tweets = self._expand_tweets(self.tweets())
            self.tweets = lambda : tweets

        seen_tweets = set() if self.config("unique", True) else None
        if self.twitpic:
            self._find_twitpic = re.compile(
                r"https?(://twitpic\.com/(?!photos/)\w+)").findall

        for tweet in self.tweets():

            data = tweet["legacy"] if "legacy" in tweet else tweet
            if not self.retweets and "retweeted_status_id_str" in data:
                self.log.debug("Skipping %s (retweet)", data["id_str"])
                continue
            if not self.quoted and "quoted_by_id_str" in data:
                self.log.debug("Skipping %s (quoted tweet)", data["id_str"])
                continue
            if "in_reply_to_user_id_str" in data and (
                not self.replies or (
                    self.replies == "self" and
                    data["user_id_str"] !=
                    (self._user_obj["rest_id"] if self._user else
                     data["in_reply_to_user_id_str"])
                )
            ):
                self.log.debug("Skipping %s (reply)", data["id_str"])
                continue

            if seen_tweets is not None:
                if data["id_str"] in seen_tweets:
                    self.log.debug(
                        "Skipping %s (previously seen)", data["id_str"])
                    continue
                seen_tweets.add(data["id_str"])

            if "withheld_scope" in data:
                txt = data.get("full_text") or data.get("text") or ""
                self.log.warning("'%s' (%s)", txt, data["id_str"])

            files = []
            if "extended_entities" in data:
                self._extract_media(
                    data, data["extended_entities"]["media"], files)
            if "card" in tweet and self.cards:
                self._extract_card(tweet, files)
            if self.twitpic:
                self._extract_twitpic(data, files)
            if not files and not self.textonly:
                continue

            tdata = self._transform_tweet(tweet)
            tdata.update(metadata)
            tdata["count"] = len(files)
            yield Message.Directory, tdata
            for tdata["num"], file in enumerate(files, 1):
                file.update(tdata)
                url = file.pop("url")
                if "extension" not in file:
                    text.nameext_from_url(url, file)
                yield Message.Url, url, file

    def _extract_media(self, tweet, entities, files):
        for media in entities:
            descr = media.get("ext_alt_text")
            width = media["original_info"].get("width", 0)
            height = media["original_info"].get("height", 0)

            if "video_info" in media:
                if self.videos == "ytdl":
                    files.append(
                        {
                            "url": f'ytdl:{self.root}/i/web/status/{tweet["id_str"]}',
                            "width": width,
                            "height": height,
                            "extension": None,
                            "description": descr,
                        }
                    )
                elif self.videos:
                    video_info = media["video_info"]
                    variant = max(
                        video_info["variants"],
                        key=lambda v: v.get("bitrate", 0),
                    )
                    files.append({
                        "url"        : variant["url"],
                        "width"      : width,
                        "height"     : height,
                        "bitrate"    : variant.get("bitrate", 0),
                        "duration"   : video_info.get(
                            "duration_millis", 0) / 1000,
                        "description": descr,
                    })
            elif "media_url_https" in media:
                url = media["media_url_https"]
                if url[-4] == ".":
                    base, _, fmt = url.rpartition(".")
                    base += f"?format={fmt}&name="
                else:
                    base = url.rpartition("=")[0] + "="
                files.append(text.nameext_from_url(url, {
                    "url"        : base + self._size_image,
                    "width"      : width,
                    "height"     : height,
                    "_fallback"  : self._image_fallback(base),
                    "description": descr,
                }))
            else:
                files.append({"url": media["media_url"]})

    def _image_fallback(self, base):
        for fmt in self._size_fallback:
            yield base + fmt

    def _extract_card(self, tweet, files):
        card = tweet["card"]
        if "legacy" in card:
            card = card["legacy"]

        name = card["name"].rpartition(":")[2]
        bvals = card["binding_values"]
        if isinstance(bvals, list):
            bvals = {bval["key"]: bval["value"]
                     for bval in card["binding_values"]}

        if cbl := self.cards_blacklist:
            if name in cbl:
                return
            if "vanity_url" in bvals:
                domain = bvals["vanity_url"]["string_value"]
                if domain in cbl or f"{name}:{domain}" in cbl:
                    return

        if name in ("summary", "summary_large_image"):
            for prefix in ("photo_image_full_size_",
                           "summary_photo_image_",
                           "thumbnail_image_"):
                for size in ("original", "x_large", "large", "small"):
                    key = prefix + size
                    if key in bvals:
                        value = bvals[key].get("image_value")
                        if value and "url" in value:
                            base, sep, size = value["url"].rpartition("&name=")
                            if sep:
                                base += sep
                                value["url"] = base + self._size_image
                                value["_fallback"] = self._image_fallback(base)
                            files.append(value)
                            return
        elif name == "unified_card":
            data = util.json_loads(bvals["unified_card"]["string_value"])
            self._extract_media(tweet, data["media_entities"].values(), files)
            return

        if self.cards == "ytdl":
            tweet_id = tweet.get("rest_id") or tweet["id_str"]
            url = f"ytdl:{self.root}/i/web/status/{tweet_id}"
            files.append({"url": url})

    def _extract_twitpic(self, tweet, files):
        urls = {}

        # collect URLs from entities
        for url in tweet["entities"].get("urls") or ():
            url = url["expanded_url"]
            if "//twitpic.com/" not in url or "/photos/" in url:
                continue
            if url.startswith("http:"):
                url = f"https{url[4:]}"
            urls[url] = None

        # collect URLs from text
        for url in self._find_twitpic(
                tweet.get("full_text") or tweet.get("text") or ""):
            urls[f"https{url}"] = None

        # extract actual URLs
        for url in urls:
            response = self.request(url, fatal=False)
            if response.status_code >= 400:
                continue
            if url := text.extr(
                response.text, 'name="twitter:image" value="', '"'
            ):
                files.append({"url": url})

    def _transform_tweet(self, tweet):
        if "author" in tweet:
            author = tweet["author"]
        elif "core" in tweet:
            author = tweet["core"]["user_results"]["result"]
        else:
            author = tweet["user"]
        author = self._transform_user(author)

        if "note_tweet" in tweet:
            note = tweet["note_tweet"]["note_tweet_results"]["result"]
        else:
            note = None

        if "legacy" in tweet:
            tweet = tweet["legacy"]

        tweet_id = int(tweet["id_str"])
        if tweet_id >= 300000000000000:
            date = text.parse_timestamp(
                ((tweet_id >> 22) + 1288834974657) // 1000)
        else:
            date = text.parse_datetime(
                tweet["created_at"], "%a %b %d %H:%M:%S %z %Y")

        tget = tweet.get
        tdata = {
            "tweet_id"      : tweet_id,
            "retweet_id"    : text.parse_int(
                tget("retweeted_status_id_str")),
            "quote_id"      : text.parse_int(
                tget("quoted_by_id_str")),
            "reply_id"      : text.parse_int(
                tget("in_reply_to_status_id_str")),
            "conversation_id": text.parse_int(
                tget("conversation_id_str")),
            "date"          : date,
            "author"        : author,
            "user"          : self._user or author,
            "lang"          : tweet["lang"],
            "favorite_count": tget("favorite_count"),
            "quote_count"   : tget("quote_count"),
            "reply_count"   : tget("reply_count"),
            "retweet_count" : tget("retweet_count"),
        }

        entities = note["entity_set"] if note else tweet["entities"]

        if hashtags := entities.get("hashtags"):
            tdata["hashtags"] = [t["text"] for t in hashtags]

        if mentions := entities.get("user_mentions"):
            tdata["mentions"] = [{
                "id": text.parse_int(u["id_str"]),
                "name": u["screen_name"],
                "nick": u["name"],
            } for u in mentions]

        content = text.unescape(
            note["text"] if note else tget("full_text") or tget("text") or "")
        if urls := entities.get("urls"):
            for url in urls:
                content = content.replace(url["url"], url["expanded_url"])
        txt, _, tco = content.rpartition(" ")
        tdata["content"] = txt if tco.startswith("https://t.co/") else content

        if "in_reply_to_screen_name" in tweet:
            tdata["reply_to"] = tweet["in_reply_to_screen_name"]
        if "quoted_by" in tweet:
            tdata["quote_by"] = tweet["quoted_by"]

        return tdata

    def _transform_user(self, user):
        try:
            uid = user.get("rest_id") or user["id_str"]
        except KeyError:
            # private/invalid user (#4349)
            return {}

        try:
            return self._user_cache[uid]
        except KeyError:
            pass

        if "legacy" in user:
            user = user["legacy"]
        elif "statuses_count" not in user and self.syndication == "extended":
            # try to fetch extended user data
            user = self.api.user_by_screen_name(user["screen_name"])["legacy"]

        uget = user.get
        if uget("withheld_scope"):
            self.log.warning("'%s'", uget("description"))

        entities = user["entities"]
        self._user_cache[uid] = udata = {
            "id"              : text.parse_int(uid),
            "name"            : user["screen_name"],
            "nick"            : user["name"],
            "location"        : uget("location"),
            "date"            : text.parse_datetime(
                uget("created_at"), "%a %b %d %H:%M:%S %z %Y"),
            "verified"        : uget("verified", False),
            "profile_banner"  : uget("profile_banner_url", ""),
            "profile_image"   : uget(
                "profile_image_url_https", "").replace("_normal.", "."),
            "favourites_count": uget("favourites_count"),
            "followers_count" : uget("followers_count"),
            "friends_count"   : uget("friends_count"),
            "listed_count"    : uget("listed_count"),
            "media_count"     : uget("media_count"),
            "statuses_count"  : uget("statuses_count"),
        }

        descr = user["description"]
        if urls := entities["description"].get("urls"):
            for url in urls:
                descr = descr.replace(url["url"], url["expanded_url"])
        udata["description"] = descr

        if "url" in entities:
            url = entities["url"]["urls"][0]
            udata["url"] = url.get("expanded_url") or url.get("url")

        return udata

    def _assign_user(self, user):
        self._user_obj = user
        self._user = self._transform_user(user)

    def _users_result(self, users):
        userfmt = self.config("users")
        if not userfmt or userfmt == "timeline":
            cls = TwitterTimelineExtractor
            fmt = (self.root + "/i/user/{rest_id}").format_map
        elif userfmt == "media":
            cls = TwitterMediaExtractor
            fmt = (self.root + "/id:{rest_id}/media").format_map
        elif userfmt == "tweets":
            cls = TwitterTweetsExtractor
            fmt = (self.root + "/id:{rest_id}/tweets").format_map
        else:
            cls = None
            fmt = userfmt.format_map

        for user in users:
            user["_extractor"] = cls
            yield Message.Queue, fmt(user), user

    def _expand_tweets(self, tweets):
        seen = set()
        for tweet in tweets:
            obj = tweet["legacy"] if "legacy" in tweet else tweet
            cid = obj.get("conversation_id_str")
            if not cid:
                tid = obj["id_str"]
                self.log.warning(
                    "Unable to expand %s (no 'conversation_id')", tid)
                continue
            if cid in seen:
                self.log.debug(
                    "Skipping expansion of %s (previously seen)", cid)
                continue
            seen.add(cid)
            try:
                yield from self.api.tweet_detail(cid)
            except Exception:
                yield tweet

    def _make_tweet(self, user, url, id_str):
        return {
            "id_str": id_str,
            "lang": None,
            "user": user,
            "entities": {},
            "extended_entities": {
                "media": [
                    {
                        "original_info": {},
                        "media_url": url,
                    },
                ],
            },
        }

    def metadata(self):
        """Return general metadata"""
        return {}

    def tweets(self):
        """Yield all relevant tweet objects"""

    def login(self):
        if self.cookies_check(self.cookies_names):
            return

        username, password = self._get_auth_info()
        if username:
            self.cookies_update(_login_impl(self, username, password))


class TwitterUserExtractor(TwitterExtractor):
    """Extractor for a Twitter user"""
    subcategory = "user"
    pattern = (BASE_PATTERN + r"/(?!search)(?:([^/?#]+)/?(?:$|[?#])"
               r"|i(?:/user/|ntent/user\?user_id=)(\d+))")
    test = (
        ("https://twitter.com/supernaturepics", {
            "options": (("include", "all"),),
            "pattern": r"https://twitter\.com/supernaturepics"
                       r"/(photo|header_photo|timeline|tweets"
                       r"|media|with_replies|likes)$",
            "count": 7,
        }),
        ("https://mobile.twitter.com/supernaturepics?p=i"),
        ("https://www.twitter.com/id:2976459548"),
        ("https://twitter.com/i/user/2976459548"),
        ("https://twitter.com/intent/user?user_id=2976459548"),
        ("https://fxtwitter.com/supernaturepics"),
        ("https://vxtwitter.com/supernaturepics"),
    )

    def __init__(self, match):
        TwitterExtractor.__init__(self, match)
        if user_id := match.group(2):
            self.user = f"id:{user_id}"

    def initialize(self):
        pass

    def items(self):
        base = f"{self.root}/{self.user}/"
        return self._dispatch_extractors(
            (
                (TwitterAvatarExtractor, f"{base}photo"),
                (TwitterBackgroundExtractor, f"{base}header_photo"),
                (TwitterTimelineExtractor, f"{base}timeline"),
                (TwitterTweetsExtractor, f"{base}tweets"),
                (TwitterMediaExtractor, f"{base}media"),
                (TwitterRepliesExtractor, f"{base}with_replies"),
                (TwitterLikesExtractor, f"{base}likes"),
            ),
            ("timeline",),
        )


class TwitterTimelineExtractor(TwitterExtractor):
    """Extractor for a Twitter user timeline"""
    subcategory = "timeline"
    pattern = BASE_PATTERN + r"/(?!search)([^/?#]+)/timeline(?!\w)"
    test = (
        ("https://twitter.com/supernaturepics/timeline", {
            "range": "1-40",
            "url": "c570ac1aae38ed1463be726cc46f31cac3d82a40",
        }),
        # suspended account (#2216)
        ("https://twitter.com/OptionalTypo/timeline", {
            "exception": exception.NotFoundError,
        }),
        # suspended account user ID
        ("https://twitter.com/id:772949683521978368/timeline", {
            "exception": exception.NotFoundError,
        }),
        ("https://mobile.twitter.com/supernaturepics/timeline#t"),
        ("https://www.twitter.com/id:2976459548/timeline"),
    )

    def tweets(self):
        # yield initial batch of (media) tweets
        tweet = None
        yield from self._select_tweet_source()(self.user)
        if tweet is None:
            return

        # build search query
        query = f'from:{self._user["name"]} max_id:{tweet["rest_id"]}'
        if self.retweets:
            query += " include:retweets include:nativeretweets"

        if not self.textonly:
            # try to search for media-only tweets
            tweet = None
            yield from self.api.search_timeline(f"{query} filter:links")
            if tweet is not None:
                return

        # yield unfiltered search results
        yield from self.api.search_timeline(query)

    def _select_tweet_source(self):
        strategy = self.config("strategy")
        if strategy is None or strategy == "auto":
            if self.retweets or self.textonly:
                return self.api.user_tweets
            else:
                return self.api.user_media
        if strategy == "tweets":
            return self.api.user_tweets
        if strategy == "with_replies":
            return self.api.user_tweets_and_replies
        return self.api.user_media


class TwitterTweetsExtractor(TwitterExtractor):
    """Extractor for Tweets from a user's Tweets timeline"""
    subcategory = "tweets"
    pattern = BASE_PATTERN + r"/(?!search)([^/?#]+)/tweets(?!\w)"
    test = (
        ("https://twitter.com/supernaturepics/tweets", {
            "range": "1-40",
            "url": "c570ac1aae38ed1463be726cc46f31cac3d82a40",
        }),
        ("https://mobile.twitter.com/supernaturepics/tweets#t"),
        ("https://www.twitter.com/id:2976459548/tweets"),
    )

    def tweets(self):
        return self.api.user_tweets(self.user)


class TwitterRepliesExtractor(TwitterExtractor):
    """Extractor for Tweets from a user's timeline including replies"""
    subcategory = "replies"
    pattern = BASE_PATTERN + r"/(?!search)([^/?#]+)/with_replies(?!\w)"
    test = (
        ("https://twitter.com/supernaturepics/with_replies", {
            "range": "1-40",
            "url": "c570ac1aae38ed1463be726cc46f31cac3d82a40",
        }),
        ("https://mobile.twitter.com/supernaturepics/with_replies#t"),
        ("https://www.twitter.com/id:2976459548/with_replies"),
    )

    def tweets(self):
        return self.api.user_tweets_and_replies(self.user)


class TwitterMediaExtractor(TwitterExtractor):
    """Extractor for Tweets from a user's Media timeline"""
    subcategory = "media"
    pattern = BASE_PATTERN + r"/(?!search)([^/?#]+)/media(?!\w)"
    test = (
        ("https://twitter.com/supernaturepics/media", {
            "range": "1-40",
            "url": "c570ac1aae38ed1463be726cc46f31cac3d82a40",
        }),
        ("https://mobile.twitter.com/supernaturepics/media#t"),
        ("https://www.twitter.com/id:2976459548/media"),
    )

    def tweets(self):
        return self.api.user_media(self.user)


class TwitterLikesExtractor(TwitterExtractor):
    """Extractor for liked tweets"""
    subcategory = "likes"
    pattern = BASE_PATTERN + r"/(?!search)([^/?#]+)/likes(?!\w)"
    test = ("https://twitter.com/supernaturepics/likes",)

    def metadata(self):
        return {"user_likes": self.user}

    def tweets(self):
        return self.api.user_likes(self.user)

    def _transform_tweet(self, tweet):
        tdata = TwitterExtractor._transform_tweet(self, tweet)
        tdata["date_liked"] = text.parse_timestamp(
            (int(tweet["sortIndex"]) >> 20) // 1000)
        return tdata


class TwitterBookmarkExtractor(TwitterExtractor):
    """Extractor for bookmarked tweets"""
    subcategory = "bookmark"
    pattern = BASE_PATTERN + r"/i/bookmarks()"
    test = ("https://twitter.com/i/bookmarks",)

    def tweets(self):
        return self.api.user_bookmarks()

    def _transform_tweet(self, tweet):
        tdata = TwitterExtractor._transform_tweet(self, tweet)
        tdata["date_bookmarked"] = text.parse_timestamp(
            (int(tweet["sortIndex"]) >> 20) // 1000)
        return tdata


class TwitterListExtractor(TwitterExtractor):
    """Extractor for Twitter lists"""
    subcategory = "list"
    pattern = BASE_PATTERN + r"/i/lists/(\d+)/?$"
    test = ("https://twitter.com/i/lists/784214683683127296", {
        "range": "1-40",
        "count": 40,
        "archive": False,
    })

    def tweets(self):
        return self.api.list_latest_tweets_timeline(self.user)


class TwitterListMembersExtractor(TwitterExtractor):
    """Extractor for members of a Twitter list"""
    subcategory = "list-members"
    pattern = BASE_PATTERN + r"/i/lists/(\d+)/members"
    test = ("https://twitter.com/i/lists/784214683683127296/members", {
        "pattern": TwitterTimelineExtractor.pattern,
        "range": "1-40",
        "count": 40,
    })

    def items(self):
        self.login()
        return self._users_result(TwitterAPI(self).list_members(self.user))


class TwitterFollowingExtractor(TwitterExtractor):
    """Extractor for followed users"""
    subcategory = "following"
    pattern = BASE_PATTERN + r"/(?!search)([^/?#]+)/following(?!\w)"
    test = (
        ("https://twitter.com/supernaturepics/following"),
        ("https://www.twitter.com/id:2976459548/following"),
    )

    def items(self):
        self.login()
        return self._users_result(TwitterAPI(self).user_following(self.user))


class TwitterSearchExtractor(TwitterExtractor):
    """Extractor for Twitter search results"""
    subcategory = "search"
    pattern = BASE_PATTERN + r"/search/?\?(?:[^&#]+&)*q=([^&#]+)"
    test = ("https://twitter.com/search?q=nature", {
        "range": "1-20",
        "count": 20,
        "archive": False,
    })

    def metadata(self):
        return {"search": text.unquote(self.user)}

    def tweets(self):
        query = text.unquote(self.user.replace("+", " "))

        user = None
        for item in query.split():
            item = item.strip("()")
            if item.startswith("from:"):
                if user:
                    user = None
                    break
                else:
                    user = item[5:]

        if user is not None:
            try:
                self._assign_user(self.api.user_by_screen_name(user))
            except KeyError:
                pass

        return self.api.search_timeline(query)


class TwitterHashtagExtractor(TwitterExtractor):
    """Extractor for Twitter hashtags"""
    subcategory = "hashtag"
    pattern = BASE_PATTERN + r"/hashtag/([^/?#]+)"
    test = ("https://twitter.com/hashtag/nature", {
        "pattern": TwitterSearchExtractor.pattern,
        "url": "3571c3a53b7647ea35517041fdc17f77ec5b2cb9",
    })

    def items(self):
        url = f"{self.root}/search?q=%23{self.user}"
        data = {"_extractor": TwitterSearchExtractor}
        yield Message.Queue, url, data


class TwitterEventExtractor(TwitterExtractor):
    """Extractor for Tweets from a Twitter Event"""
    subcategory = "event"
    directory_fmt = ("{category}", "Events",
                     "{event[id]} {event[short_title]}")
    pattern = BASE_PATTERN + r"/i/events/(\d+)"
    test = ("https://twitter.com/i/events/1484669206993903616", {
        "range": "1-20",
        "count": ">=1",
    })

    def metadata(self):
        return {"event": self.api.live_event(self.user)}

    def tweets(self):
        return self.api.live_event_timeline(self.user)


class TwitterTweetExtractor(TwitterExtractor):
    """Extractor for images from individual tweets"""
    subcategory = "tweet"
    pattern = BASE_PATTERN + r"/([^/?#]+|i/web)/status/(\d+)"
    test = (
        ("https://twitter.com/supernaturepics/status/604341487988576256", {
            "url": "88a40f7d25529c2501c46f2218f9e0de9aa634b4",
            "content": "ab05e1d8d21f8d43496df284d31e8b362cd3bcab",
        }),
        # 4 images
        ("https://twitter.com/perrypumas/status/894001459754180609", {
            "url": "3a2a43dc5fb79dd5432c701d8e55e87c4e551f47",
        }),
        # video
        ("https://twitter.com/perrypumas/status/1065692031626829824", {
            "pattern": r"https://video.twimg.com/ext_tw_video/.+\.mp4\?tag=5",
        }),
        # content with emoji, newlines, hashtags (#338)
        ("https://twitter.com/playpokemon/status/1263832915173048321", {
            "keyword": {"content": (
                r"re:Gear up for #PokemonSwordShieldEX with special Mystery "
                "Gifts! \n\nYou’ll be able to receive four Galarian form "
                "Pokémon with Hidden Abilities, plus some very useful items. "
                "It’s our \\(Mystery\\) Gift to you, Trainers! \n\n❓🎁➡️ "
            )},
        }),
        # Reply to deleted tweet (#403, #838)
        ("https://twitter.com/i/web/status/1170041925560258560", {
            "pattern": r"https://pbs.twimg.com/media/EDzS7VrU0AAFL4_",
        }),
        # 'replies' option (#705)
        ("https://twitter.com/i/web/status/1170041925560258560", {
            "options": (("replies", False),),
            "count": 0,
        }),
        # 'replies' to self (#1254)
        ("https://twitter.com/i/web/status/1424882930803908612", {
            "options": (("replies", "self"),),
            "count": 4,
            "keyword": {"user": {
                "description": "re:business email-- rhettaro.bloom@gmail.com "
                               "patreon- http://patreon.com/Princecanary",
                "url": "http://princecanary.tumblr.com",
            }},
        }),
        ("https://twitter.com/i/web/status/1424898916156284928", {
            "options": (("replies", "self"),),
            "count": 1,
        }),
        # "quoted" option (#854)
        ("https://twitter.com/StobiesGalaxy/status/1270755918330896395", {
            "options": (("quoted", True),),
            "pattern": r"https://pbs\.twimg\.com/media/Ea[KG].+=jpg",
            "count": 8,
        }),
        # quoted tweet (#526, #854)
        ("https://twitter.com/StobiesGalaxy/status/1270755918330896395", {
            "pattern": r"https://pbs\.twimg\.com/media/EaK.+=jpg",
            "count": 4,
        }),
        # different 'user' and 'author' in quoted Tweet (#3922)
        ("https://twitter.com/web/status/1644907989109751810", {
            "keyword": {
                "author": {"id": 321629993         , "name": "Cakes_Comics"},
                "user"  : {"id": 718928225360080897, "name": "StobiesGalaxy"},
            },
        }),
        # TwitPic embeds (#579)
        ("https://twitter.com/i/web/status/112900228289540096", {
            "options": (("twitpic", True), ("cards", False)),
            "pattern": r"https://\w+.cloudfront.net/photos/large/\d+.jpg",
            "count": 2,  # 1 duplicate
        }),
        # TwitPic URL not in 'urls' (#3792)
        ("https://twitter.com/shimoigusaP/status/8138669971", {
            "options": (("twitpic", True),),
            "pattern": r"https://\w+.cloudfront.net/photos/large/\d+.png",
            "count": 1,
        }),
        # Twitter card (#1005)
        ("https://twitter.com/billboard/status/1306599586602135555", {
            "options": (("cards", True),),
            "pattern": r"https://pbs.twimg.com/card_img/\d+/",
        }),
        # unified_card image_website (#2875)
        ("https://twitter.com/i/web/status/1561674543323910144", {
            "options": (("cards", True),),
            "pattern": r"https://pbs\.twimg\.com/media/F.+=jpg",
        }),
        # unified_card image_carousel_website
        ("https://twitter.com/doax_vv_staff/status/1479438945662685184", {
            "options": (("cards", True),),
            "pattern": r"https://pbs\.twimg\.com/media/F.+=png",
            "count": 6,
        }),
        # unified_card video_website (#2875)
        ("https://twitter.com/bang_dream_1242/status/1561548715348746241", {
            "options": (("cards", True),),
            "pattern": r"https://video\.twimg\.com/amplify_video"
                       r"/1560607284333449216/vid/720x720/\w+\.mp4",
        }),
        # unified_card without type
        ("https://twitter.com/i/web/status/1466183847628865544", {
            "count": 0,
        }),
        # 'cards-blacklist' option
        ("https://twitter.com/i/web/status/1571141912295243776", {
            "options": (("cards", "ytdl"),
                        ("cards-blacklist", ("twitch.tv",))),
            "count": 0,
        }),
        # retweet
        ("https://twitter.com/jessica_3978/status/1296304589591810048", {
            "options": (("retweets", True),),
            "count": 2,
            "keyword": {
                "tweet_id"  : 1296304589591810048,
                "retweet_id": 1296296016002547713,
                "date"      : "dt:2020-08-20 04:34:32",
            },
        }),
        # original retweets (#1026)
        ("https://twitter.com/jessica_3978/status/1296304589591810048", {
            "options": (("retweets", "original"),),
            "count": 2,
            "keyword": {
                "tweet_id"  : 1296296016002547713,
                "retweet_id": 1296296016002547713,
                "date"      : "dt:2020-08-20 04:00:28",
            },
        }),
        # all Tweets from a 'conversation' (#1319)
        ("https://twitter.com/supernaturepics/status/604341487988576256", {
            "options": (("conversations", True),),
            "count": 5,
        }),
        # retweet with missing media entities (#1555)
        ("https://twitter.com/morino_ya/status/1392763691599237121", {
            "options": (("retweets", True),),
            "count": 0,  # private
        }),
        # deleted quote tweet (#2225)
        ("https://twitter.com/i/web/status/1460044411165888515", {
            "count": 0,
        }),
        # "Misleading" content
        ("https://twitter.com/i/web/status/1486373748911575046", {
            "count": 4,
        }),
        # age-restricted (#2354)
        ("https://twitter.com/mightbecursed/status/1492954264909479936", {
            "options": (("syndication", True),),
            "keyword": {"date": "dt:2022-02-13 20:10:09"},
            "count": 1,
        }),
        # media alt texts / descriptions (#2617)
        ("https://twitter.com/my0nruri/status/1528379296041299968", {
            "keyword": {"description": "oc"}
        }),
        # '?format=...&name=...'-style URLs
        ("https://twitter.com/poco_dandy/status/1150646424461176832", {
            "options": (("cards", True),),
            "pattern": r"https://pbs.twimg.com/card_img/157\d+/[\w-]+"
                       r"\?format=(jpg|png)&name=orig$",
            "range": "1-2",
        }),
        # note tweet with long 'content'
        ("https://twitter.com/i/web/status/1629193457112686592", {
            "keyword": {
                "content": """\
BREAKING - DEADLY LIES: Independent researchers at Texas A&M University have \
just contradicted federal government regulators, saying that toxic air \
pollutants in East Palestine, Ohio, could pose long-term risks. \n\nThe \
Washington Post writes, "Three weeks after the toxic train derailment in \
Ohio, an analysis of Environmental Protection Agency data has found nine air \
pollutants at levels that could raise long-term health concerns in and around \
East Palestine, according to an independent analysis. \n\n\"The analysis by \
Texas A&M University seems to contradict statements by state and federal \
regulators that air near the crash site is completely safe, despite residents \
complaining about rashes, breathing problems and other health effects." \
Your reaction.""",
            },
        }),
    )

    def __init__(self, match):
        TwitterExtractor.__init__(self, match)
        self.tweet_id = match.group(2)

    def tweets(self):
        if conversations := self.config("conversations"):
            self._accessible = (conversations == "accessible")
            return self._tweets_conversation(self.tweet_id)

        endpoint = self.config("tweet-endpoint")
        if endpoint == "detail" or endpoint in (None, "auto") and \
                    self.api.headers["x-twitter-auth-type"]:
            return self._tweets_detail(self.tweet_id)

        return self._tweets_single(self.tweet_id)

    def _tweets_single(self, tweet_id):
        tweets = []

        tweet = self.api.tweet_result_by_rest_id(tweet_id)
        self._assign_user(tweet["core"]["user_results"]["result"])

        while True:
            tweets.append(tweet)
            if tweet_id := tweet["legacy"].get("quoted_status_id_str"):
                tweet = self.api.tweet_result_by_rest_id(tweet_id)

            else:
                break
        return tweets

    def _tweets_detail(self, tweet_id):
        tweets = []

        for tweet in self.api.tweet_detail(tweet_id):
            if tweet["rest_id"] == tweet_id or \
                    tweet.get("_retweet_id_str") == tweet_id:
                if self._user_obj is None:
                    self._assign_user(tweet["core"]["user_results"]["result"])
                tweets.append(tweet)

                tweet_id = tweet["legacy"].get("quoted_status_id_str")
                if not tweet_id:
                    break

        return tweets

    def _tweets_conversation(self, tweet_id):
        tweets = self.api.tweet_detail(tweet_id)
        buffer = []

        for tweet in tweets:
            buffer.append(tweet)
            if tweet["rest_id"] == tweet_id or \
                        tweet.get("_retweet_id_str") == tweet_id:
                self._assign_user(tweet["core"]["user_results"]["result"])
                break
        else:
            # initial Tweet not accessible
            return () if self._accessible else buffer
        return itertools.chain(buffer, tweets)


class TwitterAvatarExtractor(TwitterExtractor):
    subcategory = "avatar"
    filename_fmt = "avatar {date}.{extension}"
    archive_fmt = "AV_{user[id]}_{date}"
    pattern = BASE_PATTERN + r"/(?!search)([^/?#]+)/photo"
    test = (
        ("https://twitter.com/supernaturepics/photo", {
            "pattern": r"https://pbs\.twimg\.com/profile_images"
                       r"/554585280938659841/FLVAlX18\.jpeg",
            "keyword": {
                "date": "dt:2015-01-12 10:26:49",
                "extension": "jpeg",
                "filename": "FLVAlX18",
                "tweet_id": 554585280938659841,
            },
        }),
        ("https://twitter.com/User16/photo", {
            "count": 0,
        }),
    )

    def tweets(self):
        self.api._user_id_by_screen_name(self.user)
        user = self._user_obj
        url = user["legacy"]["profile_image_url_https"]

        if url == ("https://abs.twimg.com/sticky"
                   "/default_profile_images/default_profile_normal.png"):
            return ()

        url = url.replace("_normal.", ".")
        id_str = url.rsplit("/", 2)[1]

        return (self._make_tweet(user, url, id_str),)


class TwitterBackgroundExtractor(TwitterExtractor):
    subcategory = "background"
    filename_fmt = "background {date}.{extension}"
    archive_fmt = "BG_{user[id]}_{date}"
    pattern = BASE_PATTERN + r"/(?!search)([^/?#]+)/header_photo"
    test = (
        ("https://twitter.com/supernaturepics/header_photo", {
            "pattern": r"https://pbs\.twimg\.com/profile_banners"
                       r"/2976459548/1421058583",
            "keyword": {
                "date": "dt:2015-01-12 10:29:43",
                "filename": "1421058583",
                "tweet_id": 554586009367478272,
            },
        }),
        ("https://twitter.com/User16/header_photo", {
            "count": 0,
        }),
    )

    def tweets(self):
        self.api._user_id_by_screen_name(self.user)
        user = self._user_obj

        try:
            url = user["legacy"]["profile_banner_url"]
            _, timestamp = url.rsplit("/", 1)
        except (KeyError, ValueError):
            return ()

        id_str = str((int(timestamp) * 1000 - 1288834974657) << 22)
        return (self._make_tweet(user, url, id_str),)


class TwitterImageExtractor(Extractor):
    category = "twitter"
    subcategory = "image"
    pattern = r"https?://pbs\.twimg\.com/media/([\w-]+)(?:\?format=|\.)(\w+)"
    test = (
        ("https://pbs.twimg.com/media/EqcpviCVoAAG-QG?format=jpg&name=orig", {
            "options": (("size", "4096x4096,orig"),),
            "url": "cb3042a6f6826923da98f0d2b66c427e9385114c",
        }),
        ("https://pbs.twimg.com/media/EqcpviCVoAAG-QG.jpg:orig"),
    )

    def __init__(self, match):
        Extractor.__init__(self, match)
        self.id, self.fmt = match.groups()
        TwitterExtractor._init_sizes(self)

    def items(self):
        base = f"https://pbs.twimg.com/media/{self.id}?format={self.fmt}&name="

        data = {
            "filename": self.id,
            "extension": self.fmt,
            "_fallback": TwitterExtractor._image_fallback(self, base),
        }

        yield Message.Directory, data
        yield Message.Url, base + self._size_image, data


class TwitterAPI():

    def __init__(self, extractor):
        self.extractor = extractor

        self.root = "https://twitter.com/i/api"
        self._nsfw_warning = True
        self._syndication = self.extractor.syndication
        self._json_dumps = json.JSONEncoder(separators=(",", ":")).encode

        cookies = extractor.cookies
        cookies_domain = extractor.cookies_domain

        csrf = extractor.config("csrf")
        if csrf is None or csrf == "cookies":
            csrf_token = cookies.get("ct0", domain=cookies_domain)
        else:
            csrf_token = None
        if not csrf_token:
            csrf_token = util.generate_token()
            cookies.set("ct0", csrf_token, domain=cookies_domain)

        auth_token = cookies.get("auth_token", domain=cookies_domain)

        self.headers = {
            "Accept": "*/*",
            "authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejR"
                             "COuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu"
                             "4FA33AGWWjCpTnA",
            "x-guest-token": None,
            "x-twitter-auth-type": "OAuth2Session" if auth_token else None,
            "x-twitter-client-language": "en",
            "x-twitter-active-user": "yes",
            "x-csrf-token": csrf_token,
            "Referer": "https://twitter.com/",
        }
        self.params = {
            "include_profile_interstitial_type": "1",
            "include_blocking": "1",
            "include_blocked_by": "1",
            "include_followed_by": "1",
            "include_want_retweets": "1",
            "include_mute_edge": "1",
            "include_can_dm": "1",
            "include_can_media_tag": "1",
            "include_ext_has_nft_avatar": "1",
            "include_ext_is_blue_verified": "1",
            "include_ext_verified_type": "1",
            "skip_status": "1",
            "cards_platform": "Web-12",
            "include_cards": "1",
            "include_ext_alt_text": "true",
            "include_ext_limited_action_results": "false",
            "include_quote_count": "true",
            "include_reply_count": "1",
            "tweet_mode": "extended",
            "include_ext_collab_control": "true",
            "include_ext_views": "true",
            "include_entities": "true",
            "include_user_entities": "true",
            "include_ext_media_color": "true",
            "include_ext_media_availability": "true",
            "include_ext_sensitive_media_warning": "true",
            "include_ext_trusted_friends_metadata": "true",
            "send_error_codes": "true",
            "simple_quoted_tweet": "true",
            "q": None,
            "count": "100",
            "query_source": None,
            "cursor": None,
            "pc": None,
            "spelling_corrections": None,
            "include_ext_edit_control": "true",
            "ext": "mediaStats,highlightedLabel,hasNftAvatar,voiceInfo,"
                   "enrichments,superFollowMetadata,unmentionInfo,editControl,"
                   "collab_control,vibe",
        }
        self.features = {
            "hidden_profile_likes_enabled": False,
            "responsive_web_graphql_exclude_directive_enabled": True,
            "verified_phone_label_enabled": False,
            "subscriptions_verification_info_verified_since_enabled": True,
            "highlights_tweets_tab_ui_enabled": True,
            "creator_subscriptions_tweet_preview_api_enabled": True,
            "responsive_web_graphql_"
            "skip_user_profile_image_extensions_enabled": False,
            "responsive_web_graphql_timeline_navigation_enabled": True,
        }
        self.features_pagination = {
            "rweb_lists_timeline_redesign_enabled": True,
            "responsive_web_graphql_exclude_directive_enabled": True,
            "verified_phone_label_enabled": False,
            "creator_subscriptions_tweet_preview_api_enabled": True,
            "responsive_web_graphql_timeline_navigation_enabled": True,
            "responsive_web_graphql_skip_user_profile_"
            "image_extensions_enabled": False,
            "tweetypie_unmention_optimization_enabled": True,
            "responsive_web_edit_tweet_api_enabled": True,
            "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
            "view_counts_everywhere_api_enabled": True,
            "longform_notetweets_consumption_enabled": True,
            "tweet_awards_web_tipping_enabled": False,
            "freedom_of_speech_not_reach_fetch_enabled": True,
            "standardized_nudges_misinfo": True,
            "tweet_with_visibility_results_prefer_gql_"
            "limited_actions_policy_enabled": False,
            "interactive_text_enabled": True,
            "responsive_web_text_conversations_enabled": False,
            "longform_notetweets_rich_text_read_enabled": True,
            "longform_notetweets_inline_media_enabled": False,
            "responsive_web_enhance_cards_enabled": False,
        }

    def tweet_result_by_rest_id(self, tweet_id):
        endpoint = "/graphql/2ICDjqPd81tulZcYrtpTuQ/TweetResultByRestId"
        params = {
            "variables": self._json_dumps({
                "tweetId": tweet_id,
                "withCommunity": False,
                "includePromotedContent": False,
                "withVoice": False,
            }),
            "features": self._json_dumps({
                "creator_subscriptions_tweet_preview_api_enabled": True,
                "tweetypie_unmention_optimization_enabled": True,
                "responsive_web_edit_tweet_api_enabled": True,
                "graphql_is_translatable_rweb_tweet_is_translatable_enabled":
                    True,
                "view_counts_everywhere_api_enabled": True,
                "longform_notetweets_consumption_enabled": True,
                "responsive_web_twitter_article_tweet_consumption_enabled":
                    False,
                "tweet_awards_web_tipping_enabled": False,
                "freedom_of_speech_not_reach_fetch_enabled": True,
                "standardized_nudges_misinfo": True,
                "tweet_with_visibility_results_prefer_gql_"
                "limited_actions_policy_enabled": True,
                "longform_notetweets_rich_text_read_enabled": True,
                "longform_notetweets_inline_media_enabled": True,
                "responsive_web_graphql_exclude_directive_enabled": True,
                "verified_phone_label_enabled": False,
                "responsive_web_media_download_video_enabled": False,
                "responsive_web_graphql_skip_user_profile_"
                "image_extensions_enabled": False,
                "responsive_web_graphql_timeline_navigation_enabled": True,
                "responsive_web_enhance_cards_enabled": False,
            }),
            "fieldToggles": self._json_dumps({
                "withArticleRichContentState": False,
            }),
        }
        return self._call(endpoint, params)["data"]["tweetResult"]["result"]

    def tweet_detail(self, tweet_id):
        endpoint = "/graphql/JlLZj42Ltr2qwjasw-l5lQ/TweetDetail"
        variables = {
            "focalTweetId": tweet_id,
            "referrer": "profile",
            "with_rux_injections": False,
            "includePromotedContent": True,
            "withCommunity": True,
            "withQuickPromoteEligibilityTweetFields": True,
            "withBirdwatchNotes": True,
            "withVoice": True,
            "withV2Timeline": True,
        }
        return self._pagination_tweets(
            endpoint, variables, ("threaded_conversation_with_injections_v2",))

    def user_tweets(self, screen_name):
        endpoint = "/graphql/-AY51QoFpVf-w7TxjQ6lpw/UserTweets"
        variables = {
            "userId": self._user_id_by_screen_name(screen_name),
            "count": 100,
            "includePromotedContent": True,
            "withQuickPromoteEligibilityTweetFields": True,
            "withVoice": True,
            "withV2Timeline": True,
        }
        return self._pagination_tweets(endpoint, variables)

    def user_tweets_and_replies(self, screen_name):
        endpoint = "/graphql/urrCZMyyIh1FkSFi2cdPUA/UserTweetsAndReplies"
        variables = {
            "userId": self._user_id_by_screen_name(screen_name),
            "count": 100,
            "includePromotedContent": True,
            "withCommunity": True,
            "withVoice": True,
            "withV2Timeline": True,
        }
        return self._pagination_tweets(endpoint, variables)

    def user_media(self, screen_name):
        endpoint = "/graphql/lo965xQZdN2-eSM1Jc-W_A/UserMedia"
        variables = {
            "userId": self._user_id_by_screen_name(screen_name),
            "count": 100,
            "includePromotedContent": False,
            "withClientEventToken": False,
            "withBirdwatchNotes": False,
            "withVoice": True,
            "withV2Timeline": True,
        }
        return self._pagination_tweets(endpoint, variables)

    def user_media_legacy(self, screen_name):
        endpoint = "/graphql/nRybED9kRbN-TOWioHq1ng/UserMedia"
        variables = {
            "userId": self._user_id_by_screen_name(screen_name),
            "count": 100,
            "includePromotedContent": False,
            "withSuperFollowsUserFields": True,
            "withBirdwatchPivots": False,
            "withSuperFollowsTweetFields": True,
            "withClientEventToken": False,
            "withBirdwatchNotes": False,
            "withVoice": True,
            "withV2Timeline": False,
            "__fs_interactive_text": False,
            "__fs_dont_mention_me_view_api_enabled": False,
        }
        return self._pagination_tweets(
            endpoint, variables, ("user", "result", "timeline", "timeline"),
            features=False)

    def user_likes(self, screen_name):
        endpoint = "/graphql/6JET1d0iHsIzW0Zjs3OOwQ/Likes"
        variables = {
            "userId": self._user_id_by_screen_name(screen_name),
            "count": 100,
            "includePromotedContent": False,
            "withClientEventToken": False,
            "withBirdwatchNotes": False,
            "withVoice": True,
            "withV2Timeline": True,
        }
        return self._pagination_tweets(endpoint, variables)

    def user_bookmarks(self):
        endpoint = "/graphql/YNtYqNuki6_oiVwx0uP8mQ/Bookmarks"
        variables = {
            "count": 100,
        }
        features = self.features_pagination.copy()
        features["graphql_timeline_v2_bookmark_timeline"] = True
        return self._pagination_tweets(
            endpoint, variables, ("bookmark_timeline_v2", "timeline"), False,
            features=features)

    def list_latest_tweets_timeline(self, list_id):
        endpoint = "/graphql/ZBbXrl37E6za5ml-DIpmgg/ListLatestTweetsTimeline"
        variables = {
            "listId": list_id,
            "count": 100,
        }
        return self._pagination_tweets(
            endpoint, variables, ("list", "tweets_timeline", "timeline"))

    def search_timeline(self, query):
        endpoint = "/graphql/7jT5GT59P8IFjgxwqnEdQw/SearchTimeline"
        variables = {
            "rawQuery": query,
            "count": 20,
            "product": "Latest",
            "withDownvotePerspective": False,
            "withReactionsMetadata": False,
            "withReactionsPerspective": False,
        }
        features = self.features_pagination.copy()
        features["blue_business_profile_image_shape_enabled"] = False
        features["vibe_api_enabled"] = True
        return self._pagination_tweets(
            endpoint, variables,
            ("search_by_raw_query", "search_timeline", "timeline"),
            features=features)

    def live_event_timeline(self, event_id):
        endpoint = f"/2/live_event/timeline/{event_id}.json"
        params = self.params.copy()
        params["timeline_id"] = "recap"
        params["urt"] = "true"
        params["get_annotations"] = "true"
        return self._pagination_legacy(endpoint, params)

    def live_event(self, event_id):
        endpoint = f"/1.1/live_event/1/{event_id}/timeline.json"
        params = self.params.copy()
        params["count"] = "0"
        params["urt"] = "true"
        return (self._call(endpoint, params)
                ["twitter_objects"]["live_events"][event_id])

    def list_by_rest_id(self, list_id):
        endpoint = "/graphql/AmCdeFUvlrKAO96yHr-GCg/ListByRestId"
        params = {
            "variables": self._json_dumps({
                "listId": list_id,
            }),
            "features": self._json_dumps(self.features),
        }
        try:
            return self._call(endpoint, params)["data"]["list"]
        except KeyError:
            raise exception.NotFoundError("list")

    def list_members(self, list_id):
        endpoint = "/graphql/a_ZQomd3MMk1crWkeiQBPg/ListMembers"
        variables = {
            "listId": list_id,
            "count": 100,
            "withSafetyModeUserFields": True,
        }
        return self._pagination_users(
            endpoint, variables, ("list", "members_timeline", "timeline"))

    def user_following(self, screen_name):
        endpoint = "/graphql/JPZiqKjET7_M1r5Tlr8pyA/Following"
        variables = {
            "userId": self._user_id_by_screen_name(screen_name),
            "count": 100,
            "includePromotedContent": False,
        }
        return self._pagination_users(endpoint, variables)

    def user_by_rest_id(self, rest_id):
        endpoint = "/graphql/1YAM811Q8Ry4XyPpJclURQ/UserByRestId"
        features = self.features.copy()
        features["blue_business_profile_image_shape_enabled"] = True
        params = {
            "variables": self._json_dumps({
                "userId": rest_id,
                "withSafetyModeUserFields": True,
            }),
            "features": self._json_dumps(features),
        }
        return self._call(endpoint, params)["data"]["user"]["result"]

    def user_by_screen_name(self, screen_name):
        endpoint = "/graphql/XA6F1nJELYg65hxOC2Ekmg/UserByScreenName"
        params = {
            "variables": self._json_dumps({
                "screen_name": screen_name,
                "withSafetyModeUserFields": True,
            }),
            "features": self._json_dumps(self.features),
        }
        return self._call(endpoint, params)["data"]["user"]["result"]

    def _user_id_by_screen_name(self, screen_name):
        user = ()
        try:
            if screen_name.startswith("id:"):
                user = self.user_by_rest_id(screen_name[3:])
            else:
                user = self.user_by_screen_name(screen_name)
            self.extractor._assign_user(user)
            return user["rest_id"]
        except KeyError:
            if "unavailable_message" in user:
                raise exception.NotFoundError(
                    f'{user["unavailable_message"].get("text")} ({user.get("reason")})',
                    False,
                )
            else:
                raise exception.NotFoundError("user")

    @cache(maxage=3600)
    def _guest_token(self):
        endpoint = "/1.1/guest/activate.json"
        self.extractor.log.info("Requesting guest token")
        return str(self._call(
            endpoint, None, "POST", False, "https://api.twitter.com",
        )["guest_token"])

    def _authenticate_guest(self):
        guest_token = self._guest_token()
        if guest_token != self.headers["x-guest-token"]:
            self.headers["x-guest-token"] = guest_token
            self.extractor.cookies.set(
                "gt", guest_token, domain=self.extractor.cookies_domain)

    def _call(self, endpoint, params, method="GET", auth=True, root=None):
        url = (root or self.root) + endpoint

        while True:
            if not self.headers["x-twitter-auth-type"] and auth:
                self._authenticate_guest()

            response = self.extractor.request(
                url, method=method, params=params,
                headers=self.headers, fatal=None)

            if csrf_token := response.cookies.get("ct0"):
                self.headers["x-csrf-token"] = csrf_token

            if response.status_code < 400:
                # success
                return response.json()

            if response.status_code == 429:
                # rate limit exceeded
                if self.extractor.config("ratelimit") == "abort":
                    raise exception.StopExtraction("Rate limit exceeded")

                until = response.headers.get("x-rate-limit-reset")
                seconds = None if until else 60
                self.extractor.wait(until=until, seconds=seconds)
                continue

            if response.status_code == 403 and \
                        not self.headers["x-twitter-auth-type"] and \
                        endpoint == "/2/search/adaptive.json":
                raise exception.AuthorizationError(
                    "Login required to access search results")

            # error
            try:
                data = response.json()
                errors = ", ".join(e["message"] for e in data["errors"])
            except ValueError:
                errors = response.text
            except Exception:
                errors = data.get("errors", "")

            raise exception.StopExtraction(
                "%s %s (%s)", response.status_code, response.reason, errors)

    def _pagination_legacy(self, endpoint, params):
        original_retweets = (self.extractor.retweets == "original")
        bottom = ("cursor-bottom-", "sq-cursor-bottom")

        while True:
            data = self._call(endpoint, params)

            instructions = data["timeline"]["instructions"]
            if not instructions:
                return

            tweets = data["globalObjects"]["tweets"]
            users = data["globalObjects"]["users"]
            tweet_id = cursor = None
            tweet_ids = []
            entries = ()

            # process instructions
            for instr in instructions:
                if "addEntries" in instr:
                    entries = instr["addEntries"]["entries"]
                elif "replaceEntry" in instr:
                    entry = instr["replaceEntry"]["entry"]
                    if entry["entryId"].startswith(bottom):
                        cursor = (entry["content"]["operation"]
                                  ["cursor"]["value"])

            # collect tweet IDs and cursor value
            for entry in entries:
                entry_startswith = entry["entryId"].startswith

                if entry_startswith(("tweet-", "sq-I-t-")):
                    tweet_ids.append(
                        entry["content"]["item"]["content"]["tweet"]["id"])

                elif entry_startswith("homeConversation-"):
                    tweet_ids.extend(
                        entry["content"]["timelineModule"]["metadata"]
                        ["conversationMetadata"]["allTweetIds"][::-1])

                elif entry_startswith(bottom):
                    cursor = entry["content"]["operation"]["cursor"]
                    if not cursor.get("stopOnEmptyResponse", True):
                        # keep going even if there are no tweets
                        tweet_id = True
                    cursor = cursor["value"]

                elif entry_startswith("conversationThread-"):
                    tweet_ids.extend(
                        item["entryId"][6:]
                        for item in entry["content"]["timelineModule"]["items"]
                        if item["entryId"].startswith("tweet-")
                    )

            # process tweets
            for tweet_id in tweet_ids:
                try:
                    tweet = tweets[tweet_id]
                except KeyError:
                    self.extractor.log.debug("Skipping %s (deleted)", tweet_id)
                    continue

                if "retweeted_status_id_str" in tweet:
                    retweet = tweets.get(tweet["retweeted_status_id_str"])
                    if original_retweets:
                        if not retweet:
                            continue
                        retweet["retweeted_status_id_str"] = retweet["id_str"]
                        retweet["_retweet_id_str"] = tweet["id_str"]
                        tweet = retweet
                    elif retweet:
                        tweet["author"] = users[retweet["user_id_str"]]
                        if "extended_entities" in retweet and \
                                    "extended_entities" not in tweet:
                            tweet["extended_entities"] = \
                                    retweet["extended_entities"]
                tweet["user"] = users[tweet["user_id_str"]]
                yield tweet

                if "quoted_status_id_str" in tweet:
                    if quoted := tweets.get(tweet["quoted_status_id_str"]):
                        quoted = quoted.copy()
                        quoted["author"] = users[quoted["user_id_str"]]
                        quoted["quoted_by"] = tweet["user"]["screen_name"]
                        quoted["quoted_by_id_str"] = tweet["id_str"]
                        yield quoted

            # stop on empty response
            if not cursor or (not tweets and not tweet_id):
                return
            params["cursor"] = cursor

    def _pagination_tweets(self, endpoint, variables,
                           path=None, stop_tweets=True, features=None):
        extr = self.extractor
        original_retweets = (extr.retweets == "original")
        pinned_tweet = extr.pinned

        params = {"variables": None}
        if features is None:
            features = self.features_pagination
        if features:
            params["features"] = self._json_dumps(features)

        while True:
            params["variables"] = self._json_dumps(variables)
            data = self._call(endpoint, params)["data"]

            try:
                if path is None:
                    instructions = (data["user"]["result"]["timeline_v2"]
                                    ["timeline"]["instructions"])
                else:
                    instructions = data
                    for key in path:
                        instructions = instructions[key]
                    instructions = instructions["instructions"]

                cursor = None
                entries = None
                for instr in instructions:
                    instr_type = instr.get("type")
                    if instr_type == "TimelineAddEntries":
                        entries = instr["entries"]
                    elif instr_type == "TimelineReplaceEntry":
                        entry = instr["entry"]
                        if entry["entryId"].startswith("cursor-bottom-"):
                            cursor = entry["content"]["value"]
                if entries is None:
                    if not cursor:
                        return
                    entries = ()

            except LookupError:
                extr.log.debug(data)

                if user := extr._user_obj:
                    user = user["legacy"]
                    if user.get("blocked_by"):
                        if self.headers["x-twitter-auth-type"] and \
                                    extr.config("logout"):
                            extr.cookies_file = None
                            del extr.cookies["auth_token"]
                            self.headers["x-twitter-auth-type"] = None
                            extr.log.info("Retrying API request as guest")
                            continue
                        raise exception.AuthorizationError(
                            f'{user["screen_name"]} blocked your account'
                        )
                    elif user.get("protected"):
                        raise exception.AuthorizationError(
                            f"""{user["screen_name"]}'s Tweets are protected"""
                        )

                raise exception.StopExtraction(
                    "Unable to retrieve Tweets from this timeline")

            tweets = []
            tweet = None

            if pinned_tweet:
                pinned_tweet = False
                if instructions[-1]["type"] == "TimelinePinEntry":
                    tweets.append(instructions[-1]["entry"])

            for entry in entries:
                esw = entry["entryId"].startswith

                if esw("tweet-"):
                    tweets.append(entry)
                elif esw(("homeConversation-",
                          "profile-conversation-",
                          "conversationthread-")):
                    tweets.extend(entry["content"]["items"])
                elif esw("tombstone-"):
                    item = entry["content"]["itemContent"]
                    item["tweet_results"] = \
                            {"result": {"tombstone": item["tombstoneInfo"]}}
                    tweets.append(entry)
                elif esw("cursor-bottom-"):
                    cursor = entry["content"]
                    if "itemContent" in cursor:
                        cursor = cursor["itemContent"]
                    if not cursor.get("stopOnEmptyResponse", True):
                        # keep going even if there are no tweets
                        tweet = True
                    cursor = cursor.get("value")

            for entry in tweets:
                try:
                    tweet = ((entry.get("content") or entry["item"])
                             ["itemContent"]["tweet_results"]["result"])
                    if "tombstone" in tweet:
                        tweet = self._process_tombstone(
                            entry, tweet["tombstone"])
                        if not tweet:
                            continue
                    if "tweet" in tweet:
                        tweet = tweet["tweet"]
                    legacy = tweet["legacy"]
                    tweet["sortIndex"] = entry.get("sortIndex")
                except KeyError:
                    extr.log.debug(
                        "Skipping %s (deleted)",
                        (entry.get("entryId") or "").rpartition("-")[2])
                    continue

                if "retweeted_status_result" in legacy:
                    retweet = legacy["retweeted_status_result"]["result"]
                    if "tweet" in retweet:
                        retweet = retweet["tweet"]
                    if original_retweets:
                        try:
                            retweet["legacy"]["retweeted_status_id_str"] = \
                                    retweet["rest_id"]
                            retweet["_retweet_id_str"] = tweet["rest_id"]
                            tweet = retweet
                        except KeyError:
                            continue
                    else:
                        try:
                            legacy["retweeted_status_id_str"] = \
                                    retweet["rest_id"]
                            tweet["author"] = \
                                    retweet["core"]["user_results"]["result"]

                            rtlegacy = retweet["legacy"]
                            if "extended_entities" in rtlegacy and \
                                        "extended_entities" not in legacy:
                                legacy["extended_entities"] = \
                                        rtlegacy["extended_entities"]
                            if "withheld_scope" in rtlegacy and \
                                        "withheld_scope" not in legacy:
                                legacy["withheld_scope"] = \
                                        rtlegacy["withheld_scope"]
                                legacy["full_text"] = rtlegacy["full_text"]
                        except KeyError:
                            pass

                yield tweet

                if "quoted_status_result" in tweet:
                    try:
                        quoted = tweet["quoted_status_result"]["result"]
                        quoted["legacy"]["quoted_by"] = (
                            tweet["core"]["user_results"]["result"]
                            ["legacy"]["screen_name"])
                        quoted["legacy"]["quoted_by_id_str"] = tweet["rest_id"]
                        quoted["sortIndex"] = entry.get("sortIndex")

                        yield quoted
                    except KeyError:
                        extr.log.debug(
                            "Skipping quote of %s (deleted)",
                            tweet.get("rest_id"))
                        continue

            if stop_tweets and not tweet:
                return
            if not cursor or cursor == variables.get("cursor"):
                return
            variables["cursor"] = cursor

    def _pagination_users(self, endpoint, variables, path=None):
        params = {"variables": None,
                  "features" : self._json_dumps(self.features_pagination)}

        while True:
            cursor = entry = None
            params["variables"] = self._json_dumps(variables)
            data = self._call(endpoint, params)["data"]

            try:
                if path is None:
                    instructions = (data["user"]["result"]["timeline"]
                                    ["timeline"]["instructions"])
                else:
                    for key in path:
                        data = data[key]
                    instructions = data["instructions"]
            except KeyError:
                return

            for instr in instructions:
                if instr["type"] == "TimelineAddEntries":
                    for entry in instr["entries"]:
                        if entry["entryId"].startswith("user-"):
                            try:
                                user = (entry["content"]["itemContent"]
                                        ["user_results"]["result"])
                            except KeyError:
                                pass
                            else:
                                if "rest_id" in user:
                                    yield user
                        elif entry["entryId"].startswith("cursor-bottom-"):
                            cursor = entry["content"]["value"]

            if not cursor or cursor.startswith(("-1|", "0|")) or not entry:
                return
            variables["cursor"] = cursor

    def _process_tombstone(self, entry, tombstone):
        text = (tombstone.get("richText") or tombstone["text"])["text"]
        tweet_id = entry["entryId"].rpartition("-")[2]

        if text.startswith("Age-restricted"):
            if self._syndication:
                return self._syndication_tweet(tweet_id)
            elif self._nsfw_warning:
                self._nsfw_warning = False
                self.extractor.log.warning('"%s"', text)

        self.extractor.log.debug("Skipping %s (\"%s\")", tweet_id, text)

    def _syndication_tweet(self, tweet_id):
        base_url = "https://cdn.syndication.twimg.com/tweet-result?id="
        tweet = self.extractor.request(base_url + tweet_id).json()

        tweet["user"]["description"] = ""
        tweet["user"]["entities"] = {"description": {}}
        tweet["user_id_str"] = tweet["user"]["id_str"]

        if tweet["id_str"] != tweet_id:
            tweet["retweeted_status_id_str"] = tweet["id_str"]
            tweet["id_str"] = retweet_id = tweet_id
        else:
            retweet_id = None

        # assume 'conversation_id' is the same as 'id' when the tweet
        # is not a reply
        if "conversation_id_str" not in tweet and \
                "in_reply_to_status_id_str" not in tweet:
            tweet["conversation_id_str"] = tweet["id_str"]

        if int(tweet_id) < 300000000000000:
            tweet["created_at"] = text.parse_datetime(
                tweet["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ").strftime(
                "%a %b %d %H:%M:%S +0000 %Y")

        if "video" in tweet:
            video = tweet["video"]
            video["variants"] = (max(
                (v for v in video["variants"] if v["type"] == "video/mp4"),
                key=lambda v: text.parse_int(
                    v["src"].split("/")[-2].partition("x")[0])
            ),)
            video["variants"][0]["url"] = video["variants"][0]["src"]
            tweet["extended_entities"] = {"media": [{
                "video_info"   : video,
                "original_info": {"width" : 0, "height": 0},
            }]}
        elif "photos" in tweet:
            for p in tweet["photos"]:
                p["media_url_https"] = p["url"]
                p["original_info"] = {
                    "width" : p["width"],
                    "height": p["height"],
                }
            tweet["extended_entities"] = {"media": tweet["photos"]}

        return {
            "rest_id": tweet["id_str"],
            "legacy" : tweet,
            "core"   : {"user_results": {"result": tweet["user"]}},
            "_retweet_id_str": retweet_id,
        }


@cache(maxage=360*86400, keyarg=1)
def _login_impl(extr, username, password):

    import re
    import random

    if re.fullmatch(r"[\w.%+-]+@[\w.-]+\.\w{2,}", username):
        extr.log.warning(
            "Login with email is no longer possible. "
            "You need to provide your username or phone number instead.")

    def process(response):
        try:
            data = response.json()
        except ValueError:
            data = {"errors": ({"message": "Invalid response"},)}
        else:
            if response.status_code < 400:
                return data["flow_token"]

        errors = []
        for error in data.get("errors") or ():
            msg = error.get("message")
            errors.append('"{}"'.format(msg) if msg else "Unknown error")
        extr.log.debug(response.text)
        raise exception.AuthenticationError(", ".join(errors))

    extr.cookies.clear()
    api = TwitterAPI(extr)
    api._authenticate_guest()
    headers = api.headers

    extr.log.info("Logging in as %s", username)

    # init
    data = {
        "input_flow_data": {
            "flow_context": {
                "debug_overrides": {},
                "start_location": {"location": "unknown"},
            },
        },
        "subtask_versions": {
            "action_list": 2,
            "alert_dialog": 1,
            "app_download_cta": 1,
            "check_logged_in_account": 1,
            "choice_selection": 3,
            "contacts_live_sync_permission_prompt": 0,
            "cta": 7,
            "email_verification": 2,
            "end_flow": 1,
            "enter_date": 1,
            "enter_email": 2,
            "enter_password": 5,
            "enter_phone": 2,
            "enter_recaptcha": 1,
            "enter_text": 5,
            "enter_username": 2,
            "generic_urt": 3,
            "in_app_notification": 1,
            "interest_picker": 3,
            "js_instrumentation": 1,
            "menu_dialog": 1,
            "notifications_permission_prompt": 2,
            "open_account": 2,
            "open_home_timeline": 1,
            "open_link": 1,
            "phone_verification": 4,
            "privacy_options": 1,
            "security_key": 3,
            "select_avatar": 4,
            "select_banner": 2,
            "settings_list": 7,
            "show_code": 1,
            "sign_up": 2,
            "sign_up_review": 4,
            "tweet_selection_urt": 1,
            "update_users": 1,
            "upload_media": 1,
            "user_recommendations_list": 4,
            "user_recommendations_urt": 1,
            "wait_spinner": 3,
            "web_modal": 1,
        },
    }
    url = "https://api.twitter.com/1.1/onboarding/task.json?flow_name=login"
    response = extr.request(url, method="POST", headers=headers, json=data)

    data = {
        "flow_token": process(response),
        "subtask_inputs": [
            {
                "subtask_id": "LoginJsInstrumentationSubtask",
                "js_instrumentation": {
                    "response": "{}",
                    "link": "next_link",
                },
            },
        ],
    }
    url = "https://api.twitter.com/1.1/onboarding/task.json"
    response = extr.request(
        url, method="POST", headers=headers, json=data, fatal=None)

    # username
    data = {
        "flow_token": process(response),
        "subtask_inputs": [
            {
                "subtask_id": "LoginEnterUserIdentifierSSO",
                "settings_list": {
                    "setting_responses": [
                        {
                            "key": "user_identifier",
                            "response_data": {
                                "text_data": {"result": username},
                            },
                        },
                    ],
                    "link": "next_link",
                },
            },
        ],
    }
    #  url = "https://api.twitter.com/1.1/onboarding/task.json"
    extr.sleep(random.uniform(2.0, 4.0), "login (username)")
    response = extr.request(
        url, method="POST", headers=headers, json=data, fatal=None)

    # password
    data = {
        "flow_token": process(response),
        "subtask_inputs": [
            {
                "subtask_id": "LoginEnterPassword",
                "enter_password": {
                    "password": password,
                    "link": "next_link",
                },
            },
        ],
    }
    #  url = "https://api.twitter.com/1.1/onboarding/task.json"
    extr.sleep(random.uniform(2.0, 4.0), "login (password)")
    response = extr.request(
        url, method="POST", headers=headers, json=data, fatal=None)

    # account duplication check ?
    data = {
        "flow_token": process(response),
        "subtask_inputs": [
            {
                "subtask_id": "AccountDuplicationCheck",
                "check_logged_in_account": {
                    "link": "AccountDuplicationCheck_false",
                },
            },
        ],
    }
    #  url = "https://api.twitter.com/1.1/onboarding/task.json"
    response = extr.request(
        url, method="POST", headers=headers, json=data, fatal=None)
    process(response)

    return {
        cookie.name: cookie.value
        for cookie in extr.cookies
    }
