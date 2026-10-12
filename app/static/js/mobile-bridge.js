/* Delivery is retried until the native app acknowledges this request.
 * Older builds consume a matching request once; duplicates are harmless.
 * No account token or health data is stored in the page or in a URL. */
(function () {
    'use strict';
    const element = document.getElementById('paceforge-bridge-message');
    if (!element) return;
    const message = JSON.parse(element.textContent);
    const key = message.nonce || message.challenge;
    let timer, attempts = 0, acknowledged = false;
    function stop() { clearInterval(timer); }
    function send() {
        if (acknowledged) return;
        if (++attempts > 60) {
            stop();
            document.getElementById('bridge-status').textContent = 'Le retour à l’application a été interrompu. Réessaie ou ouvre les connexions.';
            document.getElementById('bridge-retry').hidden = false;
            return;
        }
        try {
            if (window.ReactNativeWebView) window.ReactNativeWebView.postMessage(JSON.stringify(message));
        } catch (_) { /* The native bridge may still be restarting. */ }
    }
    window.paceforgeBridge = {
        send,
        ack: function (received) {
            if (received !== key) return;
            acknowledged = true;
            stop();
        },
        retry: function () {
            if (acknowledged) return;
            stop(); attempts = 0;
            document.getElementById('bridge-status').textContent = 'La connexion se termine.';
            document.getElementById('bridge-retry').hidden = true;
            timer = setInterval(send, 500); send();
        },
    };
    window.addEventListener('pagehide', stop, { once: true });
    window.paceforgeBridge.retry();
}());
