# TODO Actually, all stores are promoted as bike retailers. A handful of partners are
# multisport stores (ie. in ski resorts), which don't have the best "shop" tag.
import re
from typing import AsyncIterator, Iterable

from scrapy.http import JsonRequest, Request, Response

from locations.categories import Categories, Extras, apply_category, apply_yes_no
from locations.items import Feature, SocialMedia, set_social_media
from locations.json_blob_spider import JSONBlobSpider
from locations.structured_data_spider import clean_facebook, clean_instagram


class GiantSpider(JSONBlobSpider):
    name = "giant"
    allowed_domains = ["www.giant-bicycles.com", "static.giant-bicycles.com"]
    sitemap_url = "https://static.giant-bicycles.com/sitemaps/sitemap-giant-fr-fr.xml"
    locations_key = "dealers"
    # Campaign names that make a dealer a Giant-branded store, case-insensitive
    # "store inside"/"corner" are partner formats and not GIant-branded.
    giant_campaign_names = {
        "giant store",
        "giant brand store",
        "giant compact store",
        "giant city",
        "giant experience center",
        "자이언트 스토어",
        "자이언트 익스피리언스 스토어",
    }
    # Campaign names meaning bike rental ("Loueurs" FR, "Bike Rentals" EN, ...),
    # their icons do not always contain "rental".
    rental_campaign_names = {
        "bike rentals",
        "loueurs",
        "noleggio",
        "verleihstation (ohne verkauf)",
        "高級車租賃服務",
    }
    # A null latitude or longitude disables the search radius, so an empty
    # campaigncodes list returns every dealer of the country endpoint.
    dealer_query = {
        "latitude": 0,
        "longitude": 0,
        "keyword": "",
        "NE_lat": 90,
        "NE_lng": 180,
        "SW_lat": -90,
        "SW_lng": -180,
        "campaigncodes": [],
        "onlyGiantStores": False,
    }

    async def start(self) -> AsyncIterator[Request]:
        yield Request(self.sitemap_url, callback=self.parse_sitemap)

    def parse_sitemap(self, response: Response) -> Iterable[JsonRequest]:
        # We have to loop on all country websites (listed in sitemap), then we fetch all
        # stores using a single call
        paths = {}
        for link in response.xpath("//*[local-name()='link' and @rel='alternate']"):
            hreflang = link.xpath("@hreflang").get(default="")
            href = link.xpath("@href").get(default="")
            match = re.fullmatch(r"https://www\.giant-bicycles\.com/([a-z-]+)", href)
            # pl-in redirects to the /in endpoint which only lists the HQ.
            if "-" in hreflang and match and match.group(1) != "pl-in":
                paths[match.group(1)] = hreflang.rsplit("-", 1)[1].upper()
        for path, country in sorted(paths.items()):
            yield JsonRequest(
                url=f"https://www.giant-bicycles.com/{path}/stores/dealers",
                data=self.dealer_query,
                method="POST",
                meta={"country": country},
            )

    def extract_json(self, response: Response) -> list[dict]:
        data = response.json()
        response.meta["campaigns"] = {
            campaign["code"]: (
                # Lowercase and strip hyphens so "e-bike" matches "ebike".
                (campaign.get("icon") or "").rsplit("/", 1)[-1].lower().replace("-", ""),
                (campaign.get("name") or "").casefold(),
            )
            for campaign in data.get("campaigns", [])
        }
        return data[self.locations_key]

    def pre_process_data(self, feature: dict) -> None:
        website = (feature.pop("WebAddress") or "").strip()
        if "facebook.com" in website:
            feature["Facebook"] = feature["Facebook"] or website
        elif website.startswith(("http://", "https://")) and " " not in website and "xn--" not in website:
            feature["website"] = website
        elif re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", website) and "xn--" not in website:
            feature["website"] = "https://" + website
        elif "@" in website and " " not in website and "/" not in website and not feature.get("Email"):
            feature["Email"] = website
        for key in ("Facebook", "Instagram"):
            if feature.get(key) and not feature[key].startswith("http"):
                feature[key] = "https://" + feature[key]

    def post_process_item(self, item: Feature, response: Response, feature: dict) -> Iterable[Feature]:
        item["ref"] = f"{feature['Code']}-{response.meta['country']}"
        item["country"] = response.meta["country"]
        if feature["CountryDisplayName"] == "Österreich":
            item["country"] = "AT"
        item["addr_full"] = feature["AddressLocalized"]
        campaigns = [response.meta["campaigns"].get(code, ("", "")) for code in feature["CampaignCodes"]]
        icons = [icon for icon, _ in campaigns]
        if (
            "giant" in feature["Name"].casefold()
            or feature["IsGiantStore"]
            or any(re.sub(r"\s*\([^)]*\)$", "", name).strip() in self.giant_campaign_names for _, name in campaigns)
            or "giant" in (feature.get("Email") or "").casefold()
            or "giant" in (feature.get("website") or "").casefold()
        ):
            item["brand"] = "Giant"
            item["brand_wikidata"] = "Q703557"
        if any("delivery" in icon for icon in icons):
            apply_yes_no(Extras.DELIVERY, item, True)
        if any("rental" in icon for icon, _ in campaigns) or any(
            name in self.rental_campaign_names for _, name in campaigns
        ):
            apply_yes_no("service:bicycle:rental", item, True)
        if any("ebike_certified" in icon for icon in icons):
            apply_yes_no("service:bicycle:ebike_maintenance", item, True)
        if any("ebike" in icon for icon in icons):
            apply_yes_no("service:bicycle:ebike", item, True)
        if any("service" in icon for icon in icons):
            apply_yes_no("service:bicycle:repair", item, True)
        if feature["Facebook"]:
            facebook = clean_facebook(feature["Facebook"])
            if facebook:
                set_social_media(item, SocialMedia.FACEBOOK, facebook)
        if feature["Instagram"]:
            instagram = clean_instagram(feature["Instagram"])
            if instagram:
                set_social_media(item, SocialMedia.INSTAGRAM, instagram)
        if feature["Image"]:
            item["image"] = feature["Image"]
        apply_category(Categories.SHOP_BICYCLE, item)
        yield item
