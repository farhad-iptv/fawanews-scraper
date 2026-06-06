import requests
from bs4 import BeautifulSoup
import json
import re
import time
from datetime import datetime
from urllib.parse import urljoin


BASE_URL = "http://www.fawanews.sc/"
REFERER = "http://www.fawanews.sc"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
    "Referer": BASE_URL,
}


def fetch_page(url, retries=3, timeout=15):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                print(f"[Retry] {url} ({attempt + 1}/{retries - 1})")
                time.sleep(2)
            else:
                print(f"[Error] Failed to fetch {url}: {e}")
                return None


def clean_text(text):
    if not text:
        return ""
    return " ".join(text.strip().split())


def absolute_url(url, base=BASE_URL):
    if not url:
        return ""
    return urljoin(base, url.strip())


def extract_urls_from_text(text):
    if not text:
        return []

    patterns = [
        r'https?://[^\s\'"<>]+',
        r'//[^\s\'"<>]+',
    ]

    found = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        found.extend(matches)

    cleaned = []
    for u in found:
        if u.startswith("//"):
            u = "http:" + u
        cleaned.append(u)

    return list(dict.fromkeys(cleaned))


def extract_stream_links(html, page_url):
    soup = BeautifulSoup(html, "html.parser")

    stream_data = {
        "iframe_sources": [],
        "video_sources": [],
        "embed_sources": [],
        "m3u8_links": [],
        "mpd_links": [],
        "mp4_links": [],
        "js_links": [],
        "all_candidate_urls": [],
    }

    # iframes
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src") or iframe.get("data-src") or iframe.get("data-lazy-src")
        if src:
            src = absolute_url(src, page_url)
            stream_data["iframe_sources"].append({
                "url": src,
                "width": iframe.get("width", ""),
                "height": iframe.get("height", ""),
                "allowfullscreen": iframe.has_attr("allowfullscreen")
            })

    # videos
    for video in soup.find_all("video"):
        if video.get("src"):
            src = absolute_url(video.get("src"), page_url)
            stream_data["video_sources"].append({
                "url": src,
                "type": "video_tag"
            })

        for source in video.find_all("source"):
            src = source.get("src")
            if src:
                stream_data["video_sources"].append({
                    "url": absolute_url(src, page_url),
                    "type": source.get("type", "unknown")
                })

    # embed/object
    for embed in soup.find_all("embed"):
        src = embed.get("src")
        if src:
            stream_data["embed_sources"].append({
                "url": absolute_url(src, page_url),
                "type": embed.get("type", "unknown")
            })

    for obj in soup.find_all("object"):
        data = obj.get("data")
        if data:
            stream_data["embed_sources"].append({
                "url": absolute_url(data, page_url),
                "type": "object"
            })

    # data-* attrs
    attrs_to_check = ["data-src", "data-url", "data-file", "data-stream", "data-video"]
    for attr in attrs_to_check:
        for el in soup.find_all(attrs={attr: True}):
            val = el.get(attr)
            if val:
                stream_data["all_candidate_urls"].append(absolute_url(val, page_url))

    # raw html search
    raw_urls = extract_urls_from_text(html)
    stream_data["all_candidate_urls"].extend(raw_urls)

    # regex for direct media links
    m3u8_matches = re.findall(r'https?://[^\s\'"<>]+?\.m3u8[^\s\'"<>]*', html, re.I)
    mpd_matches = re.findall(r'https?://[^\s\'"<>]+?\.mpd[^\s\'"<>]*', html, re.I)
    mp4_matches = re.findall(r'https?://[^\s\'"<>]+?\.mp4[^\s\'"<>]*', html, re.I)

    stream_data["m3u8_links"].extend(m3u8_matches)
    stream_data["mpd_links"].extend(mpd_matches)
    stream_data["mp4_links"].extend(mp4_matches)

    # JS player patterns
    js_patterns = [
        r'(?:file|source|src|url|stream|stream_url|playback_url)\s*[:=]\s*[\'"]([^\'"]+)[\'"]',
        r'jwplayer\([^)]*\)\.setup\([^)]*file\s*:\s*[\'"]([^\'"]+)[\'"]',
        r'Clappr\.Player\([^)]*source\s*:\s*[\'"]([^\'"]+)[\'"]',
        r'hls\.loadSource\([\'"]([^\'"]+)[\'"]\)',
        r'videojs\([^)]*\)\.src\([\'"]([^\'"]+)[\'"]\)',
    ]

    for pattern in js_patterns:
        matches = re.findall(pattern, html, re.I)
        for match in matches:
            stream_data["js_links"].append(absolute_url(match, page_url))

    # dedupe
    for key in stream_data:
        if isinstance(stream_data[key], list):
            unique = []
            seen = set()
            for item in stream_data[key]:
                marker = json.dumps(item, sort_keys=True) if isinstance(item, dict) else item
                if marker not in seen:
                    seen.add(marker)
                    unique.append(item)
            stream_data[key] = unique

    return stream_data


def extract_best_stream_urls(stream_data):
    """
    Return only usable stream URLs for playlist generation.
    Priority:
    1. direct m3u8
    2. video/embed/iframe urls that are already m3u8
    """
    results = []

    # direct m3u8
    for u in stream_data.get("m3u8_links", []):
        if ".m3u8" in u:
            results.append(u)

    # candidates from structured sources
    for group in ["video_sources", "embed_sources", "iframe_sources", "js_links", "all_candidate_urls"]:
        for item in stream_data.get(group, []):
            url = item["url"] if isinstance(item, dict) else item
            if isinstance(url, str) and ".m3u8" in url:
                results.append(url)

    # dedupe preserve order
    final = []
    seen = set()
    for u in results:
        if u not in seen:
            seen.add(u)
            final.append(u)

    return final


def format_m3u_stream_url(url):
    return f"{url}|Referer={REFERER}"


def scrape_fawanews():
    response = fetch_page(BASE_URL)
    if not response:
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    items = soup.find_all("div", class_="user-item")

    live_events = []
    news_articles = []

    print(f"[+] Found {len(items)} items")

    for index, item in enumerate(items, 1):
        event = {}

        name_el = item.find("div", class_="user-item__name")
        playing_el = item.find("div", class_="user-item__playing")
        avatar_el = item.find("div", class_="user-item__avatar")
        link_el = item.find("a", href=True)

        event["name"] = clean_text(name_el.get_text()) if name_el else ""
        event["type"] = "live_event" if playing_el else "news_article"

        if playing_el:
            playing_text = clean_text(playing_el.get_text())
            parts = playing_text.rsplit(" ", 1)
            if len(parts) == 2 and ":" in parts[1]:
                event["league"] = parts[0]
                event["time"] = parts[1]
            else:
                event["league"] = playing_text
                event["time"] = ""
        else:
            event["league"] = ""
            event["time"] = ""

        if avatar_el:
            img = avatar_el.find("img")
            if img and img.get("src"):
                event["image_url"] = img["src"]

        if link_el:
            event["page_link"] = absolute_url(link_el["href"], BASE_URL)
        else:
            event["page_link"] = ""

        if " vs " in event["name"]:
            teams = event["name"].split(" vs ", 1)
            event["home_team"] = clean_text(teams[0])
            event["away_team"] = clean_text(teams[1])

        print(f"[{index}/{len(items)}] {event['name']}")

        if event["page_link"]:
            event_resp = fetch_page(event["page_link"])
            if event_resp:
                event["page_status"] = event_resp.status_code
                page_soup = BeautifulSoup(event_resp.text, "html.parser")

                title_tag = page_soup.find("title")
                event["page_title"] = clean_text(title_tag.get_text()) if title_tag else ""

                meta_desc = page_soup.find("meta", attrs={"name": "description"})
                if meta_desc and meta_desc.get("content"):
                    event["description"] = clean_text(meta_desc["content"])
                else:
                    event["description"] = ""

                stream_data = extract_stream_links(event_resp.text, event["page_link"])
                event["stream_data"] = stream_data
                event["best_stream_urls"] = extract_best_stream_urls(stream_data)
            else:
                event["page_status"] = None
                event["page_title"] = ""
                event["description"] = ""
                event["stream_data"] = {}
                event["best_stream_urls"] = []

            time.sleep(1)

        if event["type"] == "live_event":
            live_events.append(event)
        else:
            news_articles.append(event)

    data = {
        "source": "fawanews.sc",
        "source_url": BASE_URL,
        "referer": REFERER,
        "fetched_at": datetime.now().isoformat(),
        "total_live_events": len(live_events),
        "total_news_articles": len(news_articles),
        "live_events": live_events,
        "news_articles": news_articles
    }

    return data


def save_json(data, filename):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    print(f"[Saved] {filename}")


def make_streams_only_json(data):
    events_with_streams = []

    for event in data.get("live_events", []) + data.get("news_articles", []):
        if event.get("best_stream_urls"):
            events_with_streams.append({
                "name": event.get("name", ""),
                "type": event.get("type", ""),
                "league": event.get("league", ""),
                "time": event.get("time", ""),
                "page_link": event.get("page_link", ""),
                "image_url": event.get("image_url", ""),
                "streams": [
                    {
                        "original_url": u,
                        "m3u_url": format_m3u_stream_url(u)
                    }
                    for u in event.get("best_stream_urls", [])
                ]
            })

    return {
        "source": data.get("source"),
        "fetched_at": data.get("fetched_at"),
        "total_with_streams": len(events_with_streams),
        "events_with_streams": events_with_streams
    }


def write_m3u_playlist(data, filename="fawanews_playlist.m3u"):
    lines = ["#EXTM3U"]

    for event in data.get("live_events", []) + data.get("news_articles", []):
        stream_urls = event.get("best_stream_urls", [])
        if not stream_urls:
            continue

        name = event.get("name", "Unknown Event")
        league = event.get("league", "")
        time_str = event.get("time", "")
        logo = event.get("image_url", "")
        group_title = league if league else event.get("type", "Other")

        for idx, stream_url in enumerate(stream_urls, 1):
            display_name = name if len(stream_urls) == 1 else f"{name} [{idx}]"
            extinf = (
                f'#EXTINF:-1 tvg-name="{display_name}" '
                f'tvg-logo="{logo}" '
                f'group-title="{group_title}",'
                f'{display_name}{" - " + time_str if time_str else ""}'
            )
            lines.append(extinf)
            lines.append(format_m3u_stream_url(stream_url))

    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[Saved] {filename}")


def write_m3u_playlist_193_only(data, filename="fawanews_193_only.m3u"):
    """
    Writes only streams already matching:
    http://193.47.62.42/hls/....m3u8|Referer=http://www.fawanews.sc
    """
    lines = ["#EXTM3U"]

    for event in data.get("live_events", []) + data.get("news_articles", []):
        stream_urls = event.get("best_stream_urls", [])
        matched = [u for u in stream_urls if u.startswith("http://193.47.62.42/hls/") and ".m3u8" in u]

        if not matched:
            continue

        name = event.get("name", "Unknown Event")
        league = event.get("league", "")
        time_str = event.get("time", "")
        logo = event.get("image_url", "")
        group_title = league if league else event.get("type", "Other")

        for idx, stream_url in enumerate(matched, 1):
            display_name = name if len(matched) == 1 else f"{name} [{idx}]"
            lines.append(
                f'#EXTINF:-1 tvg-name="{display_name}" tvg-logo="{logo}" group-title="{group_title}",{display_name}{" - " + time_str if time_str else ""}'
            )
            lines.append(f"{stream_url}|Referer={REFERER}")

    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[Saved] {filename}")


if __name__ == "__main__":
    data = scrape_fawanews()

    if not data:
        print("Failed to scrape data.")
        exit()

    save_json(data, "sports_events_complete.json")

    live_only = {
        "source": data["source"],
        "fetched_at": data["fetched_at"],
        "total_events": data["total_live_events"],
        "events": data["live_events"]
    }
    save_json(live_only, "live_events_only.json")

    streams_only = make_streams_only_json(data)
    save_json(streams_only, "streams_only.json")

    write_m3u_playlist(data, "fawanews_playlist.m3u")
    write_m3u_playlist_193_only(data, "fawanews_193_only.m3u")

    print("[Done]")
