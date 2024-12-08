import json
from datetime import datetime, timedelta

from dateutil.tz import gettz
from scrapy import Request

from feeds.exceptions import DropResponse
from feeds.loaders import FeedEntryItemLoader
from feeds.spiders import FeedsSpider


class TvthekOrfAtSpider(FeedsSpider):
    name = "tvthek.orf.at"
    http_user = "ps_android_v3_new"
    http_pass = "0652e466d1990d1fdf40da08e7933e06"

    feed_title = "TVthek.ORF.at"
    feed_subtitle = "ORF TVTHEK"
    feed_link = "https://tvthek.orf.at"

    def start_requests(self):
        # We only parse today and yesterday because at the end of the day this
        # already produces a lot of requests and feed readers cache previous
        # days (i.e. old contents of our feed) anyways.
        # It's not enough to parse only today because we might miss shows that
        # aired just before midnight but were streamed after midnight
        # (see also https://github.com/nblock/feeds/issues/27)
        today = datetime.now(gettz("Europe/Vienna"))
        for day in [today, today - timedelta(days=1)]:
            yield Request(
                "https://api-tvthek.orf.at/api/v4.3/schedule/{}".format(
                    day.strftime("%Y-%m-%d")
                ),
                meta={"dont_cache": True},
            )

    def parse(self, response):
        json_response = json.loads(response.text)

        for item in json_response:
            # Skip incomplete items or items with active youth protection.
            # We want to have working download links in the feed item.
            if not item["segments_complete"] or item["has_active_youth_protection"]:
                continue

            # We scrape the episode itself so we can get the segments which are not
            # embedded in the schedule response.
            # Furthermore since this request will be cached, the download URL will also
            # be cached which is convenient for youth protected content.
            yield Request(
                item["_links"]["self"]["href"],
                self._parse_episode,
                # Responses are > 100 KB and useless after 7 days.
                # So don't keep them longer than necessary.
                meta={"cache_expires": timedelta(days=7)},
            )

    def _parse_episode(self, response):
        item = json.loads(response.text)
        il = FeedEntryItemLoader()
        il.add_value("title", item["title"])
        # TODO find a preview image URL
        # il.add_value(
        #     "content_html",
        #     '<img src="{}">'.format(item["playlist"]["preview_image_url"]),
        # )
        if item["description"]:
            il.add_value("content_html", item["description"].replace("\r\n", "<br>"))
        il.add_value("updated", item["date"])
        il.add_value("link", item["share_body"])

        # Check how many segments are part of this episode.
        if len(item["_embedded"]["segments"]) == 1:
            # If only one segment, use the progressive HTTP source in segments[0]
            item["sources"] = item["_embedded"]["segments"][0]["_embedded"]["playlist"][
                "sources"
            ]
        else:
            # TODO find sources with a progressive HTTP URL for multi-segment episodes
            self.logger.warning(
                "Could not extract video for '{}'! "
                "Unsupported multi-segment episode...".format(item["title"])
            )
            raise DropResponse(
                f"Skipping {response.url} because it's a multi-segment episode...",
                transient=True,
            )

        if (
            False
            # TODO check if this is still necessary
            # len(item["sources"]["dash"]) > 0
            # and item["sources"]["dash"][0]["quality_description"] == "Kein DRM"
        ):
            self.logger.debug(f'Video for {item["title"]} is DRM protected')
        else:
            try:
                video = next(
                    s
                    for s in item["sources"]
                    if s["quality"] == "Q8C" and s["delivery"] == "progressive"
                )
                il.add_value("enclosure", {"iri": video["src"], "type": "video/mp4"})
            except StopIteration:
                self.logger.warning(
                    "Could not extract video for '{}'!".format(item["title"])
                )
                raise DropResponse(
                    f"Skipping {response.url} because not downloadable yet",
                    transient=True,
                )

        subtitle = item["_embedded"].get("subtitle")
        if subtitle:
            subtitle = subtitle["srt_url"]
            il.add_value("enclosure", {"iri": subtitle, "type": "text/plain"})
        else:
            self.logger.debug(
                "No subtitle file found for '{}'".format(item["_links"]["self"]["href"])
            )
        il.add_value(
            "category",
            self._categories_from_oewa_base_path(
                item["_embedded"]["profile"]["oewa_base_path"]
            ),
        )
        return il.load_item()

    def _categories_from_oewa_base_path(self, oewa_base_path):
        """Parse ÖWA Base Path into a list of categories.

        Base paths look like this:

          * RedCont/KulturUndFreizeit/FilmUndKino
          * RedCont/KulturUndFreizeit/Sonstiges
          * RedCont/Lifestyle/EssenUndTrinken
          * RedCont/Nachrichten/Nachrichtenueberblick
          * RedCont/Sport/Sonstiges
        """
        old_new = {
            "RedCont": "",
            "Sonstiges": "",
            "Und": " und ",
            "ue": "ü",
            "ae": "ä",
            "oe": "ö",
            "Ue": "Ü",
            "Ae": "Ä",
            "Oe": "Ö",
        }
        for old, new in old_new.items():
            oewa_base_path = oewa_base_path.replace(old, new)
        return list(filter(lambda x: x != "", oewa_base_path.split("/")))
