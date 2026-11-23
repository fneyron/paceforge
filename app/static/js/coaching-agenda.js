// « Mon agenda » › « Connecter Google Agenda », « Connecter Outlook » in the app: Google refuses its sign-in inside a
// WebView, so the button asks the server for the link of the system's sign-in sheet (a one-time ticket in its
// fragment) and goes to it; the app opens that link in the sheet (mobile/App.tsx) and comes back on « Mon agenda »
// with the outcome. In a browser the button is a plain link to the provider's consent page.
(function () {
    if (!window.paceforgeNativeAgenda) return;
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
            .catch(function () {
                var row = link.closest(".pf-coach-agenda"), said = row && row.querySelector("[data-agenda-said]");
                if (row && !said) {
                    said = document.createElement("p");
                    said.className = "pf-coach-error"; said.setAttribute("role", "alert"); said.setAttribute("data-agenda-said", "");
                    row.insertBefore(said, row.firstChild);
                }
                if (said) said.textContent = "La connexion ne peut pas s’ouvrir. Réessaie dans un instant.";
            })
            .finally(function () { link.removeAttribute("aria-busy"); });
    });
})();
