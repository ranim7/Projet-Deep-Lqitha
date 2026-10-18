"""Demo video v2: finder -> owner (match + ownership check) -> recovery plan (agentic RAG) -> found-items search -> board.
Same helpers as record_demo.py (caption panel, wait markers, screenshots).  python scripts/record_demo2.py"""
import time

from playwright.sync_api import sync_playwright

import record_demo as R
from record_demo import cap, ev, send, shot, pick_place

PLAN_MSG = "dhaya3li portefeuille fih carte d'identité w carte bancaire fi louage Sousse lbera7, chnowa na3mel?"
SEARCH_MSG = "fama chkoun l9a mrayet chams k7la (lunettes de soleil noires) fi Kairouan?"


def fresh(page):
    page.evaluate("localStorage.clear()")
    page.reload()
    page.wait_for_selector("#examples .chip")
    time.sleep(1)


def cool(seconds):
    """Pause between scenes so the per-minute token limits of the API recover; removed from the edited video."""
    ev("cut_start")
    time.sleep(seconds)
    ev("cut_end")


def read_last(page, steps=7, pause=1.7):
    """Scroll the chat from the top of the last answer to its end, slowly, so the viewer can read it."""
    page.evaluate("(() => { const l = document.querySelector('#log'); const m = [...l.querySelectorAll('.msg.bot')].pop();"
                  " l.style.scrollBehavior = 'smooth'; l.scrollTop = m.offsetTop - 20; })()")
    time.sleep(pause)
    for _ in range(steps):
        done = page.evaluate("(() => { const l = document.querySelector('#log'); const before = l.scrollTop; l.scrollTop += 230;"
                             " return l.scrollTop + l.clientHeight >= l.scrollHeight - 5 || l.scrollTop === before; })()")
        time.sleep(pause)
        if done:
            break


def record():
    R.OUT.mkdir(parents=True, exist_ok=True)
    raw = R.OUT / "raw"
    raw.mkdir(exist_ok=True)
    for f in raw.glob("*.webm"):
        f.unlink()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(R.PROFILE), headless=False, viewport={"width": R.W, "height": R.H},
            record_video_dir=str(raw), record_video_size={"width": R.W, "height": R.H}, args=["--window-size=1940,1180"])
        ctx.add_init_script(R.PANEL_JS)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        R.T0[0] = time.time()
        ev("start")

        # ---------------------------------------------------------------- 0. the app
        page.goto(R.APP)
        fresh(page)
        time.sleep(1)
        cap(page, "LQITHA · لقيتها", "Lost &amp; found assistant for Tunisia",
            "People who <b>lose</b> or <b>find</b> an item talk to Lqitha in <b>Derja, French, Arabic or English</b>, "
            "by <b>text, photo or voice note</b>.<br><br>Every message runs through one <b>n8n</b> workflow of specialised agents "
            "and two <b>RAG</b> pipelines.")
        time.sleep(7)

        # ---------------------------------------------------------------- 1. finder with a photo
        cap(page, "SCENE 1 · THE FINDER", "Amine found a backpack in a louage",
            "He takes a <b>photo</b>, picks the place (real OpenStreetMap list) and writes in <b>Derja</b>.")
        place = pick_place(page, "Sousse", "louage")
        ev("place", place=place)
        page.set_input_files("#photo", str(R.PHOTO))
        time.sleep(2)
        r = send(page, "l9it sac à dos fi louage Sousse lyoum")
        ev("reply", text=r[:300])
        cap(page, "AGENT 1 · INTAKE &amp; ROUTER", "Understood: found item, place, date",
            "The Intake agent extracted the details and asks <b>one</b> question: a <b>secret detail</b> that only the real "
            "owner can know. The secret never goes back to the language model.")
        time.sleep(7)
        r = send(page, "fih kteb mta3 math w stylo a7mer", timeout=240)
        ev("reply", text=r[:300])
        cap(page, "AGENTS 2 + 3 · PERCEPTION &amp; MATCHER", "The photo becomes a searchable record",
            "The <b>vision model</b> describes the photo in a normalised record, which is embedded with <b>bge-m3</b> and "
            "compared with all open lost reports. No owner yet: Amine also gets a short plan telling him where to hand it in.")
        time.sleep(8)
        shot(page, "app_finder.png")

        # ---------------------------------------------------------------- 2. owner
        cool(45)
        fresh(page)
        cap(page, "SCENE 2 · THE OWNER", "Salma lost her backpack",
            "On her own phone (a new session) she describes it <b>from memory, in Derja</b>, without a photo.")
        time.sleep(4)
        pick_place(page, "Sousse", "louage")
        r = send(page, "dhaya3li sac à dos noir (k7el) fih khtout turquoise, marque Solimo, fi louage Sousse lyoum")
        ev("reply", text=r[:300])
        for _ in range(2):   # the intake may ask one more question before matching
            if page.locator("#log .msg.bot .card").count() or "?" not in r[-200:]:
                break
            r = send(page, "fi station louage Sousse, lyoum el sbe7")
            ev("reply", text=r[:300])
        if not page.locator("#log .msg.bot .card").count():
            ev("abort", reason="no match card")
            raise SystemExit("no match - scenario aborted")
        cap(page, "AGENTS 3 + 4 + 6 · MATCH, VERIFY, NOTIFY", "Cross-modal match found",
            "Her Derja text is matched with the finder's <b>photo</b>. The <b>Verifier</b> checks for contradictions, then the "
            "<b>Notifier</b> asks an ownership question <b>without revealing the secret</b>.")
        time.sleep(8)
        shot(page, "app_owner_match.png")
        cap(page, "SECURITY", "An impostor cannot claim the item",
            "First, a <b>wrong answer</b>: the Claim-check agent rejects it. After 2 wrong answers the claim is locked.")
        time.sleep(3)
        r = send(page, "fih laptop w charger")
        ev("reply", text=r[:300])
        time.sleep(4)
        cap(page, "AGENT 6b · CLAIM CHECK", "The right answer releases the pickup place",
            "Now the real details. The answer is compared with the secret <b>by meaning</b>, in any language.")
        time.sleep(2)
        r = send(page, "kteb math w stylo a7mer")
        ev("reply", text=r[:300])
        time.sleep(6)
        shot(page, "app_owner_verified.png")

        # ---------------------------------------------------------------- 3. recovery plan (agentic RAG)
        cool(50)
        fresh(page)
        cap(page, "SCENE 3 · AGENTIC RAG", "What exactly must I do now?",
            "A wallet with an <b>ID card</b> and a <b>bank card</b> lost in a louage. One search cannot cover this: the "
            "<b>planner</b> splits the situation into sub-questions, each routed to the right sources "
            "(laws, administration, operators, internal procedures).")
        time.sleep(4)
        r = send(page, PLAN_MSG, timeout=300)
        ev("reply", text=r[:300])
        if not page.locator("#log .msg.bot .plan").count():
            r = send(page, "ma 3andich detail okhra. chnowa na3mel taw?", timeout=300)
            ev("reply", text=r[:300])
        cap(page, "PLAN · RETRIEVE · RE-RANK · WRITE · VERIFY", "A personalised plan, checked against its sources",
            "Steps grouped by timing (now, today, this week), each with a citation [n]. A second model <b>re-ranked</b> the passages "
            "and another one <b>verified every step</b>: unsupported steps are removed.")
        time.sleep(3)
        read_last(page, steps=4, pause=2.6)
        cap(page, "DEADLINES + PRE-FILLED LETTER", "Ready to use at the police station",
            "The ID-card deadline is turned into a date, and the declaration of loss is pre-filled with the user's facts. "
            "Personal data stay as placeholders.")
        read_last(page, steps=0, pause=0.2)
        time.sleep(0.5)
        page.evaluate("(() => { const l = document.querySelector('#log'); l.scrollTop = l.scrollHeight; })()")
        time.sleep(7)
        shot(page, "app_plan.png")

        # ---------------------------------------------------------------- 4. found-items search (multimodal RAG)
        cool(65)
        fresh(page)
        cap(page, "SCENE 4 · FOUND-ITEMS SEARCH", "Has someone found my sunglasses?",
            "The request (text <b>or a photo</b>) becomes the same record as the indexed found items. The search favours the "
            "right category, place and date without excluding anything, then an agent <b>re-ranks</b> the candidates.")
        time.sleep(3)
        r = send(page, SEARCH_MSG, timeout=240)
        ev("reply", text=r[:300])
        read_last(page, steps=2, pause=2.5)
        cap(page, "AGENT 8 · RE-RANK + ANSWER", "Candidates with their reasons",
            "Each item is labelled <b>very close</b> or <b>possible</b> with the agreements found. Ownership is never asserted: "
            "<b>C'est peut-être à moi</b> starts the secure check.")
        time.sleep(8)
        shot(page, "app_search.png")

        # ---------------------------------------------------------------- 5. board
        page.click("nav button[data-tab='boardTab']")
        time.sleep(2)
        page.evaluate("window.scrollTo(0, 0)")
        cap(page, "FOUND ITEMS · DERJA", "The board speaks Derja by default",
            "Real data placed in real Tunisian stations. Descriptions are translated by an LLM into <b>Derja</b>, French or English, and cached.")
        time.sleep(5)
        try:
            page.click(".langsw button[data-l='fr']")
            time.sleep(3)
            page.click(".langsw button[data-l='en']")
            time.sleep(3)
            page.click(".langsw button[data-l='derja']")
            time.sleep(2)
        except Exception as e:
            ev("lang_error", error=str(e)[:200])
        shot(page, "app_board.png")

        ev("end")
        time.sleep(1)
        video = page.video
        ctx.close()
        if video:
            ev("video", path=str(video.path()))


if __name__ == "__main__":
    record()
