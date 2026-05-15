// ADHDman read-only web memory dashboard.
// Strictly read-only: fetches GET /dashboard, renders sections, never mutates.

(function () {
  "use strict";

  const DASHBOARD_URL = "/dashboard";

  function setStatus(text) {
    const el = document.getElementById("status");
    if (el) {
      el.textContent = text;
    }
  }

  function clearChildren(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function appendEmpty(list, message) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = message;
    list.appendChild(li);
  }

  function makeItem(primary, metaText) {
    const li = document.createElement("li");
    const title = document.createElement("span");
    title.textContent = primary;
    li.appendChild(title);
    if (metaText) {
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = metaText;
      li.appendChild(meta);
    }
    return li;
  }

  function renderNow(today) {
    const message = document.getElementById("now-message");
    const oneThing = document.getElementById("now-one-thing");
    const counts = document.getElementById("now-counts");
    message.textContent = today && today.message ? today.message : "";
    clearChildren(oneThing);
    if (today && today.one_thing && today.one_thing.text) {
      oneThing.textContent = today.one_thing.text;
    } else {
      oneThing.textContent = "";
    }
    clearChildren(counts);
    const c = (today && today.counts) || {};
    const entries = [
      ["open tasks", c.open_tasks],
      ["open inbox", c.open_inbox],
      ["upcoming events", c.upcoming_events],
    ];
    entries.forEach(function (pair) {
      const li = document.createElement("li");
      li.textContent = pair[0] + ": " + (pair[1] == null ? 0 : pair[1]);
      counts.appendChild(li);
    });
  }

  function renderInbox(items) {
    const list = document.getElementById("inbox-list");
    clearChildren(list);
    if (!items || items.length === 0) {
      appendEmpty(list, "Inbox is empty.");
      return;
    }
    items.forEach(function (item) {
      list.appendChild(makeItem(item.text, "captured " + (item.created_at || "")));
    });
  }

  function renderTasks(items) {
    const list = document.getElementById("tasks-list");
    clearChildren(list);
    if (!items || items.length === 0) {
      appendEmpty(list, "No open tasks.");
      return;
    }
    items.forEach(function (task) {
      const meta = task.due_at ? "due " + task.due_at : "no due date";
      list.appendChild(makeItem(task.title, meta));
    });
  }

  function renderEvents(items) {
    const list = document.getElementById("events-list");
    clearChildren(list);
    if (!items || items.length === 0) {
      appendEmpty(list, "No upcoming events.");
      return;
    }
    items.forEach(function (event) {
      const meta = event.starts_at ? "starts " + event.starts_at : "no start time";
      list.appendChild(makeItem(event.title, meta));
    });
  }

  function renderWeek(days) {
    const list = document.getElementById("week-list");
    clearChildren(list);
    if (!days || days.length === 0) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "Nothing scheduled this week.";
      list.appendChild(li);
      return;
    }
    days.forEach(function (day) {
      const li = document.createElement("li");
      li.className = "week-day";
      const heading = document.createElement("h3");
      heading.textContent = day.date;
      li.appendChild(heading);
      const ul = document.createElement("ul");
      (day.items || []).forEach(function (item) {
        const meta = (item.time ? item.time + " · " : "") + item.type;
        ul.appendChild(makeItem(item.title, meta));
      });
      li.appendChild(ul);
      list.appendChild(li);
    });
  }

  function renderRecent(actions) {
    const list = document.getElementById("recent-list");
    clearChildren(list);
    if (!actions || actions.length === 0) {
      appendEmpty(list, "No recent activity yet.");
      return;
    }
    actions.forEach(function (action) {
      const primary = action.action_type + " · " + action.target_type + " #" + action.target_id;
      const meta = "action #" + action.id + " at " + (action.created_at || "");
      list.appendChild(makeItem(primary, meta));
    });
  }

  function formatSeconds(total) {
    if (total == null || isNaN(total)) {
      return "";
    }
    const seconds = Math.max(0, Math.round(total));
    const minutes = Math.floor(seconds / 60);
    const remainder = seconds % 60;
    if (minutes === 0) {
      return seconds + "s";
    }
    if (remainder === 0) {
      return minutes + "m";
    }
    return minutes + "m " + remainder + "s";
  }

  function renderFocus(focus) {
    const message = document.getElementById("focus-message");
    const sessionEl = document.getElementById("focus-session");
    const bodyDoubleEl = document.getElementById("focus-body-double");
    const survivalTag = document.getElementById("survival-tag");

    const isSurvival = !!(focus && focus.survival);
    if (survivalTag) {
      if (isSurvival) {
        survivalTag.textContent = "Survival mode";
        survivalTag.hidden = false;
      } else {
        survivalTag.textContent = "";
        survivalTag.hidden = true;
      }
    }

    const session = focus && focus.session;
    const target = focus && focus.target;
    if (session) {
      sessionEl.hidden = false;
      const targetText = target && target.title
        ? target.type + " · " + target.title
        : (session.target_type || "no target");
      document.getElementById("focus-target").textContent = targetText;
      document.getElementById("focus-started").textContent = session.started_at || "";
      const noteLabel = document.getElementById("focus-note-label");
      const noteEl = document.getElementById("focus-note");
      if (session.note) {
        noteLabel.hidden = false;
        noteEl.hidden = false;
        noteEl.textContent = session.note;
      } else {
        noteLabel.hidden = true;
        noteEl.hidden = true;
        noteEl.textContent = "";
      }
    } else {
      sessionEl.hidden = true;
    }

    const bodyDouble = focus && focus.body_double;
    if (bodyDouble) {
      bodyDoubleEl.hidden = false;
      const cadence = formatSeconds(bodyDouble.interval_seconds);
      document.getElementById("body-double-cadence").textContent = cadence
        ? "check in every " + cadence
        : "running";
      document.getElementById("body-double-last").textContent =
        bodyDouble.last_check_in_at || "no check-in yet";
    } else {
      bodyDoubleEl.hidden = true;
    }

    if (!session && !bodyDouble) {
      message.textContent = isSurvival
        ? "Survival mode is on. No focus or body-double session right now."
        : "No focus session right now. That is fine.";
    } else if (isSurvival) {
      message.textContent = "Survival mode is on.";
    } else {
      message.textContent = "";
    }
  }

  function render(payload) {
    renderNow(payload.today);
    renderFocus(payload.focus);
    renderInbox(payload.inbox);
    renderTasks(payload.tasks);
    renderEvents(payload.events);
    renderWeek(payload.week);
    renderRecent(payload.recent_actions);
  }

  function refresh() {
    setStatus("Loading…");
    fetch(DASHBOARD_URL, { method: "GET", headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("dashboard request failed: " + response.status);
        }
        return response.json();
      })
      .then(function (payload) {
        render(payload);
        const now = new Date();
        setStatus("Updated " + now.toLocaleTimeString());
      })
      .catch(function () {
        setStatus("Backend unavailable. Showing last known shell.");
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    const button = document.getElementById("refresh");
    if (button) {
      button.addEventListener("click", refresh);
    }
    refresh();
  });
})();
