"""CSS for fonts, route/status badges, header and footer (CLAUDE.md section
8, visual identity -- matches the presentation's first slide).

Injected once per page via st.markdown(..., unsafe_allow_html=True). The
colour palette itself also lives in .streamlit/config.toml for Streamlit's
native theme; this module covers what the theme can't reach: the heading
font, badges, the header/footer bar, the bordered-container ("card") look,
and hiding Streamlit's own chrome.

The card selector (`[data-test-wrap]`) and the hidden-chrome selectors were
confirmed against the actual rendered DOM of the installed Streamlit
version (1.65) with Playwright, not guessed from memory -- Streamlit's
internal class names are not public API and can change between versions;
`data-test-wrap` is the one attribute that reliably marks an
`st.container(border=True)` block in this version.
"""

from __future__ import annotations

import html

import streamlit as st

BACKGROUND = "#F3E6D5"
SURFACE = "#FBF5EC"
PRIMARY = "#8D001C"
PRIMARY_HOVER = "#731133"
GREY = "#ADADAD"
BODY_TEXT = "#2B1A1A"

ROUTE_COLORS = {
    "AUTO": "#4E7D4A",
    "DRAFT": "#B8860B",
    "GAP": "#C8642A",
    "HUMAN_ONLY": "#8D001C",
}

# Plain-English route names for a non-technical reviewer -- shown on both
# the DDQ list cards (with a count) and each question's badge on the
# review page, so the two use the same vocabulary.
ROUTE_LABELS = {
    "AUTO": "Answered by agent",
    "DRAFT": "Draft to approve",
    "GAP": "Information missing",
    "HUMAN_ONLY": "Human answer required",
}

# Section 8 list: Reviewed by agent = ochre, In human review = terracotta,
# Ready to send = green, Sent = grey, New = burgundy outline (handled
# separately in status_badge, since it is not a solid fill).
STATUS_COLORS = {
    "Reviewed by agent": "#B8860B",
    "In human review": "#C8642A",
    "Ready to send": "#4E7D4A",
    "Sent": "#ADADAD",
}

# Message colours (replace Streamlit's default blue st.info box -- "no blue
# anywhere" in a professional-finance interface).
MESSAGE_COLORS = {
    "neutral": "#6B5B5B",
    "success": "#4E7D4A",
    "error": PRIMARY,
}

BODY_FONT = "Arial, Helvetica, sans-serif"

_CSS = f"""
<style>
/* Section headings ("DDQs" and the others): Arial, not a display font --
   professional-finance look, not a slide look. !important: Streamlit's
   own heading styles, applied via a more specific class+element
   selector, otherwise win on specificity alone. */
h1, h2, h3 {{
    font-family: {BODY_FONT} !important;
    font-size: 20px !important;
    font-weight: 600 !important;
    color: {PRIMARY} !important;
}}

/* Streamlit adds a "#" permalink icon next to every heading on hover;
   not wanted in a finished interface. */
[data-testid="stHeaderActionElements"] {{
    display: none !important;
}}

/* Everything else: plain sans-serif, per section 8. A universal selector,
   not [class*="css"]: this Streamlit version (1.65) names its generated
   classes "st-emotion-cache-xxx", which never contains the substring
   "css" -- the old selector silently matched nothing, confirmed live (a
   sender span still computed to Streamlit's default "Source Sans"
   without this change).

   Two things this rule must not break, both caught by screenshot rather
   than guessed:
   - Streamlit's expander chevrons are a ligature-font glyph (text content
     literally "keyboard_arrow_right", drawn as an arrow only by its icon
     font) -- excluded via :not().
   - the heading rule above must still win for h1/h2/h3 (font-family now
     agrees -- Arial both places -- but keeping this selector's
     specificity at zero is still what lets font-size/color on h1-h3 win
     too). `:not()` carries the specificity of its argument, so a
     plain `*:not(...)` ties the heading selectors' specificity and -- as
     the later rule in this stylesheet -- would win that tie. Wrapping
     the whole thing in :where() forces this rule's specificity to zero,
     so the heading rule's plain class selector always outranks it,
     regardless of source order. */
html, body {{
    color: {BODY_TEXT};
}}
:where(*:not([data-testid="stIconMaterial"])) {{
    font-family: {BODY_FONT} !important;
}}

/* Flat design: no gradients, no heavy shadows, ~8px corners */
.stButton > button, .route-badge, .status-badge, div[data-testid="stExpander"] {{
    border-radius: 8px !important;
    box-shadow: none !important;
}}

/* Primary buttons: burgundy background, cream text */
.stButton > button[kind="primary"] {{
    background-color: {PRIMARY};
    color: {SURFACE};
    border: none;
}}
.stButton > button[kind="primary"]:hover {{
    background-color: {PRIMARY_HOVER};
    color: {SURFACE};
}}

/* Secondary buttons: cream background, burgundy border */
.stButton > button[kind="secondary"] {{
    background-color: {SURFACE};
    color: {PRIMARY};
    border: 1px solid {PRIMARY};
}}
.stButton > button[kind="secondary"]:hover {{
    background-color: {BACKGROUND};
    border-color: {PRIMARY_HOVER};
    color: {PRIMARY_HOVER};
}}

/* Cards: every st.container(border=True) -- DDQ list rows, review-page
   question panels. `data-test-wrap` is set only on bordered containers in
   this Streamlit version (confirmed against the live DOM); the usual
   `data-testid="stVerticalBlockBorderWrapper"` selector from older
   Streamlit versions does not exist here. */
div[data-testid="stVerticalBlock"][data-test-wrap] {{
    background-color: {SURFACE} !important;
    border: 1px solid {GREY} !important;
    border-radius: 8px !important;
    box-shadow: none !important;
    padding: 0.75rem 1rem !important;
}}

/* Sender address and other plain text that looks like an email/URL must
   never render as Streamlit's auto-linkified blue underline. */
.plain-text {{
    color: {BODY_TEXT};
    text-decoration: none;
}}

/* st.container(key=...) also sets data-test-wrap (confirmed live), so the
   card rule above would otherwise turn the header band into a bordered
   card too -- override it back to a plain band with only a bottom
   divider, on the page background (not the lighter card surface). Higher
   specificity than the card rule (testid+attr+class beats testid+attr),
   so this always wins regardless of order. */
div[data-testid="stVerticalBlock"][data-test-wrap].st-key-app_header_band {{
    background-color: transparent !important;
    border: none !important;
    border-bottom: 1px solid {GREY} !important;
    border-radius: 0 !important;
    padding: 0 !important;
}}

/* Header band: compact, ~110px, logo left / kicker text right. Targets
   the real key'd container (`st.container(key="app_header_band")` ->
   class "st-key-app_header_band"), not a markdown-only div -- a plain
   st.markdown div before/after st.columns never actually wraps them in
   the DOM (siblings, not parent/child), confirmed earlier in this
   project; a key'd container is a real wrapper. */
.st-key-app_header_band {{
    display: flex !important;
    align-items: center !important;
    min-height: 110px;
    border-bottom: 1px solid {GREY};
    margin-bottom: 1.5rem;
}}
/* !important on font-size/letter-spacing/text-transform: Streamlit sets
   these on its own <p> wrapper with higher specificity than a bare class
   selector (the same fight documented throughout this file). */
.app-kicker {{
    font-family: {BODY_FONT} !important;
    font-size: 15px !important;
    font-weight: 600 !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
    color: {PRIMARY} !important;
    text-align: right !important;
    margin: 0 !important;
}}
.app-kicker-sub {{
    font-family: {BODY_FONT} !important;
    font-size: 12px !important;
    font-weight: 400 !important;
    color: #6B5B5B !important;
    text-align: right !important;
    margin: 0.2rem 0 0 0 !important;
}}

/* Messages: replace Streamlit's default st.info/success/warning/error
   boxes (st.info is blue -- "no blue anywhere" in a finance interface).
   Plain coloured text, no box, no icon. */
.app-msg {{
    font-family: {BODY_FONT} !important;
    font-size: 0.9rem;
    padding: 0.4rem 0;
}}
.app-msg-neutral {{ color: {MESSAGE_COLORS["neutral"]}; }}
.app-msg-success {{ color: {MESSAGE_COLORS["success"]}; font-weight: 600; }}
.app-msg-error {{ color: {MESSAGE_COLORS["error"]}; font-weight: 600; }}

/* Footer */
.app-footer {{
    margin-top: 3rem;
    padding-top: 1rem;
    border-top: 1px solid {GREY};
    text-align: right;
    font-size: 0.8rem;
    color: {PRIMARY};
}}

/* Route badges -- always show the word as well as the colour */
.route-badge {{
    display: inline-block;
    padding: 0.15rem 0.6rem;
    margin: 0.1rem 0.2rem 0.1rem 0;
    font-size: 0.78rem;
    font-weight: 600;
    color: white;
    letter-spacing: 0.02em;
}}

/* Confirmation warning box (step 3 "Send?" dialog) -- a plain st.warning()
   would auto-linkify the recipient address the same way st.markdown does
   (confirmed live); this is a styled replacement built with plain_text(). */
.demo-banner {{
    background-color: {PRIMARY};
    color: {SURFACE};
    text-align: center;
    font-weight: 600;
    padding: 0.5rem 1rem;
    border-radius: 8px;
    margin-bottom: 1.5rem;
}}
.confirm-box {{
    background-color: #FBECC8;
    border: 1px solid #B8860B;
    border-radius: 8px;
    padding: 0.75rem 1rem;
    color: {BODY_TEXT};
    margin-bottom: 0.5rem;
}}

/* Answer box: bold label above it, obviously-a-field styling below. The
   label's own st.text_area label is hidden (label_visibility="collapsed")
   in app.py -- this is a separate st.markdown element instead, so the
   "bold" requirement doesn't fight Streamlit's own label styling. */
.answer-label {{
    font-weight: 700;
    color: {BODY_TEXT};
    margin: 0.5rem 0 0.25rem 0;
}}
div[data-testid="stTextArea"] textarea {{
    background-color: #FFFFFF !important;
    border: 1.5px solid {GREY} !important;
    border-radius: 8px !important;
    padding: 12px !important;
    min-height: 120px;
    font-family: {BODY_FONT} !important;
    font-size: 15px !important;
    color: {BODY_TEXT} !important;
    box-shadow: none !important;
}}
div[data-testid="stTextArea"] textarea:focus {{
    border-color: {PRIMARY} !important;
}}
div[data-testid="stTextArea"] textarea::placeholder {{
    color: {GREY};
    opacity: 1;
}}

/* Status badges: solid fill, except "New" which is a burgundy outline */
.status-badge {{
    display: inline-block;
    padding: 0.15rem 0.7rem;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: white;
}}
.status-badge.status-new {{
    background-color: transparent;
    color: {PRIMARY};
    border: 1.5px solid {PRIMARY};
}}

/* Hide Streamlit's own chrome: main menu, Deploy button, toolbar, footer.
   client.toolbarMode = "minimal" in .streamlit/config.toml does most of
   this already; these rules are a CSS backstop in case of a version
   change, per the project's request to hide them explicitly. */
#MainMenu, .stDeployButton, div[data-testid="stToolbar"],
div[data-testid="stDecoration"], div[data-testid="stStatusWidget"],
footer {{
    visibility: hidden;
    height: 0;
}}
header[data-testid="stHeader"] {{
    display: none !important;
}}
</style>
"""


def inject_once() -> None:
    """Injects the CSS block. Despite the name, this runs on every script
    run (Streamlit re-runs the whole script on every interaction): an
    earlier version skipped re-injecting via a session_state flag, but
    Streamlit's frontend diffs the element tree against what the *current*
    run emits, so skipping this st.markdown call made it remove the
    previously-injected <style> element too -- confirmed by inspecting the
    live DOM, where every custom style was gone on the second page. CSS
    injection is cheap, so there is no real cost to doing it unconditionally."""
    st.markdown(_CSS, unsafe_allow_html=True)


def header(logo_path: str = "assets/alken_fund_logo_cropped.png") -> None:
    """Compact band (~110px): large logo left, small uppercase kicker text
    right -- a professional-finance-software header, not a slide title."""
    with st.container(key="app_header_band"):
        logo_col, label_col = st.columns([1, 2.5], vertical_alignment="center")
        with logo_col:
            try:
                st.image(logo_path, width=280)
            except Exception:
                pass  # missing logo must never break the page
        with label_col:
            st.markdown(
                '<p class="app-kicker">DDQ Agent</p>'
                '<p class="app-kicker-sub">Due diligence questionnaire workspace '
                '· Fictional case study, simulated data</p>',
                unsafe_allow_html=True,
            )


def demo_banner() -> str:
    """The public-demo top banner (demo.py's DEMO_PUBLIC mode): deliberately
    a persistent, visually distinct bar -- not the same grey as a one-off
    status message() -- since it's a permanent fixture of the page, not a
    transient result."""
    return (
        '<div class="demo-banner">Public demo — fictional data, no emails are sent.</div>'
    )


def footer() -> None:
    st.markdown(
        '<div class="app-footer">Rodolfo Freda · Hult International Business School</div>',
        unsafe_allow_html=True,
    )


def route_badge(route: str) -> str:
    """HTML for one coloured route label, in plain English (section 8:
    'always show the word as well as the colour') -- the same label text
    as route_count_badges() below, so the list and review pages agree."""
    color = ROUTE_COLORS.get(route, GREY)
    label = ROUTE_LABELS.get(route, route)
    return f'<span class="route-badge" style="background-color:{color}">{label}</span>'


def route_count_badges(routes: list[str]) -> str:
    """HTML for one small badge per route present, each showing its count
    and plain-English name (e.g. '9 Answered by agent'), in the same fixed
    order as the quadrant table."""
    counts = {r: routes.count(r) for r in ROUTE_COLORS if routes.count(r)}
    return " ".join(
        f'<span class="route-badge" style="background-color:{ROUTE_COLORS[route]}">{count} {ROUTE_LABELS[route]}</span>'
        for route, count in counts.items()
    )


def message(text: str, kind: str = "neutral") -> str:
    """HTML for a plain-text status message -- replaces st.info (blue),
    st.success, st.warning and st.error so none of them show Streamlit's
    default colours ('no blue anywhere'). kind: 'neutral' (grey), 'success'
    (green) or 'error' (burgundy, also used for warnings).

    `text` is inserted as-is (not escaped) so a caller can pass `<strong>`/
    `<code>` for static copy -- any dynamic or untrusted part (an
    exception message, an email address) must be escaped by the caller
    first, e.g. with html.escape() or plain_text() above."""
    css_kind = kind if kind in MESSAGE_COLORS else "neutral"
    return f'<p class="app-msg app-msg-{css_kind}">{text}</p>'


def status_badge(status: str) -> str:
    """HTML for the DDQ-level status badge. 'New' is a burgundy outline
    (no state has happened yet); every other status is a solid fill."""
    if status not in STATUS_COLORS:
        return f'<span class="status-badge status-new">{html.escape(status)}</span>'
    color = STATUS_COLORS[status]
    return f'<span class="status-badge" style="background-color:{color}">{html.escape(status)}</span>'


def escape_text(text: str) -> str:
    """HTML-escapes text and neutralises Streamlit's markdown autolink for
    any email-like substring in it ('no blue anywhere') -- needed for ANY
    text rendered via st.markdown(unsafe_allow_html=True), not just a
    sender address: question text copied verbatim from a DDQ can contain
    an email too (caught live in Q35's prompt-injection test question).

    Wrapping text in a <span> is not enough on its own, and neither is
    HTML-encoding the "@" as "&#64;": Streamlit's autolink step runs on the
    final rendered text nodes, by which point an HTML entity has already
    decoded back to a literal "@" -- confirmed by inspecting the live DOM
    both ways. What actually works: a zero-width word-joiner (U+2060)
    right after the "@", which is invisible on screen (no visible gap) but
    breaks the autolink pattern's requirement for a domain character
    immediately after "@"."""
    return html.escape(text).replace("@", "@⁠")


def plain_text(text: str, bold: bool = False) -> str:
    """Renders text via raw HTML instead of st.markdown, so an email-like
    string is never auto-linkified into a blue underlined <a href="mailto:">
    tag (section 8: 'plain dark text, not a blue underlined link')."""
    escaped = escape_text(text)
    if bold:
        escaped = f"<strong>{escaped}</strong>"
    return f'<span class="plain-text">{escaped}</span>'
