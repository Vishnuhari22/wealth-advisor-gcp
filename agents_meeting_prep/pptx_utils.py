"""Shared PPTX generation utilities — used by both main.py (Gmail attachment) and api.py (local download)."""

import io
import re


def strip_html(html: str) -> list[str]:
    """Strip HTML tags and return a list of bullet strings."""
    items = re.findall(r"<li[^>]*>(.*?)</li>", html, re.IGNORECASE | re.DOTALL)
    if items:
        return [re.sub(r"<[^>]+>", "", item).strip() for item in items if item.strip()]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.IGNORECASE | re.DOTALL)
    if rows:
        result = []
        for row in rows:
            cells = re.findall(
                r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.IGNORECASE | re.DOTALL
            )
            row_text = "  |  ".join(
                re.sub(r"<[^>]+>", "", c).strip() for c in cells if c.strip()
            )
            if row_text.strip():
                result.append(row_text)
        if result:
            return result
    if " | " in html:
        return [s.strip() for s in html.split(" | ") if s.strip()]
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return [line.strip() for line in text.splitlines() if line.strip()]


def html_to_pptx(meeting_json: dict, subject: str, meeting_time: str) -> bytes:
    """Build a 7-slide PPTX from meeting_json structured data and return bytes."""
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor

    prs = Presentation()
    prs.slide_width = Inches(13.33)
    prs.slide_height = Inches(7.5)

    def add_title_slide(title: str, subtitle: str):
        slide = prs.slides.add_slide(prs.slide_layouts[0])
        slide.shapes.title.text = title
        slide.shapes.title.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(
            0x44, 0x44, 0x44
        )
        slide.placeholders[1].text = subtitle

    def add_content_slide(title: str, bullets: list[str]):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = title
        tf = slide.placeholders[1].text_frame
        tf.clear()
        for i, bullet in enumerate(bullets[:20]):
            p = tf.add_paragraph() if i > 0 else tf.paragraphs[0]
            p.text = bullet
            p.level = 0
            p.font.size = Pt(13)

    mj = meeting_json
    client = mj.get("client", {})
    holdings = mj.get("holdings", {})
    risk = mj.get("risk", {})

    # Slide 1: Cover
    client_name = client.get("name", subject)
    meeting_time_str = client.get("meetingTime", meeting_time)
    add_title_slide(
        client_name, f"{subject}  |  Meeting Preparation Brief  |  {meeting_time_str}"
    )

    # Slide 2: Holdings Snapshot
    holdings_bullets = []
    perf = holdings.get("performance", [])
    if perf:
        holdings_bullets.append("Fund  |  Type  |  Weight  |  2024  |  2025  |  Trend")
        for f in perf:
            if not isinstance(f, dict):
                holdings_bullets.append(str(f))
                continue
            holdings_bullets.append(
                f"{f.get('fund','')}  |  {f.get('type','')}  |  {f.get('weight','')}%  |  "
                f"{f.get('y2024','')}  |  {f.get('y2025','')}  |  {f.get('trend','')}"
            )
    if holdings.get("returns"):
        holdings_bullets.append("── Portfolio Returns ──")
    for r in holdings.get("returns", []):
        if isinstance(r, dict):
            holdings_bullets.append(f"{r.get('label','')}: {r.get('value','')}")
        else:
            holdings_bullets.append(str(r))
    for insight in holdings.get("keyInsight", []):
        holdings_bullets.append(f"Key Insight: {insight}")
    add_content_slide("Holdings Snapshot", holdings_bullets or ["No data available"])

    # Slide 3: Risks & Opportunities
    risk_bullets = []
    for section in risk.get("skillSections", []):
        if not isinstance(section, dict):
            risk_bullets.append(str(section))
            continue
        signals = section.get("signals", [])
        if signals:
            risk_bullets.append(f"── {section.get('label', '')} ──")
            for s in signals:
                if not isinstance(s, dict):
                    risk_bullets.append(str(s))
                    continue
                assessment = s.get("assessment")
                observation = s.get("observation")
                materiality = (assessment.get("materiality", "") if isinstance(assessment, dict) else str(assessment or "")).upper()
                summary = observation.get("summary", "") if isinstance(observation, dict) else str(observation or "")
                label = str(s.get("signal_type", "")).replace("_", " ")
                risk_bullets.append(f"[{materiality}] {label}: {summary}")
    add_content_slide("Risks & Opportunities", risk_bullets or ["No data available"])

    # Slide 4: Interaction History
    activity_bullets = [
        f"{a.get('date','')} — {a.get('summary','')}  →  {a.get('decision','')}"
        if isinstance(a, dict)
        else str(a)
        for a in mj.get("activity", [])
    ]
    add_content_slide("Interaction History", activity_bullets or ["No data available"])

    # Slide 5: Fund News
    news_bullets = []
    for n in mj.get("fundNews", []):
        if isinstance(n, dict):
            ticker = n.get("ticker", "")
            weight = n.get("weight_pct")
            summary = n.get("summary", "")
            weight_str = f" ({weight}%)" if weight is not None else ""
            news_bullets.append(f"{ticker}{weight_str}: {summary}")
        else:
            news_bullets.append(str(n))
    add_content_slide("Fund News", news_bullets or ["No data available"])

    # Slide 6: Next Best Action
    nba_bullets = []
    trigger = client.get("nbaTrigger", "") if isinstance(client, dict) else ""
    if trigger:
        nba_bullets.append(f"Trigger: {trigger}")
    for a in mj.get("nextBestActions", []):
        if not isinstance(a, dict):
            nba_bullets.append(str(a))
            continue
        nba_bullets.append(f"{a.get('label','')}: {a.get('desc','')}")
        if a.get("agentReasoning"):
            nba_bullets.append(f"  Rationale: {a.get('agentReasoning','')}")
    add_content_slide("Next Best Action", nba_bullets or ["No data available"])

    # Slide 7: Discussion Angles
    angle_bullets = [
        f"{d.get('title','')}: {d.get('desc','')}"
        if isinstance(d, dict)
        else str(d)
        for d in mj.get("discussionAngles", [])
    ]
    add_content_slide(
        "Discussion Angles & Agenda", angle_bullets or ["No data available"]
    )
    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()
