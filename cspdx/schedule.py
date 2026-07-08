"""Generate the CS course schedule HTML page from Banner SSB.

Fetches the 3 most recent terms, builds a tabbed HTML table page,
and renders it through base.html so it matches the rest of the site.
Written to build/site/course-schedule/index.html, overwriting whatever
the Google Doc tab produced for that slug.
"""
from __future__ import annotations
import html
from pathlib import Path
from typing import TYPE_CHECKING

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


def _fetch_courses(session: requests.Session, term_code: str, subject: str = "CS", page_size: int = 500) -> list[dict]:
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
        f"<table>"
        f"<thead><tr>{thead_cells}</tr></thead>"
        f"<tbody>{tbody}</tbody>"
        f"</table>"
    )


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
</style>"""


def generate_schedule_page(
    out_path: Path,
    template_path: str,
    base_href: str = "/",
    nav_sections=None,
    nav_exclude_ids=None,
) -> None:
    """Fetch Banner schedule for the 3 most recent terms and render to out_path."""
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
        print(f"[schedule] [{desc}] establishing session...", flush=True)
        session = _establish_session(code)
        print(f"[schedule] [{desc}] fetching CS courses...", flush=True)
        courses = _fetch_courses(session, code)
        print(f"[schedule] [{desc}] {len(courses)} sections found", flush=True)
        term_data.append((desc, courses))

    body = _build_body(term_data)
    nav_groups = build_nav_groups(nav_sections, nav_exclude_ids) if nav_sections else []

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
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    print(f"[schedule] saved -> {out_path}", flush=True)
