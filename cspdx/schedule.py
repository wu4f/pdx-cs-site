"""Generate the CS course schedule HTML page from Banner SSB.

Fetches the 3 most recent terms, builds a tabbed HTML table page,
and renders it through base.html so it matches the rest of the site.
Written to build/site/course-schedule/index.html, overwriting whatever
the Google Doc tab produced for that slug.
"""
from __future__ import annotations
from datetime import datetime, timezone
import html
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import jinja2
import requests

if TYPE_CHECKING:
    pass

BASE_URL = "https://app.banner.pdx.edu/StudentRegistrationSsb/ssb"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PSU-schedule-fetcher/1.0)",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

_COLUMNS = [
    ("CRN",        8),
    ("Course",     10),
    ("Section",    8),
    ("Title",      36),
    ("Credits",    8),
    ("Days",       7),
    ("Time",       13),
    ("Instructor", 24),
]


def _get_terms(n: int = 8) -> list[dict]:
    resp = requests.get(
        f"{BASE_URL}/classSearch/getTerms",
        params={"searchTerm": "", "offset": 1, "max": 100},
        headers=_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()[:n]


def _establish_session(term_code: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(_HEADERS)
    session.get(f"{BASE_URL}/classSearch/classSearch", timeout=30)
    session.get(
        f"{BASE_URL}/term/search",
        params={
            "mode": "search",
            "term": term_code,
            "studyPath": "",
            "studyPathText": "",
            "startDatepicker": "",
            "endDatepicker": "",
        },
        timeout=30,
    )
    return session


def _fetch_subject(session: requests.Session, term_code: str, subject: str, page_size: int = 500) -> list[dict]:
    courses: list[dict] = []
    offset = 0
    while True:
        resp = session.get(
            f"{BASE_URL}/searchResults/searchResults",
            params={
                "txt_subject": subject,
                "txt_term": term_code,
                "startDatepicker": "",
                "endDatepicker": "",
                "pageOffset": offset,
                "pageMaxSize": page_size,
                "sortColumn": "subjectDescription",
                "sortDirection": "asc",
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success") or not data.get("data"):
            break
        courses.extend(data["data"])
        total = data.get("totalCount", 0)
        offset += page_size
        if offset >= total:
            break
    return courses


def _fetch_courses(term_code: str, subjects: list[str] | None = None, page_size: int = 500) -> list[dict]:
    """Fetch courses for one or more subject codes and return them merged.

    Each subject gets its own session because Banner SSB carries subject state
    server-side; reusing a session across subjects returns stale results.
    """
    if subjects is None:
        subjects = ["CS"]
    courses: list[dict] = []
    for subject in subjects:
        session = _establish_session(term_code)
        courses.extend(_fetch_subject(session, term_code, subject, page_size))
    return courses


def _meeting_info(course: dict) -> tuple[str, str]:
    meetings = course.get("meetingsFaculty", [])
    if not meetings:
        return "", ""
    mt = meetings[0].get("meetingTime", {}) or {}
    day_map = [
        ("monday", "M"), ("tuesday", "T"), ("wednesday", "W"),
        ("thursday", "R"), ("friday", "F"), ("saturday", "S"), ("sunday", "U"),
    ]
    days = "".join(abbr for key, abbr in day_map if mt.get(key))
    begin, end = mt.get("beginTime") or "", mt.get("endTime") or ""
    time_str = ""
    if begin and end and len(begin) == 4 and len(end) == 4:
        time_str = f"{begin[:2]}:{begin[2:]}-{end[:2]}:{end[2:]}"
    return days, time_str


def _instructor(course: dict) -> str:
    faculty = course.get("faculty") or []
    primary = next((f for f in faculty if f.get("primaryIndicator")), faculty[0] if faculty else None)
    return (primary.get("displayName") or "").strip() if primary else ""


def _row_values(course: dict) -> list[str]:
    days, time_str = _meeting_info(course)
    return [
        str(course.get("courseReferenceNumber", "")),
        f"{course.get('subject', '')} {course.get('courseNumber', '')}".strip(),
        str(course.get("sequenceNumber", "")),
        html.unescape(course.get("courseTitle", "") or ""),
        str(course.get("creditHours", "")),
        days,
        time_str,
        _instructor(course),
    ]


def _build_table(courses: list[dict]) -> str:
    headers = [col[0] for col in _COLUMNS]
    thead_cells = "".join(f"<th>{h}</th>" for h in headers)
    rows = []
    for course in courses:
        cells = "".join(f"<td>{html.escape(v)}</td>" for v in _row_values(course))
        rows.append(f"<tr>{cells}</tr>")
    tbody = "\n".join(rows)
    return (
        f'<div class="table-wrap">'
        f"<table>"
        f"<thead><tr>{thead_cells}</tr></thead>"
        f"<tbody>{tbody}</tbody>"
        f"</table>"
        f"</div>"
    )


# Where the data on this page comes from, and the one thing it can't show:
# Banner returns meeting days and times to an anonymous caller but withholds
# building and room, so anyone who needs those has to go to the source signed
# in. Built from BASE_URL so the link can't drift from the endpoints we fetch.
_SOURCE_URL = f"{BASE_URL}/term/termSelection?mode=search"

_SOURCE_NOTE = f"""\
<div class="sched-note">
  <p>
    Course listings come from Portland State University's Banner site.  For building and room 
    assignments, visit PSU's 
    <a href="{_SOURCE_URL}" target="_blank" rel="noopener">Banner class schedule search page</a>.
    Then, sign in with your PSU account,
    pick a term, and search the <strong>Computer Science</strong> and
    <strong>Artificial Intelligence</strong> subject codes.
  </p>
</div>"""


# The slug of the placeholder Google Doc tab that owns /course-schedules/.
# The generated page overwrites that tab's HTML; _build_context_text() likewise
# replaces its text, so the chatbot sees the actual offerings instead of the
# tab's "do not modify" maintenance note.
SCHEDULE_SECTION_ID = "course-schedules"


def _context_line(course: dict) -> str:
    """One course section as a single compact line for the chat context.

    Built from _row_values() so the text the chatbot reads can never drift from
    the table the page shows. Blank meeting times and instructors are spelled
    out rather than left empty — an empty field reads as missing data and
    invites the model to guess.
    """
    crn, course_id, seq, title, credits, days, time_str, instructor = _row_values(course)
    when = f"{days} {time_str}".strip() or "no scheduled meeting time"
    unit = "credit" if credits == "1" else "credits"
    return (
        f"{course_id} sec {seq} | CRN {crn} | {title} | "
        f"{credits} {unit} | {when} | {instructor or 'instructor TBA'}"
    )


def _build_context_text(term_data: list[tuple[str, list[dict]]]) -> str:
    """Plain-text rendering of the schedule for the chatbot's context.

    Roughly 13k tokens for eight terms — a fifth of the Doc text already in the
    context, so every term is included rather than just the current one. The
    header states what the data is and what it lacks, because the model is
    otherwise happy to infer a room number from a building-shaped blank.
    """
    as_of = datetime.now(timezone.utc).strftime("%B %d, %Y")
    lines = [
        "CS and AI course schedules from Portland State University's Banner "
        f"registration system, retrieved {as_of}.",
        "",
        "One line per course section, in the format:",
        "  SUBJECT NUMBER sec SECTION | CRN | title | credits | meeting days and "
        "time | instructor",
        "Days are abbreviated M T W R (Thursday) F S U, and times are 24-hour.",
        "Building and room assignments are NOT available here; direct anyone who "
        f"asks for a room to {_SOURCE_URL}, where they can sign in with a PSU "
        "account and search the Computer Science and Artificial Intelligence subjects.",
    ]
    for desc, courses in term_data:
        lines += ["", f"== {desc} — {len(courses)} section(s) ==", ""]
        lines += [_context_line(c) for c in courses]
    return "\n".join(lines)


def apply_schedule_text(sections, context_text: Optional[str]) -> bool:
    """Point the `course-schedules` section's chat text at the Banner data.

    `context_text` is None when the Banner fetch failed. The placeholder text is
    still replaced in that case: leaving "do not modify (will be overwritten by
    generated course-schedule page)" in the context gives the chatbot a citable
    section whose entire content is a note to the doc's maintainer.
    """
    for s in sections:
        if s.id == SCHEDULE_SECTION_ID:
            s.text = context_text or (
                "CS and AI course schedules by term are listed at /course-schedules/. "
                "The current offerings could not be retrieved for this context; refer "
                f"the reader to that page, or to {_SOURCE_URL} for building and room "
                "assignments."
            )
            return True
    return False


def _build_body(term_data: list[tuple[str, list[dict]]]) -> str:
    tab_btns = []
    tab_panels = []
    for i, (desc, courses) in enumerate(term_data):
        tab_id = f"sched-t{i}"
        active_cls = " active" if i == 0 else ""
        hidden_attr = "" if i == 0 else " hidden"
        tab_btns.append(
            f'<button class="sched-tab-btn{active_cls}" '
            f'onclick="schedShowTab(this,\'{tab_id}\')">'
            f'{html.escape(desc)}</button>'
        )
        tab_panels.append(
            f'<div id="{tab_id}" class="sched-tab-panel"{hidden_attr}>'
            f"<p>{len(courses)} section(s) offered</p>"
            f"{_build_table(courses)}"
            f"</div>"
        )

    btns = "\n    ".join(tab_btns)
    panels = "\n  ".join(tab_panels)
    return f"""\
<h1>Course Schedules</h1>
{_SOURCE_NOTE}
<div class="sched-tabs">
  <div class="sched-tab-btns">
    {btns}
  </div>
  {panels}
</div>
<script>
function schedShowTab(btn, id) {{
  document.querySelectorAll('.sched-tab-panel').forEach(function(p) {{ p.hidden = true; }});
  document.querySelectorAll('.sched-tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
  document.getElementById(id).hidden = false;
  btn.classList.add('active');
}}
</script>"""


_STYLE = """\
<style>
.sched-tab-btns {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 1.5rem;
  padding-bottom: 0.75rem;
  border-bottom: 2px solid var(--psu-green-light);
}
.sched-tab-btn {
  background: transparent;
  border: 2px solid var(--psu-green);
  border-radius: 6px;
  padding: 0.4rem 1.1rem;
  font-family: inherit;
  font-size: 0.95rem;
  font-weight: 500;
  color: var(--psu-green-dark);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.sched-tab-btn:hover { background: var(--psu-green-light); }
.sched-tab-btn.active {
  background: var(--psu-green-dark);
  color: #fff;
  border-color: var(--psu-green-dark);
}
.content-card { max-width: 100%; }
.sched-note {
  margin: 0 0 1.75rem;
  padding: 0.9rem 1.15rem;
  border-left: 4px solid var(--psu-green);
  background: var(--psu-green-light);
  border-radius: 0 6px 6px 0;
}
/* Scoped to .content-card to outrank its own `.content-card p` rule: this
   block is injected into <head> ahead of base.html's, and at equal specificity
   the later rule wins — leaving a 1rem paragraph margin stacked under the
   note's bottom padding. */
.content-card .sched-note p { margin: 0; }
.content-card .sched-note p + p { margin-top: 0.6rem; }
</style>"""


def generate_schedule_page(
    out_path: Path,
    template_path: str,
    base_href: str = "/",
    nav_sections=None,
    nav_exclude_ids=None,
) -> str:
    """Fetch the Banner schedule, render it to out_path, and return its chat text.

    The return value is the plain-text form of the same data, for callers to
    hand to `apply_schedule_text()` before writing sections.json.
    """
    from .render.landing import build_nav_groups, CATEGORY_LABELS, CATEGORY_ICONS

    print("[schedule] fetching available terms...", flush=True)
    terms = _get_terms(8)
    if not terms:
        raise RuntimeError("could not retrieve terms from Banner")

    descriptions = [t["description"].replace(" (View Only)", "").strip() for t in terms]
    print(f"[schedule] terms: {', '.join(descriptions)}", flush=True)

    term_data: list[tuple[str, list[dict]]] = []
    for term in terms:
        code = term["code"]
        desc = term["description"].replace(" (View Only)", "").strip()
        print(f"[schedule] [{desc}] fetching CS/AI courses...", flush=True)
        courses = _fetch_courses(code, subjects=["AI", "CS"])
        print(f"[schedule] [{desc}] {len(courses)} sections found", flush=True)
        term_data.append((desc, courses))

    body = _build_body(term_data)
    nav_groups = build_nav_groups(nav_sections, nav_exclude_ids) if nav_sections else []

    import os
    base_url = os.getenv("SITE_BASE_URL", "https://web.cs.pdx.edu").rstrip("/")
    tpl_path = Path(template_path)
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(tpl_path.parent)))
    tpl = env.get_template(tpl_path.name)
    rendered = tpl.render(
        title="Course Schedules",
        body=body,
        style=_STYLE,
        base_href=base_href,
        nav_groups=nav_groups,
        cat_labels=CATEGORY_LABELS,
        cat_icons=CATEGORY_ICONS,
        canonical_url=base_url + "/course-schedules/",
        meta_description=(
            "CS and AI course schedules by term for Portland State University — "
            "browse CRN, title, credits, days, time, and instructor for all CS and AI sections."
        ),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    print(f"[schedule] saved -> {out_path}", flush=True)

    return _build_context_text(term_data)
