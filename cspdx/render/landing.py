"""Auto-generated landing page, grouped by inferred category."""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import jinja2

from ..models import Section


LANDING_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Portland State University, Department of Computer Science</title>
  <base href="{{ base_href }}">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="Portland State University Department of Computer Science: undergraduate, graduate, and student resources, plus an AI assistant trained on department content.">
  <link rel="icon" href="https://web.cs.pdx.edu/favicon.ico" type="image/vnd.microsoft.icon">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      /* WCAG 2.1 AA: white on --psu-green is 5.6:1, on --psu-green-dark is 8.3:1 */
      --psu-green: #547119;
      --psu-green-dark: #3f5512;
      --psu-green-light: #eaf2d4;
      --psu-gold: #d4a017;
      --ink: #1e2230;
      --ink-soft: #4a5060;
      --ink-muted: #6b7280;
      --bg: #f7f8fb;
      --card: #ffffff;
      --border: #e6e8ee;
      --shadow-sm: 0 1px 2px rgba(20,25,40,0.04), 0 1px 3px rgba(20,25,40,0.06);
      --shadow-md: 0 4px 12px rgba(20,25,40,0.06), 0 2px 4px rgba(20,25,40,0.04);
      --shadow-lg: 0 12px 32px rgba(20,25,40,0.10), 0 4px 8px rgba(20,25,40,0.06);
      --radius: 14px;
      --radius-sm: 8px;
    }

    * { box-sizing: border-box; }
    /* Offset anchor jumps (#category links in the nav) by the height of the
       sticky two-row .topbar (~151px) so the target heading isn't hidden
       beneath it. */
    html { scroll-behavior: smooth; scroll-padding-top: 160px; }
    body {
      margin: 0;
      font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
      color: var(--ink);
      background: var(--bg);
      line-height: 1.55;
      -webkit-font-smoothing: antialiased;
    }
    a { color: var(--psu-green-dark); text-decoration: none; }
    a:hover { text-decoration: underline; }

    /* Screen-reader-only utility (WCAG 1.3.1 — programmatic labels) */
    .visually-hidden {
      position: absolute !important;
      width: 1px; height: 1px;
      padding: 0; margin: -1px;
      overflow: hidden; clip: rect(0,0,0,0);
      white-space: nowrap; border: 0;
    }

    /* Visible focus ring for keyboard users (WCAG 2.4.7) */
    :focus-visible {
      outline: 3px solid #2a6cff;
      outline-offset: 2px;
      border-radius: 4px;
    }
    button:focus-visible, a:focus-visible {
      outline: 3px solid #2a6cff;
      outline-offset: 2px;
    }

    /* Skip link (WCAG 2.4.1) — visible only when focused */
    .skip-link {
      position: absolute; left: -10000px; top: 8px;
      background: var(--ink); color: #fff !important;
      padding: 10px 16px; border-radius: 6px;
      font-weight: 600; z-index: 1000;
    }
    .skip-link:focus {
      left: 16px;
      outline: 3px solid #ffd54f;
      text-decoration: none;
    }

    /* Respect users who prefer reduced motion (WCAG 2.3.3 / 2.2.2) */
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        animation-duration: 0.001ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.001ms !important;
        scroll-behavior: auto !important;
      }
      .top-cta:hover, .quick-ask:hover, .card:hover, .eyebrow:hover { transform: none !important; }
    }

    /* ---------- Top bar (two rows: brand row + nav row) ---------- */
    .topbar {
      background: #fff;
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 50;
    }
    .topbar-row {
      max-width: 1240px;
      margin: 0 auto;
      padding: 0 28px;
      display: flex;
      align-items: center;
    }
    .topbar-row.primary {
      padding-top: 14px;
      padding-bottom: 14px;
      gap: 20px;
    }
    .topbar-row.secondary {
      gap: 4px;
      border-top: 1px solid var(--border);
      background: #fafbfd;
      padding-top: 6px;
      padding-bottom: 6px;
      overflow-x: auto;             /* allow horizontal scroll on tiny screens */
      scrollbar-width: none;        /* Firefox */
      flex-wrap: nowrap;
    }
    .topbar-row.secondary::-webkit-scrollbar { display: none; }

    /* ---- Per-category dropdown menus (hover-triggered, position:fixed avoids overflow clipping) ---- */
    .nav-caret {
      font-size: 10px; line-height: 1; opacity: 0.6;
    }
    .nav-dropdown {
      position: fixed;
      min-width: 200px; max-width: 340px;
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 10px;
      box-shadow: 0 4px 16px rgba(20,25,40,.12), 0 1px 4px rgba(20,25,40,.06);
      padding: 6px 0;
      z-index: 1000;
      list-style: none;
      margin: 0;
    }
    .nav-dropdown[hidden] { display: none; }
    .nav-dropdown li { list-style: none; margin: 0; }
    .nav-dropdown a {
      display: block;
      padding: 8px 16px;
      font-size: 13.5px;
      color: var(--ink-soft);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .nav-dropdown a:hover {
      background: var(--psu-green-light);
      color: var(--psu-green-dark);
      text-decoration: none;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 18px;
      font-weight: 700;
      color: var(--ink);
      flex-shrink: 0;
      min-width: 0;
    }
    .brand img {
      height: 76px; width: auto; display: block;
      flex-shrink: 0;
    }
    .topbar-spacer { flex: 1; }

    .top-link {
      font-size: 14px; font-weight: 600;
      color: var(--ink-soft);
      padding: 8px 14px; border-radius: 8px;
      white-space: nowrap;
      transition: background .12s ease, color .12s ease;
      display: inline-flex; align-items: center; gap: 5px;
      cursor: pointer;
    }
    .top-link:hover {
      background: rgba(109,141,36,.08);
      color: var(--psu-green-dark);
      text-decoration: none;
    }

    .top-cta {
      background: var(--psu-green); color: #fff !important;
      padding: 10px 18px;
      border-radius: 999px;
      font-weight: 600;
      font-size: 14.5px;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      white-space: nowrap;
      flex-shrink: 0;
      line-height: 1;
      box-shadow: 0 1px 2px rgba(20,25,40,.08), inset 0 -1px 0 rgba(0,0,0,.08);
      transition: background .15s ease, transform .15s ease, box-shadow .15s ease;
    }
    .top-cta:hover {
      background: var(--psu-green-dark);
      text-decoration: none;
      transform: translateY(-1px);
      box-shadow: 0 4px 10px rgba(109,141,36,.25), inset 0 -1px 0 rgba(0,0,0,.08);
    }
    .top-cta svg { width: 16px; height: 16px; flex-shrink: 0; }
    .top-cta .label { white-space: nowrap; }

    /* ---------- Hero ---------- */
    .hero {
      background:
        radial-gradient(1200px 400px at 80% -50%, rgba(109,141,36,.18), transparent 60%),
        radial-gradient(900px 350px at -10% -20%, rgba(212,160,23,.10), transparent 60%),
        linear-gradient(180deg, #ffffff 0%, var(--bg) 100%);
      border-bottom: 1px solid var(--border);
    }
    .hero-inner {
      max-width: 1100px;
      margin: 0 auto;
      padding: 28px 24px 32px;
      text-align: center;
    }
    .eyebrow {
      display: inline-block;
      background: var(--psu-green-light);
      color: var(--psu-green-dark) !important;
      font-size: 12px; font-weight: 700;
      letter-spacing: .08em; text-transform: uppercase;
      padding: 6px 12px; border-radius: 999px;
      margin-bottom: 12px;
      text-decoration: none;
      transition: background .15s ease, transform .15s ease;
    }
    .eyebrow:hover {
      background: #d8e6b6;
      transform: translateY(-1px);
      text-decoration: none;
    }
    .hero h1 {
      font-size: clamp(1.5rem, 3.4vw, 2.2rem);
      line-height: 1.15; letter-spacing: -0.015em;
      margin: 0 0 8px; font-weight: 800; color: var(--ink);
    }
    .hero p.sub {
      font-size: clamp(0.95rem, 1.4vw, 1.05rem);
      color: var(--ink-soft);
      max-width: 600px; margin: 0 auto 20px;
      line-height: 1.5;
    }

    /* ---------- Ask box ---------- */
    .ask-card {
      max-width: 680px;
      margin: 0 auto;
      background: #fff;
      border-radius: var(--radius);
      box-shadow: var(--shadow-lg);
      padding: 18px;
      display: flex;
      gap: 10px;
      align-items: center;
      border: 1px solid var(--border);
    }
    .ask-card input {
      flex: 1;
      border: 0;
      outline: 0;
      font: inherit; font-size: 16px;
      padding: 10px 12px;
      background: transparent;
      color: var(--ink);
    }
    .ask-card button {
      background: var(--psu-green);
      color: #fff;
      border: 0;
      font-weight: 600;
      font-size: 15px;
      padding: 11px 20px;
      border-radius: 10px;
      cursor: pointer;
      transition: background .15s ease, transform .15s ease;
      display: inline-flex; align-items: center; gap: 6px;
    }
    .ask-card button:hover { background: var(--psu-green-dark); transform: translateY(-1px); }
    .ask-card button:disabled { background: var(--ink-muted); cursor: wait; transform: none; }
    .ask-hint {
      font-size: 13px; color: var(--ink-muted);
      margin: 10px auto 0;
      max-width: 600px;
    }
    .ask-hint b { color: var(--psu-green-dark); font-weight: 600; }
    .quick-asks {
      display: flex; flex-wrap: wrap; gap: 8px; justify-content: center;
      margin: 12px auto 0; max-width: 720px;
    }
    .quick-ask {
      background: #fff; border: 1px solid var(--border);
      padding: 7px 13px; border-radius: 999px;
      font-size: 13px; color: var(--ink-soft);
      cursor: pointer; transition: all .15s ease;
    }
    .quick-ask:hover { border-color: var(--psu-green); color: var(--psu-green-dark); }

    .ask-answer {
      max-width: 760px;
      margin: 24px auto 0;
      background: #fff;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px 24px;
      text-align: left;
      box-shadow: var(--shadow-md);
      display: none;
    }
    .ask-answer.show { display: block; }
    .ask-answer h4 {
      margin: 0 0 10px; font-size: 14px;
      color: var(--ink-muted); text-transform: uppercase; letter-spacing: .06em;
    }
    .ask-answer .answer-body p { margin: 0 0 .8em; }
    .ask-answer .answer-body ul { padding-left: 1.3em; margin: .4em 0; }
    .ask-answer .answer-body li { margin-bottom: .2em; }
    .ask-answer .answer-body a { color: var(--psu-green-dark); font-weight: 500; }
    .ask-answer .loading {
      display: inline-block; width: 12px; height: 12px;
      border: 2px solid var(--psu-green-light);
      border-top-color: var(--psu-green);
      border-radius: 50%; animation: spin .8s linear infinite;
      vertical-align: -2px; margin-right: 6px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* ---------- Sections ---------- */
    main { max-width: 1200px; margin: 0 auto; padding: 28px 24px 32px; }
    main > section { margin-bottom: 8px; }

    .section-head {
      display: flex; align-items: baseline; justify-content: space-between;
      margin: 0 0 16px; gap: 16px; flex-wrap: wrap;
    }
    .section-head h2 {
      font-size: clamp(1.25rem, 2.1vw, 1.6rem); letter-spacing: -0.01em;
      margin: 0; color: var(--ink); font-weight: 700;
      display: flex; align-items: center; gap: 12px;
    }
    .section-head .count {
      font-size: 13px; color: var(--ink-muted);
      background: #fff; border: 1px solid var(--border);
      padding: 3px 10px; border-radius: 999px; font-weight: 500;
    }
    .cat-icon {
      width: 32px; height: 32px; border-radius: 8px;
      display: inline-flex; align-items: center; justify-content: center;
      background: var(--psu-green-light); color: var(--psu-green-dark);
      font-size: 16px;
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
      gap: 10px;
      margin-bottom: 28px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 16px 18px;
      transition: all .15s ease;
      display: flex; align-items: center;
      gap: 12px;
      color: var(--ink);
      font-weight: 500;
      font-size: 15px;
      box-shadow: var(--shadow-sm);
    }
    .card:hover {
      border-color: var(--psu-green);
      transform: translateY(-2px);
      box-shadow: var(--shadow-md);
      text-decoration: none;
      color: var(--psu-green-dark);
    }
    .card::after {
      content: "→";
      margin-left: auto;
      color: var(--ink-muted);
      transition: transform .15s ease, color .15s ease;
    }
    .card:hover::after { transform: translateX(3px); color: var(--psu-green); }

    /* ---------- Footer ---------- */
    footer {
      margin-top: 56px;
      background: var(--ink);
      color: #d8dbe5;
      padding: 36px 24px 28px;
    }
    .footer-inner {
      max-width: 1200px; margin: 0 auto;
      display: grid; grid-template-columns: 2fr 1fr 1fr;
      gap: 32px;
    }
    footer h3 { color: #fff; font-size: 14px; letter-spacing: .05em;
      text-transform: uppercase; margin: 0 0 14px; }
    footer p { margin: 0 0 6px; font-size: 14px; }
    footer a { color: #c8e070; }
    .copy {
      max-width: 1200px; margin: 24px auto 0;
      padding-top: 18px; border-top: 1px solid #2c3142;
      font-size: 12px; color: #8a90a3;
      display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px;
    }

    /* Nav row scrolls horizontally on narrow screens; no need to hide it. */
    @media (max-width: 720px) {
      .topbar-row { padding-left: 16px; padding-right: 16px; }
      .brand img { height: 60px; }
      .top-link { font-size: 13px; padding: 7px 10px; }
      .hero-inner { padding: 20px 20px 24px; }
      .footer-inner { grid-template-columns: 1fr; gap: 24px; }
      .ask-card { padding: 12px; gap: 6px; }
      .ask-card button { padding: 10px 14px; }
      .top-cta .label { display: none; }   /* icon-only button on small screens */
      .top-cta { padding: 10px 12px; }
    }
  </style>
</head>
<body>

<a class="skip-link" href="#main-content">Skip to main content</a>

<header class="topbar">
  <div class="topbar-row primary">
    <a href="{{ base_href }}" class="brand" aria-label="PSU CS home">
      <img alt="Portland State University Department of Computer Science" src="{{ base_href }}images/pdx-cs-logo.png"/>
    </a>
    <span class="topbar-spacer"></span>
    <a class="top-cta" href="{{ base_href }}ask/" aria-label="Ask the CS Assistant">
      <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
      </svg>
      <span class="label">Ask the Assistant</span>
    </a>
  </div>
  <nav class="topbar-row secondary" aria-label="Category navigation">
    {% for cat, items in grouped %}
      <div class="nav-item">
        <a class="top-link" href="#{{ cat }}" aria-haspopup="true" aria-expanded="false">{{ cat_labels.get(cat, cat.replace('-', ' ').title()) }}<span class="nav-caret" aria-hidden="true">▾</span></a>
        <ul class="nav-dropdown" role="list" hidden>
          {% for s in items %}
          <li><a href="{{ s.url_path }}">{{ s.title }}</a></li>
          {% endfor %}
        </ul>
      </div>
    {% endfor %}
  </nav>
</header>

<main id="main-content">
<section class="hero" aria-label="Department overview and AI assistant">
  <div class="hero-inner">
    <a class="eyebrow" href="https://pdx.edu" target="_blank" rel="noopener">Portland State University ↗</a>
    <h1>Department of Computer Science</h1>
    <p class="sub">Program information and an AI assistant to answer your questions.</p>
    <form class="ask-card" id="askForm" onsubmit="return askSubmit(event)" role="search" aria-label="Ask the CS assistant">
      <span aria-hidden="true" style="padding-left:6px; color:var(--ink-muted)">🔍</span>
      <label for="askInput" class="visually-hidden">Your question</label>
      <input id="askInput" type="text" placeholder="Ask anything about the CS department..." aria-label="Your question"/>
      <button id="askBtn" type="submit">Ask <span aria-hidden="true">→</span></button>
    </form>

    <div class="quick-asks" role="group" aria-label="Example questions">
      <button class="quick-ask" onclick="quickAsk('Tell me about your AI degree options')">AI options</button>
      <button class="quick-ask" onclick="quickAsk('Who do I contact for graduate advising?')">Graduate advising</button>
      <button class="quick-ask" onclick="quickAsk('What is the cybersecurity certificate?')">Cybersecurity certificate</button>
      <button class="quick-ask" onclick="quickAsk('Tell me about the Discover CS cohort.')">Discover CS</button>
    </div>

    <p class="ask-hint">Or jump straight to the full chat: <a href="{{ base_href }}ask/"><b>open the assistant →</b></a></p>

    <div class="ask-answer" id="askAnswer" role="region" aria-label="Assistant answer" aria-live="polite">
      <h4>Assistant</h4>
      <div class="answer-body" id="answerBody"></div>
    </div>
  </div>
</section>

  {% for cat, items in grouped %}
    <section id="{{ cat }}">
      <div class="section-head">
        <h2>
          <span class="cat-icon" aria-hidden="true">{{ cat_icons.get(cat, '📄') }}</span>
          {{ cat_labels.get(cat, cat.replace('-', ' ').title()) }}
        </h2>
        <span class="count">{{ items|length }} {{ 'page' if items|length == 1 else 'pages' }}</span>
      </div>
      <div class="grid">
        {% for s in items %}
          <a class="card" href="{{ s.url_path }}">{{ s.title }}</a>
        {% endfor %}
      </div>
    </section>
  {% endfor %}
</main>

<footer>
  <div class="footer-inner">
    <div>
      <h3>Department of Computer Science</h3>
      <p>Portland State University</p>
      <p>1900 SW 4th Avenue, Suite 120</p>
      <p>Portland, OR 97201</p>
      <p style="margin-top:8px;"><a href="mailto:csoffice@pdx.edu">csoffice@pdx.edu</a></p>
      <p><a href="tel:+15037254036">+1 (503) 725-4036</a></p>
    </div>
    <div>
      <h3>Quick Links</h3>
      {% for cat, items in grouped %}
        <p><a href="#{{ cat }}">{{ cat_labels.get(cat, cat.replace('-', ' ').title()) }}</a></p>
      {% endfor %}
    </div>
    <div>
      <h3>Assistant</h3>
      <p><a href="{{ base_href }}ask/">Open the chatbot</a></p>
      <p style="color:#9ea3b6; font-size:13px; margin-top:8px;">AI-generated answers may contain errors. Verify against linked pages.</p>
    </div>
  </div>
  <div class="copy">
    <span>© Portland State University, Department of Computer Science</span>
    <span>Generated from departmental Google Docs.</span>
  </div>
</footer>

<script>
  const ANSWER_BOX = document.getElementById('askAnswer');
  const ANSWER_BODY = document.getElementById('answerBody');
  const BTN = document.getElementById('askBtn');
  const INPUT = document.getElementById('askInput');

  // Tiny markdown -> HTML for assistant replies (paragraphs, bullets, links, bold).
  function mdToHtml(s) {
    // Escape HTML
    s = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    // Links [text](url)
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
      '<a href="$2">$1</a>');
    // Bold **text**
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Split into blocks by blank lines
    const blocks = s.split(/\n{2,}/);
    return blocks.map(b => {
      const lines = b.split('\n');
      if (lines.every(l => /^\s*[-*]\s+/.test(l) || l.trim() === '')) {
        const items = lines.filter(l => l.trim()).map(l => '<li>' + l.replace(/^\s*[-*]\s+/, '') + '</li>').join('');
        return '<ul>' + items + '</ul>';
      }
      return '<p>' + b.replace(/\n/g, '<br>') + '</p>';
    }).join('');
  }

  async function ask(q) {
    if (!q) return;
    INPUT.value = q;
    ANSWER_BOX.classList.add('show');
    ANSWER_BODY.innerHTML = '<p><span class="loading"></span> Thinking…</p>';
    BTN.disabled = true;
    try {
      const res = await fetch('{{ base_href }}ask', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({question: q})
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      ANSWER_BODY.innerHTML = mdToHtml(data.answer || '(no answer)');
    } catch (e) {
      ANSWER_BODY.innerHTML = '<p style="color:#a33">Sorry, something went wrong: ' + e.message + '</p>';
    } finally {
      BTN.disabled = false;
    }
  }

  function askSubmit(e) { e.preventDefault(); ask(INPUT.value.trim()); return false; }
  function quickAsk(q) { ask(q); window.scrollTo({top: 0, behavior: 'smooth'}); }

  (function(){
    var closeTimer=null;
    function closeAll(){
      clearTimeout(closeTimer);
      document.querySelectorAll('.nav-dropdown:not([hidden])').forEach(function(d){
        d.hidden=true;
        var a=d.closest('.nav-item').querySelector('.top-link');
        if(a) a.setAttribute('aria-expanded','false');
      });
    }
    function openItem(item){
      clearTimeout(closeTimer);
      var dd=item.querySelector('.nav-dropdown');
      var label=item.querySelector('.top-link');
      if(!dd||!dd.hidden) return;
      closeAll();
      var r=label.getBoundingClientRect();
      dd.style.top=(r.bottom+2)+'px';
      dd.style.left=Math.max(0,Math.min(r.left,window.innerWidth-344))+'px';
      dd.hidden=false;
      label.setAttribute('aria-expanded','true');
    }
    document.querySelectorAll('.nav-item').forEach(function(item){
      var label=item.querySelector('.top-link');
      var dd=item.querySelector('.nav-dropdown');
      if(!dd) return;
      item.addEventListener('pointerenter',function(e){ if(e.pointerType!=='mouse') return; openItem(item); });
      item.addEventListener('pointerleave',function(e){ if(e.pointerType!=='mouse') return; closeTimer=setTimeout(closeAll,150); });
      dd.addEventListener('pointerenter',function(e){ if(e.pointerType!=='mouse') return; clearTimeout(closeTimer); });
      dd.addEventListener('pointerleave',function(e){ if(e.pointerType!=='mouse') return; closeTimer=setTimeout(closeAll,150); });
      label.addEventListener('click',function(e){
        e.preventDefault();
        if(!dd.hidden){ closeAll(); } else { openItem(item); }
      });
    });
    document.addEventListener('click',function(e){ if(!e.target.closest('.nav-item')) closeAll(); });
    document.addEventListener('keydown',function(e){ if(e.key==='Escape') closeAll(); });
  })();
</script>

</body>
</html>
"""


CATEGORY_LABELS = {
    "about": "About",
    "undergraduate": "Undergraduate",
    "graduate": "Graduate",
    "resources": "Resources",
}

CATEGORY_ICONS = {
    "about": "🏛️",
    "undergraduate": "🎓",
    "graduate": "📚",
    "resources": "🛟",
}

CATEGORY_ORDER = [
    "about",
    "undergraduate",
    "graduate",
    "resources",
]


def build_nav_groups(
    sections: list[Section],
    exclude_ids: list[str] | None = None,
) -> list[tuple[str, list[Section]]]:
    """Group sections by category in canonical display order, omitting exclude_ids."""
    skip = set(exclude_ids or [])
    by_cat: dict[str, list[Section]] = defaultdict(list)
    for s in sections:
        if s.id not in skip:
            by_cat[s.category or "other"].append(s)
    for items in by_cat.values():
        items.sort(key=lambda s: s.title.lower())
    ordered = [(c, by_cat[c]) for c in CATEGORY_ORDER if c in by_cat]
    extras = sorted(c for c in by_cat if c not in CATEGORY_ORDER)
    ordered += [(c, by_cat[c]) for c in extras]
    return ordered


def render_landing(
    sections: list[Section],
    out_path: str,
    base_href: str = "/",
    exclude_ids: list[str] | None = None,
) -> None:
    """Render the landing page.

    `exclude_ids` is a list of section ids to hide from the landing index.
    The corresponding HTML pages are still generated by render_sections()
    and remain reachable by URL; they're just omitted from the home page
    (and from the in-page nav / footer / quick links).
    """
    ordered = build_nav_groups(sections, exclude_ids)

    tpl = jinja2.Template(LANDING_TEMPLATE)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        tpl.render(
            grouped=ordered,
            cat_labels=CATEGORY_LABELS,
            cat_icons=CATEGORY_ICONS,
            base_href=base_href,
        ),
        encoding="utf-8",
    )
