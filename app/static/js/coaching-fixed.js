// « Ma séance fixe » (docs/coaching.md › « La séance fixe »): a weekday tapped fills in the duration proposed for it
// (read on the athlete's runs of that weekday, carried by its chip) and says why — until the athlete picks a duration
// themselves. On the start screen, the line « J’ai déjà une séance… » opens its answers, and the weekday chosen is
// ticked among the days. Without JavaScript every field shows and the server reads the box.
(function () {
    document.querySelectorAll("[data-fixed]").forEach(function (root) {
        var toggle = root.querySelector("[data-fixed-toggle]"), body = root.querySelector("[data-fixed-body]");
        var minutes = root.querySelector("[data-fixed-minutes]"), why = root.querySelector("[data-fixed-why]");
        var touched = false;
        if (minutes) minutes.addEventListener("change", function () { touched = true; });
        function open() { if (toggle && body) body.hidden = !toggle.checked; }
        if (toggle) toggle.addEventListener("change", open);
        open();
        root.querySelectorAll('input[name="fixed_day"]').forEach(function (chip) {
            chip.addEventListener("change", function () {
                if (!chip.checked) return;
                if (minutes && !touched) minutes.value = chip.getAttribute("data-minutes");
                if (why) why.textContent = touched ? "" : chip.getAttribute("data-why");
                var day = document.getElementById("coach-day-" + chip.value);  // the start screen's days
                if (day && day.type === "checkbox" && !day.checked) { day.checked = true; day.dispatchEvent(new Event("change", { bubbles: true })); }
            }, true);  // before the form's own listener: the week is drawn with the duration filled in
        });
    });
})();
