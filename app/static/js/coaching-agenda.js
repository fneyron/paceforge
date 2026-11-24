// Coaching › « Mon agenda » and « Dans mon calendrier », what the page does beyond its plain links and forms.
//
// - « Connecter Google Agenda », « Connecter Outlook » in the app: Google refuses its sign-in inside a WebView, so the
//   button asks the server for the link of the system's sign-in sheet (a one-time ticket in its fragment) and goes to
//   it; the app opens that link in the sheet (mobile/App.tsx) and comes back on « Mon agenda » with the outcome. In a
//   browser the button is a plain link to the provider's consent page.
// - « Calendrier de l’iPhone », in an app that reads the phone's calendar (window.paceforgeNativeCalendar): the page
//   asks the app (connect, settings), and as it loads asks for a silent reading when the last one is old or was
//   refused (its state, written by the server); the app hands back busy intervals alone, which the page posts to the
//   server in its own session (window.paceforgePhoneCalendar.send).
// - « Google Agenda », « Outlook » of « Dans mon calendrier »: in a browser, in a new tab (the app opens them in the
//   system's browser by itself).
(function () {
    function say(text) {
        var row = document.querySelector(".pf-coach-agenda"), said = row && row.querySelector("[data-agenda-said]");
        if (row && !said) {
            said = document.createElement("p");
            said.className = "pf-coach-error"; said.setAttribute("role", "alert"); said.setAttribute("data-agenda-said", "");
            row.insertBefore(said, row.firstChild);
        }
        if (said) said.textContent = text;
    }

    if (!window.ReactNativeWebView) {
        document.querySelectorAll("[data-feed-provider]").forEach(function (link) { link.target = "_blank"; });
    }

    if (window.paceforgeNativeAgenda) {
        document.addEventListener("click", function (event) {
            var link = event.target.closest && event.target.closest("[data-agenda-connect]");
            if (!link) return;
            event.preventDefault();
            if (link.getAttribute("aria-busy") === "true") return;
            link.setAttribute("aria-busy", "true");
            var provider = link.getAttribute("data-agenda-connect");
            fetch("/coaching/agenda/" + encodeURIComponent(provider) + "/app", { method: "POST", credentials: "same-origin", headers: { "Accept": "application/json" } })
                .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
                .then(function (body) { window.location.href = body.link; })
                .catch(function () { say("La connexion ne peut pas s’ouvrir. Réessaie dans un instant."); })
                .finally(function () { link.removeAttribute("aria-busy"); });
        });
    }

    if (!window.paceforgeNativeCalendar || !window.ReactNativeWebView) return;
    var box = document.querySelector("[data-phone-calendar-state]");
    var state = box ? box.getAttribute("data-phone-calendar-state") : "none";
    function ask(action) {
        window.ReactNativeWebView.postMessage(JSON.stringify({ type: "phone-calendar", action: action, state: state, page_url: location.href }));
    }
    window.paceforgePhoneCalendar = {
        // `body`: {intervals: [{start, end}]} in UTC seconds, or {refused: true}; `reload`: back on « Mon agenda »
        send: function (body, reload) {
            fetch("/coaching/agenda/iphone", { method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json", "Accept": "application/json" }, body: JSON.stringify(body) })
                .then(function (r) {
                    if (!r.ok) throw new Error(String(r.status));
                    if (!reload) return;
                    // back on « Mon agenda », read again (the same address with another fragment would not reload)
                    var agenda = "/coaching?outil=agenda";
                    if (location.pathname + location.search === agenda) { location.hash = "coach-tool-agenda"; location.reload(); }
                    else location.replace(agenda + "#coach-tool-agenda");
                })
                .catch(function () { if (reload) say("Le calendrier de l’iPhone n’a pas pu être lu. Réessaie dans un instant."); })
                .finally(function () {
                    var button = document.getElementById("coach-cal-connect-iphone");
                    if (button && !reload) button.removeAttribute("aria-busy");
                });
        }
    };
    document.addEventListener("click", function (event) {
        var button = event.target.closest && event.target.closest("[data-phone-calendar]");
        if (!button) return;
        event.preventDefault();
        if (button.getAttribute("aria-busy") === "true") return;
        var action = button.getAttribute("data-phone-calendar");
        if (action === "connect") {
            button.setAttribute("aria-busy", "true");
            setTimeout(function () { button.removeAttribute("aria-busy"); }, 20000);  // the prompt left unanswered
        }
        ask(action === "settings" ? "settings" : "connect");
    });
    if (state === "stale" || state === "refused") ask("refresh");
})();
