// PaceForge - HTMX configuration and the helpers every page shares (toast, confirmation)
(function () {
    var toastTimer = null;
    // One toast for the whole app: the page's own #pf-toast when it has one, else one added to the body.
    function pfToast(message) {
        var el = document.getElementById("pf-toast"), created = false;
        if (!el) {
            el = document.createElement("div");
            el.id = "pf-toast";
            el.className = "pf-toast hidden";
            el.setAttribute("role", "status");
            el.setAttribute("aria-live", "polite");
            document.body.appendChild(el);
            created = true;
        }
        function show() {
            el.textContent = message;
            el.classList.remove("hidden");
            // a toast inside a panel that is hidden (the plan behind the débrief) moves to the body
            if (!el.getClientRects().length && el.parentNode !== document.body) document.body.appendChild(el);
        }
        // a live region announces changes, not its first content: a new one is filled on the next tick
        if (created) setTimeout(show, 0); else show();
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { el.classList.add("hidden"); }, 2600);
    }
    // A confirmation before something irreversible: pfConfirm(message, onConfirm, confirmLabel).
    // The native dialog has one OK button, so the label is accepted but not shown.
    function pfConfirm(message, onConfirm, confirmLabel) {
        var ok = window.confirm(message);
        if (ok && typeof onConfirm === "function") onConfirm();
        return ok;
    }
    window.pfToast = pfToast;
    window.pfConfirm = pfConfirm;
})();

document.addEventListener("DOMContentLoaded", function () {
    // Initialize Lucide icons
    if (typeof lucide !== "undefined") {
        lucide.createIcons();
    }

    // Configure HTMX
    document.body.addEventListener("htmx:configRequest", function (event) {
        // Add CSRF token if needed in the future
    });

    // A request that failed or never answered: the detail in the console, one line for the person.
    // Not for a poll (hx-trigger="every …"), nor for the 404 the app answers to stop one, and not
    // again within 10 s: a server that is down must not blink the toast every 3 s.
    var FAILED = "Impossible de charger. Réessaie.", lastFailedToast = 0;
    function requestFailed(event) {
        console.error("HTMX request error:", event.detail);
        var elt = event.detail.elt, xhr = event.detail.xhr;
        if (elt && elt.getAttribute && /\bevery\b/.test(elt.getAttribute("hx-trigger") || "")) return;
        if (xhr && xhr.status === 404) return;
        if (Date.now() - lastFailedToast < 10000) return;
        lastFailedToast = Date.now();
        window.pfToast(FAILED);
    }
    document.body.addEventListener("htmx:responseError", requestFailed);
    document.body.addEventListener("htmx:sendError", requestFailed);

    // Stop polling when an element is removed or swapped out
    document.body.addEventListener("htmx:beforeSwap", function (event) {
        if (event.detail.xhr.status === 404) {
            event.detail.shouldSwap = false;
        }
    });

    // Re-initialize Lucide icons after every HTMX content swap
    document.body.addEventListener("htmx:afterSettle", function () {
        if (typeof lucide !== "undefined") {
            lucide.createIcons();
        }
    });

    // Mobile nav toggle
    const navToggle = document.getElementById("nav-toggle");
    const navMenu = document.getElementById("nav-menu");
    if (navToggle && navMenu) {
        navToggle.addEventListener("click", function () {
            navMenu.classList.toggle("hidden");
        });
    }
});
