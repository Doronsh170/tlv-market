"""
tlv-market — Israeli market review system
Built from Wall Street IL architecture, simplified for stage 1 (no prices).

Sources: @TASE_IL, @TheMarker, @Calcalist, @Globesnews, @SponserNews
Trading days: Mon-Fri (since Jan 4, 2026)
Trading hours: Mon-Thu 10:00-17:35, Fri 10:00-13:50
"""

import json
import os
import re
import requests
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

ISR_TZ = ZoneInfo("Asia/Jerusalem") if ZoneInfo else timezone(timedelta(hours=3))
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TWITTER_API_KEY = os.environ["TWITTER_API_KEY"]
REVIEW_TYPE = os.environ.get("REVIEW_TYPE", "daily_prep")

# Twitter accounts for TLV market coverage
ACCOUNTS = ["TASE_IL", "TheMarker", "Calcalist", "Globes", "SponserNews"]

# Hebrew day names — Python weekday(): Mon=0 ... Sun=6
PY_TO_HEB = {
    0: "שני", 1: "שלישי", 2: "רביעי", 3: "חמישי",
    4: "שישי", 5: "שבת", 6: "ראשון"
}

# TASE trading hours (after Jan 4, 2026 reform)
# Mon-Thu: 10:00-17:35, Fri: 10:00-13:50
# Closed: Sat, Sun (Sun is the new rest day)
TASE_OPEN_DAYS = {0, 1, 2, 3, 4}  # Mon, Tue, Wed, Thu, Fri (weekday indexing)
TASE_FRIDAY = 4

# Israel holidays 2026 — TASE closed (verify with official calendar)
ISRAEL_HOLIDAYS_2026 = [
    "2026-04-01",  # ערב פסח
    "2026-04-02",  # פסח א'
    "2026-04-08",  # שביעי של פסח
    "2026-04-22",  # יום הזיכרון
    "2026-04-23",  # יום העצמאות
    "2026-05-21",  # ערב שבועות (חמישי)
    "2026-05-22",  # שבועות (שישי)
    "2026-09-11",  # ערב ראש השנה
    "2026-09-14",  # ראש השנה ב'
    "2026-09-15",  # ראש השנה ב'
    "2026-09-20",  # ערב יום כיפור
    "2026-09-21",  # יום כיפור
    "2026-09-25",  # ערב סוכות
    "2026-09-28",  # סוכות
    "2026-10-05",  # שמיני עצרת/שמחת תורה
]

# ══════════════════════════════════════════════════════════════
# EXPECTED STRUCTURE — single source of truth for output format
# ══════════════════════════════════════════════════════════════

EXPECTED_FIRST_HEADING = {
    "daily_prep":     "נקודות מרכזיות",
    "daily_summary":  "סיכום המסחר",
    "weekly_prep":    "נקודות מרכזיות לשבוע הקרוב",
    "weekly_summary": "סיכום השבוע",
    "live_news":      "חדשות אחרונות",
}


def build_expected_title(review_type, title_day_name, title_date_str,
                          week_range=None, now_time=None):
    """Build the canonical title — forced onto output to prevent Gemini drift."""
    if review_type == "daily_prep":
        return f"נקודות חשובות לקראת פתיחת המסחר בתל אביב 🇮🇱 – יום {title_day_name} {title_date_str}"
    elif review_type == "daily_summary":
        return f"סיכום יום המסחר בתל אביב 🇮🇱 – יום {title_day_name} {title_date_str}"
    elif review_type == "weekly_prep":
        return f"הכנה לשבוע מסחר בתל אביב 🇮🇱 – {week_range}"
    elif review_type == "weekly_summary":
        return f"סיכום שבוע המסחר בתל אביב 🇮🇱 – {week_range}"
    elif review_type == "live_news":
        return f"מה קורה עכשיו בתל אביב 🇮🇱 – יום {title_day_name}, {title_date_str} | {now_time}"
    return ""


# ══════════════════════════════════════════════════════════════
# DATE HELPERS — TASE-aware
# ══════════════════════════════════════════════════════════════

def is_trading_day(dt, holidays):
    """TASE trades Mon-Fri (weekday 0-4), excluding holidays."""
    if dt.weekday() not in TASE_OPEN_DAYS:
        return False
    if dt.strftime("%Y-%m-%d") in holidays:
        return False
    return True


def get_next_trading_day(now, holidays):
    day = now + timedelta(days=1)
    for _ in range(10):
        if is_trading_day(day, holidays):
            return day
        day = day + timedelta(days=1)
    return now


def get_last_trading_day(now, holidays):
    day = now
    for _ in range(10):
        if is_trading_day(day, holidays):
            return day
        day = day - timedelta(days=1)
    return now


def get_prev_week_range_str(now):
    """Returns 'DD/MM–DD/MM/YYYY' for the previous trading week (Mon-Fri)."""
    # Find last Friday
    weekday = now.weekday()  # Mon=0 ... Sun=6
    if weekday == 4:  # Today is Fri
        last_friday = now
    elif weekday < 4:  # Mon-Thu — prev Friday is 3-6 days ago
        last_friday = now - timedelta(days=(weekday + 3))
    else:  # Sat/Sun
        last_friday = now - timedelta(days=(weekday - 4))
    last_monday = last_friday - timedelta(days=4)
    return f"{last_monday.strftime('%d/%m')}–{last_friday.strftime('%d/%m/%Y')}"


def load_holidays():
    return ISRAEL_HOLIDAYS_2026


# ══════════════════════════════════════════════════════════════
# TWEET FETCHING
# ══════════════════════════════════════════════════════════════

def _extract_tweets_from_response(data):
    """twitterapi.io may return tweets at top level: {"tweets": [...]}.
    Older/other wrappers may return {"data": {"tweets": [...]}}. Support both.
    """
    if not isinstance(data, dict):
        return []

    tweets = data.get("tweets")
    if isinstance(tweets, list):
        return tweets

    nested = data.get("data")
    if isinstance(nested, dict) and isinstance(nested.get("tweets"), list):
        return nested.get("tweets", [])

    return []


def fetch_tweets():
    all_t = []
    for acc in ACCOUNTS:
        try:
            r = requests.get(
                "https://api.twitterapi.io/twitter/user/last_tweets",
                headers={"X-API-Key": TWITTER_API_KEY},
                params={"userName": acc},
                timeout=60,
            )
            print(f"  @{acc}: status={r.status_code}")
            if r.ok:
                data = r.json()
                tweets = _extract_tweets_from_response(data)
                print(f"    -> {len(tweets)} tweets")

                if not tweets:
                    msg = data.get("message") if isinstance(data, dict) else ""
                    status = data.get("status") if isinstance(data, dict) else ""
                    print(f"    -> Empty response. status={status!r}, message={msg!r}, keys={list(data.keys()) if isinstance(data, dict) else 'not-dict'}")

                for t in tweets[:10]:
                    if not isinstance(t, dict):
                        continue
                    text = t.get('text', '')
                    ts = t.get('createdAt') or t.get('created_at') or t.get('date') or ''
                    if text:
                        if ts:
                            all_t.append(f"@{acc} [{ts}]: {text}")
                        else:
                            all_t.append(f"@{acc}: {text}")
            else:
                print(f"    -> Error: {r.text[:500]}")
        except Exception as e:
            print(f"  Error fetching {acc}: {e}")
    return "\n\n".join(all_t)


# ══════════════════════════════════════════════════════════════
# PRIOR CONTEXT — avoid repeating the previous review
# ══════════════════════════════════════════════════════════════

def get_prior_review_context(review_type, data):
    """Inject yesterday's/last week's review to prevent Gemini from repeating it."""
    section_map = {
        "daily_prep":     "dailySummary",
        "daily_summary":  "dailyPrep",
        "weekly_prep":    "weeklySummary",
        "weekly_summary": "weeklyPrep",
        "live_news":      "dailySummary",
    }
    section_key = section_map.get(review_type)
    if not section_key:
        return ""
    prior = data.get(section_key)
    if not prior or not prior.get("sections"):
        return ""

    lines = [f"\n\nPRIOR REVIEW CONTEXT (for awareness — do not repeat verbatim):"]
    lines.append(f"Previous '{section_key}' title: {prior.get('title', '')}")
    for s in prior.get("sections", [])[:3]:
        heading = s.get("heading", "")
        content = s.get("content", "")
        if isinstance(content, list):
            content = "\n".join(content)
        # Truncate to keep prompt manageable
        content_short = content[:400] + ("..." if len(content) > 400 else "")
        lines.append(f"\n[{heading}]\n{content_short}")
    lines.append("\nThe new review should advance the story, not repeat the above.")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# PROMPT BUILDER
# ══════════════════════════════════════════════════════════════

def get_output_format_block(first_heading, expected_title):
    return f"""
RETURN ONLY VALID JSON. No markdown, no commentary, no code fences.

Required JSON shape:
{{
  "title": "{expected_title}",
  "date": "YYYY-MM-DD",
  "sections": [
    {{ "heading": "{first_heading}", "content": "..." }},
    {{ "heading": "...", "content": "..." }}
  ]
}}

CONTENT RULES:
- "content" is a string. Use "\\n" to separate bullets.
- Each bullet must start with "* " (asterisk + space).
- 3-6 bullets per section. Each bullet 1-2 short sentences.
- Use <b>bold</b> for company names, numbers, and key terms.
- Write in clear, professional Hebrew. RTL formatting handled by the client.
"""


def get_prompt(tweets, review_type, date_str, day_name, title_date_str,
                title_day_name, week_range, expected_title, prior_context,
                now_time):
    first_heading = EXPECTED_FIRST_HEADING[review_type]
    output_block = get_output_format_block(first_heading, expected_title)

    common_rules = f"""
You are a senior financial analyst writing a Hebrew market review for Israeli retail investors.
The market is the Tel Aviv Stock Exchange (TASE / בורסת תל אביב).

CRITICAL FACTS ABOUT TASE (Jan 4, 2026 reform):
- Trading days: Monday through Friday (Sunday is now closed)
- Hours: Mon-Thu 10:00-17:35, Fri 10:00-13:50 (short day)
- Currency: ILS (שקל)
- Main indices: ת"א-35, ת"א-125, ת"א-90, ת"א-בנקים-5, ת"א-טק-עילית

SOURCE QUALITY RULES:
- Source tweets are in Hebrew from: TASE_IL (official), TheMarker, Calcalist, Globes, SponserNews.
- TASE_IL is authoritative for official numbers/closures.
- Cross-reference numbers across sources. If only one source says something specific, hedge it.
- DO NOT invent prices, percentages, or specific numbers not in the tweets.
- If sources contradict, mention the disagreement.

LANGUAGE RULES:
- Professional, clear Hebrew. No English jargon unless universally used (e.g. ETF).
- Use Hebrew company names where standard (פועלים, לאומי, טבע, נייס).
- Numbers in Hebrew format: "1,250 נקודות", "עלייה של 1.3%".
"""

    if review_type == "daily_prep":
        type_specific = f"""
TASK: Write the morning prep review for Israeli traders BEFORE TASE opens at 10:00 today.
Date: {title_date_str} ({title_day_name}).
Local time now: {now_time}.

Focus on:
1. What happened overnight in global markets (US close, Asia)
2. What's expected in TASE today (key earnings, events, macro releases)
3. Sector/stock watch — what's worth following at the open

Sections (3-5 total):
- "נקודות מרכזיות" (must be first)
- "מה לעקוב היום"
- "מבט גלובלי" (US/Asia overnight context)
- "סקטורים וטריגרים"
"""
    elif review_type == "daily_summary":
        type_specific = f"""
TASK: Write the end-of-day summary AFTER TASE closes.
Date: {title_date_str} ({title_day_name}).

Focus on:
1. How the indices closed (ת"א-35, ת"א-125, banks, tech)
2. Notable gainers/losers
3. Volume/flow notes if mentioned in sources
4. Tomorrow's setup briefly

Sections (3-5 total):
- "סיכום המסחר" (must be first)
- "מניות בולטות"
- "מבט גלובלי"
- "מבט קדימה"
"""
    elif review_type == "weekly_prep":
        type_specific = f"""
TASK: Write the weekly prep covering the upcoming TASE trading week.
Week: {week_range}.

Focus on:
1. Key macro events (BoI rate decision, CPI, US Fed, ECB)
2. Earnings expected from major Israeli companies
3. Sector watch / themes for the week
4. Geopolitical risks

Sections (3-5 total):
- "נקודות מרכזיות לשבוע הקרוב" (must be first)
- "אירועי מאקרו בשבוע"
- "דוחות ואירועי חברה"
- "סיכונים ותרחישים"
"""
    elif review_type == "weekly_summary":
        type_specific = f"""
TASK: Write the weekly summary for the trading week that just ended.
Week: {week_range}.

Focus on:
1. How indices performed across the week
2. Biggest movers (sectors and stocks)
3. Main narrative drivers
4. What to watch next week

Sections (3-5 total):
- "סיכום השבוע" (must be first)
- "סקטורים ומניות"
- "מאקרו ונרטיב"
- "מבט קדימה"
"""
    else:  # live_news
        type_specific = f"""
TASK: Write a live news snapshot of what's happening RIGHT NOW.
Time: {now_time} on {title_date_str} ({title_day_name}).

Focus on:
1. Most recent material developments (from last 2-4 hours)
2. Market status if open / pre-open / post-close
3. Anything breaking

Sections (2-4 total):
- "חדשות אחרונות" (must be first)
- "מה זה אומר לשוק"
- "מה הלאה היום"
"""

    return f"""{common_rules}

{type_specific}

{output_block}

{prior_context}

SOURCE TWEETS (latest from each account):
{tweets}
"""


# ══════════════════════════════════════════════════════════════
# GEMINI CALL
# ══════════════════════════════════════════════════════════════

def call_gemini(prompt, temperature=0.2):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 4000,
            "responseMimeType": "application/json",
        }
    }
    r = requests.post(url, json=body, timeout=120)
    print(f"  Gemini status: {r.status_code}")
    if not r.ok:
        print(f"  Gemini error: {r.text[:500]}")
        return None
    try:
        data = r.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return text
    except Exception as e:
        print(f"  Gemini parse error: {e}")
        print(f"  Raw: {r.text[:500]}")
        return None


# ══════════════════════════════════════════════════════════════
# STRUCTURE ENFORCEMENT
# ══════════════════════════════════════════════════════════════

def normalize_bullets(text):
    """Ensure every line that should be a bullet starts with '* '."""
    if not isinstance(text, str):
        if isinstance(text, list):
            text = "\n".join(text)
        else:
            text = str(text)
    lines = text.split("\n")
    out = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        # Already a bullet?
        if s.startswith("* "):
            out.append(s)
        elif s.startswith("- "):
            out.append("* " + s[2:])
        elif s.startswith("• "):
            out.append("* " + s[2:])
        elif re.match(r"^\d+[\.\)]\s", s):
            out.append("* " + re.sub(r"^\d+[\.\)]\s+", "", s))
        else:
            # Multi-bullet on one line? Leave as paragraph
            out.append(s)
    return "\n".join(out)


def enforce_structure(result, review_type, expected_title, review_date):
    """Force the canonical title, date, and first heading onto the output."""
    if not isinstance(result, dict):
        return result

    # Force title
    result["title"] = expected_title

    # Force date
    result["date"] = review_date

    # Normalize sections
    sections = result.get("sections", [])
    if not isinstance(sections, list):
        sections = []

    # Force first heading
    first_heading = EXPECTED_FIRST_HEADING.get(review_type)
    if first_heading and sections:
        sections[0]["heading"] = first_heading

    # Normalize bullets in every section
    for s in sections:
        if "content" in s:
            s["content"] = normalize_bullets(s["content"])

    result["sections"] = sections
    return result


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    now = datetime.now(ISR_TZ)
    date_str = now.strftime("%Y-%m-%d")
    day_name = PY_TO_HEB[now.weekday()]

    holidays = load_holidays()
    today_is_trading = is_trading_day(now, holidays)

    print(f"Running {REVIEW_TYPE} for {date_str} ({day_name}), "
          f"trading day: {today_is_trading}")

    # Compute title date/week range
    title_date_str = date_str
    title_day_name = day_name
    week_range = None

    if REVIEW_TYPE == "daily_prep":
        target = now if today_is_trading else get_next_trading_day(now, holidays)
        title_date_str = target.strftime("%Y-%m-%d")
        title_day_name = PY_TO_HEB[target.weekday()]

    elif REVIEW_TYPE == "daily_summary":
        target = get_last_trading_day(now, holidays)
        title_date_str = target.strftime("%Y-%m-%d")
        title_day_name = PY_TO_HEB[target.weekday()]

    elif REVIEW_TYPE == "weekly_summary":
        week_range = get_prev_week_range_str(now)

    elif REVIEW_TYPE == "weekly_prep":
        weekday = now.weekday()  # Mon=0
        if weekday <= 4:  # Mon-Fri
            monday = now - timedelta(days=weekday)
        else:  # Sat/Sun
            monday = now + timedelta(days=(7 - weekday))
        friday = monday + timedelta(days=4)
        week_range = f"{monday.strftime('%d/%m')}–{friday.strftime('%d/%m/%Y')}"

    print(f"  Title date: {title_date_str} ({title_day_name}), "
          f"week_range: {week_range}")

    # Canonical review_date forced onto output
    if REVIEW_TYPE in ("daily_prep", "daily_summary"):
        review_date = title_date_str
    elif REVIEW_TYPE == "weekly_prep":
        weekday = now.weekday()
        if weekday <= 4:
            wp_monday = now - timedelta(days=weekday)
        else:
            wp_monday = now + timedelta(days=(7 - weekday))
        review_date = wp_monday.strftime("%Y-%m-%d")
    elif REVIEW_TYPE == "weekly_summary":
        weekday = now.weekday()
        if weekday == 4:
            last_friday = now
        elif weekday < 4:
            last_friday = now - timedelta(days=(weekday + 3))
        else:
            last_friday = now - timedelta(days=(weekday - 4))
        review_date = last_friday.strftime("%Y-%m-%d")
    else:  # live_news
        review_date = date_str

    print(f"  Review date forced: {review_date}")

    # Build expected title
    now_time_str = now.strftime("%H:%M")
    expected_title = build_expected_title(
        REVIEW_TYPE, title_day_name, title_date_str, week_range, now_time_str
    )
    print(f"  Expected title: {expected_title}")

    # Fetch tweets
    tweets = fetch_tweets()
    if not tweets:
        raise RuntimeError("No tweets fetched from twitterapi.io. Check TWITTER_API_KEY, account names, or response schema.")

    print(f"Fetched ~{len(tweets.split(chr(10)+chr(10)))} tweet blocks")

    # Load existing data.json for prior context
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            existing_data = json.load(f)
    except Exception:
        existing_data = {}

    prior_context = get_prior_review_context(REVIEW_TYPE, existing_data)

    # Build prompt and call Gemini
    prompt = get_prompt(
        tweets, REVIEW_TYPE, date_str, day_name,
        title_date_str, title_day_name, week_range,
        expected_title, prior_context, now_time_str
    )

    raw = call_gemini(prompt)
    if not raw:
        print("Gemini returned nothing. Aborting.")
        return

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        # Try to strip code fences
        cleaned = re.sub(r"^```(?:json)?", "", raw.strip())
        cleaned = re.sub(r"```$", "", cleaned).strip()
        try:
            result = json.loads(cleaned)
        except Exception as e2:
            print(f"Could not recover JSON: {e2}")
            print(f"Raw: {raw[:500]}")
            return

    # Enforce structure
    result = enforce_structure(result, REVIEW_TYPE, expected_title, review_date)

    # Map review type to data.json key
    section_keys = {
        "daily_prep":     "dailyPrep",
        "daily_summary":  "dailySummary",
        "weekly_prep":    "weeklyPrep",
        "weekly_summary": "weeklySummary",
        "live_news":      "liveNews",
    }
    key = section_keys[REVIEW_TYPE]

    # Build merged data
    existing_data[key] = result
    existing_data["lastUpdated"] = now.isoformat()
    if "marketStatus" not in existing_data:
        existing_data["marketStatus"] = {}
    existing_data["marketStatus"]["israelHolidays2026"] = holidays

    # Save
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=2)

    print(f"✓ Saved {key} to data.json")


if __name__ == "__main__":
    main()
