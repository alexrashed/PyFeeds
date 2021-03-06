import html
from datetime import datetime, timedelta

import scrapy

from feeds.loaders import FeedEntryItemLoader
from feeds.spiders import FeedsXMLFeedSpider
from feeds.utils import generate_feed_header


class DerStandardAtSpider(FeedsXMLFeedSpider):
    name = "derstandard.at"
    custom_settings = {"COOKIES_ENABLED": False}

    _titles = {}
    # Some ressorts have articles that are regulary updated, e.g. cartoons.
    _cache_expires = {"47": timedelta(minutes=60)}
    _max_articles = 10
    _ressorts_num_articles = {}

    def start_requests(self):
        self._ressorts = self.settings.get("FEEDS_SPIDER_DERSTANDARD_AT_RESSORTS")
        if self._ressorts:
            self._ressorts = self._ressorts.split()
        else:
            self.logger.info("No ressorts given, falling back to general ressort!")
            self._ressorts = ["seite1"]

        for ressort in self._ressorts:
            if str.isnumeric(ressort):
                param = "ressortid={}".format(ressort)
            else:
                param = "ressort={}".format(ressort)
            yield scrapy.Request(
                "https://{}/?page=rss&{}".format(self.name, param),
                meta={"dont_cache": True, "ressort": ressort},
            )

        self._users = {
            user_id: None
            for user_id in self.settings.get(
                "FEEDS_SPIDER_DERSTANDARD_AT_USERS", ""
            ).split()
        }
        for user_id in self._users.keys():
            for page in range(3):
                yield scrapy.Request(
                    (
                        "https://{}/userprofil/postings/{}?"
                        + "pageNumber={}&sortMode=1"
                    ).format(self.name, user_id, page),
                    self._parse_user_profile,
                    meta={
                        # Older pages should be cached longer.
                        "cache_expires": timedelta(hours=page),
                        "path": "userprofil/postings/{}".format(user_id),
                        "user_id": user_id,
                    },
                )

    def feed_headers(self):
        for ressort in self._ressorts:
            yield generate_feed_header(
                title=self._titles[ressort],
                subtitle="Nachrichten in Echtzeit",
                link="https://{}".format(self.name),
                icon="https://at.staticfiles.at/sites/mainweb/img/icons/dst/dst-16.ico",
                logo="https://at.staticfiles.at/sites/mainweb/img/icons/dst/"
                "dst-228.png",
                path=ressort,
            )

        for user_id, name in self._users.items():
            yield generate_feed_header(
                title="derStandard.at › Postings von {}".format(name),
                subtitle="Nachrichten in Echtzeit",
                link="https://{}/userprofil/postings/{}".format(self.name, user_id),
                icon="https://at.staticfiles.at/sites/mainweb/img/icons/dst/dst-16.ico",
                logo="https://at.staticfiles.at/sites/mainweb/img/icons/dst/"
                "dst-228.png",
                path="userprofil/postings/{}".format(user_id),
            )

    def parse_node(self, response, node):
        if response.meta["ressort"] not in self._titles:
            self._titles[response.meta["ressort"]] = node.xpath(
                "//channel/title/text()"
            ).extract_first()

        url = node.xpath("link/text()").extract_first()
        if url.startswith("https://{}/jetzt/livebericht".format(self.name)):
            return

        num_articles = self._ressorts_num_articles.get(response.meta["ressort"], 0)
        if num_articles >= self._max_articles:
            return
        self._ressorts_num_articles[response.meta["ressort"]] = num_articles + 1

        updated = node.xpath("pubDate/text()").extract_first()
        cache_expires = self._cache_expires.get(response.meta["ressort"])
        return scrapy.Request(
            url,
            self._parse_article,
            meta={
                "updated": updated,
                "ressort": response.meta["ressort"],
                "cache_expires": cache_expires,
            },
            # Cookie handling is disabled, so we have to send this as a header.
            headers={"Cookie": "DSGVO_ZUSAGE_V1=true"},
        )

    def _parse_article(self, response):
        def _fix_img_src(elem):
            src = elem.attrib.pop("data-zoom-src", None)
            # data-zoom-src is only valid if it starts with //images.derstandard.at.
            if src and src.startswith("//images.derstandard.at"):
                elem.attrib["src"] = src
            elem.attrib.pop("width", None)
            elem.attrib.pop("height", None)
            elem.attrib.pop("class", None)
            return elem

        remove_elems = [
            ".credits",
            ".owner-info",
            ".image-zoom",
            ".continue",
            ".sequence-number",
            ".js-embed-output",
            "#mycountrytalks-embed",
            # Remove self-promotion for (other) ressorts.
            '.js-embed-output-feeds a[href^="/r"]',
            '.js-embed-output-feeds a[href^="https://derstandard.at/"]',
            (
                ".js-embed-output-feeds "
                + 'img[src="https://images.derstandard.at/2018/10/18/'
                + 'Immobiliensuche202x122.png"]'
            ),
        ]
        change_tags = {
            "#media-list li .description": "figcaption",
            "#media-list li": "figure",
            "#media-list": "div",
            ".photo": "figure",
            ".caption": "figcaption",
        }
        replace_elems = {
            ".embedded-posting": "<p><em>Hinweis: Das eingebettete Posting ist nur "
            + "im Artikel verfügbar.</em></p>",
            # Replace every special script container with its unescaped content.
            "script.js-embed-template": lambda elem: (
                '<div class="js-embed-output-feeds">'
                + html.unescape(elem.text or "")
                + "</div>"
            ),
            "img": _fix_img_src,
        }
        il = FeedEntryItemLoader(
            response=response,
            base_url="https://{}".format(self.name),
            remove_elems=remove_elems,
            change_tags=change_tags,
            replace_elems=replace_elems,
        )
        il.add_value("link", response.url)
        il.add_css("title", 'meta[property="og:title"]::attr(content)')
        for author in response.css("span.author::text").extract():
            # Sometimes the author name is messed up and written in upper case.
            # This happens usually for articles written by Günter Traxler.
            if author.upper() == author:
                author = author.title()
            il.add_value("author_name", author)
        il.add_value("path", response.meta["ressort"])
        il.add_value("updated", response.meta["updated"])
        il.add_css("category", "#breadcrumb .item a::text")
        blog_id = response.css("#userblogentry::attr(data-objectid)").extract_first()
        if blog_id:
            url = (
                "https://{}/userprofil/bloggingdelivery/blogeintrag?godotid={}"
            ).format(self.name, blog_id)
            return scrapy.Request(url, self._parse_blog_article, meta={"il": il})
        elif response.css("#feature-content"):
            cover_photo = response.css("#feature-cover-photo::attr(style)").re_first(
                r"\((.*)\)"
            )
            il.add_value("content_html", '<img src="{}">'.format(cover_photo))
            il.add_css("content_html", "#feature-cover-title h2")
            il.add_css("content_html", "#feature-content > .copytext")
            return il.load_item()
        else:
            il.add_css("content_html", "#content-aside")
            il.add_css("content_html", "#objectContent > .copytext")
            il.add_css("content_html", "#content-main > .copytext")
            il.add_css("content_html", ".slide")
            return il.load_item()

    def _parse_blog_article(self, response):
        il = response.meta["il"]
        il.add_value("content_html", response.text)
        return il.load_item()

    def _parse_user_profile(self, response):
        self._users[response.meta["user_id"]] = (
            response.css("#up_user h2::text").extract_first().strip()
        )
        for posting in response.css(".posting"):
            il = FeedEntryItemLoader(
                selector=posting,
                base_url="https://{}".format(self.name),
                change_tags={"span": "p"},
            )
            il.add_css("title", ".text strong::text")
            il.add_css("link", '.text a::attr("href")')
            il.add_value(
                "updated",
                datetime.utcfromtimestamp(
                    int(posting.css('.date::attr("data-timestamp")').extract_first())
                    / 1000
                ),
            )
            il.add_css("content_html", ".text span")
            il.add_css("content_html", ".article h4")
            il.add_value("path", response.meta["path"])
            yield il.load_item()
