/* Neural Strata — Phase 9 (B6): the outbox chip and FORCE SYNC.  Zero deps.
   ---------------------------------------------------------------------------
   This is the visible half of the two-tier database, and it exists because the
   offline-first promise is worthless if nobody can see whether the second tier
   has caught up. Four numbers, one line:

       OUTBOX: 12 pending · synced 3m ago

   and a button that drains the queue on demand. Everything the chip prints is
   read from rows (`/api/sync/status` counts `synced_at IS NULL`), never from a
   remembered counter — a chip that quotes what the daemon last thought is a chip
   that disagrees with the database exactly when someone is looking at it for
   reassurance.

   Rules, each one earned by a failure mode:

   1. **An outage must be visible, not spectacular.** With the link down the queue
      climbs and the chip says so in amber; it does not shout, and it does not go
      blank. The drill script depends on this: "watch the counter climb, then
      press the button and watch it go to zero".
   2. **`LOCAL ONLY` is a state, not an error.** With no cloud configured the
      server runs perfectly and *no* cloud code executes (§F.5). The chip says so
      plainly, because a chip that reads "0 pending · synced never" forever would
      look like a bug in a system that is working exactly as designed.
   3. **A refusal is shown in the server's words.** `POST /api/sync` answers 409
      with a reason when sync is unconfigured or already running, and the chip
      prints that reason rather than a shrug — the same discipline as the alert
      panel's suppression rows.
   4. **Never blank on a failed poll.** The last good frame stays, with its age.
      The one moment this chip must not be empty is the moment the network is gone,
      which is precisely when a poll fails.

   Mounted from `index.html` as `<span id="sync-chip">` + `<button id="sync-force">`
   in the health strip's third row; this file finds them and starts its own poll.
*/
(function () {
  "use strict";

  const POLL_MS = 5000;
  const $ = (id) => document.getElementById(id);

  let lastGood = null;      /* the last frame that parsed — never blank on failure */
  let busy = false;

  function ago(seconds) {
    if (seconds == null) return "never synced";
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return "synced " + s + "s ago";
    if (s < 3600) return "synced " + Math.round(s / 60) + "m ago";
    if (s < 86400) return "synced " + Math.round(s / 3600) + "h ago";
    return "synced " + Math.round(s / 86400) + "d ago";
  }

  /* The server's own field names, read exactly (`GET /api/sync/status`):
     `total`, `rollups`, `alerts`, `outbox`, `cloud_http`, `cloud_db`, `signed`,
     `site_id`, `reachable`, `last_error`, `last_success_s_ago`, `outbox_detail`.
     A chip that guessed a name would render `undefined pending` — and it would do
     it on stage, where the number is the point. */
  function enabled(s) {
    return !!(s && (s.cloud_http || s.cloud_db));
  }

  /* The colour is the same vocabulary the strips and the alert rows use: green =
     nothing to do, amber = there is something waiting, red = something is wrong
     with the uplink itself (a full queue, rows that gave up, a recorded error). */
  function severity(s) {
    if (!enabled(s)) return "local";
    if (!s) return "local";
    const d = s.outbox_detail || {};
    if (d.full || (d.failed || 0) > 0 || s.last_error) return "bad";
    if ((s.total || 0) > 0) return "wait";
    return "ok";
  }

  const COLOUR = {
    ok:    ["var(--ok)", "rgba(74,222,128,.10)"],
    wait:  ["#ffb84d", "rgba(224,165,32,.12)"],
    bad:   ["#ff6b6b", "rgba(214,69,69,.16)"],
    local: ["#9fb3c8", "rgba(159,179,200,.10)"]
  };

  function render(s, note) {
    const chip = $("sync-chip");
    const btn = $("sync-force");
    if (!chip) return;

    const kind = severity(s);
    const pair = COLOUR[kind] || COLOUR.local;
    chip.style.color = pair[0];
    chip.style.background = pair[1];

    if (!enabled(s)) {
      chip.textContent = "OUTBOX: " + ((s && s.total) || 0) + " pending · LOCAL ONLY";
    } else {
      chip.textContent = "OUTBOX: " + (s.total || 0) + " pending · "
        + ago(s.last_success_s_ago);
    }
    if (note) chip.textContent += " · " + note;

    /* The tooltip is where the honesty lives: the breakdown, the age of the oldest
       waiting row, and whether the far end answered the last probe. A chip can only
       carry four words; the detail has to be one hover away. */
    const parts = [];
    if (s) {
      parts.push("site " + (s.site_id || "?"),
                 "rollups " + (s.rollups || 0), "alerts " + (s.alerts || 0),
                 "outbox " + (s.outbox || 0));
      if (s.outbox_detail) {
        parts.push("oldest pending " + Math.round(s.outbox_detail.oldest_pending_age_s || 0) + "s");
        parts.push("max attempts " + (s.outbox_detail.max_attempts_pending || 0));
        if (s.outbox_detail.full) parts.push("QUEUE FULL — nothing is being dropped, "
          + "but a flush is overdue");
        if (s.outbox_detail.failed) parts.push(s.outbox_detail.failed + " row(s) gave up "
          + "after " + (s.max_attempts || 0) + " attempts — see last_error");
      }
      parts.push((s.signed ? "signed" : "unsigned"), "every " + (s.interval_s || 0) + "s");
      // The half-configured case is a real state, not a bug: the HTTP path carries the
      // outbox, but the hourly rollups and the incident record need the central
      // Postgres — so they wait, visibly, instead of looking like a stuck queue.
      if (!s.cloud_db) parts.push("no central Postgres (NS_CLOUD_DB_URL) — rollups "
        + "and alerts wait, the outbox still ships");
      if (s.reachable && !s.reachable.ok) parts.push("backhaul probe: "
        + (s.reachable.reason || s.reachable.error || "no answer"));
      if (s.last_error) parts.push("last error: " + s.last_error);
    }
    chip.title = parts.join(" · ") || "no sync status yet";
    chip.classList.toggle("nodata", !s);

    if (btn) {
      btn.disabled = busy;
      btn.textContent = busy ? "SYNCING…" : "FORCE SYNC";
    }
  }

  function tick() {
    if (busy) return;                      /* a force-sync owns the chip meanwhile */
    fetch(window.apiUrl("/api/sync/status"))
      .then((r) => r.json())
      .then((s) => { lastGood = s; render(s); })
      .catch(() => {
        /* Keep the last good frame, and say how stale it is. */
        if (lastGood) render(lastGood, "status stale");
        else render(null, "no status");
      });
  }

  function forceSync() {
    if (busy) return;
    busy = true;
    render(lastGood);
    const btn = $("sync-force");
    if (btn) { btn.disabled = true; btn.textContent = "SYNCING…"; }

    fetch(window.apiUrl("/api/sync"), { method: "POST" })
      .then(function (r) {
        return r.json().then(function (body) { return { status: r.status, body: body }; });
      })
      .then(function (out) {
        /* 409 is the documented refusal: no cloud configured, or a cycle already
           running. Printed in the server's own words, because "it did nothing" is
           not an explanation. */
        const body = out.body || {};
        const note = out.status === 409 ? (body.reason || "refused")
          : out.status >= 400 ? ("failed: " + String(body.error || "").split("\n")[0])
          : "sent " + ((body.rollups || 0) + (body.alerts || 0) + (body.outbox || 0))
            + " row(s)";
        return fetch(window.apiUrl("/api/sync/status"))
          .then((r) => r.json())
          .then((s) => { lastGood = s; render(s, note); });
      })
      .catch(function () { render(lastGood, "offline — the queue keeps filling"); })
      .finally(function () {
        busy = false;
        const b = $("sync-force");
        if (b) { b.disabled = false; b.textContent = "FORCE SYNC"; }
      });
  }

  function mount() {
    const chip = $("sync-chip");
    if (!chip || chip.dataset.syncMounted) return;      /* never poll twice */
    chip.dataset.syncMounted = "1";
    const btn = $("sync-force");
    if (btn) btn.addEventListener("click", forceSync);
    tick();
    setInterval(tick, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
