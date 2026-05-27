"""
tlv-market - Israeli market review system
Aligned with the Wall Street IL generator settings, adapted to TASE.

Sources: @TASE_IL, @TheMarker, @Calcalist, @Globesnews, @SponserNews
Trading days: Mon-Fri, after the 2026 TASE reform.
"""

import json
import os
import re
import sys
import time
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

# TLV sources
ACCOUNTS = ["TASE_IL", "TheMarker", "Calcalist", "Globes", "SponserNews"]

PY_TO_HEB = {
    0: "שני",
    1: "שלישי",
    2: "רביעי",
    3: "חמישי",
    4: "שישי",
    5: "שבת",
    6: "ראשון",
}

TASE_OPEN_DAYS = {0, 1, 2, 3, 4}  # Monday to Friday
TASE_FRIDAY = 4

ISRAEL_HOLIDAYS_2026 = [
    "2026-04-01",
    "2026-04-02",
    "2026-04-08",
    "2026-04-22",
    "2026-04-23",
    "2026-05-21",
    "2026-05-22",
    "2026-09-11",
    "2026-09-14",
    "2026-09-15",
    "2026-09-20",
    "2026-09-21",
    "2026-09-25",
    "2026-09-28",
    "2026-10-05",
]

EXPECTED_FIRST_HEADING = {
    "daily_prep": "נקודות מרכזיות",
    "daily_summary": "סיכום המסחר",
    "weekly_prep": "נקודות מרכזיות לשבוע הקרוב",
    "weekly_summary": "סיכום השבוע",
    "live_news": "חדשות אחרונות",
    "events": "אירועים",
}

DATA_KEYS = {
    "daily_prep": "dailyPrep",
    "daily_summary": "dailySummary",
    "weekly_prep": "weeklyPrep",
    "weekly_summary": "weeklySummary",
    "live_news": "liveNews",
}


# ══════════════════════════════════════════════════════════════
# DATE HELPERS
# ══════════════════════════════════════════════════════════════

def load_holidays():
    return ISRAEL_HOLIDAYS_2026


def is_trading_day(dt, holidays):
    if dt.weekday() not in TASE_OPEN_DAYS:
        return False
    return dt.strftime("%Y-%m-%d") not in holidays


def get_next_trading_day(now, holidays):
    d = now + timedelta(days=1)
    for _ in range(14):
        if is_trading_day(d, holidays):
            return d
        d += timedelta(days=1)
    return now + timedelta(days=1)


def get_last_trading_day(now, holidays):
    d = now
    for _ in range(14):
        if is_trading_day(d, holidays):
            return d
        d -= timedelta(days=1)
    return now - timedelta(days=1)


def get_prev_week_range_str(now):
    weekday = now.weekday()
    if weekday == 4:
        last_friday = now
    elif weekday < 4:
        last_friday = now - timedelta(days=(weekday + 3))
    else:
        last_friday = now - timedelta(days=(weekday - 4))
    last_monday = last_friday - timedelta(days=4)
    return f"{last_monday.strftime('%d/%m')}-{last_friday.strftime('%d/%m/%Y')}"


def get_next_week_range_str(now):
    weekday = now.weekday()
    if weekday <= 4:
        monday = now - timedelta(days=weekday)
    else:
        monday = now + timedelta(days=(7 - weekday))
    friday = monday + timedelta(days=4)
    return f"{monday.strftime('%d/%m')}-{friday.strftime('%d/%m/%Y')}"


def build_expected_title(review_type, title_day_name, title_date_str, week_range=None, now_time=None):
    if review_type == "daily_prep":
        return f"נקודות חשובות לקראת פתיחת המסחר בתל אביב 🇮🇱 - יום {title_day_name} {title_date_str}"
    if review_type == "daily_summary":
        return f"סיכום יום המסחר בתל אביב 🇮🇱 - יום {title_day_name} {title_date_str}"
    if review_type == "weekly_prep":
        return f"הכנה לשבוע מסחר בתל אביב 🇮🇱 - {week_range}"
    if review_type == "weekly_summary":
        return f"סיכום שבוע המסחר בתל אביב 🇮🇱 - {week_range}"
    if review_type == "live_news":
        return f"מה קורה עכשיו בתל אביב 🇮🇱 - יום {title_day_name}, {title_date_str} | {now_time}"
    if review_type == "events":
        return f"אירועים כלכליים בתל אביב 🇮🇱 - {title_date_str}"
    return ""


def calculate_review_dates(now, holidays, review_type):
    date_str = now.strftime("%Y-%m-%d")
    day_name = PY_TO_HEB[now.weekday()]
    title_date_str = date_str
    title_day_name = day_name
    week_range = None

    if review_type == "daily_prep":
        target = now if is_trading_day(now, holidays) else get_next_trading_day(now, holidays)
        title_date_str = target.strftime("%Y-%m-%d")
        title_day_name = PY_TO_HEB[target.weekday()]
        review_date = title_date_str

    elif review_type == "daily_summary":
        target = get_last_trading_day(now, holidays)
        title_date_str = target.strftime("%Y-%m-%d")
        title_day_name = PY_TO_HEB[target.weekday()]
        review_date = title_date_str

    elif review_type == "weekly_prep":
        week_range = get_next_week_range_str(now)
        weekday = now.weekday()
        if weekday <= 4:
            monday = now - timedelta(days=weekday)
        else:
            monday = now + timedelta(days=(7 - weekday))
        review_date = monday.strftime("%Y-%m-%d")

    elif review_type == "weekly_summary":
        week_range = get_prev_week_range_str(now)
        weekday = now.weekday()
        if weekday == 4:
            last_friday = now
        elif weekday < 4:
            last_friday = now - timedelta(days=(weekday + 3))
        else:
            last_friday = now - timedelta(days=(weekday - 4))
        review_date = last_friday.strftime("%Y-%m-%d")

    else:
        review_date = date_str

    return date_str, day_name, title_date_str, title_day_name, week_range, review_date


# ══════════════════════════════════════════════════════════════
# TWEETS
# ══════════════════════════════════════════════════════════════

def _extract_tweets_from_response(payload):
    """Support several twitterapi.io response shapes."""
    if not isinstance(payload, dict):
        return []

    candidates = [
        payload.get("tweets"),
        payload.get("data", {}).get("tweets") if isinstance(payload.get("data"), dict) else None,
        payload.get("data", {}).get("data", {}).get("tweets") if isinstance(payload.get("data"), dict) else None,
        payload.get("result", {}).get("tweets") if isinstance(payload.get("result"), dict) else None,
    ]

    for c in candidates:
        if isinstance(c, list):
            return c
    return []


def fetch_tweets():
    all_tweets = []
    for acc in ACCOUNTS:
        try:
            r = requests.get(
                f"https://api.twitterapi.io/twitter/user/last_tweets?userName={acc}",
                headers={"X-API-Key": TWITTER_API_KEY},
                timeout=30,
            )
            print(f"  @{acc}: status={r.status_code}")

            if not r.ok:
                print(f"    -> Error: {r.text[:300]}")
                continue

            data = r.json()
            tweets = _extract_tweets_from_response(data)
            print(f"    -> {len(tweets)} tweets")

            for t in tweets[:12]:
                if not isinstance(t, dict):
                    continue
                text = (t.get("text") or t.get("fullText") or t.get("content") or "").strip()
                if not text:
                    continue
                ts = t.get("createdAt") or t.get("created_at") or t.get("date") or t.get("time") or ""
                if ts:
                    all_tweets.append(f"@{acc} [{ts}]: {text}")
                else:
                    all_tweets.append(f"@{acc}: {text}")

        except Exception as e:
            print(f"  Error fetching @{acc}: {e}")

    return "\n\n".join(all_tweets)


# ══════════════════════════════════════════════════════════════
# PRIOR CONTEXT
# ══════════════════════════════════════════════════════════════

def get_prior_review_context(review_type, data):
    if not isinstance(data, dict):
        return ""

    section_map = {
        "daily_prep": "dailySummary",
        "daily_summary": "dailyPrep",
        "weekly_prep": "weeklySummary",
        "weekly_summary": "weeklyPrep",
        "live_news": "dailySummary",
    }
    section_key = section_map.get(review_type)
    prior = data.get(section_key) if section_key else None

    if not prior or not isinstance(prior, dict) or not prior.get("sections"):
        return ""

    lines = [
        "",
        "══ PRIOR REVIEW CONTEXT - for awareness only, do not repeat verbatim ══",
        f"Previous review key: {section_key}",
        f"Previous title: {prior.get('title', '')}",
    ]

    for s in prior.get("sections", [])[:2]:
        heading = s.get("heading", "")
        content = s.get("content", "")
        if isinstance(content, list):
            content = "\n".join(str(x) for x in content)
        content = str(content)
        content_short = content[:800] + ("..." if len(content) > 800 else "")
        lines.append(f"\n[{heading}]\n{content_short}")

    lines.append("The new review must advance the story and avoid repetition.")
    lines.append("══════════════════════════════════════════════════════════════")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# PROMPTS
# ══════════════════════════════════════════════════════════════

SHARED_RULES = """Rules:
- Write ONLY in Hebrew. Use English only for tickers, index names, and universally known financial terms.
- Be specific. Prefer numbers, dates, percentages, index names, company names, and event names.
- Do NOT invent index levels, percentages, prices, dates, or facts.
- Every number must come from one of these sources: source tweets, Google Search verification, or TASE official information.
- If a number cannot be verified, omit it or clearly write that it was not verified.
- No buy/sell recommendations and no investment advice.
- No personal opinions. Use analytical, factual language.
- No separate "שורה תחתונה", "סיכום", "מסקנה", or closing paragraph.
- Do NOT repeat the same item in multiple bullets.
- Do NOT use HTML, markdown bold, emojis inside bullets, or decorative symbols.
- Each bullet must start with "* ".
- Keep the language clean, institutional, and practical.

TASE CONTEXT:
- Market: Tel Aviv Stock Exchange, בורסת תל אביב.
- Trading days: Monday to Friday.
- Hours: Monday to Thursday 10:00-17:35. Friday 10:00-13:50.
- Closed: Sunday and Saturday, plus Israeli exchange holidays.
- Main indices: ת"א-35, ת"א-125, ת"א-90, ת"א-בנקים-5, ת"א-טק-עילית.
- Currency: ILS, שקל.
- Core sources: @TASE_IL, @TheMarker, @Calcalist, @Globes, @SponserNews.

ACCURACY RULES:
- TASE_IL is the preferred source for official market status and official exchange information.
- For current index levels and percentage changes, use Google Search or TASE official information when available.
- If source tweets are stale or insufficient, say less. Do not fill gaps with guesses.
- If sources contradict each other, prefer the official exchange source or use neutral wording.
- Do not call unverified moves "sharp", "dramatic", or "historic".
- If the market is open, do not write a final daily summary as if the session has closed.
- If the market is closed, do not write as if active trading is still occurring.
"""


def get_output_format_block(first_heading, expected_title):
    return f"""
CRITICAL - OUTPUT FORMAT:
Return ONLY valid JSON. No markdown, no commentary, no code fences.

Required JSON shape:
{{
  "title": "{expected_title}",
  "date": "YYYY-MM-DD",
  "sections": [
    {{ "heading": "{first_heading}", "content": "* bullet 1\\n* bullet 2" }}
  ]
}}

Mandatory structure:
- EXACTLY 1 section in the "sections" array.
- The only section heading MUST be EXACTLY "{first_heading}".
- The title field MUST be EXACTLY "{expected_title}".
- The section content MUST be a single string.
- Content must contain 6-12 bullets for daily reviews, 8-14 bullets for weekly reviews, 4-7 bullets for live news.
- Every bullet must start with "* ".
- Do NOT add another section.
- Do NOT add "שורה תחתונה".
- Do NOT use <b>, <strong>, **, #, ■, 📍, or decorative formatting.
"""


def get_prompt(
    tweets,
    review_type,
    date_str,
    day_name,
    title_date_str,
    title_day_name,
    week_range,
    expected_title,
    prior_context,
    now_time,
    today_is_trading,
):
    first_heading = EXPECTED_FIRST_HEADING.get(review_type, "נקודות מרכזיות")
    format_block = get_output_format_block(first_heading, expected_title)

    tweets_block = f"""SOURCE POSTS FROM X:
{tweets}
"""
    if prior_context:
        tweets_block = prior_context + "\n\n" + tweets_block

    if review_type == "daily_prep":
        if today_is_trading:
            timing = f"""The target date {title_date_str} is a TASE trading day.
This is a prep note. If the script runs before 10:00, write in future tense for the trading day.
If the script runs after 10:00, write it as an update for the remainder of the session, not as a final summary."""
        else:
            timing = f"""The target date {title_date_str} is not a TASE trading day. State when trading resumes and focus on the next trading day."""

        task = f"""You are a senior Israeli capital-market analyst writing a PRE-MARKET / intraday preparation note for TASE.

DATES:
- Script run date: {date_str} ({day_name})
- Target review date: {title_date_str} ({title_day_name})
- Local time now: {now_time}
- {timing}

Focus:
1. What investors should watch in Tel Aviv today or on the next trading day.
2. Global backdrop relevant to Israel: Wall Street close, futures if available, Europe/Asia, rates, oil, shekel-dollar.
3. Israeli macro and policy events: Bank of Israel, CPI, fiscal/geopolitical developments.
4. Company or sector triggers in Tel Aviv.
5. Avoid final closing language unless the trading day has actually ended.
"""

    elif review_type == "daily_summary":
        task = f"""You are a senior Israeli capital-market analyst writing an END-OF-DAY market wrap for TASE.

DATES:
- Summary date: {title_date_str} ({title_day_name})
- Script run date: {date_str} ({day_name})
- Local time now: {now_time}

Focus:
1. How ת"א-35, ת"א-125, banks, tech, and other relevant indices moved.
2. Notable stocks and sectors, only if supported by sources or Google verification.
3. Volume, flows, institutional activity, bonds, shekel, or macro if materially relevant.
4. Explain what drove the session and what may matter tomorrow.
5. Write in past tense only if the session has already closed.
"""

    elif review_type == "weekly_prep":
        task = f"""You are a senior Israeli capital-market strategist writing a weekly outlook for TASE.

WEEK:
- Target week: {week_range}
- Script run date: {date_str} ({day_name})
- Local time now: {now_time}

Focus:
1. Events scheduled for the coming TASE week.
2. Israeli macro events: CPI, Bank of Israel, bond auctions, fiscal data, shekel.
3. Major company reports or corporate events if available.
4. Global catalysts relevant to Israel: Wall Street, Fed, oil, geopolitics.
5. Forward-looking only. Do not recap last week's performance.
"""

    elif review_type == "weekly_summary":
        task = f"""You are a senior Israeli capital-market strategist writing a weekly TASE review.

WEEK:
- Review week: {week_range}
- Script run date: {date_str} ({day_name})
- Local time now: {now_time}

Focus:
1. Weekly performance of major TASE indices.
2. Main sector and stock drivers across the week.
3. Macro, rates, shekel, bonds, commodities, and geopolitical effects.
4. What the week signals for the next trading week.
5. Use weekly changes only. Do not confuse a daily move with a weekly move.
"""

    elif review_type == "live_news":
        two_hours_ago = (datetime.now(ISR_TZ) - timedelta(hours=2)).strftime("%H:%M")
        task = f"""You are a live market-news editor for Israeli investors.

DATES:
- Current date: {date_str} ({day_name})
- Current time: {now_time} Israel time
- Lookback window: only material items from {two_hours_ago} to {now_time}, if available.

Focus:
1. Real breaking items: official exchange messages, material company announcements, sharp market moves, macro releases, geopolitical items.
2. If there are no material live items, return one bullet saying there is no material live update.
3. Keep bullets short and factual.
"""

    else:
        task = f"""You are a financial calendar editor for Israeli investors.
Create a concise TASE-focused economic events update for {date_str}.
"""

    return f"""{task}

{SHARED_RULES}

{format_block}

SEARCH / VERIFICATION INSTRUCTIONS:
- Use Google Search when needed to verify current TASE index data, major company moves, Israeli macro releases, and exact dates.
- Prefer official TASE or official company information for exchange facts.
- Do not cite sources in the output. The website displays a general sources line.

{tweets_block}

Return ONLY the JSON object."""


# ══════════════════════════════════════════════════════════════
# GEMINI CALLS
# ══════════════════════════════════════════════════════════════

def _extract_json_object(text):
    if not isinstance(text, str):
        raise ValueError("Gemini response text is not a string")

    text = text.strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    text = re.sub(r"\s*\[\d+(?:,\s*\d+)*\]", "", text)

    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in Gemini response")

    depth = 0
    end = None
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end is None:
        raise ValueError("JSON object was not closed")

    return text[start:end]


def call_gemini(prompt, temperature=0.2, model="gemini-2.5-pro", use_search=True):
    max_retries = 3
    last_error = None

    for attempt in range(max_retries):
        try:
            body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": 8192,
                },
            }
            if use_search:
                body["tools"] = [{"google_search": {}}]

            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json=body,
                timeout=180,
            )

            print(f"  Gemini status: {r.status_code} (attempt {attempt + 1}/{max_retries}, model={model}, temp={temperature})")

            if r.status_code in (429, 500, 502, 503, 504):
                last_error = RuntimeError(f"Gemini transient error {r.status_code}: {r.text[:500]}")
                if attempt < max_retries - 1:
                    wait = 30 * (attempt + 1)
                    print(f"  Gemini transient error, retrying in {wait}s")
                    time.sleep(wait)
                    continue

            if not r.ok:
                raise RuntimeError(f"Gemini returned {r.status_code}: {r.text[:1000]}")

            resp_data = r.json()
            candidate = resp_data.get("candidates", [{}])[0]
            parts = candidate.get("content", {}).get("parts", [])

            text = ""
            for part in parts:
                if "text" in part:
                    text += part["text"]

            if not text.strip():
                raise RuntimeError(f"Gemini returned no text: {str(resp_data)[:1000]}")

            json_text = _extract_json_object(text)
            return json.loads(json_text)

        except Exception as e:
            last_error = e
            print(f"  Gemini call error: {e}")
            if attempt < max_retries - 1:
                wait = 30 * (attempt + 1)
                print(f"  Retrying in {wait}s")
                time.sleep(wait)

    raise RuntimeError(f"Gemini failed after {max_retries} attempts: {last_error}")


# ══════════════════════════════════════════════════════════════
# POST PROCESSING
# ══════════════════════════════════════════════════════════════

_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[\*\-•■●▪▫◦‣⁃]|\d+[\.\)])\s+")


def clean_formatting(text):
    text = str(text)
    text = re.sub(r"</?(?:b|strong|em|i)>", "", text)
    text = text.replace("**", "")
    text = text.replace("📍", "")
    text = text.replace("■", "")
    return text.strip()


def normalize_bullets(text):
    if isinstance(text, list):
        text = "\n".join(str(x) for x in text)
    elif text is None:
        text = ""
    else:
        text = str(text)

    text = clean_formatting(text)
    lines = [x.strip() for x in text.split("\n") if x.strip()]

    if not lines:
        return ""

    out = []
    for line in lines:
        line = clean_formatting(line)
        line = _BULLET_PREFIX_RE.sub("", line).strip()
        if not line:
            continue
        out.append(f"* {line}")

    return "\n".join(out)


def enforce_structure(result, review_type, expected_title, review_date):
    if not isinstance(result, dict):
        raise ValueError("Result is not a JSON object")

    first_heading = EXPECTED_FIRST_HEADING.get(review_type, "נקודות מרכזיות")

    result["title"] = expected_title
    result["date"] = review_date

    sections = result.get("sections", [])
    if not isinstance(sections, list) or not sections:
        raise ValueError("Gemini output has no valid sections")

    merged_parts = []
    dropped = 0

    for section in sections:
        if not isinstance(section, dict):
            continue

        heading = str(section.get("heading", ""))
        content = section.get("content", "")

        if "שורה תחתונה" in heading or heading.lower().strip() in {"bottom line", "summary", "סיכום", "מסקנה"}:
            dropped += 1
            continue

        if isinstance(content, list):
            content = "\n".join(str(x) for x in content)

        content = str(content).strip()
        if content:
            merged_parts.append(content)

    if not merged_parts:
        for section in sections:
            if isinstance(section, dict):
                content = section.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(str(x) for x in content)
                if str(content).strip():
                    merged_parts.append(str(content).strip())

    if not merged_parts:
        raise ValueError("Gemini output contains no section content")

    normalized = normalize_bullets("\n".join(merged_parts))
    bullet_count = sum(1 for line in normalized.split("\n") if line.strip().startswith("* "))

    if bullet_count == 0:
        raise ValueError("No valid bullets after normalization")

    if len(sections) != 1 or dropped:
        print(f"  ✅ Sections normalized: {len(sections)} -> 1, dropped bottom-line sections: {dropped}")

    result["sections"] = [{
        "heading": first_heading,
        "content": normalized,
    }]

    return result


def fact_check_review(result, review_type):
    """Lightweight second-pass check, aligned with the Wall Street workflow idea."""
    if not isinstance(result, dict) or review_type == "events":
        return result

    prompt = f"""You are a strict Hebrew financial-news fact checker for a TASE market review.

TASK:
Check the JSON review below for:
- invented numbers,
- unverified index levels or percentage changes,
- contradiction between market status and tense,
- vague claims without support,
- separate bottom-line or summary language,
- wrong financial terminology.

If something is clearly unsupported, remove or neutralize it.
Keep the exact JSON shape:
{{"title": "...", "date": "...", "sections": [{{"heading": "...", "content": "* bullet\\n* bullet"}}]}}

Rules:
- Return valid JSON only.
- Keep exactly one section.
- Keep bullets starting with "* ".
- Do not add citations.
- Do not add new facts unless they are safely verifiable with Google Search.

JSON REVIEW:
{json.dumps(result, ensure_ascii=False)}
"""

    try:
        checked = call_gemini(prompt, temperature=0.1, model="gemini-2.5-flash", use_search=True)
        if isinstance(checked, dict) and checked.get("sections"):
            print("  ✅ Fact-check pass completed")
            return checked
        print("  Fact-check returned invalid structure, using original")
        return result
    except Exception as e:
        print(f"  Fact-check failed, using original: {e}")
        return result


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    now = datetime.now(ISR_TZ)
    holidays = load_holidays()
    today_is_trading = is_trading_day(now, holidays)

    date_str, day_name, title_date_str, title_day_name, week_range, review_date = calculate_review_dates(
        now, holidays, REVIEW_TYPE
    )

    now_time_str = now.strftime("%H:%M")
    expected_title = build_expected_title(
        REVIEW_TYPE,
        title_day_name,
        title_date_str,
        week_range,
        now_time_str,
    )

    print(f"Running {REVIEW_TYPE} for {date_str} ({day_name})")
    print(f"  Trading day: {today_is_trading}")
    print(f"  Target date: {title_date_str} ({title_day_name})")
    print(f"  Review date forced: {review_date}")
    print(f"  Week range: {week_range}")
    print(f"  Expected title: {expected_title}")

    tweets = fetch_tweets()
    if not tweets.strip():
        raise RuntimeError("No tweets fetched. Failing the workflow instead of producing a green empty run.")

    print(f"Fetched ~{len(tweets.split(chr(10) + chr(10)))} tweet blocks")

    try:
        with open("data.json", "r", encoding="utf-8") as f:
            existing_data = json.load(f)
    except Exception:
        existing_data = {}

    prior_context = get_prior_review_context(REVIEW_TYPE, existing_data)

    prompt = get_prompt(
        tweets=tweets,
        review_type=REVIEW_TYPE,
        date_str=date_str,
        day_name=day_name,
        title_date_str=title_date_str,
        title_day_name=title_day_name,
        week_range=week_range,
        expected_title=expected_title,
        prior_context=prior_context,
        now_time=now_time_str,
        today_is_trading=today_is_trading,
    )

    result = call_gemini(prompt, temperature=0.2, model="gemini-2.5-pro", use_search=True)
    result = enforce_structure(result, REVIEW_TYPE, expected_title, review_date)
    result = fact_check_review(result, REVIEW_TYPE)
    result = enforce_structure(result, REVIEW_TYPE, expected_title, review_date)

    if REVIEW_TYPE not in DATA_KEYS:
        raise ValueError(f"Unsupported REVIEW_TYPE for data.json writing: {REVIEW_TYPE}")

    key = DATA_KEYS[REVIEW_TYPE]
    existing_data[key] = result
    existing_data["lastUpdated"] = now.isoformat()

    if "marketStatus" not in existing_data or not isinstance(existing_data["marketStatus"], dict):
        existing_data["marketStatus"] = {}

    existing_data["marketStatus"]["israelHolidays2026"] = holidays

    # Keep events object if the site expects it.
    if "events" not in existing_data or not isinstance(existing_data.get("events"), dict):
        existing_data["events"] = {"lastUpdated": None, "items": []}

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=2)

    print(f"✓ Saved {key} to data.json")
    print(f"✓ Bullet count: {len(result['sections'][0]['content'].splitlines())}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
