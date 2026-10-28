"""
agents/youtube_agent.py
=======================
YouTube channel investigator using Apify SDK.
Actor: h7sDV53CddomktSi5  (streamers/youtube-scraper)
Exclusive to: reviewer2 + admin
"""

import os
import re
from typing import Any

import httpx
from backend import providers
from backend.logger import GLOBAL_LOGGER as logger

APIFY_TOKEN = os.getenv("APIFY_API_TOKEN", "")
ACTOR_ID    = os.getenv("YOUTUBE_ACTOR_ID","")


def _parse_human_count(text: str) -> int:
    """Parse strings like '1,234', '1.2K', '3.4M' into integer count."""
    if not text:
        return 0
    s = text.strip().replace(",", "").upper()
    try:
        if s.endswith("K"):
            return int(float(s[:-1]) * 1_000)
        if s.endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        if s.endswith("B"):
            return int(float(s[:-1]) * 1_000_000_000)
        return int(float(s))
    except Exception:
        return 0


def _fetch_actual_total_videos(channel_url: str, channel_id: str = "") -> int:
    """
    Best-effort actual total video count from channel page metadata.
    Tries channel /videos page and parses JSON blobs in HTML.
    """
    if providers.sample():
        return 0
    candidates = []
    if channel_url:
        base = channel_url.rstrip("/")
        candidates.extend([f"{base}/videos", base])
    if channel_id:
        candidates.extend([
            f"https://www.youtube.com/channel/{channel_id}/videos",
            f"https://www.youtube.com/channel/{channel_id}",
        ])

    for url in candidates:
        try:
            resp = httpx.get(url, timeout=12, follow_redirects=True)
            if resp.status_code != 200:
                continue
            html = resp.text

            # Pattern 1: "videoCountText":{"simpleText":"1,234 videos"}
            m = re.search(r'"videoCountText"\s*:\s*\{\s*"simpleText"\s*:\s*"([0-9.,KMBkmb]+)\\s+videos"', html)
            if m:
                count = _parse_human_count(m.group(1))
                if count > 0:
                    return count

            # Pattern 2: "videosCountText":{"runs":[{"text":"1.2K"},{"text":" videos"}]}
            m = re.search(r'"videosCountText"\s*:\s*\{\s*"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([0-9.,KMBkmb]+)"\s*\}\s*,\s*\{\s*"text"\s*:\s*"\\s*videos"', html)
            if m:
                count = _parse_human_count(m.group(1))
                if count > 0:
                    return count
        except Exception:
            continue
    return 0


def _estimate_income(subscribers: int, total_views: int, video_count: int) -> dict:
    """Rough YouTube income estimate using RPM model."""
    avg_views     = (total_views // video_count) if video_count > 0 else 0
    monthly_views = avg_views * 4          # ~4 uploads/month estimate
    rpm_low       = 0.5                    # typical RPM low (USD)
    rpm_high      = 2.0                    # typical RPM high (USD)
    usd_low       = round((monthly_views / 1000) * rpm_low, 2)
    usd_high      = round((monthly_views / 1000) * rpm_high, 2)
    return {
        "monthly_usd_low":  usd_low,
        "monthly_usd_high": usd_high,
        "range_usd":        f"${usd_low:,.0f} – ${usd_high:,.0f}",
    }


def _analyze_channel(item: dict, declared_income: float) -> dict:
    """Extract income signals and fraud flags from YouTube channel data."""
    risk_flags    = []
    income_signals = []
    score         = 0.1

    channel_name = (
        item.get("channelName") or
        item.get("title") or
        item.get("channel", {}).get("name", "") or ""
    )
    channel_url  = (
        item.get("url") or
        item.get("channelUrl") or
        item.get("channel", {}).get("url", "") or ""
    )
    subscribers  = int(
        item.get("numberOfSubscribers") or
        item.get("subscriberCount") or
        item.get("channel", {}).get("subscriberCount", 0) or 0
    )
    total_videos = int(
        item.get("numberOfVideos") or
        item.get("videoCount") or
        item.get("channel", {}).get("videoCount") or
        item.get("channel", {}).get("numberOfVideos") or
        0
    )
    total_views  = int(
        item.get("numberOfViews") or
        item.get("viewCount") or 0
    )
    videos       = item.get("videos") or []

    has_video_evidence = bool(videos) or bool(item.get("id")) or item.get("type") == "video"
    if total_videos == 0 and not has_video_evidence:
        risk_flags.append("No videos found — inactive or empty channel")
        score += 0.05

    if total_videos == 0 and has_video_evidence:
        total_videos = max(total_videos, len(videos), 1)

    # Monetization threshold — YouTube Partner Program requires 1000 subs + 4000 hrs
    monetized = subscribers >= 1000 and total_videos >= 10
    income_est = _estimate_income(subscribers, total_views, max(total_videos, 1))

    income_signals.append(f"Channel: {channel_name}")
    income_signals.append(f"Subscribers: {subscribers:,}")
    income_signals.append(f"Total videos: {total_videos}")
    income_signals.append(f"Total views: {total_views:,}")
    income_signals.append(f"Estimated monthly income: {income_est['range_usd']}")
    income_signals.append(f"Monetized: {'Yes' if monetized else 'No (below YPP threshold)'}")

    # ── Income mismatch check ──────────────────────────
    monthly_usd_high = income_est["monthly_usd_high"]
    if declared_income > 0 and monthly_usd_high > 0:
        if monthly_usd_high > declared_income * 3:
            risk_flags.append(
                f"YouTube income est. ${monthly_usd_high:,.0f}/mo is "
                f"{monthly_usd_high/declared_income:.1f}x declared income "
                f"${declared_income:,.0f}/mo — undisclosed income source"
            )
            score += 0.35
        elif monthly_usd_high > declared_income:
            risk_flags.append(
                "YouTube income exceeds declared income — verify tax returns for additional income sources"
            )
            score += 0.15

    return {
        "found":                  True,
        "channel_name":           channel_name,
        "channel_url":            channel_url,
        "subscribers":            subscribers,
        "total_videos":           total_videos,
        "total_views":            total_views,
        "monetized":              monetized,
        "est_monthly_income_usd": income_est["monthly_usd_high"],
        "est_monthly_income_usd": income_est["monthly_usd_high"],
        "income_range_usd":       income_est["range_usd"],
        "recent_videos":          [
            {"title": v.get("title",""), "views": v.get("viewCount", 0)}
            for v in (videos[:5] if isinstance(videos, list) else [])
        ],
        "income_signals":         income_signals,
        "risk_flags":             risk_flags,
        "youtube_score":          round(min(score, 0.95), 3),
        "source":                 "apify",
    }


def _no_channel(name: str, reason: str) -> dict:
    return {
        "found":                  False,
        "channel_name":           None,
        "channel_url":            None,
        "subscribers":            0,
        "total_videos":           0,
        "total_views":            0,
        "monetized":              False,
        "est_monthly_income_usd": 0,
        "income_range_usd":       "$0",
        "recent_videos":          [],
        "income_signals":         [f"No YouTube channel found: {reason}"],
        "risk_flags":             [],
        "youtube_score":          0.1,
        "source":                 "apify",
    }


async def run_youtube_agent(state: dict[str, Any]) -> dict[str, Any]:
    """
    YouTube agent — hits Apify actor h7sDV53CddomktSi5.
    Searches by customer name, estimates income and checks for mismatches.
    """
    entity_id = state.get("entity_id", "")
    customer  = state.get("customer_info", {}) or {}
    name      = customer.get("name", entity_id)
    income    = float(customer.get("monthly_income", 0) or 0)

    logger.info(f"[YouTube] Investigating {entity_id} — {name}")

    if not APIFY_TOKEN and not providers.sample():
        logger.error("[YouTube] APIFY_API_TOKEN not set")
        youtube_data = _no_channel(name, "APIFY_API_TOKEN missing")
        return {**state, "youtube_findings": youtube_data,
                "fraud_indicators": state.get("fraud_indicators", [])}

    try:
        client = providers.apify_client(APIFY_TOKEN, "youtube")

        run_input = {
            "searchQueries":   [f"{name} youtube channel"],
            "maxResults":      200,
            "maxResultsShorts":200,
            "maxResultStreams": 50,
            "startUrls":       [],
            "downloadSubtitles":           True,
            "saveSubsToKVS":               True,
            "subtitlesLanguage":           "en",
            "preferAutoGeneratedSubtitles": True,
            "subtitlesFormat":             "srt",
            "sortingOrder":    "relevance",
            "videoType":       "video"
        }

        logger.info(f"[YouTube] Running actor {ACTOR_ID} — search: '{name} youtube channel'")
        run   = client.actor(ACTOR_ID).call(run_input=run_input)
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        logger.info(">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")
        logger.info(items)
        logger.info(">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")
        logger.info(f"[YouTube] Got {len(items)} results")

        if not items:
            youtube_data = _no_channel(name, "No channel found in search results")
        else:
            first = items[0]
            video_items = [i for i in items if i.get("type") == "video" or i.get("id")]
            total_videos_from_results = len(video_items)
            total_views_from_results = sum(int((i.get("viewCount") or 0)) for i in video_items)
            actual_total_videos = _fetch_actual_total_videos(
                first.get("channelUrl") or first.get("channel", {}).get("url", ""),
                first.get("channelId") or first.get("channel", {}).get("id", "")
            )

            normalized_item = {
                **first,
                "numberOfVideos": (
                    actual_total_videos
                    or
                    first.get("numberOfVideos")
                    or first.get("videoCount")
                    or first.get("channel", {}).get("videoCount")
                    or first.get("channel", {}).get("numberOfVideos")
                    or total_videos_from_results
                ),
                "numberOfViews": (
                    first.get("numberOfViews")
                    or first.get("viewCount")
                    or total_views_from_results
                ),
                "channelUrl": first.get("channelUrl") or first.get("channel", {}).get("url"),
                "videos": [
                    {
                        "title": i.get("title", ""),
                        "viewCount": i.get("viewCount", 0),
                        "url": i.get("url", ""),
                        "published_at": i.get("date"),
                    }
                    for i in video_items
                ],
            }
            youtube_data = _analyze_channel(normalized_item, income)

    except Exception as e:
        logger.error(f"[YouTube] Apify error for {entity_id}: {e}")
        youtube_data = _no_channel(name, str(e))

    existing = state.get("fraud_indicators", []) or []
    for flag in youtube_data.get("risk_flags", []):
        existing.append({
            "source":      "youtube",
            "flag":        "YOUTUBE_INCOME_MISMATCH",
            "description": flag,
        })

    return {**state, "youtube_findings": youtube_data, "fraud_indicators": existing}