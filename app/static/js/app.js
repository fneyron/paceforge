// PaceForge - HTMX configuration
document.addEventListener("DOMContentLoaded", function () {
    // Initialize Lucide icons
    if (typeof lucide !== "undefined") {
        lucide.createIcons();
    }

    // Configure HTMX
    document.body.addEventListener("htmx:configRequest", function (event) {
        // Add CSRF token if needed in the future
    });

    // Handle HTMX errors gracefully
    document.body.addEventListener("htmx:responseError", function (event) {
        console.error("HTMX request error:", event.detail);
    });

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
