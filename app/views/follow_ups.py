"""Follow-ups — the action queue, grouped by what to send, with a ready draft for each."""
from __future__ import annotations

import streamlit as st

from app.ui import components as ui
from app.ui import data

s = data.settings()
queue = data.metric("follow_up_queue")

# Tier order = value of the follow-up: after an interview first, plain nudges last.
TIERS = {
    "Thank-you / status check": (
        "after an interview — thank them and ask about timing",
        "A short thank-you that restates fit and asks for a decision timeline. The highest-value "
        "email in the queue.",
        "Subject: Thank you — {role} interview\n\n"
        "Hi {contact},\n\n"
        "Thank you again for the conversation about the {role} role at {company}. I enjoyed "
        "learning about the team and remain very interested.\n\n"
        "Could you share where things stand and the expected timeline for next steps? I'm happy "
        "to provide anything else that would help.\n\n"
        "Best regards,\n[Your name]"),
    "Confirm the assessment was received": (
        "with an assessment outstanding — confirm it arrived",
        "Confirm the submission was received and ask what happens next.",
        "Subject: {role} assessment — confirming receipt\n\n"
        "Hi {contact},\n\n"
        "I completed the assessment for the {role} position at {company} and wanted to confirm "
        "it was received. Please let me know if anything else is needed from my side.\n\n"
        "Kind regards,\n[Your name]"),
    "Final nudge": (
        "about to be marked ghosted — one last, polite nudge",
        "Silent long enough that the ghosting rule is close. One courteous message, then move "
        "on.",
        "Subject: Following up — {role} application\n\n"
        "Hi {contact},\n\n"
        "I applied for the {role} role at {company} on {applied} and wanted to follow up once "
        "more. I'm still very interested and would welcome the chance to discuss how I could "
        "contribute.\n\n"
        "If the role has been filled, I'd be grateful for a quick note so I can update my "
        "records.\n\n"
        "Thank you,\n[Your name]"),
    "Polite follow-up": (
        "quiet for a week or more — a polite check-in",
        "A brief, friendly check-in that adds one new reason you fit.",
        "Subject: {role} application — following up\n\n"
        "Hi {contact},\n\n"
        "I hope you're well. I applied for the {role} role at {company} on {applied} and wanted "
        "to check on the status of my application. I'm particularly drawn to the role because "
        "[one specific reason].\n\n"
        "Thank you for your time,\n[Your name]"),
}

n = len(queue)
ui.lede("Follow-ups", [
    (f"{n} application{'s' if n != 1 else ''}", "em"),
    (f" {'have' if n != 1 else 'has'} gone quiet for {s.follow_up_after_days}+ days." if n
     else " — nothing is waiting on you.", ""),
], "Grouped by what to send, most valuable first, each with a draft you can copy. Marking one "
   "as followed up logs a dated note and restarts its silence clock, so the queue and the "
   "ghosting rule measure silence since your nudge.")

if queue.empty:
    ui.empty_state("All caught up", "Nothing has been quiet long enough to chase.")
    st.stop()

counts = queue["suggested_action"].value_counts()
ui.mini_stats([(tier, f"{int(counts.get(tier, 0))}") for tier in TIERS])

section_no = 0
for tier, (headline, advice, template) in TIERS.items():
    rows = queue[queue["suggested_action"] == tier]
    if rows.empty:
        continue
    section_no += 1
    with ui.section(section_no, tier, f"{len(rows)} {headline}", advice):
        for i, r in enumerate(rows.itertuples(index=False)):
            with st.container(key=f"jt-fu-{section_no}-{i}"):
                left, right = st.columns([9, 3], vertical_alignment="top")
                with left:
                    ui.render(
                        f'<div class="jt-fu-title">{ui.esc(str(r.company_name))}</div>'
                        f'<div class="jt-fu-sub">{ui.esc(str(r.role_title))}</div>'
                        f'<div class="jt-fu-meta">Applied {ui.fmt_date(r.applied_date)} via '
                        f'{ui.esc(str(r.platform))} · last activity '
                        f'{ui.fmt_date(r.last_activity_date)} · {ui.status_mark(str(r.current_status))}'
                        '</div>')
                with right:
                    ui.render(f'<div class="jt-fu-age">{int(r.days_since_activity)}'
                              f'<small>days quiet</small></div>')
                draft = template.format(role=r.role_title, company=r.company_name,
                                        contact="[Hiring team]",
                                        applied=ui.fmt_date(r.applied_date, "%d %B"))
                actions = st.container(horizontal=True, gap="small", vertical_alignment="center")
                with actions:
                    with st.popover("Draft email", icon=":material/edit_note:"):
                        st.caption("Copy with the icon in the corner of the block.")
                        st.code(draft, language=None, wrap_lines=True)
                    if st.button("Mark followed up", key=f"fu_done_{r.application_id}",
                                 icon=":material/done:"):
                        data.repo().record_follow_up(str(r.application_id), note=tier)
                        data.rebuild(f"Logged a follow-up to {r.company_name}")
                        st.rerun()
                    if isinstance(r.gmail_link, str) and r.gmail_link.startswith("http"):
                        st.link_button("Open thread", r.gmail_link, type="tertiary",
                                       icon=":material/open_in_new:")
                    if isinstance(r.job_url, str) and r.job_url.startswith("http"):
                        st.link_button("Posting", r.job_url, type="tertiary",
                                       icon=":material/open_in_new:")
