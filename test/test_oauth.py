#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2018-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gallery_dl import oauth, text  # noqa E402

TESTSERVER = "http://term.ie/oauth/example"
CONSUMER_KEY = "key"
CONSUMER_SECRET = "secret"
REQUEST_TOKEN = "requestkey"
REQUEST_TOKEN_SECRET = "requestsecret"
ACCESS_TOKEN = "accesskey"
ACCESS_TOKEN_SECRET = "accesssecret"


class TestOAuthSession(unittest.TestCase):

    def test_concat(self):
        concat = oauth.concat

        self.assertEqual(concat(), "")
        self.assertEqual(concat("str"), "str")
        self.assertEqual(concat("str1", "str2"), "str1&str2")

        self.assertEqual(concat("&", "?/"), "%26&%3F%2F")
        self.assertEqual(
            concat("GET", "http://example.org/", "foo=bar&baz=a"),
            "GET&http%3A%2F%2Fexample.org%2F&foo%3Dbar%26baz%3Da"
        )

    def test_nonce(self, size=16):
        nonce_values = {oauth.nonce(size) for _ in range(size)}

        # uniqueness
        self.assertEqual(len(nonce_values), size)

        # length
        for nonce in nonce_values:
            self.assertEqual(len(nonce), size)

    def test_quote(self):
        quote = oauth.quote

        reserved = ",;:!\"§$%&/(){}[]=?`´+*'äöü"
        unreserved = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                      "abcdefghijklmnopqrstuvwxyz"
                      "0123456789-._~")

        for char in unreserved:
            self.assertEqual(quote(char), char)

        for char in reserved:
            quoted = quote(char)
            quoted_hex = quoted.replace("%", "")
            self.assertTrue(quoted.startswith("%"))
            self.assertTrue(len(quoted) >= 3)
            self.assertEqual(quoted_hex.upper(), quoted_hex)

    def test_generate_signature(self):
        client = oauth.OAuth1Client(
            CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

        request = MockRequest()
        params = []
        self.assertEqual(
            client.generate_signature(request, params),
            "Wt2xo49dM5pkL4gsnCakNdHaVUo%3D")

        request = MockRequest("https://example.org/")
        params = [("hello", "world"), ("foo", "bar")]
        self.assertEqual(
            client.generate_signature(request, params),
            "ay2269%2F8uKpZqKJR1doTtpv%2Bzn0%3D")

        request = MockRequest("https://example.org/index.html"
                              "?hello=world&foo=bar", method="POST")
        params = [("oauth_signature_method", "HMAC-SHA1")]
        self.assertEqual(
            client.generate_signature(request, params),
            "yVZWb1ts4smdMmXxMlhaXrkoOng%3D")

    def test_dunder_call(self):
        client = oauth.OAuth1Client(
            CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)
        request = MockRequest("https://example.org/")

        with patch("time.time") as tmock, \
             patch("gallery_dl.oauth.nonce") as nmock:
            tmock.return_value = 123456789.123
            nmock.return_value = "abcdefghijklmno"

            client(request)

        self.assertEqual(
            request.headers["Authorization"],
            """OAuth \
oauth_consumer_key="key",\
oauth_nonce="abcdefghijklmno",\
oauth_signature_method="HMAC-SHA1",\
oauth_timestamp="123456789",\
oauth_version="1.0",\
oauth_token="accesskey",\
oauth_signature="DjtTk5j5P3BDZFnstZ%2FtEYcwD6c%3D"\
""")

    def test_request_token(self):
        response = self._oauth_request(
            "/request_token.php", {})
        expected = "oauth_token=requestkey&oauth_token_secret=requestsecret"
        self.assertEqual(response, expected, msg=response)

        data = text.parse_query(response)
        self.assertTrue(data["oauth_token"], REQUEST_TOKEN)
        self.assertTrue(data["oauth_token_secret"], REQUEST_TOKEN_SECRET)

    def test_access_token(self):
        response = self._oauth_request(
            "/access_token.php", {}, REQUEST_TOKEN, REQUEST_TOKEN_SECRET)
        expected = "oauth_token=accesskey&oauth_token_secret=accesssecret"
        self.assertEqual(response, expected, msg=response)

        data = text.parse_query(response)
        self.assertTrue(data["oauth_token"], ACCESS_TOKEN)
        self.assertTrue(data["oauth_token_secret"], ACCESS_TOKEN_SECRET)

    def test_authenticated_call(self):
        params = {"method": "foo", "a": "äöüß/?&#", "äöüß/?&#": "a"}
        response = self._oauth_request(
            "/echo_api.php", params, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

        self.assertEqual(text.parse_query(response), params)

    def _oauth_request(self, endpoint, params=None,
                       oauth_token=None, oauth_token_secret=None):
        # the test server at 'term.ie' is unreachable
        raise unittest.SkipTest()


class MockRequest():

    def __init__(self, url="", method="GET"):
        self.url = url
        self.method = method
        self.headers = {}


if __name__ == "__main__":
    unittest.main(warnings="ignore")
