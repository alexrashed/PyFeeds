from collections import OrderedDict
from datetime import timedelta

import scrapy
from inline_requests import inline_requests

from feeds.loaders import FeedEntryItemLoader
from feeds.spiders import FeedsXMLFeedSpider
from feeds.utils import generate_feed_header


class NachrichtenAtSpider(FeedsXMLFeedSpider):
    name = "nachrichten.at"
    custom_settings = {"COOKIES_ENABLED": True}

    def start_requests(self):
        self._ressorts = self.settings.get("FEEDS_SPIDER_NACHRICHTEN_AT_RESSORTS")
        if self._ressorts:
            self._ressorts = self._ressorts.split()
        else:
            self.logger.info("No ressorts given, falling back to general ressort!")
            self._ressorts = ["nachrichten"]

        self._username = self.settings.get("FEEDS_SPIDER_NACHRICHTEN_AT_USERNAME")
        self._password = self.settings.get("FEEDS_SPIDER_NACHRICHTEN_AT_PASSWORD")
        if self._username and self._password:
            yield from self._login(None)
        else:
            # We can still try to scrape the free articles.
            self.logger.info("Login failed: No username or password given")

        for ressort in self._ressorts:
            yield scrapy.Request(
                "https://www.{}/storage/rss/rss/{}.xml".format(self.name, ressort),
                meta={"ressort": ressort, "dont_cache": True},
            )

    def feed_headers(self):
        for ressort in self._ressorts:
            yield generate_feed_header(
                title="Oberösterreichische Nachrichten {}".format(ressort.title()),
                path=ressort,
                subtitle="OÖN",
                link="https://www.{}".format(self.name),
                icon="https://static1.{}.at/oonup/images/"
                "apple-touch-icon.png".format(self.name),
                logo="https://www.{}/pics/webapp/"
                "touchicon_180x180.png".format(self.name),
            )

    @inline_requests
    def _login(self, response):
        response = yield scrapy.Request(
            "https://www.{}/login/".format(self.name),
            meta={"cache_expires": timedelta(days=14)},
        )
        response = yield scrapy.FormRequest(
            "https://www.{}/login/".format(self.name),
            formdata=OrderedDict(
                [
                    ("user[control][login]", "true"),
                    ("permanent", "checked"),
                    ("username", self._username),
                    ("password", self._password),
                ]
            ),
            meta={"cache_expires": timedelta(days=14)},
        )
        if response and response.css(".notloggedin"):
            # We tried to login but we failed.
            self.logger.error("Login failed: Username or password wrong")

    def parse_node(self, response, node):
        url = node.xpath("link/text()").extract_first()
        return scrapy.Request(
            url.replace("#ref=rss", ",PRINT"),
            self._parse_article,
            meta={"handle_httpstatus_list": [410], "ressort": response.meta["ressort"]},
        )

    def _parse_article(self, response):
        if response.status == 410:
            # Articles has been deleted.
            return

        remove_elems = [".bildtext .author", "iframe"]
        change_tags = {"h1": "h2", ".bildbox": "figure", ".bildtext": "figcaption"}
        il = FeedEntryItemLoader(
            response=response,
            timezone="Europe/Vienna",
            base_url="https://www.{}".format(self.name),
            remove_elems=remove_elems,
            change_tags=change_tags,
            dayfirst=True,
            yearfirst=False,
        )
        if response.css(".payment"):
            il.add_value("category", "paywalled")
        il.add_css("link", 'link[rel="canonical"]::attr(href)')
        il.add_css("title", 'meta[property="og:title"]::attr(content)')
        il.add_css("author_name", ".druckheadline::text", re=r"·\s*(.*)\s*·")
        # Mon, 01 Oct 18 13:42:45 +0200
        il.add_css("updated", 'meta[http-equiv="last-modified"]::attr(content)')
        il.add_css("content_html", ".druckcontent")
        il.add_value("path", response.meta["ressort"])
        return il.load_item()
